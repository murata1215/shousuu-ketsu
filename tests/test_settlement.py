"""
Settlementフェイズのテスト（§7.1）

本サイクルは契約（§6）が無いため、手順3・4・6のフックが常に空であることと、
手順の並び・現金が負にならないことを確認する。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.models import PlayerState, Vote
from engine.settlement import execute_settlement, _audit_type_b, _evaluate_type_c, _plan_contract_payments


def _players(n: int = 12) -> dict[str, PlayerState]:
    return {
        f"P{i:02d}": PlayerState(
            player_id=f"P{i:02d}", cash=1_200_000, debt_pre=1_200_000, debt_post=0, initial_loan=1_200_000,
        )
        for i in range(1, n + 1)
    }


def _votes_7_5() -> dict[str, Vote]:
    ids = list(_players().keys())
    return {**{pid: Vote.YES for pid in ids[:7]}, **{pid: Vote.NO for pid in ids[7:]}}


def test_contract_hooks_return_empty_in_cycle_1_0() -> None:
    """手順3・4・6は契約が無いため常に空リストを返す（1.1で差し込む前提）"""
    players = _players()
    assert _audit_type_b(players, round_num=1) == []
    from engine.minority import resolve_minority
    outcome = resolve_minority(_votes_7_5(), GameConfig.default_12(), 0, 1, is_final_round=False)
    assert _evaluate_type_c(players, outcome, round_num=1) == []
    assert _plan_contract_payments(players, {}, [], [], round_num=1) == []


def test_settlement_pays_minority_and_cash_never_negative() -> None:
    """配当が反映され、現金が負にならない（§7.1手順7）"""
    config = GameConfig.default_12()
    players = _players()
    # Commit相当（参加費徴収済みの状態をシミュレート）
    for pid in players:
        players[pid] = players[pid].model_copy(update={"cash": players[pid].cash - config.entry_fee})

    logger = EventLogger()
    updated, outcome = execute_settlement(
        players, _votes_7_5(), config, carryover_before=0, round_num=1, logger=logger, is_final_round=False,
    )

    assert outcome.minority_side == Vote.NO
    for p in updated.values():
        assert p.cash >= 0
    # 少数派は参加費が戻り配当を受け取るので元の現金より増えている
    for pid in outcome.minority_ids:
        assert updated[pid].cash > players[pid].cash
    # 多数派はCommitで引かれたままで変化しない
    for pid in outcome.majority_ids:
        assert updated[pid].cash == players[pid].cash


def test_settlement_event_steps_are_logged_in_order() -> None:
    """手順1(Reveal)・2(配当)のイベントがstep番号付きで記録される"""
    config = GameConfig.default_12()
    players = _players()
    logger = EventLogger()
    execute_settlement(
        players, _votes_7_5(), config, carryover_before=0, round_num=1, logger=logger, is_final_round=False,
    )
    events = logger.events
    reveal = [e for e in events if e.event_type == "VOTE_REVEALED"]
    resolved = [e for e in events if e.event_type == "MINORITY_RESOLVED"]
    assert len(reveal) == 1 and reveal[0].step == 1
    assert len(resolved) == 1 and resolved[0].step == 2
