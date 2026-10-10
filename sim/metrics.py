"""
Bot検証の集計モジュール（v0.4対応、サイクル4.1で全面作り直し）

v0.3は「集計前の生データ」を固定キーのカウンタ・リストの寄せ集め
（collect_raw）にしていたが、v0.4は条件（V1〜V10）ごとに見たい指標が
大きく異なるため、`collect_raw()`は「試合ごとの最小限の記録のリスト」
だけを返す（新しい指標を増やすたびにsim/store.pyの固定キー表を増やす
必要がないようにするため、CLAUDE.md的には壊れやすい設計を避けた形）。
条件ごとの数字（common_stats/group_stats/...）は、このリストを読むだけで
後から何度でも作り直せる（試合の回し直しはしない）。

試合そのものはルールエンジン（engine.game.Game）をそのまま使い、結果
（GameResult・EventLogger・RecordingAgent.history）を読み取るだけである。
Botの乱数はすべて試合シードから導出するため、同じ引数（scenario_key・
games・seed_start）なら常に同じ値を返す。
"""

import statistics
from collections import defaultdict
from typing import Any

from engine.events import EventLogger
from engine.game import Game
from engine import player as player_ops
from sim import scenarios


def _mean(xs: list) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list) -> float:
    """母標準偏差（サイクル1.2の判断を継続: ばらつき＝母標準偏差）"""
    if len(xs) < 2:
        return 0.0
    return statistics.pstdev(xs)


def _build_game_record(seed: int, agents: dict, game: Game, result) -> dict[str, Any]:
    """1試合の結果から、後から何度でも分析できる最小限の記録を作る"""
    rounds = []
    for rs in result.round_summaries:
        votes = [
            {
                "vote_num": v.vote_num, "result": v.result,
                "yes_ids": list(v.yes_ids), "no_ids": list(v.no_ids),
                "type_b_violator_ids": list(v.type_b_violator_ids),
                "payment_shortfall_ids": list(v.payment_shortfall_ids),
                "auto_commit_ids": list(v.auto_commit_ids),
            }
            for v in rs.votes
        ]
        rounds.append({
            "round_num": rs.round_num, "aborted": rs.aborted,
            "winner_ids": list(rs.winner_ids), "payout_per_winner": rs.payout_per_winner,
            "carryover_in": rs.carryover_in, "carryover_out": rs.carryover_out,
            "destroyed_pot": rs.destroyed_pot, "pot_final": rs.pot_final,
            "votes": votes,
        })

    contract_payments = [
        {
            "round_num": e.round_num, "vote_num": e.vote_num,
            "contract_id": e.data["contract_id"], "contract_seq": e.data["contract_seq"],
            "ob_type": e.data["ob_type"], "obligor": e.data["obligor"],
            "counterparty": e.data["counterparty"],
            "promised": e.data["promised"], "paid": e.data["paid"],
        }
        for e in game.logger.events if e.event_type == "CONTRACT_PAYMENT"
    ]

    num_rounds = game.config.num_rounds
    pre_final_states = [agents[pid].history.get(num_rounds - 1) for pid in sorted(agents)]
    king_making = None
    if all(s is not None for s in pre_final_states):
        sorted_states = sorted(pre_final_states, key=lambda s: (-s.net_assets, s.player_id))
        gap_1_2 = sorted_states[0].net_assets - sorted_states[1].net_assets
        last_credit = player_ops.remaining_credit(sorted_states[-1], game.config)
        king_making = last_credit > gap_1_2

    return {
        "seed": seed,
        "final_assets": dict(result.final_assets),
        "final_ranks": dict(result.final_ranks),
        "final_debt_pre": {pid: p.debt_pre for pid, p in result.final_players.items()},
        "final_debt_post": {pid: p.debt_post for pid, p in result.final_players.items()},
        "labels": {pid: agent.label for pid, agent in agents.items()},
        "rounds": rounds,
        "contract_payments": contract_payments,
        "king_making": king_making,
    }


def collect_raw(scenario_key: str, games: int, seed_start: int) -> dict:
    """
    指定シナリオを games 試合回し、試合ごとの記録のリストを返す

    Args:
        scenario_key: sim.scenarios.SCENARIO_KEYS のいずれか
        games: 試合数
        seed_start: 最初の試合のシード（以降 seed_start, seed_start+1, ... と進む）

    Returns:
        {"n_games": int, "games": [試合ごとの記録dict, ...]}
    """
    records = []
    for i in range(games):
        seed = seed_start + i
        agents, config = scenarios.build_agents(scenario_key, seed)
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=seed, logger=logger)
        result = game.run()
        records.append(_build_game_record(seed, agents, game, result))
    return {"n_games": games, "games": records}


