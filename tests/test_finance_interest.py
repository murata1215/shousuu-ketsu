"""
利息計算のテスト（§3.3）

整数の切り上げ除算のみを使い、浮動小数点の乗算を一切使わないことを
数値で確認する（CLAUDE.md: 「利息などの計算に小数を使わず、整数の計算で
切り上げる」）。期待値はユーザー指定の実測値:
120万 → 1,434,749円 / 1000万 → 11,956,186円（1.5%・12R）、
100万 → 1,425,767円（3%・12R）。
"""

from engine.config import GameConfig
from engine.models import PlayerState
from engine import player as player_ops


def _player_pre(debt_pre: int) -> PlayerState:
    return PlayerState(player_id="P01", cash=0, debt_pre=debt_pre, debt_post=0, initial_loan=debt_pre)


def _player_post(debt_post: int) -> PlayerState:
    return PlayerState(player_id="P01", cash=0, debt_pre=0, debt_post=debt_post, initial_loan=0)


def _compound_12_rounds(player: PlayerState, config: GameConfig) -> PlayerState:
    for _ in range(12):
        player, _pre, _post = player_ops.apply_interest(player, config)
    return player


def test_pre_interest_120man_over_12_rounds() -> None:
    """1.5%複利・12ラウンド: 120万 → 1,434,749円"""
    config = GameConfig.default_12()
    p = _compound_12_rounds(_player_pre(1_200_000), config)
    assert p.debt_pre == 1_434_749


def test_pre_interest_1000man_over_12_rounds() -> None:
    """1.5%複利・12ラウンド: 1000万 → 11,956,186円"""
    config = GameConfig.default_12()
    p = _compound_12_rounds(_player_pre(10_000_000), config)
    assert p.debt_pre == 11_956_186


def test_post_interest_100man_over_12_rounds() -> None:
    """3%複利・12ラウンド: 100万 → 1,425,767円"""
    config = GameConfig.default_12()
    p = _compound_12_rounds(_player_post(1_000_000), config)
    assert p.debt_post == 1_425_767


def test_interest_applies_to_same_round_borrowing() -> None:
    """そのラウンドに発生した借金にも、同じラウンドのFinanceから利息がつく（§3.3）"""
    config = GameConfig.default_12()
    # 参加費の立替でこのラウンド中に発生した開始後の借金とみなす
    p = PlayerState(player_id="P01", cash=0, debt_pre=0, debt_post=100_000, initial_loan=0)
    new_p, interest_pre, interest_post = player_ops.apply_interest(p, config)
    assert interest_post == 3_000  # ceil(100,000 * 3/100) = 3,000（割り切れるので切り上げなし）
    assert new_p.debt_post == 103_000


def test_interest_rounds_up_fractional() -> None:
    """端数は切り上げになることを確認する（割り切れない残高で検証）"""
    config = GameConfig.default_12()
    p = PlayerState(player_id="P01", cash=0, debt_pre=1_000_001, debt_post=0, initial_loan=1_000_001)
    new_p, interest_pre, _interest_post = player_ops.apply_interest(p, config)
    # 1,000,001 * 15 / 1000 = 15,000.015 → 切り上げで15,001
    assert interest_pre == 15_001
    assert new_p.debt_pre == 1_015_002


def test_no_float_arithmetic_in_interest_formula() -> None:
    """利息計算が整数演算（切り上げ除算）のみで行われることをソース検査で確認する"""
    import inspect
    source = inspect.getsource(player_ops.apply_interest)
    assert "float(" not in source
    assert " * config.interest_rate_pre_num // config.interest_rate_pre_den" in source
    assert " * config.interest_rate_post_num // config.interest_rate_post_den" in source
