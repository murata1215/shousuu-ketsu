"""
キャラカード（`blog/seats.py`・`blog/cards.py`）のテスト（サイクル6.0新設）

Chromiumの起動を要するレンダリング確認（`render_card`/`render_summary_card`）は
本番試合の記録がある環境でのみ行う（`logs/`はコミット対象外）。HTML文字列の組み立てと
表情割り当てのアルゴリズムはfixtureだけで確認する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blog.cards import _EMOTIONS_DIR, build_card_html, build_summary_card_html
from blog.seats import EMOTION_EN, SeatInfo, _assign_emotions, build_seat_table

FX_DIR = Path(__file__).resolve().parent / "fixtures" / "viewer_logs"
FX_GAME = "fx_blog"

REAL_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs" / "llm_v0_4"
REAL_GAME = "t12_r4_4501c"


def _seat(pid, vendor, final_rank, emotion_jp="奸") -> SeatInfo:
    return SeatInfo(
        pid=pid, model_id=f"model-{pid}", short_label=f"model-{pid}", display_name=f"Model {pid}",
        provider=vendor.title(), vendor=vendor, is_devrelay=False, final_rank=final_rank,
        final_assets=0, emotion_en=EMOTION_EN[emotion_jp], emotion_jp=emotion_jp,
    )


# ---------------------------------------------------------------------------
# 表情割り当てアルゴリズム（blog/seats.py::_assign_emotions）
# ---------------------------------------------------------------------------

def test_emotion_assignment_avoids_collision_within_vendor() -> None:
    """同じ会社の複数席は、最頻値が同じでも異なる表情になる。"""
    pids_by_rank = ["P01", "P02", "P03"]
    vendor_of = {"P01": "openai", "P02": "openai", "P03": "openai"}
    emotion_counts = {
        "P01": {"奸": 50, "疑": 10},
        "P02": {"奸": 40, "疑": 30},
        "P03": {"奸": 20, "楽": 5},
    }
    chosen = _assign_emotions(pids_by_rank, vendor_of, emotion_counts)
    assert chosen["P01"] == "奸"  # 1位は無条件で最頻値
    assert chosen["P02"] == "疑"  # 奸は1位が既に使用、次点の疑を採用
    assert chosen["P03"] == "楽"  # 奸も疑も使用済みなので次点の楽
    assert len(set(chosen.values())) == 3


def test_emotion_assignment_different_vendors_no_collision_check() -> None:
    """会社が違えば、同じ表情が重複してもよい。"""
    pids_by_rank = ["P01", "P02"]
    vendor_of = {"P01": "openai", "P02": "anthropic"}
    emotion_counts = {"P01": {"奸": 50}, "P02": {"奸": 50}}
    chosen = _assign_emotions(pids_by_rank, vendor_of, emotion_counts)
    assert chosen["P01"] == "奸"
    assert chosen["P02"] == "奸"


def test_emotion_assignment_deterministic() -> None:
    pids_by_rank = ["P01", "P02", "P03", "P04"]
    vendor_of = {"P01": "openai", "P02": "openai", "P03": "openai", "P04": "openai"}
    emotion_counts = {
        "P01": {"奸": 10}, "P02": {"奸": 10}, "P03": {"奸": 10}, "P04": {"奸": 10},
    }
    chosen1 = _assign_emotions(pids_by_rank, vendor_of, emotion_counts)
    chosen2 = _assign_emotions(pids_by_rank, vendor_of, emotion_counts)
    assert chosen1 == chosen2


def test_build_seat_table_from_fixture_sorted_by_rank() -> None:
    seats = build_seat_table(FX_DIR, FX_GAME)
    assert [s.final_rank for s in seats] == [1, 2, 3, 4]
    assert seats[0].pid == "P02"  # fx_blogのGAME_ENDでfinal_ranks P02=1


# ---------------------------------------------------------------------------
# HTML組み立て（Chromium不要、文字列の検査のみ）
# ---------------------------------------------------------------------------

def test_card_html_contains_pid_and_model_and_rank() -> None:
    seat = _seat("P06", "openai", final_rank=1)
    html = build_card_html(seat)
    assert ">P06<" in html
    assert "Model P06" in html
    assert "最終 1位" in html


def test_card_html_escapes_special_characters() -> None:
    seat = _seat("P06", "openai", final_rank=1)
    object.__setattr__(seat, "display_name", 'Model <script>&"P06"')
    html = build_card_html(seat)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_card_html_marks_devrelay_seat() -> None:
    seat = _seat("P06", "openai", final_rank=1)
    object.__setattr__(seat, "is_devrelay", True)
    html = build_card_html(seat)
    assert "DevRelay実験席" in html


def test_summary_card_html_includes_all_seats_in_rank_order() -> None:
    seats = [_seat(f"P{i:02d}", "openai", final_rank=i) for i in range(1, 13)]
    html = build_summary_card_html(seats, label="L", title="T", badge="B", stat="S")
    # 出現順が順位順であること
    positions = [html.index(f">{s.pid}<") for s in seats]
    assert positions == sorted(positions)


def test_all_vendor_emotion_images_exist_on_disk() -> None:
    """全ベンダー×全表情の組み合わせが`viewer/static/emotions/`に揃っていること。"""
    vendors = ["anthropic", "openai", "google", "xai", "moonshot", "deepseek"]
    emotions = set(EMOTION_EN.values())
    for vendor in vendors:
        for emo in emotions:
            path = _EMOTIONS_DIR / f"{vendor}_{emo}.png"
            assert path.exists(), f"missing: {path}"


# ---------------------------------------------------------------------------
# 本番試合（12席）: 実際のレンダリング（Chromium起動）確認
# ---------------------------------------------------------------------------

pytestmark_real = pytest.mark.skipif(
    not (REAL_LOGS_DIR / f"{REAL_GAME}_events.jsonl").exists(),
    reason="本番試合t12_r4_4501cの記録がこの環境に無い（logs/はコミット対象外）",
)


@pytestmark_real
def test_real_game_seat_table_no_emotion_collision_within_vendor() -> None:
    seats = build_seat_table(REAL_LOGS_DIR, REAL_GAME)
    assert len(seats) == 12
    by_vendor: dict[str, list[str]] = {}
    for s in seats:
        by_vendor.setdefault(s.vendor, []).append(s.emotion_en)
    for vendor, emos in by_vendor.items():
        assert len(emos) == len(set(emos)), f"{vendor}: 表情の重複 {emos}"


@pytestmark_real
def test_real_game_render_card_and_summary(tmp_path) -> None:
    from blog.cards import CardRenderer, render_card, render_summary_card

    seats = build_seat_table(REAL_LOGS_DIR, REAL_GAME)
    with CardRenderer(scale=1) as renderer:
        out = render_card(seats[0], renderer, tmp_path / "01_card.png")
        assert out.exists()
        summary_out = render_summary_card(
            seats, renderer, tmp_path / "00_summary.png",
            label="L", title="T", badge="B", stat="S",
        )
        assert summary_out.exists()
