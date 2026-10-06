"""
ドライランスクリプト（動作確認用、§12.3の目視確認に対応）

12人×12ラウンドの試合を1本実行し、ラウンドごとの票の割れ方・配当・
持ち越しと、最終順位を表示する。gentei-janken `scripts/dry_run.py`
（B分類）の構造を12人12R・少数決向けに書き換えた。

使用方法:
    uv run python scripts/dry_run.py --seed 42
    uv run python scripts/dry_run.py --seed 42 --bots
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent


def build_agents(config: GameConfig, use_bots: bool) -> dict:
    if not use_bots:
        return {f"P{i:02d}": StubAgent() for i in range(1, config.num_players + 1)}

    from bots import BOT_REGISTRY, DEFAULT_ROSTER
    agents = {}
    names = DEFAULT_ROSTER
    for i in range(1, config.num_players + 1):
        pid = f"P{i:02d}"
        bot_name = names[(i - 1) % len(names)]
        agents[pid] = BOT_REGISTRY[bot_name](seed=i)
    return agents


def main() -> None:
    parser = argparse.ArgumentParser(description="少数決 ドライラン")
    parser.add_argument("--seed", type=int, default=42, help="乱数シード（デフォルト: 42）")
    parser.add_argument("--bots", action="store_true", help="StubAgentの代わりにbots/のロスターを使う")
    parser.add_argument("--output", type=str, default=None, help="JSONLログ出力先パス")
    args = parser.parse_args()

    config = GameConfig.default_12()
    agents = build_agents(config, args.bots)
    logger = EventLogger()

    print("=== 少数決 ドライラン ===")
    print(f"プレイヤー数: {config.num_players} / ラウンド数: {config.num_rounds}")
    print(f"借入範囲: {config.loan_min:,}〜{config.loan_max:,}円")
    print(f"エージェント: {'bots ロスター' if args.bots else 'StubAgent'}")
    print(f"シード: {args.seed}")
    print("---")

    game = Game(config=config, agents=agents, seed=args.seed, logger=logger)
    result = game.run()

    for summary in result.round_summaries:
        outcome = summary.minority_outcome
        n_yes, n_no = len(outcome.yes_ids), len(outcome.no_ids)
        if outcome.minority_side is None:
            line = (
                f"R{summary.round_num:>2}: YES {n_yes:>2}人 / NO {n_no:>2}人 "
                f"→ 少数派なし。持ち越し {outcome.carryover_after:,}円"
            )
            if outcome.destroyed_carryover:
                line += f"（うち{outcome.destroyed_carryover:,}円が消滅）"
        else:
            line = (
                f"R{summary.round_num:>2}: YES {n_yes:>2}人 / NO {n_no:>2}人 "
                f"→ 少数派={outcome.minority_side.value}（{len(outcome.minority_ids)}人、"
                f"1人あたり+{outcome.payout_per_minority:,}円）"
            )
        if summary.auto_commit_ids:
            line += f" [AUTO COMMIT: {', '.join(summary.auto_commit_ids)}]"
        if summary.public_ranks is not None:
            line += " / 全員順位を公開"
        print(line)

    print("\n=== 最終結果 ===")
    print(f"利息合計: {result.total_interest:,}円")
    print(f"R12で消滅した持ち越し: {result.total_destroyed_carryover:,}円")
    print(f"切り捨て没収: {result.total_forfeited_remainder:,}円")
    print(f"収支チェック: Σ最終資産 = {sum(result.final_assets.values()):,}円 "
          f"（期待値 = {-(result.total_interest + result.total_destroyed_carryover + result.total_forfeited_remainder):,}円）")

    print("\n順位 | ID | 最終資産 | 初期借入額")
    ordered = sorted(result.final_ranks.items(), key=lambda kv: (kv[1], kv[0]))
    for pid, rank in ordered:
        p = result.final_players[pid]
        print(f"{rank:>3}位 | {pid} | {result.final_assets[pid]:>12,}円 | {p.initial_loan:>10,}円")

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path("logs/llm") / f"dry_run_seed{args.seed}_12p_events.jsonl"
    logger.save_jsonl(output_path)
    print(f"\nJSONLログ出力: {output_path}")
    print(f"イベント数: {len(logger.events)}")


if __name__ == "__main__":
    main()
