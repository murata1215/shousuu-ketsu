"""
投票判定（engine/vote.py）とラウンド集計（engine/round.py）の単体テスト（§4、v0.4）

v0.3の `tests/test_minority.py`（投票=ラウンド前提、60件）を全面置き換えた
（サイクル4.0）。v0.4は「決着＝退場のみ・お金は動かない」「やり直し＝延長料
徴収」「連続3回で打ち切り」「山の支払いはラウンドの終わりにまとめて」
（§4.3〜§4.5）という別の仕組みのため、同じ関数を呼んで確かめることはできない。

仕様書v0.4 §12.3の対応する受け入れ番号は docstring に明記する
（網羅的な対応表は doc/analysis/acceptance_v0_4.md）。
"""

from engine.config import GameConfig
from engine.models import Vote
from engine import round as round_ops
from engine import vote as vote_ops


def _votes(n_yes: int, n_no: int) -> dict[str, Vote]:
    ids = [f"P{i:02d}" for i in range(1, n_yes + n_no + 1)]
    return {
        **{pid: Vote.YES for pid in ids[:n_yes]},
        **{pid: Vote.NO for pid in ids[n_yes:]},
    }


def test_acceptance_01_decisive_7_against_5_no_money_moves() -> None:
    """#1: R1V1が7対5 → 多い側の7人が退場し、5人が残る。お金は動かない"""
    config = GameConfig.default_12()
    outcome = vote_ops.resolve_vote(_votes(7, 5), config, round_num=1, vote_num=1, consecutive_ties_before=0)
    assert outcome.result == "decisive"
    assert len(outcome.eliminated_ids) == 7
    assert len(outcome.remaining_ids) == 5
    assert outcome.extension_fee_collected == 0
    assert outcome.round_over is False  # 5人残り、survivors_max=2を超える


def test_acceptance_02_decisive_11_against_1_ends_round() -> None:
    """#2: R1V1が11対1 → 1人が残ってラウンド終了"""
    config = GameConfig.default_12()
    outcome = vote_ops.resolve_vote(_votes(11, 1), config, round_num=1, vote_num=1, consecutive_ties_before=0)
    assert len(outcome.remaining_ids) == 1
    assert outcome.round_over is True


def test_acceptance_03_decisive_10_against_2_ends_round() -> None:
    """#3: R1V1が10対2 → 2人が残ってラウンド終了"""
    config = GameConfig.default_12()
    outcome = vote_ops.resolve_vote(_votes(10, 2), config, round_num=1, vote_num=1, consecutive_ties_before=0)
    assert len(outcome.remaining_ids) == 2
    assert outcome.round_over is True


def test_acceptance_04_five_remaining_3_against_2_ends_round() -> None:
    """#4: 5人が残った次の投票が3対2 → 2人が残ってラウンド終了"""
    config = GameConfig.default_12()
    outcome = vote_ops.resolve_vote(_votes(3, 2), config, round_num=1, vote_num=2, consecutive_ties_before=0)
    assert len(outcome.remaining_ids) == 2
    assert outcome.round_over is True


def test_acceptance_05_five_remaining_4_against_1_ends_round() -> None:
    """#5: 5人が残った次の投票が4対1 → 1人が残ってラウンド終了"""
    config = GameConfig.default_12()
    outcome = vote_ops.resolve_vote(_votes(4, 1), config, round_num=1, vote_num=2, consecutive_ties_before=0)
    assert len(outcome.remaining_ids) == 1
    assert outcome.round_over is True


def test_acceptance_06_tie_6_against_6_is_retry() -> None:
    """#6: R1V1が6対6 → やり直し。12人が延長料10万ずつを払い、
    山は1,320万。やり直しの連続回数は1。誰も退場しない"""
    config = GameConfig.default_12()
    outcome = vote_ops.resolve_vote(_votes(6, 6), config, round_num=1, vote_num=1, consecutive_ties_before=0)
    assert outcome.result == "retry"
    assert outcome.eliminated_ids == []
    assert len(outcome.remaining_ids) == 12
    assert outcome.consecutive_ties_after == 1
    assert outcome.extension_fee_collected == 1_200_000  # 12 * 10万
    pot = round_ops.initial_pot(config, carryover_in=0) + outcome.extension_fee_collected
    assert pot == 13_200_000


