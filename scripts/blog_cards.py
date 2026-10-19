#!/usr/bin/env python3
"""
キャラカード13枚（席12枚＋総括1枚）の生成CLI（サイクル6.0新設）

使い方:
    uv run python scripts/blog_cards.py              # 13枚すべて生成
    uv run python scripts/blog_cards.py --pid P06    # 1枚だけ
    uv run python scripts/blog_cards.py --summary-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from blog.cards import CardRenderer, render_card, render_summary_card  # noqa: E402
from blog.seats import build_seat_table  # noqa: E402

GAME_ID = "t12_r4_4501c"
LOG_DIR = PROJECT_ROOT / "logs" / "llm_v0_4"
OUT_DIR = PROJECT_ROOT / "doc" / "blog" / "20261013" / "images" / "cards"

ORDER_BY_PID = {
    "P07": "02", "P08": "03", "P05": "04", "P11": "05", "P12": "06", "P10": "07",
    "P02": "08", "P04": "09", "P09": "10", "P03": "11", "P01": "12", "P06": "13",
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", default=None, help="席ID（例: P06）。指定しなければ全13枚")
    ap.add_argument("--summary-only", action="store_true", help="総括カードだけ生成する")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seats = build_seat_table(LOG_DIR, GAME_ID)

    with CardRenderer(scale=1) as renderer:
        if not args.pid:
            card_path = render_summary_card(
                seats, renderer, OUT_DIR / "01_summary_card.png",
                label="T12R4・少数決", title="12席 総括",
                badge="v0.5・山分け作動", stat="優勝 P06 2,194万円",
            )
            print(f"[生成] {card_path}")
        if args.summary_only:
            return

        target_seats = [s for s in seats if s.pid == args.pid] if args.pid else seats
        for seat in target_seats:
            order = ORDER_BY_PID[seat.pid]
            card_path = render_card(seat, renderer, OUT_DIR / f"{order}_{seat.pid}_card.png")
            print(f"[生成] {card_path}")


if __name__ == "__main__":
    main()
