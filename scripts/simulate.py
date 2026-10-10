"""
Bot検証シミュレーションCLI（サイクル1.2で新規実装。サイクル4.1でv0.4
（L12R4V6・V1〜V10）対応に全面作り直した）

仕様書§12.1「Botで確認すること」の生データを作るコマンド。ルールエンジン
（engine/）は一切変更せず、AI呼び出しは行わない（費用0円）。

結果は --out-dir（既定 data/sim_v0_4、gitの管理外）にJSONで保存され、
保存済みの条件は（--force を付けない限り）回し直さない。1回の実行が長くなる
条件は --shard-size で分割して回せる（同じシード範囲なので、分割しても
一括実行と同じ数字になる。sim/store.py::merge_raw 参照）。

集計・Markdownレポートの作成は本スクリプトの範囲外にした（v0.3のS1〜S6は
金額ベースの指標が共通だったが、v0.4のV1〜V10は組・借入・重ね売りなど
見るべき指標がシナリオごとに大きく異なるため、1つの汎用summarize/report
関数に押し込めなかった）。保存済みJSONから報告書を作るのは
scripts/report_bot_simulation_v0_4.py が担う。

使用方法:
    uv run python scripts/simulate.py --scenario V1 --games 1000 --seed-start 1
    uv run python scripts/simulate.py --scenario V3g3 --games 10   # 動作確認
    uv run python scripts/simulate.py --scenario V8keep_pen100,V8break_pen100 --games 100 --progress
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim.metrics import common_stats
from sim.scenarios import SCENARIO_KEYS
from sim.store import load_or_compute


def _parse_scenarios(arg: str) -> list[str]:
    if arg == "all":
        return list(SCENARIO_KEYS)
    keys = [k.strip() for k in arg.split(",") if k.strip()]
    for k in keys:
        if k not in SCENARIO_KEYS:
            raise SystemExit(f"Unknown scenario key: {k!r} (valid: {SCENARIO_KEYS})")
    return keys


def main() -> None:
    parser = argparse.ArgumentParser(description="少数決 Bot検証シミュレーション（v0.4）")
    parser.add_argument(
        "--scenario", type=str, default="all",
        help=f"カンマ区切りのシナリオキー、または 'all'（{', '.join(SCENARIO_KEYS)}）",
    )
    parser.add_argument("--games", type=int, default=1000, help="シナリオごとの試合数")
    parser.add_argument("--seed-start", type=int, default=1, help="最初の試合のシード")
    parser.add_argument("--progress", action="store_true", help="シナリオごとの進捗を標準出力に表示")
    parser.add_argument(
        "--out-dir", type=str, default="data/sim_v0_4",
        help="生データの保存先ディレクトリ（既定: data/sim_v0_4。gitの管理外）",
    )
    parser.add_argument(
        "--shard-size", type=int, default=0,
        help="このサイズごとに分割して回し、結果を足し合わせる（既定0=分割しない）",
    )
    parser.add_argument("--force", action="store_true", help="保存済みの結果があっても回し直す")
    args = parser.parse_args()

    scenario_keys = _parse_scenarios(args.scenario)
    out_dir = Path(args.out_dir)

    for key in scenario_keys:
        t0 = time.time()
        raw = load_or_compute(
            key, args.games, args.seed_start, out_dir,
            shard_size=args.shard_size, force=args.force,
        )
        elapsed = time.time() - t0
        stats = common_stats(raw["games"])
        if args.progress:
            print(
                f"{key}: {args.games}試合 完了（{elapsed:.1f}秒、"
                f"打ち切り{stats['abort_ratio'] * 100:.1f}%、"
                f"1人勝ち{stats['one_winner_ratio'] * 100:.1f}%、"
                f"2人勝ち{stats['two_winner_ratio'] * 100:.1f}%）",
                file=sys.stderr,
            )
        else:
            print(f"=== {key} ({raw['n_games']}試合) ===")
            print(f"  打ち切り率: {stats['abort_ratio'] * 100:.1f}%")
            print(f"  1人勝ち/2人勝ち: {stats['one_winner_ratio'] * 100:.1f}%/{stats['two_winner_ratio'] * 100:.1f}%")
            print(f"  1ラウンドの投票回数の平均: {stats['vote_count_mean']:.2f}回")
            print(f"  プラスで終わる人数の平均: {stats['positive_count_mean']:.2f}人")
            print(f"  持ち越し最大: {stats['carryover_out_max']:,}円")
            print(f"  王様作りの余地: {stats['king_making_ratio'] * 100:.1f}%")


if __name__ == "__main__":
    main()