def test_acceptance_07_unanimous_12_against_0_is_retry() -> None:
    """#7: R1V1が12対0 → #6と同じ（やり直し）"""
    config = GameConfig.default_12()
    outcome = vote_ops.resolve_vote(_votes(12, 0), config, round_num=1, vote_num=1, consecutive_ties_before=0)
    assert outcome.result == "retry"
    assert outcome.extension_fee_collected == 1_200_000


def test_acceptance_08_four_remaining_tie_only_remaining_pay() -> None:
    """#8: 4人が残った投票が2対2 → やり直し。4人が10万ずつ払い、
    山は40万増える。退場者は払わない（延長料の対象は呼び出し側が
    remaining_idsだけに限定することで保証する、§4.4）"""
    config = GameConfig.default_12()
    votes = _votes(2, 2)  # 4人だけが投票（退場者はvotesに含まれない）
    outcome = vote_ops.resolve_vote(votes, config, round_num=1, vote_num=3, consecutive_ties_before=0)
    assert outcome.result == "retry"
    assert set(outcome.remaining_ids) == set(votes.keys())
    assert outcome.extension_fee_collected == 400_000  # 4 * 10万


def test_acceptance_09_three_consecutive_ties_abort() -> None:
    """#9: R1のV1〜V3がすべて6対6 → 打ち切り。勝ち残りなし。
    山1,560万をR2へ持ち越し、R2開始時の山は2,760万"""
    config = GameConfig.default_12()
    ties = 0
    pot = round_ops.initial_pot(config, carryover_in=0)
    for vote_num in (1, 2, 3):
        outcome = vote_ops.resolve_vote(_votes(6, 6), config, round_num=1, vote_num=vote_num, consecutive_ties_before=ties)
        ties = outcome.consecutive_ties_after
        pot += outcome.extension_fee_collected
    assert outcome.result == "abort"
    assert pot == 15_600_000
    carryover_out, destroyed = round_ops.resolve_pot_carryover(pot, aborted=True, is_final_round=False)
    assert carryover_out == 15_600_000
    assert destroyed == 0

    r2_pot = round_ops.initial_pot(config, carryover_in=carryover_out)
    assert r2_pot == 27_600_000


def test_acceptance_10_consecutive_count_resets_on_decisive() -> None:
    """#10: やり直し2回の後に決着し、その後やり直しが2回 → 打ち切りにならない
    （連続回数は決着で0に戻る）。その次もやり直しなら打ち切り"""
    config = GameConfig.default_12()
    ties = 0
    for _ in range(2):
        outcome = vote_ops.resolve_vote(_votes(6, 6), config, round_num=1, vote_num=1, consecutive_ties_before=ties)
        ties = outcome.consecutive_ties_after
    assert ties == 2

    decisive = vote_ops.resolve_vote(_votes(7, 5), config, round_num=1, vote_num=3, consecutive_ties_before=ties)
    assert decisive.result == "decisive"
    ties = decisive.consecutive_ties_after
    assert ties == 0  # 決着で0に戻る

    # 決着後の残り5人は奇数なので「同数」のやり直しは作れない。§4.3の表の
    # とおり「全員が同じ側」（5対0）もやり直しに数える
    unanimous_5 = _votes(5, 0)
    for _ in range(2):
        outcome = vote_ops.resolve_vote(unanimous_5, config, round_num=1, vote_num=4, consecutive_ties_before=ties)
        ties = outcome.consecutive_ties_after
        assert outcome.result == "retry"
    assert ties == 2
    assert outcome.round_over is False  # まだ打ち切りではない

    outcome = vote_ops.resolve_vote(unanimous_5, config, round_num=1, vote_num=6, consecutive_ties_before=ties)
    assert outcome.result == "abort"
    assert outcome.round_over is True


def test_acceptance_11_r4_abort_destroys_pot() -> None:
    """#11: R4が打ち切り → 山は没収。誰にも渡らない"""
    config = GameConfig.default_12()
    carryover_out, destroyed = round_ops.resolve_pot_carryover(15_600_000, aborted=True, is_final_round=True)
    assert carryover_out == 0
    assert destroyed == 15_600_000


