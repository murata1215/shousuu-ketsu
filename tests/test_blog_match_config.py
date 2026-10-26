"""
試合ごとのブログ設定（`blog/match_config.py`）のテスト（サイクル7.3新設）

`ORDER_BY_PID`（`scripts/blog_cards.py`）・`SLOTS`（`scripts/build_blog_posts.py`）の
手書き表を削除した安全網: 登録した全試合で `seat_order` に重複・欠番が無く、
`order_of`/`pid_of` が整合し、`seat_copy` のキーが `seat_order` と一致することを固定する。
"""

from __future__ import annotations

import pytest

from blog.match_config import MATCHES


@pytest.mark.parametrize("match", list(MATCHES.values()), ids=lambda m: m.date)
def test_seat_order_has_no_duplicates(match) -> None:
    assert len(set(match.seat_order)) == len(match.seat_order)


@pytest.mark.parametrize("match", list(MATCHES.values()), ids=lambda m: m.date)
def test_seat_order_pids_look_valid(match) -> None:
    for pid in match.seat_order:
        assert pid.startswith("P")
        assert pid[1:].isdigit()


@pytest.mark.parametrize("match", list(MATCHES.values()), ids=lambda m: m.date)
def test_order_of_and_pid_of_round_trip(match) -> None:
    orders = [match.order_of(pid) for pid in match.seat_order]
    assert orders == [f"{i:02d}" for i in range(2, match.post_count + 1)]
    for pid in match.seat_order:
        assert match.pid_of(match.order_of(pid)) == pid


@pytest.mark.parametrize("match", list(MATCHES.values()), ids=lambda m: m.date)
def test_seat_copy_keys_match_seat_order(match) -> None:
    assert set(match.seat_copy) == set(match.seat_order)


@pytest.mark.parametrize("match", list(MATCHES.values()), ids=lambda m: m.date)
def test_slug_prefix_matches_date(match) -> None:
    assert match.slug_prefix == f"sk-{match.date[4:]}"
    assert match.summary_slug == f"{match.slug_prefix}-01-summary"


def test_match1_title_prefix_and_card_header_are_empty() -> None:
    """第1戦は match_no==1 のため、title_prefix・card_header が空文字になる
    （呼び出し側に分岐を書かずに既存記事・カードを不変に保つ設計）。"""
    m1 = MATCHES["20261013"]
    assert m1.title_prefix == ""
    assert m1.card_header == ""


def test_match2_title_prefix_and_card_header_show_match_number() -> None:
    m2 = MATCHES["20261023"]
    assert m2.title_prefix == "【少数決 第2戦】"
    assert m2.card_header == "嘘八百万 —少数決— 第2戦"
