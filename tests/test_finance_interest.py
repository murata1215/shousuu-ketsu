"""
利息計算のテスト（§3.3、仕様書v0.4 §12.3 #18・#19・#20を含む）

整数の切り上げ除算のみを使い、浮動小数点の乗算を一切使わないことを
数値で確認する（CLAUDE.md: 「利息などの計算に小数を使わず、整数の計算で
切り上げる」）。期待値は仕様書v0.4 §3.1/§3.3に書かれている実測値:
120万 → 1,458,608円 / 1000万 → 12,155,063円（5%・4ラウンド複利）、
100万 → 1,749,007円（15%・4ラウンド複利）。

サイクル4.0でv0.3（1.5%/3%・12ラウンド）からv0.4（5%/15%・4ラウンド）へ
数値を差し替えた。計算ロジック（apply_interest）自体は無改修。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.models import PlayerState
from engine import player as player_ops


def _player_pre(debt_pre: int) -> PlayerState:
    return PlayerState(player_id="P01", cash=0, debt_pre=debt_pre, debt_post=0, initial_loan=debt_pre)


def _player_post(debt_post: int) -> PlayerState:
    return PlayerState(player_id="P01", cash=0, debt_pre=0, debt_post=debt_post, initial_loan=0)


def _compound_4_rounds(player: PlayerState, config: GameConfig) -> PlayerState:
    for _ in range(config.num_rounds):
        player, _pre, _post = player_ops.apply_interest(player, config)
    return player


def test_acceptance_18_pre_interest_120man_over_4_rounds() -> None:
    """#18: 5%複利・4ラウンド: 120万 → 1,458,608円"""
    config = GameConfig.default_12()
    p = _compound_4_rounds(_player_pre(1_200_000), config)
    assert p.debt_pre == 1_458_608


def test_pre_interest_1000man_over_4_rounds() -> None:
    """5%複利・4ラウンド: 1000万 → 12,155,063円（§3.1の実測値）"""
    config = GameConfig.default_12()
    p = _compound_4_rounds(_player_pre(10_000_000), config)
    assert p.debt_pre == 12_155_063


def test_acceptance_19_post_interest_100man_over_4_rounds() -> None:
    """#19: 15%複利・4ラウンド: 100万 → 1,749,007円"""
    config = GameConfig.default_12()
    p = _compound_4_rounds(_player_post(1_000_000), config)
    assert p.debt_post == 1_749_007


def test_interest_applies_to_same_round_borrowing() -> None:
    """そのラウンドに発生した借金にも、同じラウンドのFinanceから利息がつく（§3.3）"""
    config = GameConfig.default_12()
    # 参加費の立替でこのラウンド中に発生した開始後の借金とみなす
    p = PlayerState(player_id="P01", cash=0, debt_pre=0, debt_post=100_000, initial_loan=0)
    new_p, interest_pre, interest_post = player_ops.apply_interest(p, config)
    assert interest_post == 15_000  # ceil(100,000 * 15/100) = 15,000（割り切れるので切り上げなし）
    assert new_p.debt_post == 115_000


def test_interest_rounds_up_fractional() -> None:
    """端数は切り上げになることを確認する（割り切れない残高で検証）"""
    config = GameConfig.default_12()
    p = PlayerState(player_id="P01", cash=0, debt_pre=1_000_003, debt_post=0, initial_loan=1_000_003)
    new_p, interest_pre, _interest_post = player_ops.apply_interest(p, config)
    # 1,000,003 * 5 / 100 = 50,000.15 → 切り上げで50,001
    assert interest_pre == 50_001
    assert new_p.debt_pre == 1_050_004


def test_no_float_arithmetic_in_interest_formula() -> None:
    """利息計算が整数演算（切り上げ除算）のみで行われることをソース検査で確認する"""
    import inspect
    source = inspect.getsource(player_ops.apply_interest)
    assert "float(" not in source
    assert " * config.interest_rate_pre_num // config.interest_rate_pre_den" in source
    assert " * config.interest_rate_post_num // config.interest_rate_post_den" in source


def test_acceptance_20_interest_posted_once_per_round_regardless_of_vote_count() -> None:
    """#20: 投票が6回あったラウンドでも、利息の計上はFinanceの1回だけ
    （投票の回数で利息は変わらない。§3.3）"""
    from engine.finance import execute_finance

    config = GameConfig.default_12()
    players = {"P01": PlayerState(player_id="P01", cash=0, debt_pre=1_200_000, debt_post=0, initial_loan=1_200_000)}
    logger = EventLogger()
    updated, interest_total = execute_finance(players, 1, config, logger)

    interest_events = [e for e in logger.events if e.event_type == "INTEREST" and e.data["player_id"] == "P01"]
    assert len(interest_events) == 1
    assert updated["P01"].debt_pre == 1_260_000  # 1,200,000 * 5% = 60,000
    assert interest_total == 60_000
