"""
Settlementフェイズモジュール（§7.1）

§7.1 の8手順を手順番号つきで並べる。1.0では手順3（型Bの監査）・手順4（型Cの
条件判定）・手順6（支払額の決定）は契約（§6）が無いため常に空リストを返す
フックだったが、サイクル1.1で中身を実装した（呼び出し側=execute_settlement
の手順の並びは1.0から変更していない。CLAUDE.md: 「次回この手順を差し込める
形にしておく」を実際に差し込んだ）。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.models import (
    Contract, ContractSettlementReport, MinorityOutcome, ObligationPayment,
    ObligationType, PlayerState, Vote,
)
from engine import contracts as contract_ops
from engine import minority as minority_ops
from engine import player as player_ops


class SettlementResult:
    """
    execute_settlement() の戻り値

    1.0は (players, outcome) の2値タプルだったが、契約処理の結果
    （違反者・支払明細・取りはぐれ）を呼び出し側（game.py/テスト）に渡す必要が
    あるため1.1でオブジェクトに変更した。
    """

    __slots__ = ("players", "outcome", "report")

    def __init__(
        self,
        players: dict[str, PlayerState],
        outcome: MinorityOutcome,
        report: ContractSettlementReport,
    ) -> None:
        self.players = players
        self.outcome = outcome
        self.report = report


def _collect_due_items(
    contracts: list[Contract],
    votes: dict[str, Vote],
    outcome: MinorityOutcome,
    config: GameConfig,
    round_num: int,
) -> tuple[list[ObligationPayment], list[tuple[str, str]]]:
    """
    手順3（型Bの監査）・手順4（型Cの条件判定）: このラウンドの支払い対象になる
    義務を集める（§7.1手順3/4）

    契約の成立順（contract_seq昇順）・同じ契約内では義務の記載順で並べる
    （§7.1手順6の順序をここで確定する）。違約金は破った型B義務のインデックス
    位置にそのまま置く（§11.4 #12: 「違約金は、破った型B義務の記載位置に置く」）。
    条件が成立しなかった型C・違反しなかった型Bは何も起きない（アイテムを作らない）。

    Returns:
        (promised確定・paid未決定のObligationPaymentのリスト（既に支払い順）,
         型Bの違反 (obligor, obligation_id) のリスト)
    """
    violated_obligations = contract_ops.audit_type_b(contracts, votes, round_num)
    violated_ids = {ob.obligation_id for ob in violated_obligations}
    violations = [(ob.obligor, ob.obligation_id) for ob in violated_obligations]

    active = sorted(
        (c for c in contracts if c.contract_seq is not None),
        key=lambda c: c.contract_seq,
    )

    items: list[ObligationPayment] = []
    for contract in active:
        for ob_index, ob in enumerate(contract.obligations):
            if ob.round_num != round_num:
                continue

            if ob.ob_type == ObligationType.TYPE_A_PAYMENT:
                amount = ob.details.get("amount", 0)
            elif ob.ob_type == ObligationType.TYPE_C_CONDITIONAL:
                if not contract_ops.evaluate_type_c_condition(ob, outcome):
                    continue
                amount = ob.details.get("amount", 0)
            elif ob.ob_type == ObligationType.TYPE_B_VOTE:
                if ob.obligation_id not in violated_ids:
                    continue
                amount = config.penalty_amount
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


def _plan_payments(
    items: list[ObligationPayment],
    payable_limits: dict[str, int],
) -> list[ObligationPayment]:
    """
    手順6: 支払額の決定（§7.1手順6）

    items は既に (contract_seqの小さい順, 同じ契約内では記載順) で並んでいる
    （_collect_due_items参照）。義務者ごとに残り上限を取り崩しながら進み、
    上限に届いた義務は残額だけ部分払いし、それより後の同じ義務者の義務は
    0円にする（他の義務者の残り上限には影響しない）。

    Args:
        items: _collect_due_items() が返した順序どおりのリスト
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


