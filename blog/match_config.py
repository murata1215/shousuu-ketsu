"""
試合ごとのブログ連載設定（サイクル7.3新設）

サイクル6.0は試合固有の値（`GAME_ID`・日付・URLスラグ接頭辞・席順・宣伝文等）を
4つのスクリプトの定数に直接書いていた。第2戦（`t17_r4_7002`）を連載するにあたり、
試合ごとに番号を渡せる形にここへ集める。

`title_prefix`・`card_header` は `match_no == 1` のとき空文字になるよう設計してある。
これにより、呼び出し側に分岐を書かなくても第1戦の既存記事・カードが変わらない
（`tests/test_build_blog_posts.py::test_rebuild_summary_is_byte_identical` で固定）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "llm_v0_4"
BLOG_ROOT = PROJECT_ROOT / "doc" / "blog"


@dataclass(frozen=True)
class SeatCopy:
    """手記1本ぶんの宣伝文（第1戦の`SLOTS`の`lead`/`x_text`/`x_hook`相当）。"""

    lead: str
    x_text: str
    x_hook: str


@dataclass(frozen=True)
class SummaryCopy:
    """総括記事の宣伝文。"""

    title: str
    lead: str
    x_text: str
    x_hook: str


@dataclass(frozen=True)
class BlogMatch:
    date: str                       # "20261013" / "20261023"
    game_id: str                    # "t12_r4_4501c" / "t17_r4_7002"
    match_no: int                   # 1 / 2
    footer_tag: str                 # "T12R4" / "T17R4"（カード下部の末尾）
    seat_order: tuple[str, ...]     # order 02.. の席順（唯一の正。ORDER_BY_PID/SLOTSを置き換える）
    summary: SummaryCopy
    seat_copy: Mapping[str, SeatCopy]
    summary_badge: str = ""
    summary_stat: str = ""

    def __post_init__(self) -> None:
        if len(set(self.seat_order)) != len(self.seat_order):
            raise ValueError(f"{self.date}: seat_orderに重複がある: {self.seat_order}")
        if set(self.seat_copy) != set(self.seat_order):
            missing = set(self.seat_order) - set(self.seat_copy)
            extra = set(self.seat_copy) - set(self.seat_order)
            raise ValueError(f"{self.date}: seat_copyとseat_orderが一致しない（不足{missing}・余分{extra}）")

    # ------------------------------------------------------------------
    # 導出プロパティ（手で書かない。dateやmatch_noとずれようがない）
    # ------------------------------------------------------------------
    @property
    def series(self) -> str:
        return self.date

    @property
    def index_date(self) -> str:
        return f"{self.date[:4]}-{self.date[4:6]}-{self.date[6:]}"

    @property
    def slug_prefix(self) -> str:
        return f"sk-{self.date[4:]}"

    @property
    def summary_slug(self) -> str:
        return f"{self.slug_prefix}-01-summary"

    @property
    def title_prefix(self) -> str:
        return "" if self.match_no == 1 else f"【少数決 第{self.match_no}戦】"

    @property
    def card_header(self) -> str:
        return "" if self.match_no == 1 else f"嘘八百万 —少数決— 第{self.match_no}戦"

    @property
    def card_footer(self) -> str:
        return f"嘘八百万 —少数決—　{self.footer_tag}"

    @property
    def seat_count(self) -> int:
        return len(self.seat_order)

    @property
    def post_count(self) -> int:
        return self.seat_count + 1

    def order_of(self, pid: str) -> str:
        return f"{self.seat_order.index(pid) + 2:02d}"

    def pid_of(self, order: str) -> str:
        idx = int(order) - 2
        if idx < 0 or idx >= len(self.seat_order):
            raise KeyError(f"{self.date}: order={order!r} に対応する席が無い")
        return self.seat_order[idx]

    # ------------------------------------------------------------------
    # パス
    # ------------------------------------------------------------------
    @property
    def out_dir(self) -> Path:
        return BLOG_ROOT / self.date

    @property
    def raw_dir(self) -> Path:
        return self.out_dir / "raw"

    @property
    def posts_dir(self) -> Path:
        return self.out_dir / "posts"

    @property
    def cards_dir(self) -> Path:
        return self.out_dir / "images" / "cards"

    @property
    def plan_path(self) -> Path:
        return self.out_dir / "plan.md"

    @property
    def post_sets_path(self) -> Path:
        return self.out_dir / "post_sets.md"

    @property
    def summary_body_path(self) -> Path:
        return self.out_dir / "summary_body.md"

    @property
    def preflight_path(self) -> Path:
        return PROJECT_ROOT / ".devrelay-output" / f"blog_{self.date}_preflight.md"


def _load_matches() -> dict[str, BlogMatch]:
    from blog.matches.m20261013 import MATCH as m1
    from blog.matches.m20261023 import MATCH as m2

    return {m1.date: m1, m2.date: m2}


MATCHES: dict[str, BlogMatch] = _load_matches()
MATCHES_BY_GAME_ID: dict[str, BlogMatch] = {m.game_id: m for m in MATCHES.values()}

# 現行サイクルの試合。試合を追加するときは、ここを意図的に書き換える
# （「最後に追加したものを自動で既定にする」のような暗黙の仕組みは持たない）。
DEFAULT_DATE = "20261023"
