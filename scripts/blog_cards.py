#!/usr/bin/env python3
"""
キャラカード（席N枚＋総括1枚）の生成CLI（サイクル6.0新設、サイクル7.3で`--date`対応）

使い方:
    uv run python scripts/blog_cards.py --date 20261023              # 全部生成
    uv run python scripts/blog_cards.py --date 20261023 --pid P01    # 1枚だけ
    uv run python scripts/blog_cards.py --date 20261023 --summary-only

`--date`省略時は`blog.match_config.DEFAULT_DATE`（現行サイクルの試合）を使う。
席順・順位・宣伝文は`blog.match_config.MATCHES`の唯一の正から読む
（手書きの`ORDER_BY_PID`は廃止。サイクル7.3）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from blog.cards import CardRenderer, render_card, render_summary_card  # noqa: E402
from blog.match_config import DEFAULT_DATE, LOG_DIR, MATCHES  # noqa: E402
from blog.seats import build_seat_table  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=DEFAULT_DATE, choices=sorted(MATCHES), help="対象試合の日付（既定: 現行サイクルの試合）")
    ap.add_argument("--pid", default=None, help="席ID（例: P01）。指定しなければ全席＋総括")
    ap.add_argument("--summary-only", action="store_true", help="総括カードだけ生成する")
    args = ap.parse_args()

    match = MATCHES[args.date]
    if args.pid is not None and args.pid not in match.seat_order:
        raise SystemExit(f"[エラー] {match.date}（{match.game_id}）に席 {args.pid} は無い。")

    out_dir = match.cards_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    seats = build_seat_table(LOG_DIR, match.game_id)

    # 他席は席番号を使わない方針（match_no>=2）の試合では、総括カードのタイルの札も
    # 席番号ではなく最終順位に切り替える（けいすけさんの決定）。
    tile_tag_fn = (lambda seat: seat.pid) if match.match_no == 1 else (lambda seat: f"{seat.final_rank}位")

    with CardRenderer(scale=1) as renderer:
        if not args.pid:
            card_path = render_summary_card(
                seats, renderer, out_dir / "01_summary_card.png",
                label=f"{match.footer_tag}・少数決", title=f"{match.seat_count}席 総括",
                badge=match.summary_badge, stat=match.summary_stat,
                footer=match.card_footer, header=match.card_header, tile_tag_fn=tile_tag_fn,
            )
            print(f"[生成] {card_path}")
        if args.summary_only:
            return

        target_seats = [s for s in seats if s.pid == args.pid] if args.pid else seats
        for seat in target_seats:
            order = match.order_of(seat.pid)
            card_path = render_card(
                seat, renderer, out_dir / f"{order}_{seat.pid}_card.png",
                footer=match.card_footer, header=match.card_header,
            )
            print(f"[生成] {card_path}")


if __name__ == "__main__":
    main()