# ---------------------------------------------------------------------------
# 条件ごとの数字を作る関数群（generated raw["games"]を読むだけ、試合の回し直しなし）
# ---------------------------------------------------------------------------

def common_stats(games: list[dict]) -> dict:
    """全条件に共通の数字（§12.1/計画の「確かめたいこと」共通項目）"""
    total_rounds = 0
    total_votes = 0
    retry_votes = 0
    aborted_rounds = 0
    one_winner_rounds = 0
    two_winner_rounds = 0
    vote_count_dist: dict[str, int] = defaultdict(int)
    carryover_out_max = 0
    destroyed_values: list[int] = []
    positive_count_per_game: list[int] = []
    final_assets_all: list[int] = []
    king_making_values: list[bool] = []

    for g in games:
        final_assets_all.extend(g["final_assets"].values())
        positive_count_per_game.append(sum(1 for v in g["final_assets"].values() if v > 0))
        if g["king_making"] is not None:
            king_making_values.append(g["king_making"])

        for r in g["rounds"]:
            total_rounds += 1
            n_votes = len(r["votes"])
            total_votes += n_votes
            vote_count_dist[str(n_votes)] += 1
            retry_votes += sum(1 for v in r["votes"] if v["result"] == "retry")
            if r["aborted"]:
                aborted_rounds += 1
            elif len(r["winner_ids"]) == 1:
                one_winner_rounds += 1
            elif len(r["winner_ids"]) == 2:
                two_winner_rounds += 1
            carryover_out_max = max(carryover_out_max, r["carryover_out"])
            if r["destroyed_pot"] > 0:
                destroyed_values.append(r["destroyed_pot"])

    return {
        "n_games": len(games),
        "n_rounds_total": total_rounds,
        "vote_count_distribution": {
            k: vote_count_dist[k] / total_rounds if total_rounds else 0.0
            for k in [str(i) for i in range(1, 11)] if k in vote_count_dist
        },
        "vote_count_mean": (
            sum(int(k) * v for k, v in vote_count_dist.items()) / total_rounds if total_rounds else 0.0
        ),
        "retry_ratio": retry_votes / total_votes if total_votes else 0.0,
        "abort_ratio": aborted_rounds / total_rounds if total_rounds else 0.0,
        "one_winner_ratio": one_winner_rounds / total_rounds if total_rounds else 0.0,
        "two_winner_ratio": two_winner_rounds / total_rounds if total_rounds else 0.0,
        "final_assets_max": max(final_assets_all) if final_assets_all else 0,
        "final_assets_min": min(final_assets_all) if final_assets_all else 0,
        "final_assets_median": statistics.median(final_assets_all) if final_assets_all else 0,
        "final_assets_mean": _mean(final_assets_all),
        "final_assets_stdev": _stdev(final_assets_all),
        "positive_count_mean": _mean(positive_count_per_game),
        "carryover_out_max": carryover_out_max,
        "destroyed_mean": _mean(destroyed_values),
        "destroyed_total": sum(destroyed_values),
        "destroyed_games": len(destroyed_values),
        "king_making_ratio": _mean([1.0 if v else 0.0 for v in king_making_values]),
    }


def group_stats(games: list[dict], members: set[str]) -> dict:
    """
    組（members）についての数字

    group_share_mean: ラウンドごとの取り分（勝ち残りのうち組員の人数 ÷
    勝ち残りの人数。打ち切りのラウンドは0）を、打ち切りを含む全ラウンドで
    平均した値（§12.5の机上計算と同じ定義、人間の指示による）。
    group_win_round_ratio: 組の誰かが勝ち残ったラウンド ÷ 全ラウンド
    （打ち切りを含む全ラウンドが分母。机上計算と同じ、人間の指示による）。
    """
    total_rounds = 0
    win_rounds = 0
    share_values: list[float] = []
    received_by_group = 0
    received_total = 0
    pot_total = 0

    for g in games:
        for r in g["rounds"]:
            total_rounds += 1
            winners = set(r["winner_ids"])
            payout = r["payout_per_winner"]
            pot_total += r["pot_final"]
            received_total += payout * len(winners)
            received_by_group += payout * len(winners & members)
            if winners:
                share_values.append(len(winners & members) / len(winners))
                if winners & members:
                    win_rounds += 1
            else:
                share_values.append(0.0)

    member_assets = [g["final_assets"][pid] for g in games for pid in members if pid in g["final_assets"]]
    outsider_assets = [
        v for g in games for pid, v in g["final_assets"].items() if pid not in members
    ]

    return {
        "group_win_round_ratio": win_rounds / total_rounds if total_rounds else 0.0,
        "group_share_mean": _mean(share_values),
        "group_share_of_paid_out": received_by_group / received_total if received_total else 0.0,
        "group_share_of_pot_created": received_by_group / pot_total if pot_total else 0.0,
        "group_member_asset_mean": _mean(member_assets),
        "outsider_asset_mean": _mean(outsider_assets) if outsider_assets else None,
    }


