"""
義務ごとの状態の文のテスト（§7.5・§9、サイクル4.2c A-1）

`llm/prompt_builder.py::_obligation_status_sentence` が、状態6つ
（pending・paid・type_b_kept・type_b_violated・condition_unmet・expired）×
義務の種類6つ（型A、型B、型Cの条件3種、割合で払う型C）×自分が義務者／相手が
義務者の、全72通りについて、文を丸ごと固定する（部分一致ではなく全文一致）。

サイクル4.2cの実行で見つかった「はが」「はの」「へ済み」の壊れた日本語
（義務の中身を書かずに状態だけを書いたことが原因）が再発しないことも、
72通り全部について機械的に確かめる。
"""

from __future__ import annotations

import pytest

from engine.config import GameConfig
from llm.prompt_builder import _obligation_status_sentence

config = GameConfig.default_12()

_BASE_OBLIGATIONS: dict[str, dict] = {
    "type_a": dict(ob_type="type_a_payment", round_num=1, vote_num=None, details={"amount": 200_000}),
    "type_b": dict(ob_type="type_b_vote", round_num=1, vote_num=2, details={"vote": "YES"}),
    "type_c_minority_side": dict(
        ob_type="type_c_conditional", round_num=1, vote_num=2,
        details={"condition_type": "minority_side", "condition": {"side": "NO"}, "amount": 300_000},
    ),
    "type_c_in_minority": dict(
        ob_type="type_c_conditional", round_num=1, vote_num=2,
        details={"condition_type": "in_minority", "condition": {"target_player": "P03"}, "amount": 300_000},
    ),
    "type_c_wins_round_fixed": dict(
        ob_type="type_c_conditional", round_num=1, vote_num=None,
        details={"condition_type": "wins_round", "condition": {"target_player": "P01"}, "amount": 3_000_000},
    ),
    "type_c_wins_round_share": dict(
        ob_type="type_c_conditional", round_num=1, vote_num=None,
        details={"condition_type": "wins_round", "condition": {"target_player": "P01"}, "share_percent": 30},
    ),
}

_STATUS_EXTRAS: dict[str, dict] = {
    "pending": {"status": "pending"},
    "paid": {"status": "paid", "promised": 200_000, "paid": 200_000},
    "type_b_kept": {"status": "type_b_kept"},
    "type_b_violated": {"status": "type_b_violated", "promised": 1_000_000, "paid": 1_000_000},
    "condition_unmet": {"status": "condition_unmet"},
    "expired": {"status": "expired"},
}

