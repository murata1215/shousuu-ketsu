"""
ドライランスクリプト（動作確認用、§12の目視確認に対応、v0.4）

12人×4ラウンド（L12R4V6）の試合を1本実行し、ラウンドごとの投票回数・
やり直し・打ち切り・勝ち残りと山の額、最終資産・順位を表示する。

サイクル4.0でv0.3から全面改訂した。v0.3は`tests.helpers.RandomContractAgent`
を本番コードから逆方向にimportしていたが、v0.4では`bots.random_contract_bot
.RandomContractBot`に移したためその依存を解消した。

使用方法:
    uv run python scripts/dry_run.py --seed 42
    uv run python scripts/dry_run.py --seed 42 --bots
    uv run python scripts/dry_run.py --seed 42 --contracts
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent


def build_agents(config: GameConfig, use_bots: bool, use_contracts: bool) -> dict:
    if use_contracts:
        from bots.random_contract_bot import RandomContractBot
        return {
            f"P{i:02d}": RandomContractBot(
                f"P{i:02d}", seed=i, num_rounds=config.num_rounds,
                max_votes_per_round=config.max_votes_per_round,
            )
            for i in range(1, config.num_players + 1)
        }

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


def _format_contract_payment(data: dict) -> str:
    promised, paid = data["promised"], data["paid"]
    if paid == promised:
        kind = "全額"
    elif paid == 0:
        kind = "0円"
    else:
        kind = "部分"
    return (
        f"      {data['contract_id']}(seq{data['contract_seq']}) "
        f"{data['obligor']}→{data['counterparty']} [{data['ob_type']}] "
        f"約束{promised:,}円 → 支払{paid:,}円（{kind}）"
    )


def _format_vote(v) -> str:
    n_yes, n_no = len(v.yes_ids), len(v.no_ids)
    if v.result == "decisive":
        return (
            f"    V{v.vote_num}: YES {n_yes:>2}人 / NO {n_no:>2}人 "
            f"→ 決着。{v.minority_side.value if v.minority_side else '?'}側"
            f"{len(v.remaining_ids)}人が残留、{len(v.eliminated_ids)}人退場"
        )
    kind = "打ち切り" if v.result == "abort" else "やり直し"
    return (
        f"    V{v.vote_num}: YES {n_yes:>2}人 / NO {n_no:>2}人 "
        f"→ {kind}（連続{v.consecutive_ties_after}回、延長料+{v.extension_fee_collected:,}円）"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="少数決 ドライラン（v0.4 L12R4V6）")
    parser.add_argument("--seed", type=int, default=42, help="乱数シード（デフォルト: 42）")
    parser.add_argument("--bots", action="store_true", help="StubAgentの代わりにbots/のロスターを使う")
    parser.add_argument(
        "--contracts", action="store_true",
        help="無作為に契約を提案・署名するRandomContractBotを使う（--botsと併用不可）",
    )
    parser.add_argument("--output", type=str, default=None, help="JSONLログ出力先パス")
    args = parser.parse_args()

    config = GameConfig.default_12()
    agents = build_agents(config, args.bots, args.contracts)
    logger = EventLogger()

    if args.contracts:
        agent_label = "RandomContractBot"
    elif args.bots:
        agent_label = "bots ロスター"
    else:
        agent_label = "StubAgent"

    print("=== 少数決 ドライラン（v0.4 L12R4V6） ===")
    print(f"プレイヤー数: {config.num_players} / ラウンド数: {config.num_rounds} "
          f"/ 1ラウンド最大投票数: {config.max_votes_per_round}")
    print(f"参加費: {config.entry_fee:,}円 / 延長料: {config.extension_fee:,}円")
    print(f"借入範囲: {config.loan_min:,}〜{config.loan_max:,}円")
    print(f"エージェント: {agent_label}")
    print(f"シード: {args.seed}")
    print("---")

    game = Game(config=config, agents=agents, seed=args.seed, logger=logger)
    result = game.run()

    payments_by_round: dict[int, list[dict]] = {}
    for e in logger.events:
        if e.event_type == "CONTRACT_PAYMENT":
            payments_by_round.setdefault(e.round_num, []).append(e.data)

    for summary in result.round_summaries:
        print(f"R{summary.round_num}: 山の持ち越し {summary.carryover_in:,}円 → 最終 {summary.pot_final:,}円"
              f"（投票{len(summary.votes)}回）")
        for v in summary.votes:
            print(_format_vote(v))
        if summary.aborted:
            line = f"  → 打ち切り。勝ち残りなし。持ち越し {summary.carryover_out:,}円"
            if summary.destroyed_pot:
                line += f"（{summary.destroyed_pot:,}円が没収）"
        else:
            winners = "・".join(summary.winner_ids)
            line = f"  → 勝ち残り {winners}（1人あたり+{summary.payout_per_winner:,}円）"
            if summary.forfeited_remainder:
                line += f"（端数{summary.forfeited_remainder:,}円没収）"
        if summary.auto_commit_ids:
            line += f" [AUTO COMMIT: {', '.join(summary.auto_commit_ids)}]"
        if summary.public_ranks is not None:
            line += " / 全員順位を公開"
        if summary.established_contract_seqs:
            line += f" [成立: {len(summary.established_contract_seqs)}本]"
        if summary.type_b_violator_ids:
            line += f" [型B違反: {', '.join(summary.type_b_violator_ids)}]"
        if summary.payment_shortfall_ids:
            line += f" [払いきれず: {', '.join(summary.payment_shortfall_ids)}]"
        line += f" / 利息合計{summary.interest_total:,}円"
        print(line)
        for data in payments_by_round.get(summary.round_num, []):
            print(_format_contract_payment(data))

    print("\n=== 最終結果 ===")
    print(f"利息合計: {result.total_interest:,}円")
    print(f"R{config.num_rounds}で没収された山: {result.total_destroyed_pot:,}円")
    print(f"山の端数没収: {result.total_forfeited_remainder:,}円")
    print(f"収支チェック: Σ最終資産 = {sum(result.final_assets.values()):,}円 "
          f"（期待値 = {-(result.total_interest + result.total_destroyed_pot + result.total_forfeited_remainder):,}円）")

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
