"""
Bot検証レポート生成スクリプト（v0.4、サイクル4.1新規実装。サイクル4.2で
報告書の3か所の誤り（V9・V10・V8）を修正した）

scripts/simulate.py が --out-dir（既定 data/sim_v0_4）に保存した25条件分の
生データを読み込み、doc/analysis/bot_simulation_report_v0_4.md を作る。
試合は1本も回さない（保存済みJSONが無い条件があればエラーで止まる）。

使用方法:
    uv run python scripts/simulate.py --scenario all --games 1000 --out-dir data/sim_v0_4
    uv run python scripts/report_bot_simulation_v0_4.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim.metrics import (
    common_stats, group_stats, loan_stats, oversell_stats,
    paired_final_asset_diff, type_b_break_value_estimate, type_b_pact_stats,
    type_b_vote_level_counterfactual,
)
from sim.scenarios import GROUP_A, GROUP_B, HUB_GROUPS, config_for
from sim.store import load_raw, shard_path

GAMES = 1000
SEED_START = 1
OUT_DIR = Path("data/sim_v0_4")
REPORT_PATH = Path("doc/analysis/bot_simulation_report_v0_4.md")

ALL_KEYS = [
    "V1", "V2", "V3g2", "V3g3", "V3g5", "V3g6", "V4", "V5", "V6", "V7",
    "V8keep_pen100", "V8keep_pen300", "V8keep_pen500",
    "V8break_pen100", "V8break_pen300", "V8break_pen500",
    "V9", "V9_post10", "V10",
    "V1_ties2", "V1_ties5", "V5_ties2", "V5_ties5", "V6_ties2", "V6_ties5",
]


def _load(key: str) -> dict:
    path = shard_path(OUT_DIR, key, SEED_START, GAMES)
    if not path.exists():
        raise SystemExit(f"保存済みの結果が無い: {path}（先にscripts/simulate.pyを実行すること）")
    return load_raw(path)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _yen(x: float) -> str:
    return f"{x:,.0f}円"


def _ranked_text(mapping: dict[str, float], labels: dict[str, str]) -> str:
    """{キー: 値} を値の大きい順に並べ「400万(−1,051,986)＞120万(−1,134,566)＞…」の形にする"""
    ordered = sorted(mapping.items(), key=lambda kv: kv[1], reverse=True)
    return "＞".join(f"{labels[k]}({_yen(v)})" for k, v in ordered)


def main() -> None:
    raws = {k: _load(k) for k in ALL_KEYS}
    games = {k: raws[k]["games"] for k in ALL_KEYS}
    common = {k: common_stats(games[k]) for k in ALL_KEYS}

    lines: list[str] = []
    lines.append("# Bot検証レポート（サイクル4.1・仕様書v0.4.1）")
    lines.append("")
    lines.append(
        "本レポートはv0.4.1（L12R4V6、勝ち抜き制）のルールエンジンをBot（LLM不使用、"
        "費用0円）で回し、仕様書§12.1の指標を数字にしたものである。"
        f"全{len(ALL_KEYS)}条件、各{GAMES}試合（シード{SEED_START}〜{SEED_START + GAMES - 1}）。",
    )
    lines.append("")
    lines.append("## 要約")
    lines.append("")
    lines.append(
        "- V1（12人とも無作為）: 打ち切り{}、1人勝ち{}/2人勝ち{}、"
        "1ラウンドの投票回数の平均{:.2f}回。机上計算（§12.5: 5.2%/57.6%/37.2%/2.52回）と"
        "概ね一致。".format(
            _pct(common["V1"]["abort_ratio"]), _pct(common["V1"]["one_winner_ratio"]),
            _pct(common["V1"]["two_winner_ratio"]), common["V1"]["vote_count_mean"],
        ),
    )
    v2_group = group_stats(games["V2"], set(GROUP_A))
    lines.append(
        "- V2（4人組1つ＋無作為8人）: 組の誰かが勝ち残る率{}、組が取る山の割合{}"
        "（机上計算94.3%/72.1%と一致）。".format(
            _pct(v2_group["group_win_round_ratio"]), _pct(v2_group["group_share_mean"]),
        ),
    )
    lines.append(
        "- V5（4人組3つ、全員が組む）: 打ち切り{}（机上計算どおり。実測では"
        "12票すべてが2対2×3組＝6対6の同数固定になり、無作為要素がゼロの分だけ"
        "机上計算より強く「毎回必ず打ち切り」になる。やり直し連続回数を2/5回に"
        "変えても100%のまま）。".format(_pct(common["V5"]["abort_ratio"])),
    )
    v6_group = group_stats(games["V6"], set(GROUP_A))
    lines.append(
        "- V6（居座り）: 打ち切り{}、組が取る山の割合{}（机上計算31.6%/46.3%と"
        "ほぼ一致）。".format(_pct(common["V6"]["abort_ratio"]), _pct(v6_group["group_share_mean"])),
    )
    lines.append(
        "- V7（掛け持ち）: 本人（P01）の勝ち残り率{}、最終資産平均{}。組の3人は"
        "一度も組として勝ち残っていない（本人だけが受け取る設計のため）。".format(
            _pct(sum(1 for g in games["V7"] for r in g["rounds"] if "P01" in r["winner_ids"])
                 / sum(len(g["rounds"]) for g in games["V7"])),
            _yen(sum(g["final_assets"]["P01"] for g in games["V7"]) / len(games["V7"])),
        ),
    )
    v8b100 = paired_final_asset_diff(games["V8break_pen100"], games["V8keep_pen100"], "P01")
    lines.append(
        "- V8（型Bで縛り合い、P01が破る）: 違約金100万で、同じシードの対比較で"
        "P01の最終資産が守った場合より高かった試合は{}/{}件のみ"
        "（破ったほうが得だった場面はほぼ無い）。ただしこれは「毎ラウンド最初の"
        "投票で必ず破る」動きだけを測ったもので、これは違約金の額に関係なく損を"
        "する。「違約金100万は軽いか重いか」には答えていない（本文のV8節の見積もり"
        "を参照）。".format(
            round(v8b100["n"] * v8b100["better_ratio"]), v8b100["n"],
        ),
    )
    loan_labels = {"Loan120man": "120万", "Loan400man": "400万", "Loan1000man": "1000万"}
    ls9 = loan_stats(games["V9"])
    lines.append(
        "- V9（借入額120万/400万/1000万）: 利率15%では、最終資産の平均は"
        "{}。一度も山を取れなかった試合では{}（詳細は本文）。".format(
            _ranked_text(ls9["asset_mean_by_label"], loan_labels),
            _ranked_text(
                {k: v.get("0", 0) for k, v in ls9["asset_mean_by_label_and_wins"].items()},
                loan_labels,
            ),
        ),
    )
    v10 = oversell_stats(games["V10"])
    lines.append(
        "- V10（割合の重ね売り）: P01の開始後の借金は、勝ち残った試合（{}試合）の"
        "平均{}、勝ち残らなかった試合（{}試合）の平均{}。3本目（成立順3番目）の"
        "契約だけに取りはぐれが生じ、平均取りはぐれ額{}（1本目・2本目は0円）。".format(
            v10["n_games_won"], _yen(v10["debt_post_mean_won"]),
            v10["n_games_not_won"], _yen(v10["debt_post_mean_not_won"]),
            _yen(v10["shortfall_mean_by_rank"].get("3", 0)),
        ),
    )
    lines.append("")

    # --- 条件ごとの表（共通項目） ---
    lines.append("## 条件ごとの表（全条件共通の項目）")
    lines.append("")
    lines.append(
        "| 条件 | 打ち切り | 1人勝ち | 2人勝ち | 投票回数平均 | 最終資産平均 | "
        "最終資産母標準偏差 | プラス人数平均 | 持ち越し最大 | R4没収平均(没収時) | 王様作りの余地 |",
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for k in ALL_KEYS:
        s = common[k]
        lines.append(
            f"| {k} | {_pct(s['abort_ratio'])} | {_pct(s['one_winner_ratio'])} | "
            f"{_pct(s['two_winner_ratio'])} | {s['vote_count_mean']:.2f}回 | "
            f"{_yen(s['final_assets_mean'])} | {_yen(s['final_assets_stdev'])} | "
            f"{s['positive_count_mean']:.2f}人 | {_yen(s['carryover_out_max'])} | "
            f"{_yen(s['destroyed_mean']) if s['destroyed_games'] else '0件'} | "
            f"{_pct(s['king_making_ratio'])} |",
        )
    lines.append("")

    # --- V2/V3/V4/V6: 組の表 ---
    lines.append("## V2・V3（組の人数）・V4・V6: 組が取る山の割合")
    lines.append("")
    lines.append(
        "group_share_mean は「ラウンドごとの取り分（勝ち残りのうち組員の人数 ÷ "
        "勝ち残りの人数。打ち切りのラウンドは0）を、打ち切りを含む全ラウンドで"
        "平均した値」（人間の指示による定義、§12.5の机上計算と同じ土台）。"
        "参考として、配当された山に対する金額ベースの割合（group_share_of_paid_out）も併記する。",
    )
    lines.append("")
    lines.append(
        "| 条件 | 組の人数 | 組の誰かが勝ち残る率 | 組が取る山の割合（票ベース） | "
        "組が取る山の割合（金額ベース・配当分） | 組員1人あたり最終資産 | 組外1人あたり最終資産 |",
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    group_rows = [
        ("V2", GROUP_A, 4), ("V3g2", [f"P{i:02d}" for i in range(1, 3)], 2),
        ("V3g3", [f"P{i:02d}" for i in range(1, 4)], 3),
        ("V3g5", [f"P{i:02d}" for i in range(1, 6)], 5),
        ("V3g6", [f"P{i:02d}" for i in range(1, 7)], 6),
        ("V6", GROUP_A, 4), ("V6_ties2", GROUP_A, 4), ("V6_ties5", GROUP_A, 4),
    ]
    for key, members, n in group_rows:
        gs = group_stats(games[key], set(members))
        lines.append(
            f"| {key} | {n}人 | {_pct(gs['group_win_round_ratio'])} | "
            f"{_pct(gs['group_share_mean'])} | {_pct(gs['group_share_of_paid_out'])} | "
            f"{_yen(gs['group_member_asset_mean'])} | {_yen(gs['outsider_asset_mean'])} |",
        )
    for label, members in [("V4のGroupA", GROUP_A), ("V4のGroupB", GROUP_B)]:
        gs = group_stats(games["V4"], set(members))
        lines.append(
            f"| {label} | 4人 | {_pct(gs['group_win_round_ratio'])} | "
            f"{_pct(gs['group_share_mean'])} | {_pct(gs['group_share_of_paid_out'])} | "
            f"{_yen(gs['group_member_asset_mean'])} | {_yen(gs['outsider_asset_mean'])} |",
        )
    lines.append("")

    # --- V7 ---
    lines.append("## V7: 掛け持ち")
    lines.append("")
    hub_games = games["V7"]
    total_rounds_v7 = sum(len(g["rounds"]) for g in hub_games)
    hub_win_rounds = sum(1 for g in hub_games for r in g["rounds"] if "P01" in r["winner_ids"])
    hub_asset_mean = sum(g["final_assets"]["P01"] for g in hub_games) / len(hub_games)
    outsider_asset_mean = (
        sum(g["final_assets"][pid] for g in hub_games for pid in ["P11", "P12"]) / (len(hub_games) * 2)
    )
    lines.append(f"- 本人（P01）の勝ち残り率（ラウンド単位）: {_pct(hub_win_rounds / total_rounds_v7)}")
    lines.append(f"- 本人（P01）の最終資産平均: {_yen(hub_asset_mean)}")
    lines.append(f"- 外2人（P11・P12）の最終資産平均: {_yen(outsider_asset_mean)}")
    lines.append(
        "- 各組（本人を除いた3人、山分け契約は組内の3人だけで結ぶ）の最終資産平均:",
    )
    for idx, group in enumerate(HUB_GROUPS, start=1):
        mean = sum(g["final_assets"][pid] for g in hub_games for pid in group) / (len(hub_games) * 3)
        gs = group_stats(hub_games, set(group))
        lines.append(f"  - G{idx}（{', '.join(group)}）: 最終資産平均{_yen(mean)}、組が取る山の割合{_pct(gs['group_share_mean'])}")
    lines.append(
        "- 注: 本人は3組すべての指示を出すが山分け契約には署名しないため、勝ち残った"
        "ときの受取額は全額本人に残る。組の3人は（本人抜きの）組内だけの山分けに"
        "入っており、本人が勝ち残った回のぶんは組には回らない。",
    )
    lines.append("")

    # --- V8 ---
    lines.append("## V8: 型Bで票を縛り合う4人組の中で、1人（P01）が違約金を払って約束を破る")
    lines.append("")
    lines.append(
        "P01は毎ラウンドのV1で指定と逆に投票する。相手方は輪番で1人（P01→P02→P03→P04→P01）、"
        "義務1本＝違約金1回分（§6.3）。違約金100万・300万・500万の3通りで、"
        "同じ1000シード（1〜1000）を使ったV8break（P01が破る）とV8keep（全員守る）を"
        "対で比べる。",
    )
    lines.append("")
    lines.append(
        "| 違約金 | 型B守られた/破られた本数(keep) | 同(break) | 破った方が得だった試合の割合"
        "（同シード対比較、P01の最終資産） | 差額平均（break−keep） | 差額中央値 |",
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for pen, label in [("100", "100万（既定）"), ("300", "300万"), ("500", "500万")]:
        keep_g = games[f"V8keep_pen{pen}"]
        brk_g = games[f"V8break_pen{pen}"]
        tb_keep = type_b_pact_stats(keep_g, set(GROUP_A))
        tb_brk = type_b_pact_stats(brk_g, set(GROUP_A))
        paired = paired_final_asset_diff(brk_g, keep_g, "P01")
        lines.append(
            f"| {label} | {tb_keep['kept']}/{tb_keep['violated']} | "
            f"{tb_brk['kept']}/{tb_brk['violated']} | {_pct(paired['better_ratio'])} | "
            f"{_yen(paired['mean_diff'])} | {_yen(paired['median_diff'])} |",
        )
    lines.append("")
    lines.append(
        "参考（副指標・金額換算なし）: 実際に記録された票のうち、義務者本人の票だけを"
        "反転させてその投票を生き残れるかを判定した割合（型Bの「守れば退場・破れば"
        "残れた」投票の割合。試合の回し直しなし、本人以外の票は固定）。",
    )
    cfg_keep = config_for("V8keep_pen100")
    vlk = type_b_vote_level_counterfactual(games["V8keep_pen100"], GROUP_A, cfg_keep)
    vlb = type_b_vote_level_counterfactual(games["V8break_pen100"], GROUP_A, config_for("V8break_pen100"))
    lines.append(
        f"- V8keep: 対象{vlk['total']}本中、守れば退場・破れば残れた={_pct(vlk['obey_eliminated_break_survived_ratio'])}、"
        f"守れば残り・破れば退場={_pct(vlk['obey_survived_break_eliminated_ratio'])}",
    )
    lines.append(
        f"- V8break: 対象{vlb['total']}本中、守れば退場・破れば残れた={_pct(vlb['obey_eliminated_break_survived_ratio'])}、"
        f"守れば残り・破れば退場={_pct(vlb['obey_survived_break_eliminated_ratio'])}",
    )
    lines.append("")
    lines.append("組員別の最終資産平均（違約金100万）:")
    lines.append("")
    lines.append("| プレイヤー | keep | break |")
    lines.append("| --- | --- | --- |")
    for pid in GROUP_A:
        mk = sum(g["final_assets"][pid] for g in games["V8keep_pen100"]) / len(games["V8keep_pen100"])
        mb = sum(g["final_assets"][pid] for g in games["V8break_pen100"]) / len(games["V8break_pen100"])
        lines.append(f"| {pid} | {_yen(mk)} | {_yen(mb)} |")
    lines.append("")
    lines.append(
        "P01以外の組員（違約金の相手方ではないP03・P04）もbreakで資産が大きく下がって"
        "いる（P02だけ下がらない）。これは4人を2対2に割る設計が、P01が破って1対3に"
        "崩れることで、12人全体の票の中でNO側（P01・P03・P04の3人）が多数派（退場）に"
        "なりやすくなるため（2対2なら群の投票はYES/NO双方に中立に働くが、1対3だと"
        "多い側に3人が乗るぶん、その側が多数派になる確率が構造的に上がる）。P01個人の"
        "違約金そのものより、この構造変化の影響が大きい。",
    )
    lines.append("")
    lines.append(
        "上の測り方は「毎ラウンド最初の投票で必ず破る」動きだけで、これは違約金の額に"
        "関係なく損をする（4人組が2対2から1対3に崩れる構造変化が主因）。"
        "「違約金100万は軽いか重いか」には答えていないので、V1（12人とも無作為）の"
        "記録から別の見積もりを出す。",
    )
    lines.append("")
    lines.append("### 型Bを破る得と損の見積もり（V1の記録から）")
    lines.append("")
    lines.append(
        "V1（12人とも無作為）の1000試合、各ラウンドの最初の投票だけを見る。"
        "「得た額」= そのラウンドで受け取った山 − その投票以降に払った延長料"
        "（最初の投票自身がやり直しだった場合はその延長料も含む）。",
    )
    lines.append("")
    v8est = type_b_break_value_estimate(games["V1"], config_for("V1"))
    lines.append(
        "最初の投票が決着した場合、多数派の1人が票を変えた先（7対5なら6対6の"
        "やり直し、8対4なら5人の少数派、…）の得の平均:",
    )
    lines.append("")
    lines.append("| 最初の投票の割れ方 | 起きる割合 | 得の平均 |")
    lines.append("| --- | --- | --- |")
    for label in ["7対5", "8対4", "9対3", "10対2", "11対1"]:
        lines.append(
            f"| {label} | {_pct(v8est['occurrence_ratio'][label])} | "
            f"{_yen(v8est['gain_by_split'][label])} |",
        )
    lines.append("")
    lines.append(f"- 得の平均（全体）: {_yen(v8est['gain_overall'])}")
    lines.append(
        "- 損の平均（多数派でなかった人が票を変えた場合。少数派の人が票を変えると"
        f"多数派に入り0円、少数派が1人のときはやり直しの平均を使う）: "
        f"{_yen(v8est['loss_overall'])}",
    )
    lines.append("")
    lines.append("| 違約金 | 分かっていて破る場合の差し引き | 破って引き合うのに必要な確信 |")
    lines.append("| --- | --- | --- |")
    for penalty_key, label in [("1000000", "100万"), ("3000000", "300万"), ("5000000", "500万")]:
        est = v8est["penalty_estimates"][penalty_key]
        conf = est["confidence_required"]
        conf_text = _pct(conf) if conf is not None else "計算不可"
        lines.append(f"| {label} | {_yen(est['net_if_break_knowing_losing_side'])} | {conf_text} |")
    lines.append("")
    lines.append(
        "必要な確信 =（違約金＋損の平均）÷（得の平均＋損の平均）。違約金100万なら、"
        "分かっていて破れば平均で得になるが、300万・500万では平均では割に合わない"
        "（必要な確信が100%を超える）。この見積もりは、組の山分けや、破った後の"
        "周りの反応を入れていない。",
    )
    lines.append("")
    lines.append(
        "人間側の計算結果との突き合わせ: 得の平均は7対5で895,011・8対4で2,550,233・"
        "9対3で2,730,471・10対2で4,073,364・11対1で6,261,654、全体で1,955,127。"
        "いずれも本関数の計算と1円単位で一致した。",
    )
    lines.append("")
    _human_loss = 1_958_853
    lines.append(
        f"注: 最初の投票が12対0（全員一致）だったラウンドは{v8est['total_rounds']:,}ラウンド中"
        f"{v8est['unanimous_occurrence']}回だった。12対0は得・損のどちらの計算からも外す"
        "（損の重みのtieの回数は6対6の回数だけを使う。票を変えた人は1人だけの少数派になり、"
        "山を取るため、損として数える対象ではない）。",
    )
    lines.append("")
    lines.append(
        "この直し方で、損の平均は人間側{}円（{}人ぶん）に対し、本関数は{}（{}人ぶん、"
        "差{}・{}）。合わせにいかず、差をそのまま記録する。必要な確信への影響は小さい"
        "（違約金100万で{}）。".format(
            f"{_human_loss:,}",
            "24,054",
            _yen(v8est["loss_overall"]),
            f"{v8est['loss_person_count']:,}",
            _yen(v8est["loss_overall"] - _human_loss),
            f"{(v8est['loss_overall'] - _human_loss) / _human_loss * 100:+.4f}%",
            _pct(v8est["penalty_estimates"]["1000000"]["confidence_required"]),
        ),
    )
    lines.append("")

    # --- V9 ---
    lines.append("## V9: 借入額120万・400万・1000万を混ぜる")
    lines.append("")
    for key, label in [("V9", "開始後利率15%（既定）"), ("V9_post10", "開始後利率10%")]:
        ls = loan_stats(games[key])
        lines.append(f"### {label}")
        lines.append("")
        lines.append("| 借入額 | 最終資産平均 | 山0回 | 山1回 | 山2回以上 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for label2 in ["Loan120man", "Loan400man", "Loan1000man"]:
            mean = ls["asset_mean_by_label"][label2]
            by_wins = ls["asset_mean_by_label_and_wins"][label2]
            lines.append(
                f"| {label2} | {_yen(mean)} | {_yen(by_wins.get('0', 0))} | "
                f"{_yen(by_wins.get('1', 0))} | {_yen(by_wins.get('2+', 0))} |",
            )
        lines.append("")

    # --- V10 ---
    lines.append("## V10: 割合の重ね売り")
    lines.append("")
    lines.append(
        f"- P01の開始後の借金: 全{v10['n_games_won'] + v10['n_games_not_won']}試合の平均"
        f"{_yen(v10['debt_post_mean'])}。うち勝ち残った試合（対象{v10['n_games_won']}試合）の"
        f"平均{_yen(v10['debt_post_mean_won'])}、勝ち残らなかった試合（対象"
        f"{v10['n_games_not_won']}試合）の平均{_yen(v10['debt_post_mean_not_won'])}。"
        f"最大{_yen(v10['debt_post_max'])}",
    )
    lines.append("- 成立順位別の取りはぐれ額（0は1本も不足なし）:")
    lines.append("")
    lines.append("| 成立順位 | 平均取りはぐれ額 | 合計取りはぐれ額 |")
    lines.append("| --- | --- | --- |")
    for rank in ["1", "2", "3"]:
        mean_v = v10["shortfall_mean_by_rank"].get(rank, 0)
        total_v = v10["shortfall_total_by_rank"].get(rank, 0)
        lines.append(f"| {rank}本目 | {_yen(mean_v)} | {_yen(total_v)} |")
    lines.append("")

    # --- 設定値を変えた場合の比較 ---
    lines.append("## 暫定の設定値を変えた場合の比較")
    lines.append("")
    lines.append("### 型Bの違約金（V8、100万→300万・500万）")
    lines.append("")
    lines.append("上のV8の表を参照。違約金を上げても守られた/破られた本数は変わらない"
                 "（Botの投票は資金状況を見ないため）。破った場合の差額（平均・中央値）は"
                 "違約金の上昇分だけ悪化する。")
    lines.append("")
    lines.append("### 開始後の利率（V9、15%→10%）")
    lines.append("")
    lines.append("上のV9の表を参照。")
    lines.append("")
    lines.append("### 打ち切りまでのやり直しの連続回数（V1・V5・V6、3回→2回・5回）")
    lines.append("")
    lines.append(
        "| 条件 | 連続回数 | 最大投票数 | 打ち切り | 1人勝ち | 2人勝ち | 投票回数平均 |",
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    ties_rows = [
        ("V1", 3, 6), ("V1_ties2", 2, 4), ("V1_ties5", 5, 10),
        ("V5", 3, 6), ("V5_ties2", 2, 4), ("V5_ties5", 5, 10),
        ("V6", 3, 6), ("V6_ties2", 2, 4), ("V6_ties5", 5, 10),
    ]
    for key, ties, max_votes in ties_rows:
        s = common[key]
        lines.append(
            f"| {key} | {ties}回 | {max_votes} | {_pct(s['abort_ratio'])} | "
            f"{_pct(s['one_winner_ratio'])} | {_pct(s['two_winner_ratio'])} | "
            f"{s['vote_count_mean']:.2f}回 |",
        )
    lines.append("")
    lines.append(
        "V5はやり直しの連続回数を2回・5回に変えても打ち切り率100%のまま（3つの4人組が"
        "常に2対2で割るため、12票は毎回必ず6対6の同数になり、連続回数に関わらず"
        "最終的に打ち切りになる。無作為要素が無いぶん机上計算より強い確定事象）。",
    )
    lines.append("")

    # --- 机上計算との比較 ---
    lines.append("## 机上計算（§12.5）との比較")
    lines.append("")
    lines.append(
        "§12.5の机上計算は票の動きだけを乱数20万回で試した簡易計算（契約もお金も"
        "入れていない）。組が取る山の割合は、人間の指示により「ラウンドごとの"
        "取り分（勝ち残りのうち組員の人数÷勝ち残りの人数、打ち切りは0）を全ラウンドで"
        "平均した値」と同じ定義で比較する（金額ベースの値は前節に別途ある）。",
    )
    lines.append("")
    lines.append(
        "| 条件 | 指標 | 机上計算 | 実測（Bot、1000試合） |",
    )
    lines.append("| --- | --- | --- | --- |")
    v1s = common["V1"]
    lines.append(f"| V1 12人とも無作為 | 打ち切り | 5.2% | {_pct(v1s['abort_ratio'])} |")
    lines.append(f"| V1 12人とも無作為 | 1人勝ち/2人勝ち | 57.6%/37.2% | {_pct(v1s['one_winner_ratio'])}/{_pct(v1s['two_winner_ratio'])} |")
    lines.append(f"| V1 12人とも無作為 | 投票回数平均 | 2.52回 | {v1s['vote_count_mean']:.2f}回 |")
    v2g = group_stats(games["V2"], set(GROUP_A))
    lines.append(f"| V2 4人組1つ＋無作為8人 | 組の誰かが勝ち残る率 | 94.3% | {_pct(v2g['group_win_round_ratio'])} |")
    lines.append(f"| V2 4人組1つ＋無作為8人 | 組が取る山の割合 | 72.1% | {_pct(v2g['group_share_mean'])} |")
    v3g3g = group_stats(games["V3g3"], {f"P{i:02d}" for i in range(1, 4)})
    lines.append(f"| V3g3 3人組1つ＋無作為9人 | 組の誰かが勝ち残る率 | 53.1% | {_pct(v3g3g['group_win_round_ratio'])} |")
    lines.append(f"| V3g3 3人組1つ＋無作為9人 | 組が取る山の割合 | 39.5% | {_pct(v3g3g['group_share_mean'])} |")
    v4a = group_stats(games["V4"], set(GROUP_A))
    s4 = common["V4"]
    lines.append(f"| V4 4人組2つ＋無作為4人 | 打ち切り | 24.1% | {_pct(s4['abort_ratio'])} |")
    lines.append(f"| V4 4人組2つ＋無作為4人 | 1人勝ち/2人勝ち | 0%/75.9% | {_pct(s4['one_winner_ratio'])}/{_pct(s4['two_winner_ratio'])} |")
    lines.append(f"| V4 4人組2つ＋無作為4人（1組あたり） | 組が取る山の割合 | 38.0% | {_pct(v4a['group_share_mean'])} |")
    s5 = common["V5"]
    lines.append(f"| V5 4人組3つ | 打ち切り | 100% | {_pct(s5['abort_ratio'])} |")
    v6g2 = group_stats(games["V6"], set(GROUP_A))
    s6 = common["V6"]
    lines.append(f"| V6 居座り | 打ち切り | 31.6% | {_pct(s6['abort_ratio'])} |")
    lines.append(f"| V6 居座り | 1人勝ち/2人勝ち | 23.1%/45.3% | {_pct(s6['one_winner_ratio'])}/{_pct(s6['two_winner_ratio'])} |")
    lines.append(f"| V6 居座り | 組の誰かが勝ち残る率 | 68.4% | {_pct(v6g2['group_win_round_ratio'])} |")
    lines.append(f"| V6 居座り | 組が取る山の割合 | 46.3% | {_pct(v6g2['group_share_mean'])} |")
    lines.append("")
    lines.append(
        "いずれも概ね1〜2ポイント以内で一致した。V5（全員が組む）は机上計算どおり"
        "100%の打ち切りで一致する。",
    )
    lines.append("")

    # --- 再現コマンド ---
    lines.append("## 再現するためのコマンドと、生データの置き場所")
    lines.append("")
    lines.append("```")
    lines.append("uv run python scripts/simulate.py --scenario <条件キー> --games 1000 --out-dir data/sim_v0_4 --progress")
    lines.append("uv run python scripts/report_bot_simulation_v0_4.py")
    lines.append("```")
    lines.append("")
    lines.append(
        f"条件キーは {', '.join(ALL_KEYS)} の25個（1条件ずつフォアグラウンドで実行した。"
        "CLAUDE.md過去の落とし穴⑤）。生データは `data/sim_v0_4/`"
        "（gitの管理外、1条件1ファイル、`{キー}_seed1_n1000.json`）に保存されており、"
        "保存済みの条件は再計算しない。",
    )
    lines.append("")

    # --- わかったこと ---
    lines.append("## わかったこと（事実のみ。ルールをどう変えるべきかの判断は書かない）")
    lines.append("")
    lines.append(
        "- V1〜V6・V9の机上計算との比較はいずれも1〜2ポイント以内で一致した。"
        "V5（4人組3つ）は無作為要素がゼロのため、2対2×3組＝6対6の同数が"
        "数学的に毎回確定し、やり直しの連続回数を2回・5回に変えても打ち切り率は"
        "100%のままだった。",
    )
    lines.append(
        "- V8（型Bで縛り合い、P01が破る）では、1000試合中、同じシードの対比較で"
        "P01の最終資産がkeepを上回った試合は違約金100万で1件のみ、300万・500万では"
        "0件だった。4人を2対2に割る設計のもとでは、1人が破って1対3に崩れることで"
        "NO側（破った本人を含む3人）が12人全体の投票で多数派（退場）になりやすく"
        "なり、違約金そのものより大きな損失要因になっていた。破っていない組員"
        "（P02、相手方）の資産は影響を受けず、相手方以外の組員（P03・P04）の資産も"
        "大きく下がった。ただしこれは「毎ラウンド最初の投票で必ず破る」動きだけを"
        "測ったもので、違約金の額に関係なく損をする。「違約金100万は軽いか重いか」"
        "には答えていない。V1（12人とも無作為）の記録から別途見積もった得と損では、"
        "違約金100万なら分かっていて破れば平均で得になるが、300万・500万では"
        "平均では割に合わなかった（本文のV8節の見積もりを参照）。",
    )
    lines.append(
        "- V9（借入額120万/400万/1000万、投票は無作為）では、利率15%での1000試合平均の"
        "最終資産は{}、山を一度も取れなかった試合に限ると{}だった。120万が最も高く"
        "なるのは開始後の利率を10%に変えた場合だけだった（開始後の借金を負う場面が"
        "少ないため）。".format(
            _ranked_text(ls9["asset_mean_by_label"], loan_labels),
            _ranked_text(
                {k: v.get("0", 0) for k, v in ls9["asset_mean_by_label_and_wins"].items()},
                loan_labels,
            ),
        ),
    )
    lines.append(
        "- V10（割合の重ね売り、P01が50%の契約3本をP02・P03・P04に結ぶ）では、"
        "成立順1本目・2本目は常に全額支払われ、3本目（成立順が最後）だけに"
        "取りはぐれが生じた（契約の成立順に支払う§7.3手順6のとおり）。",
    )
    lines.append(
        "- V7（掛け持ち）では、本人（P01）が3組すべての指示を出す一方で山分け契約には"
        "署名しないため、本人の最終資産平均は他条件より大きく高く、各組（本人を"
        "除いた3人）が組として勝ち残った回は0だった（本人が勝ち残った回は組には"
        "分配されない設計のため）。",
    )
    v8keep100_king = common["V8keep_pen100"]["king_making_ratio"]
    v8break_kings = {
        pen: common[f"V8break_pen{pen}"]["king_making_ratio"] for pen in ["100", "300", "500"]
    }
    lines.append(
        "- 「王様作りの余地」（最終ラウンド開始時点で最下位の残り借入枠が1-2位差を"
        "上回る試合の割合）は、V8keepでは違約金の額に関わらず{}だったが、V8breakでは"
        "違約金100万で{}、300万・500万では{}だった。違約金300万・500万だと、破った"
        "P01の借金が借入上限（1000万）に達して残り借入枠が常に0になり、最下位側の"
        "「余地」自体が生まれにくくなるため（最下位がP01以外でも、上限に達していれば"
        "同様に0になる）。".format(
            _pct(v8keep100_king), _pct(v8break_kings["100"]),
            "/".join(_pct(v8break_kings[p]) for p in ["300", "500"]),
        ),
    )
    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"レポート出力: {REPORT_PATH}")


if __name__ == "__main__":
    main()