# 72通り（6種類×6状態×自分/相手）。obligor="P01"・counterparty="P07"で固定。
# is_mine=True: 自分(P01)が義務者。is_mine=False: 相手(P01)が義務者で自分はP07(あなた)。
GOLDEN: list[tuple[str, str, bool, str]] = [
    ("type_a", "pending", True, "R1: あなたは P07へ200,000円支払わなければならない（これから）"),
    ("type_a", "pending", False, "R1: P01は あなたへ200,000円支払わなければならない（これから）"),
    ("type_a", "paid", True, "R1: あなたは P07へ200,000円支払う義務 → 済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_a", "paid", False, "R1: P01は あなたへ200,000円支払う義務 → 済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_a", "type_b_kept", True, "R1: あなたは P07へ200,000円支払う義務 → 守った"),
    ("type_a", "type_b_kept", False, "R1: P01は あなたへ200,000円支払う義務 → 守った"),
    ("type_a", "type_b_violated", True, "R1: あなたは P07へ200,000円支払う義務 → 違反した（違約金を P07へ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_a", "type_b_violated", False, "R1: P01は あなたへ200,000円支払う義務 → 違反した（違約金を あなたへ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_a", "condition_unmet", True, "R1: あなたは P07へ200,000円支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_a", "condition_unmet", False, "R1: P01は あなたへ200,000円支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_a", "expired", True, "R1: あなたは P07へ200,000円支払う義務 → 失効した"),
    ("type_a", "expired", False, "R1: P01は あなたへ200,000円支払う義務 → 失効した"),
    ("type_b", "pending", True, "R1V2: あなたは YESへ投票しなければならない（違反時はP07へ違約金1,000,000円）（これから）"),
    ("type_b", "pending", False, "R1V2: P01は YESへ投票しなければならない（違反時はあなたへ違約金1,000,000円）（これから）"),
    ("type_b", "paid", True, "R1V2: あなたは YESへ投票する義務 → 済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_b", "paid", False, "R1V2: P01は YESへ投票する義務 → 済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_b", "type_b_kept", True, "R1V2: あなたは YESへ投票する義務 → 守った"),
    ("type_b", "type_b_kept", False, "R1V2: P01は YESへ投票する義務 → 守った"),
    ("type_b", "type_b_violated", True, "R1V2: あなたは YESへ投票する義務 → 違反した（違約金を P07へ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_b", "type_b_violated", False, "R1V2: P01は YESへ投票する義務 → 違反した（違約金を あなたへ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_b", "condition_unmet", True, "R1V2: あなたは YESへ投票する義務 → 条件は成立しなかった（支払いなし）"),
    ("type_b", "condition_unmet", False, "R1V2: P01は YESへ投票する義務 → 条件は成立しなかった（支払いなし）"),
    ("type_b", "expired", True, "R1V2: あなたは YESへ投票する義務 → 失効した"),
    ("type_b", "expired", False, "R1V2: P01は YESへ投票する義務 → 失効した"),
    ("type_c_minority_side", "pending", True, "R1V2の少数派がNOになった場合、あなたは P07へ300,000円支払わなければならない（これから）"),
    ("type_c_minority_side", "pending", False, "R1V2の少数派がNOになった場合、P01は あなたへ300,000円支払わなければならない（これから）"),
    ("type_c_minority_side", "paid", True, "R1V2の少数派がNOになった場合、あなたは P07へ300,000円支払う義務 → 条件が成立し、済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_c_minority_side", "paid", False, "R1V2の少数派がNOになった場合、P01は あなたへ300,000円支払う義務 → 条件が成立し、済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_c_minority_side", "type_b_kept", True, "R1V2の少数派がNOになった場合、あなたは P07へ300,000円支払う義務 → 守った"),
    ("type_c_minority_side", "type_b_kept", False, "R1V2の少数派がNOになった場合、P01は あなたへ300,000円支払う義務 → 守った"),
    ("type_c_minority_side", "type_b_violated", True, "R1V2の少数派がNOになった場合、あなたは P07へ300,000円支払う義務 → 違反した（違約金を P07へ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_c_minority_side", "type_b_violated", False, "R1V2の少数派がNOになった場合、P01は あなたへ300,000円支払う義務 → 違反した（違約金を あなたへ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_c_minority_side", "condition_unmet", True, "R1V2の少数派がNOになった場合、あなたは P07へ300,000円支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_c_minority_side", "condition_unmet", False, "R1V2の少数派がNOになった場合、P01は あなたへ300,000円支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_c_minority_side", "expired", True, "R1V2の少数派がNOになった場合、あなたは P07へ300,000円支払う義務 → 失効した"),
    ("type_c_minority_side", "expired", False, "R1V2の少数派がNOになった場合、P01は あなたへ300,000円支払う義務 → 失効した"),
    ("type_c_in_minority", "pending", True, "R1V2でP03が少数派に入った場合、あなたは P07へ300,000円支払わなければならない（これから）"),
    ("type_c_in_minority", "pending", False, "R1V2でP03が少数派に入った場合、P01は あなたへ300,000円支払わなければならない（これから）"),
    ("type_c_in_minority", "paid", True, "R1V2でP03が少数派に入った場合、あなたは P07へ300,000円支払う義務 → 条件が成立し、済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_c_in_minority", "paid", False, "R1V2でP03が少数派に入った場合、P01は あなたへ300,000円支払う義務 → 条件が成立し、済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_c_in_minority", "type_b_kept", True, "R1V2でP03が少数派に入った場合、あなたは P07へ300,000円支払う義務 → 守った"),
    ("type_c_in_minority", "type_b_kept", False, "R1V2でP03が少数派に入った場合、P01は あなたへ300,000円支払う義務 → 守った"),
    ("type_c_in_minority", "type_b_violated", True, "R1V2でP03が少数派に入った場合、あなたは P07へ300,000円支払う義務 → 違反した（違約金を P07へ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_c_in_minority", "type_b_violated", False, "R1V2でP03が少数派に入った場合、P01は あなたへ300,000円支払う義務 → 違反した（違約金を あなたへ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_c_in_minority", "condition_unmet", True, "R1V2でP03が少数派に入った場合、あなたは P07へ300,000円支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_c_in_minority", "condition_unmet", False, "R1V2でP03が少数派に入った場合、P01は あなたへ300,000円支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_c_in_minority", "expired", True, "R1V2でP03が少数派に入った場合、あなたは P07へ300,000円支払う義務 → 失効した"),
    ("type_c_in_minority", "expired", False, "R1V2でP03が少数派に入った場合、P01は あなたへ300,000円支払う義務 → 失効した"),
    ("type_c_wins_round_fixed", "pending", True, "R1でP01が勝ち残った場合、あなたは P07へ3,000,000円支払わなければならない（これから）"),
    ("type_c_wins_round_fixed", "pending", False, "R1でP01が勝ち残った場合、P01は あなたへ3,000,000円支払わなければならない（これから）"),
    ("type_c_wins_round_fixed", "paid", True, "R1でP01が勝ち残った場合、あなたは P07へ3,000,000円支払う義務 → 条件が成立し、済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_c_wins_round_fixed", "paid", False, "R1でP01が勝ち残った場合、P01は あなたへ3,000,000円支払う義務 → 条件が成立し、済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_c_wins_round_fixed", "type_b_kept", True, "R1でP01が勝ち残った場合、あなたは P07へ3,000,000円支払う義務 → 守った"),
    ("type_c_wins_round_fixed", "type_b_kept", False, "R1でP01が勝ち残った場合、P01は あなたへ3,000,000円支払う義務 → 守った"),
    ("type_c_wins_round_fixed", "type_b_violated", True, "R1でP01が勝ち残った場合、あなたは P07へ3,000,000円支払う義務 → 違反した（違約金を P07へ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_c_wins_round_fixed", "type_b_violated", False, "R1でP01が勝ち残った場合、P01は あなたへ3,000,000円支払う義務 → 違反した（違約金を あなたへ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_c_wins_round_fixed", "condition_unmet", True, "R1でP01が勝ち残った場合、あなたは P07へ3,000,000円支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_c_wins_round_fixed", "condition_unmet", False, "R1でP01が勝ち残った場合、P01は あなたへ3,000,000円支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_c_wins_round_fixed", "expired", True, "R1でP01が勝ち残った場合、あなたは P07へ3,000,000円支払う義務 → 失効した"),
    ("type_c_wins_round_fixed", "expired", False, "R1でP01が勝ち残った場合、P01は あなたへ3,000,000円支払う義務 → 失効した"),
    ("type_c_wins_round_share", "pending", True, "R1でP01が勝ち残った場合、あなたは P07へ受け取った山の30%を支払わなければならない（これから）"),
    ("type_c_wins_round_share", "pending", False, "R1でP01が勝ち残った場合、P01は あなたへ受け取った山の30%を支払わなければならない（これから）"),
    ("type_c_wins_round_share", "paid", True, "R1でP01が勝ち残った場合、あなたは P07へ受け取った山の30%を支払う義務 → 条件が成立し、済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_c_wins_round_share", "paid", False, "R1でP01が勝ち残った場合、P01は あなたへ受け取った山の30%を支払う義務 → 条件が成立し、済み（約束 200,000円 / 実際に払われた額 200,000円）"),
    ("type_c_wins_round_share", "type_b_kept", True, "R1でP01が勝ち残った場合、あなたは P07へ受け取った山の30%を支払う義務 → 守った"),
    ("type_c_wins_round_share", "type_b_kept", False, "R1でP01が勝ち残った場合、P01は あなたへ受け取った山の30%を支払う義務 → 守った"),
    ("type_c_wins_round_share", "type_b_violated", True, "R1でP01が勝ち残った場合、あなたは P07へ受け取った山の30%を支払う義務 → 違反した（違約金を P07へ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_c_wins_round_share", "type_b_violated", False, "R1でP01が勝ち残った場合、P01は あなたへ受け取った山の30%を支払う義務 → 違反した（違約金を あなたへ。約束 1,000,000円 / 実際に払われた額 1,000,000円）"),
    ("type_c_wins_round_share", "condition_unmet", True, "R1でP01が勝ち残った場合、あなたは P07へ受け取った山の30%を支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_c_wins_round_share", "condition_unmet", False, "R1でP01が勝ち残った場合、P01は あなたへ受け取った山の30%を支払う義務 → 条件は成立しなかった（支払いなし）"),
    ("type_c_wins_round_share", "expired", True, "R1でP01が勝ち残った場合、あなたは P07へ受け取った山の30%を支払う義務 → 失効した"),
    ("type_c_wins_round_share", "expired", False, "R1でP01が勝ち残った場合、P01は あなたへ受け取った山の30%を支払う義務 → 失効した"),
]


