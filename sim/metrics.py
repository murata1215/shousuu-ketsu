"""
Bot検証の集計モジュール（サイクル1.2、新規実装）

1シナリオをgames試合回し、仕様書§12.1の数字を集計する。試合そのものは
ルールエンジン（engine.game.Game）をそのまま使い、結果（GameResult・
EventLogger・Game.contracts・RecordingAgent.history）を読み取るだけで、
試合の回し直しは行わない（「破っていたら得だったか」も実際に記録された
票からsim.counterfactualの純粋計算関数で求める。再シミュレーションしない）。

collect_raw() は生データ（リスト・カウンタ）を返し、summarize() がそこから
最終的な数字（割合・平均・分布）を作る。同じ引数（scenario_key・games・
seed_start・config）なら常に同じ値を返す（Botの乱数はすべて試合シードから
導出するため、§12.1「乱数はシードから」を満たす）。
"""

import statistics
from collections import defaultdict

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import ObligationType
from engine import contracts as contract_ops
from engine import player as player_ops
from sim import scenarios
from sim.counterfactual import type_b_obligation_gain

PENALTY_AMOUNTS: list[int] = [300_000, 500_000, 1_000_000, 2_000_000]
"""違約金ごとの「破った方が得だった割合」を出す対象額（計画§3）"""


def _mean(xs: list) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list) -> float:
    """母標準偏差（計画§6判断8: ばらつき＝母標準偏差）"""
    if len(xs) < 2:
        return 0.0
    return statistics.pstdev(xs)


def _run_one_game(scenario_key: str, seed: int, config: GameConfig):
    agents = scenarios.build_agents(scenario_key, seed, config)
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=seed, logger=logger)
    result = game.run()
    return agents, game, result, logger


def collect_raw(scenario_key: str, games: int, seed_start: int, config: GameConfig) -> dict:
    """
    指定シナリオを games 試合回し、集計前の生データを返す

    Args:
        scenario_key: sim.scenarios.SCENARIO_KEYS のいずれか
        games: 試合数
        seed_start: 最初の試合のシード（以降 seed_start, seed_start+1, ... と進む）
        config: ゲーム設定

    Returns:
        生データの辞書（summarize()に渡す）
    """
    raw: dict = {
        "n_games": 0,
        "minority_count": defaultdict(int),  # キー: 1..5(int) または "none"
        "n_rounds_total": 0,
        "carryovers": [],
        "destroyed_per_game": [],
        "final_assets_all": [],
        "positive_count_per_game": [],
        "gap_1_2_per_game": [],
        "gap_1_last_per_game": [],
        "assets_by_label": defaultdict(list),
        "rank_by_label": defaultdict(list),
        "rank1_by_label": defaultdict(int),
        "interest_pre_by_label": defaultdict(list),
        "interest_post_by_label": defaultdict(list),
        "type_b_kept": 0,
        "type_b_violated": 0,
        "gains": [],
        "debt_cap_hits_per_game": [],
        "shortfall_total": 0,
        "recovery_by_position": defaultdict(list),  # 1, 2, 3(=3本目以降)
        "king_making_hits": 0,
        "king_making_total": 0,
    }

    for i in range(games):
        seed = seed_start + i
        agents, game, result, logger = _run_one_game(scenario_key, seed, config)
        raw["n_games"] += 1

        # --- 全体: 少数派人数の分布・持ち越し ---
        for rs in result.round_summaries:
            outcome = rs.minority_outcome
            key = "none" if outcome.minority_side is None else len(outcome.minority_ids)
            raw["minority_count"][key] += 1
            raw["n_rounds_total"] += 1
            raw["carryovers"].append(outcome.carryover_after)

        raw["destroyed_per_game"].append(result.total_destroyed_carryover)

        # --- 全体: 最終資産の分布・プラス人数・1-2位差・1-最下位差 ---
        assets_values = list(result.final_assets.values())
        raw["final_assets_all"].extend(assets_values)
        raw["positive_count_per_game"].append(sum(1 for v in assets_values if v > 0))
        sorted_desc = sorted(assets_values, reverse=True)
        raw["gap_1_2_per_game"].append(sorted_desc[0] - sorted_desc[1])
        raw["gap_1_last_per_game"].append(sorted_desc[0] - sorted_desc[-1])

        # --- 種類別（ラベルはRecordingAgent.label） ---
        for pid, agent in agents.items():
            label = agent.label
            raw["assets_by_label"][label].append(result.final_assets[pid])
            raw["rank_by_label"][label].append(result.final_ranks[pid])
            if result.final_ranks[pid] == 1:
                raw["rank1_by_label"][label] += 1

        # --- S5用: 種類別の利息合計（1.5%分/3%分を分けて。ユーザー追加指示） ---
        interest_pre_by_pid: dict[str, int] = defaultdict(int)
        interest_post_by_pid: dict[str, int] = defaultdict(int)
        for e in logger.events:
            if e.event_type == "INTEREST":
                interest_pre_by_pid[e.data["player_id"]] += e.data["interest_pre"]
                interest_post_by_pid[e.data["player_id"]] += e.data["interest_post"]
        for pid, agent in agents.items():
            raw["interest_pre_by_label"][agent.label].append(interest_pre_by_pid[pid])
            raw["interest_post_by_label"][agent.label].append(interest_post_by_pid[pid])

        # --- 契約: 型Bの守られた/破られた本数、gain分布（試合の回し直しなし） ---
        for rs in result.round_summaries:
            round_num = rs.round_num
            due = contract_ops.obligations_due(game.contracts, round_num, ObligationType.TYPE_B_VOTE)
            if not due:
                continue
            violated = contract_ops.audit_type_b(game.contracts, rs.votes, round_num)
            violated_ids = {ob.obligation_id for ob in violated}
            raw["type_b_violated"] += len(violated_ids)
            raw["type_b_kept"] += len(due) - len(violated_ids)
            is_final_round = round_num == config.num_rounds
            for ob in due:
                gain = type_b_obligation_gain(ob, rs, config, is_final_round=is_final_round)
                raw["gains"].append(gain)

        # --- 契約: 借金上限に達した人数（各ラウンド終了時点の自分の状態を参照） ---
        hit_pids: set[str] = set()
        for pid, agent in agents.items():
            for state in agent.history.values():
                if state.total_debt >= config.debt_cap:
                    hit_pids.add(pid)
                    break
        raw["debt_cap_hits_per_game"].append(len(hit_pids))

        # --- 契約: 払いきれなかった額の合計・成立順の回収率 ---
        payments = [e for e in logger.events if e.event_type == "CONTRACT_PAYMENT"]
        for e in payments:
            gap = e.data["promised"] - e.data["paid"]
            if gap > 0:
                raw["shortfall_total"] += gap

        by_round_obligor: dict[tuple[int, str], list[dict]] = defaultdict(list)
        for e in payments:
            by_round_obligor[(e.round_num, e.data["obligor"])].append(e.data)
        for items in by_round_obligor.values():
            if not any(it["paid"] < it["promised"] for it in items):
                continue
            for idx, it in enumerate(items):
                position = idx + 1
                bucket = position if position <= 2 else 3
                raw["recovery_by_position"][bucket].append(it["paid"] / it["promised"])

        # --- 王様作りの余地（R12開始時点＝R11終了時点、RecordingAgent.history[11]） ---
        states_r11 = [agents[pid].history.get(11) for pid in scenarios.PLAYER_IDS]
        if all(s is not None for s in states_r11):
            sorted_states = sorted(states_r11, key=lambda s: (-s.net_assets, s.player_id))
            gap_1_2 = sorted_states[0].net_assets - sorted_states[1].net_assets
            last_credit = player_ops.remaining_credit(sorted_states[-1], config)
            raw["king_making_total"] += 1
            if last_credit > gap_1_2:
                raw["king_making_hits"] += 1

    return raw