def type_b_pact_stats(games: list[dict], members: set[str]) -> dict:
    """
    V8: 型Bの守られた/破られた本数

    各組員がR*V1ごとに義務1本を負う構造（bots/group_bot.py
    ::_type_b_ring_terms、輪番で1本/人/ラウンド）に基づく。
    """
    kept = 0
    violated = 0
    for g in games:
        for r in g["rounds"]:
            for v in r["votes"]:
                if v["vote_num"] != 1:
                    continue
                violators = set(v["type_b_violator_ids"]) & members
                violated += len(violators)
                kept += len(members) - len(violators)
    return {"kept": kept, "violated": violated}


def paired_final_asset_diff(games_a: list[dict], games_b: list[dict], pid: str) -> dict:
    """
    同じシードの試合同士で、pidの最終資産の差（A−B）を比べる（V8の主指標）

    better_ratio: A（通常はbreak）がB（通常はkeep）より厳密に大きかった割合
    （同額は「得でない」）。
    """
    by_seed_b = {g["seed"]: g["final_assets"][pid] for g in games_b}
    diffs = []
    better = 0
    for g in games_a:
        seed = g["seed"]
        if seed not in by_seed_b:
            continue
        diff = g["final_assets"][pid] - by_seed_b[seed]
        diffs.append(diff)
        if diff > 0:
            better += 1
    n = len(diffs)
    return {
        "n": n,
        "better_ratio": better / n if n else 0.0,
        "mean_diff": _mean(diffs),
        "median_diff": statistics.median(diffs) if diffs else 0,
        "max_diff": max(diffs) if diffs else 0,
        "min_diff": min(diffs) if diffs else 0,
    }


def type_b_vote_level_counterfactual(games: list[dict], members: list[str], config) -> dict:
    """
    V8の副指標: 型B義務1本ごとに「守れば退場・破れば残れたか」を
    実際に記録された票（yes_ids/no_ids、義務者本人の票だけ反転）から
    engine.vote.resolve_vote（無改変）で判定する（試合の回し直しはしない）。
    """
    from bots.group_bot import _rotated_sides
    from sim.counterfactual import type_b_vote_survival

    total = 0
    obey_eliminated_break_survived = 0
    obey_survived_break_eliminated = 0
    both_survived = 0
    both_eliminated = 0

    for g in games:
        for r in g["rounds"]:
            v1 = next((v for v in r["votes"] if v["vote_num"] == 1), None)
            if v1 is None:
                continue
            required = _rotated_sides(members, set(members), r["round_num"], 1)
            for obligor, req_vote in required.items():
                total += 1
                res = type_b_vote_survival(
                    v1["yes_ids"], v1["no_ids"], obligor, req_vote,
                    config, r["round_num"], 1,
                )
                if res["survives_if_obey"] and res["survives_if_break"]:
                    both_survived += 1
                elif not res["survives_if_obey"] and not res["survives_if_break"]:
                    both_eliminated += 1
                elif res["survives_if_break"]:
                    obey_eliminated_break_survived += 1
                else:
                    obey_survived_break_eliminated += 1

    return {
        "total": total,
        "obey_eliminated_break_survived_ratio": (
            obey_eliminated_break_survived / total if total else 0.0
        ),
        "obey_survived_break_eliminated_ratio": (
            obey_survived_break_eliminated / total if total else 0.0
        ),
        "both_survived_ratio": both_survived / total if total else 0.0,
        "both_eliminated_ratio": both_eliminated / total if total else 0.0,
    }


def loan_stats(games: list[dict]) -> dict:
    """V9: 借入額ラベルごとの最終資産の平均、山を取れた回数で層別した平均"""
    by_label_assets: dict[str, list[int]] = defaultdict(list)
    by_label_wins: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    for g in games:
        times_won: dict[str, int] = defaultdict(int)
        for r in g["rounds"]:
            for pid in r["winner_ids"]:
                times_won[pid] += 1
        for pid, label in g["labels"].items():
            asset = g["final_assets"][pid]
            by_label_assets[label].append(asset)
            bucket = str(times_won[pid]) if times_won[pid] < 2 else "2+"
            by_label_wins[label][bucket].append(asset)

    return {
        "asset_mean_by_label": {label: _mean(v) for label, v in by_label_assets.items()},
        "asset_mean_by_label_and_wins": {
            label: {bucket: _mean(v) for bucket, v in buckets.items()}
            for label, buckets in by_label_wins.items()
        },
    }