def test_golden_table_covers_all_72_combinations() -> None:
    """6種類×6状態×自分/相手の72通りがすべて揃っていること（抜け漏れの検出）"""
    assert len(GOLDEN) == 72
    assert len({(t, s, m) for t, s, m, _ in GOLDEN}) == 72
    assert set(t for t, _, _, _ in GOLDEN) == set(_BASE_OBLIGATIONS)
    assert set(s for _, s, _, _ in GOLDEN) == set(_STATUS_EXTRAS)


@pytest.mark.parametrize("type_key,status_key,is_mine,expected", GOLDEN)
def test_obligation_status_sentence_matches_golden_text(type_key, status_key, is_mine, expected) -> None:
    ob = {**_BASE_OBLIGATIONS[type_key], "obligor": "P01", "counterparty": "P07", **_STATUS_EXTRAS[status_key]}
    actual = _obligation_status_sentence(ob, is_mine=is_mine, config=config)
    assert actual == expected


@pytest.mark.parametrize("type_key,status_key,is_mine,expected", GOLDEN)
def test_obligation_status_sentence_has_no_broken_japanese(type_key, status_key, is_mine, expected) -> None:
    """
    サイクル4.2cで見つかった壊れた日本語（「はが」「はの」「へ済み」）が
    どの72通りにも出ないこと。golden表そのものと、実際の出力の両方を確認する
    （golden表自体に同じ誤記が紛れ込むのを防ぐため）。
    """
    actual = _obligation_status_sentence(
        {**_BASE_OBLIGATIONS[type_key], "obligor": "P01", "counterparty": "P07", **_STATUS_EXTRAS[status_key]},
        is_mine=is_mine, config=config,
    )
    for broken in ("はが", "はの", "へ済み"):
        assert broken not in expected, f"golden表自体に壊れた日本語が含まれる: {broken!r} in {expected!r}"
        assert broken not in actual, f"壊れた日本語が出た: {broken!r} in {actual!r}"