def summarize(raw: dict) -> dict:
    """集計前の生データ（collect_raw()の戻り値）から、最終的な数字を作る"""
    n_games = raw["n_games"]
    n_rounds = raw["n_rounds_total"]

    minority_dist = {
        str(k): raw["minority_count"].get(k, 0) / n_rounds if n_rounds else 0.0
        for k in [1, 2, 3, 4, 5, "none"]
    }

    gains = raw["gains"]

    summary: dict = {
        "n_games": n_games,
        "n_rounds_total": n_rounds,
        "minority_count_dist": minority_dist,
        "no_minority_ratio": minority_dist["none"],
        "carryover_max": max(raw["carryovers"]) if raw["carryovers"] else 0,
        "carryover_mean": _mean(raw["carryovers"]),
        "destroyed_mean_per_game": _mean(raw["destroyed_per_game"]),
        "destroyed_total": sum(raw["destroyed_per_game"]),
        "final_assets_max": max(raw["final_assets_all"]) if raw["final_assets_all"] else 0,
        "final_assets_min": min(raw["final_assets_all"]) if raw["final_assets_all"] else 0,
        "final_assets_median": (
            statistics.median(raw["final_assets_all"]) if raw["final_assets_all"] else 0
        ),
        "final_assets_stdev": _stdev(raw["final_assets_all"]),
        "positive_count_mean": _mean(raw["positive_count_per_game"]),
        "gap_1_2_mean": _mean(raw["gap_1_2_per_game"]),
        "gap_1_last_mean": _mean(raw["gap_1_last_per_game"]),
        "by_label": {},
        "type_b_kept": raw["type_b_kept"],
        "type_b_violated": raw["type_b_violated"],
        "gain_mean": _mean(gains),
        "gain_median": statistics.median(gains) if gains else 0,
        "gain_max": max(gains) if gains else 0,
        "gain_positive_ratio": (sum(1 for g in gains if g > 0) / len(gains)) if gains else 0.0,
        "break_even_ratio_by_penalty": {},
        "debt_cap_hits_mean": _mean(raw["debt_cap_hits_per_game"]),
        "shortfall_total": raw["shortfall_total"],
        "shortfall_mean_per_game": raw["shortfall_total"] / n_games if n_games else 0.0,
        "recovery_by_position": {},
        "king_making_ratio": (
            raw["king_making_hits"] / raw["king_making_total"] if raw["king_making_total"] else 0.0
        ),
    }

    for label, values in raw["assets_by_label"].items():
        n = len(values)
        summary["by_label"][label] = {
            "n": n,
            "asset_mean": _mean(values),
            "rank_mean": _mean(raw["rank_by_label"][label]),
            "rank1_ratio": raw["rank1_by_label"][label] / n if n else 0.0,
            "interest_pre_mean": _mean(raw["interest_pre_by_label"].get(label, [])),
            "interest_post_mean": _mean(raw["interest_post_by_label"].get(label, [])),
        }

    for penalty in PENALTY_AMOUNTS:
        ratio = sum(1 for g in gains if g > penalty) / len(gains) if gains else 0.0
        summary["break_even_ratio_by_penalty"][penalty] = ratio

    for bucket in (1, 2, 3):
        values = raw["recovery_by_position"].get(bucket, [])
        summary["recovery_by_position"][bucket] = _mean(values)

    return summary