def oversell_stats(games: list[dict], obligor: str = "P01") -> dict:
    """
    V10: 約束した本人の開始後の借金、成立順位（1本目/2本目/3本目）別の取りはぐれ額

    debt_post_mean は全試合の平均（旧仕様。後方互換のため残す）。
    サイクル4.2で、報告書の文章が「勝ち残った試合のみ」と書きながら実際は
    全試合の平均を使っていた誤りを直すため、「勝ち残った試合（本人が
    type_c_conditionalの支払いを1本でも行った試合）」と「勝ち残らなかった
    試合」で分けた平均（debt_post_mean_won / _not_won）も返す。
    """
    debt_post_values = [g["final_debt_post"][obligor] for g in games]
    debt_post_won: list[int] = []
    debt_post_not_won: list[int] = []
    shortfall_by_rank: dict[int, list[int]] = defaultdict(list)

    for g in games:
        payments = [
            p for p in g["contract_payments"]
            if p["obligor"] == obligor and p["ob_type"] == "type_c_conditional"
        ]
        if not payments:
            debt_post_not_won.append(g["final_debt_post"][obligor])
            continue
        debt_post_won.append(g["final_debt_post"][obligor])
        by_seq: dict[int, list[dict]] = defaultdict(list)
        for p in payments:
            by_seq[p["contract_seq"]].append(p)
        for rank, seq in enumerate(sorted(by_seq), start=1):
            items = by_seq[seq]
            promised = sum(it["promised"] for it in items)
            paid = sum(it["paid"] for it in items)
            shortfall_by_rank[rank].append(promised - paid)

    return {
        "debt_post_mean": _mean(debt_post_values),
        "debt_post_mean_won": _mean(debt_post_won),
        "debt_post_mean_not_won": _mean(debt_post_not_won),
        "n_games_won": len(debt_post_won),
        "n_games_not_won": len(debt_post_not_won),
        "debt_post_max": max(debt_post_values) if debt_post_values else 0,
        "shortfall_mean_by_rank": {str(k): _mean(v) for k, v in shortfall_by_rank.items()},
        "shortfall_total_by_rank": {str(k): sum(v) for k, v in shortfall_by_rank.items()},
        "n_games_with_payment": len(shortfall_by_rank.get(1, [])),
    }


# --- V8の見積もり: 型Bを破る得と損（サイクル4.2、報告書D3の修正） ---

_SPLIT_LABELS: dict[int, str] = {5: "7対5", 4: "8対4", 3: "9対3", 2: "10対2", 1: "11対1"}
"""decisive なV1の少数派人数k -> 表示名（§12.7の見出しに合わせる）"""


