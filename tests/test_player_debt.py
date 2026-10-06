"""
借金・現金操作のテスト（§3、仕様書§12.3 #7・#10・#15・#16を含む）
"""

from engine.config import GameConfig
from engine.models import PlayerState, TransferAction
from engine import actions as action_ops
from engine import player as player_ops


def _player(cash: int, debt_pre: int = 0, debt_post: int = 0) -> PlayerState:
    return PlayerState(player_id="P01", cash=cash, debt_pre=debt_pre, debt_post=debt_post, initial_loan=1_200_000)


def test_acceptance_7_cash_zero_entry_fee_becomes_post_debt() -> None:
    """#7: 現金0で投票 → 参加費10万が開始後の借金（3%）になる"""
    config = GameConfig.default_12()
    p = _player(cash=0)
    result = player_ops.pay_or_borrow(p, config.entry_fee, config, cap_exempt=True)
    assert result.paid == 100_000
    assert result.borrowed == 100_000
    assert result.shortfall == 0
    assert result.player.cash == 0
    assert result.player.debt_post == 100_000


def test_acceptance_10_entry_fee_exempt_from_cap() -> None:
    """#10: 借金残高1000万・現金0で投票 → 参加費は貸す（上限の例外）"""
    config = GameConfig.default_12()
    p = _player(cash=0, debt_pre=10_000_000, debt_post=0)
    assert player_ops.remaining_credit(p, config) == 0

    result = player_ops.pay_or_borrow(p, config.entry_fee, config, cap_exempt=True)
    assert result.shortfall == 0
    assert result.borrowed == 100_000
    assert result.player.total_debt == 10_100_000


def test_pay_or_borrow_without_exemption_respects_cap() -> None:
    """cap_exempt=Falseのときは残り借入枠を超えた分が取りはぐれになる（§3.4）"""
    config = GameConfig.default_12()
    p = _player(cash=1_000_000, debt_pre=9_700_000, debt_post=0)
    assert player_ops.remaining_credit(p, config) == 300_000

    result = player_ops.pay_or_borrow(p, 5_000_000, config, cap_exempt=False)
    assert result.paid == 1_300_000  # 現金100万 + 残り枠30万
    assert result.borrowed == 300_000
    assert result.shortfall == 3_700_000
    assert result.player.cash == 0
    assert result.player.debt_post == 300_000


def test_acceptance_15_repay_post_debt_first() -> None:
    """#15: 開始前の借金120万と開始後の借金50万がある状態で60万を返済
    → 開始後の50万を完済し、残り10万を開始前の借金に充てる"""
    p = _player(cash=1_000_000, debt_pre=1_200_000, debt_post=500_000)
    new_p, actual = player_ops.repay(p, 600_000)
    assert actual == 600_000
    assert new_p.debt_post == 0
    assert new_p.debt_pre == 1_100_000
    assert new_p.cash == 400_000


def test_repay_clamped_to_total_debt() -> None:
    """返済額は min(指定額, 現金, 借金合計) にクランプされる（§3.5）"""
    p = _player(cash=5_000_000, debt_pre=200_000, debt_post=100_000)
    new_p, actual = player_ops.repay(p, 1_000_000)
    assert actual == 300_000
    assert new_p.debt_pre == 0
    assert new_p.debt_post == 0


def test_acceptance_16_transfer_exceeding_cash_is_rejected() -> None:
    """#16: 手持ち20万で50万を送金 → 不成立"""
    config = GameConfig.default_12()
    me = _player(cash=200_000)
    other = _player(cash=0)
    other = other.model_copy(update={"player_id": "P02"})
    players = {"P01": me, "P02": other}
    action = TransferAction(player_id="P01", to="P02", amount=500_000)
    result = action_ops.validate_action(action, me, config, players)
    assert result.success is False


def test_transfer_within_cash_succeeds() -> None:
    """送金は手持ちの現金まで可能（§3.2）"""
    config = GameConfig.default_12()
    me = _player(cash=500_000)
    other = _player(cash=0).model_copy(update={"player_id": "P02"})
    players = {"P01": me, "P02": other}
    action = TransferAction(player_id="P01", to="P02", amount=500_000)
    result = action_ops.validate_action(action, me, config, players)
    assert result.success is True


def test_remaining_credit_clips_at_zero_when_over_cap() -> None:
    """利息の計上で残高が上限を超えても、残り枠は0未満にならない（§3.4）"""
    config = GameConfig.default_12()
    p = _player(cash=0, debt_pre=10_500_000, debt_post=0)
    assert player_ops.remaining_credit(p, config) == 0