def execute_settlement(
    players: dict[str, PlayerState],
    votes: dict[str, Vote],
    config: GameConfig,
    carryover_before: int,
    round_num: int,
    logger: EventLogger,
    *,
    is_final_round: bool,
    contracts: list[Contract] | None = None,
) -> SettlementResult:
    """
    Settlementフェイズを実行する（§7.1 の8手順）

    Args:
        players: 全プレイヤーの状態辞書（Commitで参加費を徴収済みの状態）
        votes: player_id -> Vote（全員分）
        config: ゲーム設定
        carryover_before: このラウンド開始時点の持ち越し額
        round_num: ラウンド番号
        logger: イベントロガー
        is_final_round: 最終ラウンド（R12）かどうか
        contracts: 全契約リスト（ACTIVEのみ判定・執行対象。省略時は契約なし扱い）

    Returns:
        SettlementResult(players, outcome, report)
    """
    contracts = contracts or []
    updated = dict(players)

    # --- 手順1: Reveal（全員の票を公開） ---
    logger.log("VOTE_REVEALED", round_num, "settlement", step=1, data={
        "votes": {pid: v.value for pid, v in sorted(votes.items())},
    })

    # --- 手順2: 少数決の判定と配当 ---
    outcome = minority_ops.resolve_minority(
        votes, config, carryover_before, round_num, is_final_round=is_final_round,
    )
    updated = minority_ops.apply_payouts(updated, outcome, config)
    logger.log("MINORITY_RESOLVED", round_num, "settlement", step=2, data={
        "minority_side": outcome.minority_side.value if outcome.minority_side else None,
        "minority_ids": outcome.minority_ids,
        "majority_ids": outcome.majority_ids,
        "payout_per_minority": outcome.payout_per_minority,
        "forfeited_remainder": outcome.forfeited_remainder,
        "carryover_after": outcome.carryover_after,
        "destroyed_carryover": outcome.destroyed_carryover,
    })
    if outcome.minority_side is None:
        logger.log("CARRYOVER", round_num, "settlement", step=2, data={
            "carryover_after": outcome.carryover_after,
            "destroyed": outcome.destroyed_carryover,
        })

    # --- 手順3: 型Bの監査／手順4: 型Cの条件判定 ---
    due_items, violations = _collect_due_items(contracts, votes, outcome, config, round_num)
    violator_ids = sorted({obligor for obligor, _ in violations})
    if violator_ids:
        # 違反者名のみ公示（相手方・契約内容は非公開、§6.3/§8）
        logger.log("TYPE_B_VIOLATION", round_num, "settlement", step=3, data={
            "player_ids": violator_ids,
        })

    # --- 手順5: 支払える上限の固定（手順2の配当を反映した現金＋残り枠） ---
    # 同じ決済で契約から受け取るお金は含めない（§7.1手順5）。
    payable_limits = {
        pid: p.cash + player_ops.remaining_credit(p, config)
        for pid, p in updated.items()
    }
    logger.log("PAYABLE_LIMIT_FIXED", round_num, "settlement", step=5, data={
        "payable_limits": dict(sorted(payable_limits.items())),
    })

    # --- 手順6: 支払額の決定（contract_seq順→契約内の記載順） ---
    planned = _plan_payments(due_items, payable_limits)
    for item in planned:
        logger.log("CONTRACT_PAYMENT", round_num, "settlement", step=6, data={
            "contract_id": item.contract_id, "contract_seq": item.contract_seq,
            "ob_index": item.ob_index, "obligor": item.obligor,
            "counterparty": item.counterparty, "ob_type": item.ob_type.value,
            "promised": item.promised, "paid": item.paid,
        })

    # --- 手順7: 一斉の受け渡しと借金の確定 ---
    # 全員分の支払い・受取を同時に（1人ずつ順に適用せず、合算した差分として）
    # 反映する（§7.1手順7）。その後にマイナス分だけを開始後の借金（3%）にする。
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

    # --- 手順8: 公示（払いきれなかった者の名前。金額・相手は非公開） ---
    shortfall_by_obligor: dict[str, int] = {}
    for item in planned:
        gap = item.promised - item.paid
        if gap > 0:
            shortfall_by_obligor[item.obligor] = shortfall_by_obligor.get(item.obligor, 0) + gap
    shortfall_ids = sorted(shortfall_by_obligor.keys())
    if shortfall_ids:
        logger.log("PAYMENT_SHORTFALL", round_num, "settlement", step=8, data={
            "player_ids": shortfall_ids,
        })

    report = ContractSettlementReport(
        violations=violations,
        payable_limits=payable_limits,
        payments=planned,
        shortfall_ids=shortfall_ids,
    )
    return SettlementResult(updated, outcome, report)