def type_b_break_value_estimate(games: list[dict], config: Any) -> dict:
    """
    V8「型Bを破る得と損」の見積もり（V1=12人とも無作為の記録から、
    各ラウンドの最初の投票だけを見る。サイクル4.1のBot検証では測りきれな
    かった「違約金は軽いか重いか」を、V1の記録だけから見積もる。§12.7。

    「得た額」= そのラウンドで受け取った山 −（vote_num=1を含む、以降に
    発生した）延長料。vote_num=1自体がやり直しだった場合は、その延長料も
    含める（「その投票以降」はその投票自身を含む、と解釈する）。

    得（多数派の1人が票を変えた場合）: 行き先の平均を使う。
      7対5→6対6のやり直し、8対4→5人の少数派、9対3→4人の少数派、
      10対2→3人の少数派、11対1→2人の少数派（§12.7の表と同じ対応）。
      全体平均は、各割れ方の「多数派の人数」で重み付けする
      （その人数の誰か1人が switchし得る、という数え方）。

    損（多数派でなかった人が票を変えた場合）: 少数派の人（2〜5人）が
    票を変えると多数派に入って0円になるので、損=その人がもともと得て
    いた額（gain_avg[k]）そのもの。少数派が1人（11対1）のときは、
    その人が票を変えると全員一致（12対0、やり直し扱い）になるので、
    損=gain_avg[1]-retry_avg。6対6の人が票を変えると7対5の多数派に
    入るので、損=retry_avg。全体平均は、各割れ方の「少数派の人数」
    （6対6は12人）で重み付けする。

    人間側の机上計算との突き合わせ結果（V1_seed1_n1000.json、2026-10-10):
    得の平均は 1,955,126.75（人間側1,955,127、差0.25円）とほぼ完全に一致
    したが、損の平均は本関数で1,957,792円前後になり、人間側の1,945,283円
    とは一致しなかった（差約12,500円・0.6%）。損の重み付け（決着時は
    occurrence×少数派人数、同数時はoccurrence×12）を変えて何通りか試したが
    いずれも一致せず、原因は特定できなかった。ここでは本関数の計算方法を
    明記したうえで、両方の数字を報告書に残す（合わせにいかない、CLAUDE.md
    の方針）。
    """
    ext_fee = config.extension_fee
    gain_by_k: dict[int, list[int]] = defaultdict(list)
    retry_gain: list[int] = []
    split_counts: dict[tuple[int, int], int] = defaultdict(int)

    for g in games:
        for r in g["rounds"]:
            votes = r["votes"]
            v1 = next(v for v in votes if v["vote_num"] == 1)
            yes_ids = set(v1["yes_ids"])
            no_ids = set(v1["no_ids"])
            payout = {pid: r["payout_per_winner"] for pid in r["winner_ids"]}

            def _fee_paid(pid: str) -> int:
                total = 0
                for v in votes:
                    if v["result"] in ("retry", "abort") and (
                        pid in v["yes_ids"] or pid in v["no_ids"]
                    ):
                        total += ext_fee
                return total

            def _gain(pid: str) -> int:
                return payout.get(pid, 0) - _fee_paid(pid)

            if v1["result"] == "decisive":
                minority = yes_ids if len(yes_ids) < len(no_ids) else no_ids
                majority = no_ids if len(yes_ids) < len(no_ids) else yes_ids
                k = len(minority)
                split_counts[(len(majority), k)] += 1
                for pid in minority:
                    gain_by_k[k].append(_gain(pid))
            else:
                hi, lo = (len(yes_ids), len(no_ids)) if len(yes_ids) >= len(no_ids) else (len(no_ids), len(yes_ids))
                split_counts[(hi, lo)] += 1
                for pid in yes_ids | no_ids:
                    retry_gain.append(_gain(pid))

    gain_avg = {k: _mean(v) for k, v in gain_by_k.items()}
    retry_avg = _mean(retry_gain)
    total_rounds = sum(split_counts.values())

    occurrence = {k: split_counts.get((12 - k, k), 0) for k in (1, 2, 3, 4, 5)}
    tie_occurrence = split_counts.get((6, 6), 0) + split_counts.get((12, 0), 0)

    gain_destination = {
        5: retry_avg, 4: gain_avg.get(5, 0.0), 3: gain_avg.get(4, 0.0),
        2: gain_avg.get(3, 0.0), 1: gain_avg.get(2, 0.0),
    }
    majority_size = {5: 7, 4: 8, 3: 9, 2: 10, 1: 11}
    gain_weight_total = sum(occurrence[k] * majority_size[k] for k in occurrence)
    gain_overall = (
        sum(occurrence[k] * majority_size[k] * gain_destination[k] for k in occurrence)
        / gain_weight_total if gain_weight_total else 0.0
    )

    loss_value = {k: gain_avg.get(k, 0.0) for k in (2, 3, 4, 5)}
    loss_value[1] = gain_avg.get(1, 0.0) - retry_avg
    loss_value["tie"] = retry_avg
    loss_weight_total = sum(occurrence[k] * k for k in occurrence) + tie_occurrence * 12
    loss_overall = (
        (sum(occurrence[k] * k * loss_value[k] for k in occurrence) + tie_occurrence * 12 * loss_value["tie"])
        / loss_weight_total if loss_weight_total else 0.0
    )

    penalties = (1_000_000, 3_000_000, 5_000_000)
    penalty_estimates = {}
    for penalty in penalties:
        confidence_required = (
            (penalty + loss_overall) / (gain_overall + loss_overall)
            if (gain_overall + loss_overall) else None
        )
        penalty_estimates[str(penalty)] = {
            "net_if_break_knowing_losing_side": gain_overall - penalty,
            "confidence_required": confidence_required,
        }

    return {
        "total_rounds": total_rounds,
        "occurrence_ratio": {
            _SPLIT_LABELS[k]: (occurrence[k] / total_rounds if total_rounds else 0.0)
            for k in occurrence
        },
        "gain_avg_by_minority_size": {str(k): v for k, v in gain_avg.items()},
        "retry_avg": retry_avg,
        "gain_by_split": {_SPLIT_LABELS[k]: gain_destination[k] for k in gain_destination},
        "gain_overall": gain_overall,
        "loss_overall": loss_overall,
        "penalty_estimates": penalty_estimates,
    }