def test_acceptance_12_payout_after_retry_and_decisive() -> None:
    """#12: V1が7対5、V2が5対0、V3が3対2 → V2で5人が10万ずつ払い、
    山は1,250万。残った2人が625万ずつ受け取る"""
    config = GameConfig.default_12()

    v1 = vote_ops.resolve_vote(_votes(7, 5), config, round_num=1, vote_num=1, consecutive_ties_before=0)
    assert v1.result == "decisive"
    assert len(v1.remaining_ids) == 5

    pot = round_ops.initial_pot(config, carryover_in=0)
    v2_votes = {pid: Vote.YES for pid in v1.remaining_ids}  # 5対0（全員一致）
    v2 = vote_ops.resolve_vote(v2_votes, config, round_num=1, vote_num=2, consecutive_ties_before=v1.consecutive_ties_after)
    assert v2.result == "retry"
    pot += v2.extension_fee_collected
    assert pot == 12_500_000

    remaining_5 = sorted(v1.remaining_ids)
    v3_votes = {pid: (Vote.YES if i < 3 else Vote.NO) for i, pid in enumerate(remaining_5)}
    v3 = vote_ops.resolve_vote(v3_votes, config, round_num=1, vote_num=3, consecutive_ties_before=v2.consecutive_ties_after)
    assert v3.result == "decisive"
    assert len(v3.remaining_ids) == 2
    assert v3.round_over is True

    payout, remainder = round_ops.compute_payout(pot, v3.remaining_ids)
    assert payout == 6_250_000
    assert remainder == 0


def test_acceptance_13_vote6_decides_round_without_vote7() -> None:
    """#13: やり直し2回→決着→やり直し2回の後のV6 → V6が決着なら
    ラウンド終了、やり直しなら打ち切り。V7は起きない"""
    config = GameConfig.default_12()
    # V1,V2やり直し→V3決着→V4,V5やり直し→V6
    ties = 0
    for vote_num in (1, 2):
        outcome = vote_ops.resolve_vote(_votes(6, 6), config, round_num=1, vote_num=vote_num, consecutive_ties_before=ties)
        ties = outcome.consecutive_ties_after
    v3 = vote_ops.resolve_vote(_votes(7, 5), config, round_num=1, vote_num=3, consecutive_ties_before=ties)
    ties = v3.consecutive_ties_after
    assert ties == 0
    for vote_num in (4, 5):
        outcome = vote_ops.resolve_vote({pid: Vote.YES for pid in v3.remaining_ids}, config, round_num=1, vote_num=vote_num, consecutive_ties_before=ties)
        ties = outcome.consecutive_ties_after
    assert ties == 2

    # V6がやり直しなら打ち切り（連続3回）
    v6_retry = vote_ops.resolve_vote({pid: Vote.YES for pid in v3.remaining_ids}, config, round_num=1, vote_num=6, consecutive_ties_before=ties)
    assert v6_retry.result == "abort"
    assert v6_retry.round_over is True

    # V6が決着ならラウンド終了（5人からの決着で残り<=survivors_maxになることを確認）
    remaining_5 = sorted(v3.remaining_ids)
    v6_decisive_votes = {pid: (Vote.YES if i < 4 else Vote.NO) for i, pid in enumerate(remaining_5)}
    v6_decisive = vote_ops.resolve_vote(v6_decisive_votes, config, round_num=1, vote_num=6, consecutive_ties_before=ties)
    assert v6_decisive.result == "decisive"
    assert v6_decisive.round_over is True
    assert config.max_votes_per_round == 6  # V7は構造的に起きない


def test_payout_table_no_carryover() -> None:
    """山の配当表（持ち越しなし）: 勝ち残り1人・2人"""
    config = GameConfig.default_12()
    pot = round_ops.initial_pot(config, carryover_in=0)
    assert pot == 12_000_000

    payout_1, remainder_1 = round_ops.compute_payout(pot, ["P01"])
    assert payout_1 == 12_000_000
    assert remainder_1 == 0

    payout_2, remainder_2 = round_ops.compute_payout(pot, ["P01", "P02"])
    assert payout_2 == 6_000_000
    assert remainder_2 == 0


def test_forfeited_remainder_when_not_divisible() -> None:
    """山が割り切れない場合は1円未満を切り捨て、余りを没収する（§4.5）

    12人・参加費100万・延長料10万の通常設定では必ず割り切れるため、
    この端数処理経路を踏ませるために現実の試合では起きない山の額
    （333,333円）をあえて与える。"""
    payout, remainder = round_ops.compute_payout(1_000_001, ["P01", "P02", "P03"])
    assert payout == 1_000_001 // 3
    assert remainder == 1_000_001 % 3
    assert remainder > 0


def test_resolve_pot_carryover_decisive_round_has_no_carryover() -> None:
    """決着（打ち切りでない）なら持ち越しも没収もない"""
    carryover_out, destroyed = round_ops.resolve_pot_carryover(12_000_000, aborted=False, is_final_round=False)
    assert carryover_out == 0
    assert destroyed == 0
