"""
少数決の判定・配当・持ち越しのテスト（§4、仕様書§12.3 #1〜#6を含む）
"""

from engine.config import GameConfig
from engine.models import Vote
from engine.minority import resolve_minority


def _votes(n_yes: int, n_no: int) -> dict[str, Vote]:
    ids = [f"P{i:02d}" for i in range(1, n_yes + n_no + 1)]
    return {
        **{pid: Vote.YES for pid in ids[:n_yes]},
        **{pid: Vote.NO for pid in ids[n_yes:]},
    }


def test_acceptance_1_7_against_5() -> None:
    """#1: 7対5 → 少数派5人は各+14万、多数派7人は各−10万"""
    config = GameConfig.default_12()
    outcome = resolve_minority(_votes(7, 5), config, carryover_before=0, round_num=1, is_final_round=False)
    assert outcome.minority_side == Vote.NO
    assert len(outcome.minority_ids) == 5
    assert outcome.payout_per_minority == 140_000
    assert outcome.forfeited_remainder == 0


def test_acceptance_2_11_against_1() -> None:
    """#2: 11対1 → 少数派1人は+110万"""
    config = GameConfig.default_12()
    outcome = resolve_minority(_votes(11, 1), config, carryover_before=0, round_num=1, is_final_round=False)
    assert len(outcome.minority_ids) == 1
    assert outcome.payout_per_minority == 1_100_000


def test_acceptance_3_6_against_6() -> None:
    """#3: 6対6 → 全員−10万。持ち越し120万"""
    config = GameConfig.default_12()
    outcome = resolve_minority(_votes(6, 6), config, carryover_before=0, round_num=1, is_final_round=False)
    assert outcome.minority_side is None
    assert outcome.minority_ids == []
    assert outcome.carryover_after == 1_200_000
    assert outcome.destroyed_carryover == 0


def test_acceptance_4_carryover_then_8_against_4() -> None:
    """#4: 持ち越し120万の次ラウンドが8対4 → 少数派は各+50万。持ち越しは0に戻る"""
    config = GameConfig.default_12()
    outcome = resolve_minority(
        _votes(8, 4), config, carryover_before=1_200_000, round_num=2, is_final_round=False,
    )
    assert len(outcome.minority_ids) == 4
    assert outcome.payout_per_minority == 500_000
    assert outcome.carryover_after == 0


def test_acceptance_5_12_against_0() -> None:
    """#5: 12対0 → 全員−10万。持ち越し120万"""
    config = GameConfig.default_12()
    outcome = resolve_minority(_votes(12, 0), config, carryover_before=0, round_num=1, is_final_round=False)
    assert outcome.minority_side is None
    assert outcome.carryover_after == 1_200_000


def test_acceptance_6_r12_6_against_6_destroys_carryover() -> None:
    """#6: R12で6対6 → 持ち越しは消滅"""
    config = GameConfig.default_12()
    outcome = resolve_minority(
        _votes(6, 6), config, carryover_before=1_200_000, round_num=12, is_final_round=True,
    )
    assert outcome.minority_side is None
    assert outcome.carryover_after == 0
    assert outcome.destroyed_carryover == 2_400_000  # 12*10万 + 持ち越し120万


def test_payout_table_no_carryover() -> None:
    """§4.2の配当表（持ち越しなし）: 少数派1〜5人"""
    config = GameConfig.default_12()
    expected = {1: 1_100_000, 2: 500_000, 3: 300_000, 4: 200_000, 5: 140_000}
    for minority_count, payout in expected.items():
        majority_count = 12 - minority_count
        outcome = resolve_minority(
            _votes(majority_count, minority_count), config,
            carryover_before=0, round_num=1, is_final_round=False,
        )
        assert outcome.payout_per_minority == payout, minority_count
        assert outcome.forfeited_remainder == 0


def test_forfeited_remainder_when_not_divisible() -> None:
    """持ち越しがあり、12人・10万円の設定を外れると端数が出る場合の没収処理

    12人・参加費10万の通常設定では必ず割り切れる（§4.3/§11.4#2）ため、
    この端数処理経路を踏ませるために現実の試合では起きない持ち越し額
    （333,333円）をあえて与える。
    """
    config = GameConfig.default_12()
    # majority=7 (7*10万=70万) + carryover 333,333円 = 1,033,333円。少数派5人で割ると割り切れない
    outcome = resolve_minority(
        _votes(7, 5), config, carryover_before=333_333, round_num=1, is_final_round=False,
    )
    assert outcome.pool == 1_033_333
    assert outcome.payout_per_minority == 1_033_333 // 5
    assert outcome.forfeited_remainder == 1_033_333 % 5
    assert outcome.forfeited_remainder > 0
