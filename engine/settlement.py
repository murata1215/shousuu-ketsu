"""
Settlementフェイズモジュール（§7.1）

完全新規実装（C分類）。§7.1 の8手順を手順番号つきで並べる。本サイクル
（1.0）で中身があるのは手順1（Reveal）・2（少数決の判定と配当）・5（支払
える上限の固定）・7（一斉の受け渡しと借金の確定）・8（公示）だけで、
手順3（型Bの監査）・手順4（型Cの条件判定）・手順6（contract_seq順の支払額
決定）は契約（§6、サイクル1.1）が無いため常に空リストを返すフックとして
置いてある。1.1ではこの3関数の中身を実装として差し込むだけで、
呼び出し側（execute_settlement の手順の並び）は変更しない想定
（CLAUDE.md: 「次回この手順を差し込める形にしておく」）。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.models import MinorityOutcome, PlayerState, Vote
from engine import minority as minority_ops
from engine import player as player_ops


def _audit_type_b(players: dict[str, PlayerState], round_num: int) -> list[dict]:
    """
    手順3: 型Bの監査（§6.2/§6.3、§7.1手順3）

    サイクル1.1で実装するフック。契約が存在しない1.0では常に空リストを返す
    （違約金の確定は発生しない）。
    """
    return []


def _evaluate_type_c(players: dict[str, PlayerState], outcome: MinorityOutcome, round_num: int) -> list[dict]:
    """
    手順4: 型Cの条件判定（§6.4、§7.1手順4）

    サイクル1.1で実装するフック。契約が存在しない1.0では常に空リストを返す
    （条件成立による支払い対象の追加は発生しない）。
    """
    return []


def _plan_contract_payments(
    players: dict[str, PlayerState],
    payable_limits: dict[str, int],
    type_b_violations: list[dict],
    type_c_due: list[dict],
    round_num: int,
) -> list[dict]:
    """
    手順6: 支払額の決定（§7.1手順6: contract_seqの小さい順に上限から割り当て）

    サイクル1.1で実装するフック。契約が存在しない1.0では、型B監査・型C判定
    （手順3・4）が常に空である以上、割り当てる義務も常に空になる。
    """
    return []


def execute_settlement(
    players: dict[str, PlayerState],
    votes: dict[str, Vote],
    config: GameConfig,
    carryover_before: int,
    round_num: int,
    logger: EventLogger,
    *,
    is_final_round: bool,
) -> tuple[dict[str, PlayerState], MinorityOutcome]:
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

    Returns:
        (更新されたplayers, MinorityOutcome)
    """
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

    # --- 手順3: 型Bの監査 ---
    type_b_violations = _audit_type_b(updated, round_num)

    # --- 手順4: 型Cの条件判定 ---
    type_c_due = _evaluate_type_c(updated, outcome, round_num)

    # --- 手順5: 支払える上限の固定（手順2の配当を反映した現金＋残り枠） ---
    payable_limits = {
        pid: p.cash + player_ops.remaining_credit(p, config)
        for pid, p in updated.items()
    }

    # --- 手順6: 支払額の決定（contract_seq順。1.0では常に空） ---
    planned_payments = _plan_contract_payments(
        updated, payable_limits, type_b_violations, type_c_due, round_num,
    )

    # --- 手順7: 一斉の受け渡しと借金の確定 ---
    # 1.0には契約が無いため planned_payments は常に空。現金がマイナスになる
    # 経路も無いが、1.1以降の契約執行に備えて安全網として適用しておく。
    for pid in list(updated):
        updated[pid] = player_ops.settle_negative_cash(updated[pid])

    # --- 手順8: 公示（払いきれなかった者の名前） ---
    unpaid_ids = [p.player_id for p in updated.values() if p.cash < 0]
    if unpaid_ids:
        logger.log("PAYMENT_SHORTFALL", round_num, "settlement", step=8, data={
            "player_ids": sorted(unpaid_ids),
        })

    return updated, outcome
