"""
Bot検証シミュレーションCLI（サイクル1.2、新規実装）

仕様書§12.1「Botで確認すること」を数字にするコマンド。ルールエンジン
（engine/）は一切変更せず、AI呼び出しは行わない（費用0円）。

結果は --out-dir（既定 data/sim、gitの管理外）にJSONで保存され、保存済みの
条件は（--force を付けない限り）回し直さない。1回の実行が長くなる条件は
--shard-size で分割して回せる（同じシード範囲なので、分割しても一括実行と
同じ数字になる。sim/store.py::merge_raw 参照）。--report-only を付けると
1試合も回さず、保存済みJSONだけからレポートを作る。

使用方法:
    uv run python scripts/simulate.py --scenario all --games 1000 --seed-start 1 \\
        --report doc/analysis/bot_simulation_report.md
    uv run python scripts/simulate.py --scenario S3k3 --games 10   # 動作確認
    uv run python scripts/simulate.py --scenario S1,S2 --games 100 --progress
    uv run python scripts/simulate.py --scenario all --games 1000 --report-only \\
        --report doc/analysis/bot_simulation_report.md   # 保存済みJSONだけから作成
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig
from sim import report as report_mod
from sim.metrics import summarize
from sim.scenarios import SCENARIO_KEYS
from sim.store import load_or_compute, load_raw, shard_path


def _parse_scenarios(arg: str) -> list[str]:
    if arg == "all":
        return list(SCENARIO_KEYS)
    keys = [k.strip() for k in arg.split(",") if k.strip()]
    for k in keys:
        if k not in SCENARIO_KEYS:
            raise SystemExit(f"Unknown scenario key: {k!r} (valid: {SCENARIO_KEYS})")
    return keys


def main() -> None:
    parser = argparse.ArgumentParser(description="少数決 Bot検証シミュレーション")
    parser.add_argument(
        "--scenario", type=str, default="all",
        help=f"カンマ区切りのシナリオキー、または 'all'（{', '.join(SCENARIO_KEYS)}）",
    )
    parser.add_argument("--games", type=int, default=1000, help="シナリオごとの試合数")
    parser.add_argument("--seed-start", type=int, default=1, help="最初の試合のシード")
    parser.add_argument("--report", type=str, default=None, help="Markdownレポートの出力先パス")
    parser.add_argument("--progress", action="store_true", help="シナリオごとの進捗を標準出力に表示")
    parser.add_argument(
        "--out-dir", type=str, default="data/sim",
        help="生データの保存先ディレクトリ（既定: data/sim。gitの管理外）",
    )
    parser.add_argument(
        "--shard-size", type=int, default=0,
        help="このサイズごとに分割して回し、結果を足し合わせる（既定0=分割しない）",
    )
    parser.add_argument("--force", action="store_true", help="保存済みの結果があっても回し直す")
    parser.add_argument(
        "--report-only", action="store_true",
        help="試合を回さず、保存済みの結果だけからレポートを作る（無ければエラー）",
    )
    args = parser.parse_args()

    scenario_keys = _parse_scenarios(args.scenario)
    config = GameConfig.default_12()
    out_dir = Path(args.out_dir)

    summaries: dict[str, dict] = {}
    for key in scenario_keys:
        t0 = time.time()
        if args.report_only:
            path = shard_path(out_dir, key, args.seed_start, args.games)
            if not path.exists():
                raise SystemExit(f"保存済みの結果が無い: {path}（--report-onlyを外して先に実行すること）")
            raw = load_raw(path)
        else:
            raw = load_or_compute(
                key, args.games, args.seed_start, config, out_dir,
                shard_size=args.shard_size, force=args.force,
            )
        summaries[key] = summarize(raw)
        if args.progress:
            elapsed = time.time() - t0
            print(f"{key}: {args.games}試合 完了（{elapsed:.1f}秒）", file=sys.stderr)

    if args.report:
        text = report_mod.render_report(summaries, args.games)
        output_path = Path(args.report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        print(f"レポート出力: {output_path}")
    else:
        for key in scenario_keys:
            s = summaries[key]
            print(f"=== {key} ({s['n_games']}試合) ===")
            print(f"  少数派なしの割合: {s['no_minority_ratio'] * 100:.1f}%")
            print(f"  持ち越し最大: {s['carryover_max']:,}円")
            print(f"  プラスで終わる人数の平均: {s['positive_count_mean']:.2f}人")
            if s["type_b_kept"] + s["type_b_violated"] > 0:
                print(f"  型B 守られた/破られた: {s['type_b_kept']}/{s['type_b_violated']}")


if __name__ == "__main__":
    main()
