"""
Settlementフェイズのテスト（§7.1）

手順3（型Bの監査）・4（型Cの条件判定）・6（支払額の決定）はサイクル1.1で
実装した。契約を使った詳細な確認（§12.3受け入れテスト等）は
tests/test_settlement_contracts.py で行い、ここでは手順の並び・契約なしの
基本挙動・現金が負にならないことを確認する。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.models import PlayerState, Vote
from engine.settlement import execute_settlement


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


def test_no_contracts_means_no_payments_or_violations() -> None:
    """契約が無い場合、手順3・4・6は何も生み出さない（1.0の挙動を維持）"""
    players = _players()
    logger = EventLogger()
    result = execute_settlement(
        players, _votes_7_5(), GameConfig.default_12(), carryover_before=0,
        round_num=1, logger=logger, is_final_round=False, contracts=None,
    )
    assert result.report.violations == []
    assert result.report.payments == []
    assert result.report.shortfall_ids == []


def test_settlement_pays_minority_and_cash_never_negative() -> None:
    """配当が反映され、現金が負にならない（§7.1手順7）"""
    config = GameConfig.default_12()
    players = _players()
    # Commit相当（参加費徴収済みの状態をシミュレート）
    for pid in players:
        players[pid] = players[pid].model_copy(update={"cash": players[pid].cash - config.entry_fee})

    logger = EventLogger()
    result = execute_settlement(
        players, _votes_7_5(), config, carryover_before=0, round_num=1, logger=logger, is_final_round=False,
    )
    updated, outcome = result.players, result.outcome

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
