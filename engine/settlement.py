"""
精算（Settlement）モジュール（§7.3/§7.4、v0.4）

v0.3は「投票=ラウンド」で精算が1段だったが、v0.4はラウンドの中に複数の
投票が入る二重構造（§1.1）のため、精算も2段に分かれる:

- `settle_vote()`: 1回の投票の精算（§7.3の8手順）。Reveal→判定（退場または
  延長料徴収）→型Bの監査→型Cの投票単位条件判定（minority_side/in_minority）
  →支払える上限の固定→支払額の決定→一斉受け渡し→公示。
- `settle_round()`: ラウンドの精算（§7.4の8手順）。山の支払い→型Cの
  ラウンド単位条件判定（wins_round）→型Aの追加→支払える上限の固定→
  支払額の決定→一斉受け渡し→公示→（Financeは呼び出し側engine/game.pyが行う）。

「支払える上限の固定」（fix_payable_limits）と「成立順の割り当て」
（_plan_payments）はv0.3の実装をそのまま流用し、両方の精算から共通で呼ぶ。
「一斉の受け渡しと借金の確定」も `apply_payments()` に切り出して共通化した。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.models import (
    Contract, ContractSettlementReport, ObligationPayment,
    ObligationType, PlayerState, RoundOutcome, VoteOutcome, Vote,
)
from engine import contracts as contract_ops
from engine import player as player_ops
from engine import round as round_ops
from engine import vote as vote_ops


class VoteSettlementResult:
    """settle_vote() の戻り値"""

    __slots__ = ("players", "outcome", "report")

    def __init__(
        self,
        players: dict[str, PlayerState],
        outcome: VoteOutcome,
        report: ContractSettlementReport,
    ) -> None:
        self.players = players
        self.outcome = outcome
        self.report = report


class RoundSettlementResult:
    """settle_round() の戻り値"""

    __slots__ = ("players", "outcome", "report")

    def __init__(
        self,
        players: dict[str, PlayerState],
        outcome: RoundOutcome,
        report: ContractSettlementReport,
    ) -> None:
        self.players = players
        self.outcome = outcome
        self.report = report


def _sorted_active_contracts(contracts: list[Contract]) -> list[Contract]:
    """ACTIVE契約をcontract_seq昇順で返す（成立順の割り当ての土台、§7.3手順6）"""
    from engine.models import ContractStatus
    return sorted(
        (c for c in contracts if c.status == ContractStatus.ACTIVE and c.contract_seq is not None),
        key=lambda c: c.contract_seq,
    )


def fix_payable_limits(players: dict[str, PlayerState], config: GameConfig) -> dict[str, int]:
    """
    支払える上限を固定する（§7.3手順5・§7.4手順4）

    その時点の現金＋残り借入枠。同じ精算で契約から受け取るお金は含めない。
    呼び出し側は、山の受け取り（§7.4手順1）・延長料の徴収（§7.3手順2）を
    players に反映した「後」でこの関数を呼ぶこと。
    """
    return {
        pid: p.cash + player_ops.remaining_credit(p, config)
        for pid, p in players.items()
    }


def _plan_payments(
    items: list[ObligationPayment],
    payable_limits: dict[str, int],
) -> list[ObligationPayment]:
    """
    支払額の決定（§7.3手順6・§7.4手順5）

    items は既に (contract_seqの小さい順, 同じ契約内では記載順) で並んでいる
    前提。義務者ごとに残り上限を取り崩しながら進み、上限に届いた義務は
    残額だけ部分払いし、それより後の同じ義務者の義務は0円にする（他の
    義務者の残り上限には影響しない）。

    Args:
        items: 呼び出し側が確定した順序どおりのリスト
        payable_limits: 義務者ごとの支払える上限（手順5で固定済み。
            受け取りはこの時点では一切反映しない）

    Returns:
        paid を確定したObligationPaymentのリスト（同じ順序）
    """
    remaining = dict(payable_limits)
    planned: list[ObligationPayment] = []
    for item in items:
        cap = max(remaining.get(item.obligor, 0), 0)
        paid = min(item.promised, cap)
        remaining[item.obligor] = cap - paid
        planned.append(item.model_copy(update={"paid": paid}))
    return planned


def apply_payments(
    players: dict[str, PlayerState], planned: list[ObligationPayment],
) -> dict[str, PlayerState]:
    """
    一斉の受け渡しと借金の確定（§7.3手順7・§7.4手順6）

    全員分の支払い・受取を同時に（1人ずつ順に適用せず、合算した差分として）
    反映する。その後にマイナス分だけを開始後の借金（15%）にする。
    """
    updated = dict(players)
    net_delta: dict[str, int] = {}
    for item in planned:
        if item.paid <= 0:
            continue
        net_delta[item.obligor] = net_delta.get(item.obligor, 0) - item.paid
        net_delta[item.counterparty] = net_delta.get(item.counterparty, 0) + item.paid
    for pid, delta in net_delta.items():
        if delta == 0:
            continue
        updated[pid] = updated[pid].model_copy(update={"cash": updated[pid].cash + delta})
    for pid in list(updated):
        updated[pid] = player_ops.settle_negative_cash(updated[pid])
    return updated


def _shortfall_ids(planned: list[ObligationPayment]) -> list[str]:
    """払いきれなかった者のプレイヤーIDを返す（§7.3手順8・§7.4手順7）"""
    shortfall_by_obligor: dict[str, int] = {}
    for item in planned:
        gap = item.promised - item.paid
        if gap > 0:
            shortfall_by_obligor[item.obligor] = shortfall_by_obligor.get(item.obligor, 0) + gap
    return sorted(shortfall_by_obligor.keys())


# =============================================================================
# 投票の精算（§7.3）
# =============================================================================

def _collect_vote_due_items(
    contracts: list[Contract],
    votes: dict[str, Vote],
    outcome: VoteOutcome,
    round_num: int,
    vote_num: int,
    config: GameConfig,
) -> tuple[list[ObligationPayment], list[tuple[str, str]]]:
    """
    手順3（型Bの監査）・手順4（型Cの投票単位条件判定）: この投票の支払い対象
    になる義務を集める（§7.3手順3/4）

    契約の成立順（contract_seq昇順）・同じ契約内では義務の記載順で並べる。
    違約金は破った型B義務のインデックス位置にそのまま置く（§6.3）。
    """
    violated_obligations = contract_ops.audit_type_b(contracts, votes, round_num, vote_num)
    violated_ids = {ob.obligation_id for ob in violated_obligations}
    violations = [(ob.obligor, ob.obligation_id) for ob in violated_obligations]

    items: list[ObligationPayment] = []
    for contract in _sorted_active_contracts(contracts):
        for ob_index, ob in enumerate(contract.obligations):
            if ob.round_num != round_num or ob.vote_num != vote_num:
                continue

            if ob.ob_type == ObligationType.TYPE_B_VOTE:
                if ob.obligation_id not in violated_ids:
                    continue
                amount = config.penalty_amount
            elif ob.ob_type == ObligationType.TYPE_C_CONDITIONAL:
                if not contract_ops.evaluate_type_c_vote_condition(ob, outcome):
                    continue
                amount = contract_ops.resolve_type_c_amount(ob)
            else:
                continue

            items.append(ObligationPayment(
                contract_id=contract.contract_id,
                contract_seq=contract.contract_seq,
                ob_index=ob_index,
                obligor=ob.obligor,
                counterparty=ob.counterparty,
                ob_type=ob.ob_type,
                promised=amount,
                paid=0,
            ))

    return items, violations


def settle_vote(
    players: dict[str, PlayerState],
    votes: dict[str, Vote],
    config: GameConfig,
    round_num: int,
    vote_num: int,
    consecutive_ties_before: int,
    logger: EventLogger,
    *,
    contracts: list[Contract] | None = None,
) -> VoteSettlementResult:
    """
    1回の投票の精算を実行する（§7.3 の8手順）

    Args:
        players: 全プレイヤーの状態辞書（参加費はラウンド開始で既に徴収済み）
        votes: player_id -> Vote（この投票に参加した全員分）
        config: ゲーム設定
        round_num: ラウンド番号
        vote_num: 投票番号
        consecutive_ties_before: この投票の前までのやり直し連続回数
        logger: イベントロガー
        contracts: 全契約リスト（省略時は契約なし扱い）

    Returns:
        VoteSettlementResult(players, outcome, report)
    """
    contracts = contracts or []
    updated = dict(players)

    # --- 手順1: Reveal（参加した全員の票を公開） ---
    logger.log(
        "VOTE_REVEALED", round_num, "settlement", step=1, vote_num=vote_num, visibility="public",
        data={"votes": {pid: v.value for pid, v in sorted(votes.items())}},
    )

    # --- 手順2: 判定（決着=退場、やり直し=延長料徴収、打ち切り判定） ---
    outcome = vote_ops.resolve_vote(votes, config, round_num, vote_num, consecutive_ties_before)

    if outcome.extension_fee_collected > 0:
        for pid in outcome.remaining_ids:
            new_p, paid, borrowed, _shortfall = player_ops.pay_or_borrow(
                updated[pid], config.extension_fee, config, cap_exempt=True,
            )
            updated[pid] = new_p
            logger.log(
                "EXTENSION_FEE_COLLECTED", round_num, "settlement", vote_num=vote_num,
                visibility="self", data={"player_id": pid, "paid": paid, "borrowed": borrowed},
            )

    logger.log(
        "VOTE_RESOLVED", round_num, "settlement", step=2, vote_num=vote_num, visibility="public",
        data={
            "result": outcome.result,
            "eliminated_ids": outcome.eliminated_ids,
            "remaining_ids": outcome.remaining_ids,
            "consecutive_ties_after": outcome.consecutive_ties_after,
            "extension_fee_collected": outcome.extension_fee_collected,
        },
    )
    if outcome.result == "abort":
        logger.log(
            "ROUND_ABORTED", round_num, "settlement", vote_num=vote_num, visibility="public",
            data={"consecutive_ties": outcome.consecutive_ties_after},
        )

    # --- 手順3: 型Bの監査／手順4: 型Cの投票単位条件判定 ---
    due_items, violations = _collect_vote_due_items(
        contracts, votes, outcome, round_num, vote_num, config,
    )
    violator_ids = sorted({obligor for obligor, _ in violations})
    if violator_ids:
        logger.log(
            "TYPE_B_VIOLATION", round_num, "settlement", step=3, vote_num=vote_num, visibility="public",
            data={"player_ids": violator_ids},
        )

    # --- 手順5: 支払える上限の固定（延長料徴収後の現金＋残り枠） ---
    payable_limits = fix_payable_limits(updated, config)
    logger.log(
        "PAYABLE_LIMIT_FIXED", round_num, "settlement", step=5, vote_num=vote_num, visibility="spectator",
        data={"payable_limits": dict(sorted(payable_limits.items()))},
    )

    # --- 手順6: 支払額の決定 ---
    planned = _plan_payments(due_items, payable_limits)
    for item in planned:
        logger.log(
            "CONTRACT_PAYMENT", round_num, "settlement", step=6, vote_num=vote_num, visibility="parties",
            data={
                "contract_id": item.contract_id, "contract_seq": item.contract_seq,
                "ob_index": item.ob_index, "obligor": item.obligor,
                "counterparty": item.counterparty, "ob_type": item.ob_type.value,
                "promised": item.promised, "paid": item.paid,
            },
        )

    # --- 手順7: 一斉の受け渡しと借金の確定 ---
    updated = apply_payments(updated, planned)

    # --- 手順8: 公示 ---
    shortfall_ids = _shortfall_ids(planned)
    if shortfall_ids:
        logger.log(
            "PAYMENT_SHORTFALL", round_num, "settlement", step=8, vote_num=vote_num, visibility="public",
            data={"player_ids": shortfall_ids},
        )

    report = ContractSettlementReport(
        violations=violations,
        payable_limits=payable_limits,
        payments=planned,
        shortfall_ids=shortfall_ids,
    )
    return VoteSettlementResult(updated, outcome, report)


# =============================================================================
# ラウンドの精算（§7.4）
# =============================================================================

def _collect_round_due_items(
    contracts: list[Contract],
    round_num: int,
    winner_ids: list[str],
    payout_per_winner: int,
) -> list[ObligationPayment]:
    """
    手順2（型Cのラウンド単位条件判定）・手順3（型Aの追加）: このラウンドの
    支払い対象になる義務を集める（§7.4手順2/3）
    """
    items: list[ObligationPayment] = []
    for contract in _sorted_active_contracts(contracts):
        for ob_index, ob in enumerate(contract.obligations):
            if ob.round_num != round_num or ob.vote_num is not None:
                continue

            if ob.ob_type == ObligationType.TYPE_A_PAYMENT:
                amount = ob.details.get("amount", 0)
            elif ob.ob_type == ObligationType.TYPE_C_CONDITIONAL:
                if not contract_ops.evaluate_type_c_round_condition(ob, winner_ids):
                    continue
                amount = contract_ops.resolve_type_c_amount(ob, payout_per_winner=payout_per_winner)
            else:
                continue

            items.append(ObligationPayment(
                contract_id=contract.contract_id,
                contract_seq=contract.contract_seq,
                ob_index=ob_index,
                obligor=ob.obligor,
                counterparty=ob.counterparty,
                ob_type=ob.ob_type,
                promised=amount,
                paid=0,
            ))
    return items


def settle_round(
    players: dict[str, PlayerState],
    config: GameConfig,
    round_num: int,
    votes_in_round: list[VoteOutcome],
    carryover_in: int,
    pot_final: int,
    aborted: bool,
    logger: EventLogger,
    *,
    is_final_round: bool,
    contracts: list[Contract] | None = None,
) -> RoundSettlementResult:
    """
    ラウンドの精算を実行する（§7.4 の8手順）

    最後の投票（決着して残り2人以下、または打ち切り）の精算の後に呼ぶ。

    Args:
        players: 全プレイヤーの状態辞書（最後の投票の精算まで反映済み）
        config: ゲーム設定
        round_num: ラウンド番号
        votes_in_round: このラウンドで行われた全投票のVoteOutcome
        carryover_in: このラウンド開始時点の持ち越し額
        pot_final: このラウンドの山の最終額
        aborted: 打ち切りで終わったか
        logger: イベントロガー
        is_final_round: 最終ラウンド（R4）かどうか
        contracts: 全契約リスト（省略時は契約なし扱い）

    Returns:
        RoundSettlementResult(players, outcome, report)
    """
    contracts = contracts or []
    updated = dict(players)

    last_vote = votes_in_round[-1]
    winner_ids = [] if aborted else list(last_vote.remaining_ids)

    # --- 手順1: 山の支払い ---
    payout_per_winner, forfeited_remainder = round_ops.compute_payout(pot_final, winner_ids)
    for pid in winner_ids:
        updated[pid] = player_ops.receive(updated[pid], payout_per_winner)

    carryover_out, destroyed_pot = round_ops.resolve_pot_carryover(
        pot_final, aborted=aborted, is_final_round=is_final_round,
    )

    logger.log(
        "ROUND_RESOLVED", round_num, "round_settlement", step=1, visibility="public",
        data={
            "aborted": aborted,
            "winner_ids": winner_ids,
            "payout_per_winner": payout_per_winner,
            "forfeited_remainder": forfeited_remainder,
            "carryover_out": carryover_out,
            "destroyed_pot": destroyed_pot,
            "pot_final": pot_final,
        },
    )
    for pid in winner_ids:
        logger.log(
            "ROUND_PAYOUT", round_num, "round_settlement", visibility="public",
            data={"player_id": pid, "amount": payout_per_winner},
        )

    # --- 手順2: 型Cのラウンド単位条件判定／手順3: 型Aの追加 ---
    due_items = _collect_round_due_items(contracts, round_num, winner_ids, payout_per_winner)

    # --- 手順4: 支払える上限の固定（山の受け取りを反映した後） ---
    payable_limits = fix_payable_limits(updated, config)
    logger.log(
        "PAYABLE_LIMIT_FIXED", round_num, "round_settlement", step=4, visibility="spectator",
        data={"payable_limits": dict(sorted(payable_limits.items()))},
    )

    # --- 手順5: 支払額の決定 ---
    planned = _plan_payments(due_items, payable_limits)
    for item in planned:
        logger.log(
            "CONTRACT_PAYMENT", round_num, "round_settlement", step=5, visibility="parties",
            data={
                "contract_id": item.contract_id, "contract_seq": item.contract_seq,
                "ob_index": item.ob_index, "obligor": item.obligor,
                "counterparty": item.counterparty, "ob_type": item.ob_type.value,
                "promised": item.promised, "paid": item.paid,
            },
        )

    # --- 手順6: 一斉の受け渡しと借金の確定 ---
    updated = apply_payments(updated, planned)

    # --- 手順7: 公示 ---
    shortfall_ids = _shortfall_ids(planned)
    if shortfall_ids:
        logger.log(
            "PAYMENT_SHORTFALL", round_num, "round_settlement", step=7, visibility="public",
            data={"player_ids": shortfall_ids},
        )

    outcome = RoundOutcome(
        round_num=round_num,
        votes=list(votes_in_round),
        carryover_in=carryover_in,
        pot_final=pot_final,
        aborted=aborted,
        winner_ids=winner_ids,
        payout_per_winner=payout_per_winner,
        forfeited_remainder=forfeited_remainder,
        carryover_out=carryover_out,
        destroyed_pot=destroyed_pot,
    )
    report = ContractSettlementReport(
        violations=[],
        payable_limits=payable_limits,
        payments=planned,
        shortfall_ids=shortfall_ids,
    )
    return RoundSettlementResult(updated, outcome, report)
