"""
Bot検証レポートのMarkdown整形モジュール（サイクル1.2、新規実装）

sim.metrics.summarize() の結果を doc/analysis/bot_simulation_report.md 用の
Markdown文字列に整形する。事実と数字だけを書き、ルールをどう直すべきかは
書かない（CLAUDE.md「守ること」）。
"""

SCENARIO_LABELS: dict[str, str] = {
    "S1": "S1 全員無作為（基準）",
    "S2": "S2 契約なしの混成",
    "S3k1": "S3 ペア割り k=1",
    "S3k3": "S3 ペア割り k=3",
    "S3k6": "S3 ペア割り k=6",
    "S4": "S4 ペア割り k=3（各組片方を裏切りに置換）",
    "S5": "S5 借入の3通り（120万/500万/1000万、持ち続け）",
    "S6": "S6 サイクル1.1無作為契約Bot12人",
}

LABEL_SCENARIOS: list[str] = ["S2", "S3k1", "S3k3", "S3k6", "S4", "S5"]
CONTRACT_SCENARIOS: list[str] = ["S3k1", "S3k3", "S3k6", "S4", "S6"]
KING_MAKING_SCENARIOS: list[str] = ["S1", "S2"]

PENALTY_LABELS: dict[int, str] = {
    300_000: "30万", 500_000: "50万", 1_000_000: "100万", 2_000_000: "200万",
}

JUDGMENT_POINTS: list[tuple[str, str]] = [
    (
        "Follow系Bot（前回の少数派/多数派に乗る）の、前回「少数派なし」時とR1の行動",
        "判断材料が無いため無作為に投票する。R1（前回の結果が無い）も同様に無作為。",
    ),
    (
        "ペア割り契約の作り方（誰が提案するか・何巡目か・不成立時の扱い）",
        "プレイヤーIDの小さい方が提案者、大きい方が署名者（役割は固定）。提案者はそのラウンドの"
        "最初の自分の巡で、当該ラウンド対象の型B義務2本（自分→相手・相手→自分）を1契約に"
        "まとめて提案する。署名者は相手の提案が見えた次の巡で署名する。巡が尽きて不成立でも、"
        "PairSplitBotは約束どおりに投票する（必ず守る）。",
    ),
    (
        "裏切りBotの判定時点",
        "契約の提案・署名は必ず行う。Commitでラウンドごとに1回、自分のRNGで確率20%判定し、"
        "投票だけを義務と逆にする。",
    ),
    (
        "「破っていたら得だったか」の定義",
        "他のプレイヤー全員の票は実際に記録された票のまま固定し、義務者本人の票だけを"
        "「守った場合」「破った場合」で切り替えて engine.minority.resolve_minority()"
        "（ルールエンジンそのもの）に通し、当該ラウンドの受取額（参加費の戻り＋配当。"
        "多数派・少数派なしは0円）の差を取る。将来の持ち越しへの影響は含めない。1ラウンドに"
        "複数本の型B義務があっても1本ずつ独立に計算する。gain>違約金を「得」とし、同額は"
        "「得でない」とする。",
    ),
    (
        "「1位になった割合」の同率1位の扱い",
        "同率1位は全員1位として数える（final_ranks[pid]==1の全員を対象にする）。",
    ),
    (
        "「借金の上限に達した」の判定基準",
        "各ラウンド終了時点（Finance後）の借金合計（開始前+開始後）が1000万円以上になった"
        "ことが試合中に一度でもあるか。達したプレイヤーの人数を数える。",
    ),
    (
        "「最下位」「1位と2位の差」の基準時点",
        "R12開始時点＝R11のFinance終了時点のnet_assets（同額はプレイヤーID順）。",
    ),
    (
        "「ばらつき」の定義",
        "母標準偏差（Pythonのstatistics.pstdev）。",
    ),
    (
        "「持ち越しの平均」の範囲",
        "全試合・全ラウンドの、ラウンド終了時点carryover_afterの単純平均。",
    ),
    (
        "S5（借入の3通り）の構成（サイクル1.3で改訂）",
        "仕様書v0.3（§3.5）で返済が開始後の借金にしか効かなくなり、サイクル1.2の"
        "「1000万借りてR1で880万返す」役が成り立たなくなったため、120万／500万／"
        "1000万をそれぞれ持ち続ける3通り（4人ずつ）に組み替えた。500万という中間額は"
        "仕様書に定数が無く、120万〜1000万の中間として判断して定めた。",
    ),
    (
        "S6が使う無作為契約Botの実装場所",
        "新規に作らず、既存のtests/helpers.py::RandomContractAgentを再利用する"
        "（scripts/dry_run.py --contractsの前例と同じ扱い）。",
    ),
    (
        "S4で各ペアのどちらを裏切りにするか",
        "組の中でIDが小さい方を裏切り（BetrayerPairBot）に固定し、大きい方はPairSplitBotの"
        "ままにする（どちらでも良い選択だが、再現性のため固定した）。",
    ),
    (
        "Bot固有の乱数シードの導出",
        "試合シード×100＋プレイヤー番号（P01〜P12）。同一の試合シードなら、どのシナリオでも"
        "各プレイヤーのBot内部の乱数は常に同じ列になる。",
    ),
    (
        "「成立順と回収率」の集計対象",
        "同一ラウンド・同一義務者のCONTRACT_PAYMENTのうち、1本でも約束額（promised）に届か"
        "なかった義務者だけを対象にする。対象になった義務者について、記録順（contract_seq＝"
        "成立順→契約内の記載順）で1本目／2本目／3本目以降に分け、各本の「支払額÷約束額」を"
        "平均する。",
    ),
    (
        "分割実行と足し合わせの等価性",
        "生データ（collect_raw()の戻り値）の全項目はカウンタの加算・リストの連結で合成できる"
        "設計にしたため、同じシード範囲を分割して回しても、1回で回した場合と完全に同じ数字に"
        "なる（sim/store.py::merge_raw、tests/test_store.pyで固定）。",
    ),
]


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _yen(x: float) -> str:
    return f"{x:,.0f}円"


def _num(x: float) -> str:
    return f"{x:,.2f}"


def render_overall_tables(summaries: dict[str, dict]) -> str:
    lines = ["### 少数派の人数の分布・持ち越し（全ラウンドに対する割合）", ""]
    lines.append(
        "| シナリオ | 試合数 | 少数派なし | 1人 | 2人 | 3人 | 4人 | 5人 | "
        "持ち越し最大 | 持ち越し平均 | R12消滅平均/試合 |",
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for key, s in summaries.items():
        dist = s["minority_count_dist"]
        lines.append(
            f"| {SCENARIO_LABELS[key]} | {s['n_games']} | {_pct(dist['none'])} | "
            f"{_pct(dist['1'])} | {_pct(dist['2'])} | {_pct(dist['3'])} | "
            f"{_pct(dist['4'])} | {_pct(dist['5'])} | {_yen(s['carryover_max'])} | "
            f"{_yen(s['carryover_mean'])} | {_yen(s['destroyed_mean_per_game'])} |",
        )

    lines += ["", "### 最終資産の分布・順位差", ""]
    lines.append(
        "| シナリオ | 最大 | 最小 | 中央 | 標準偏差 | プラス人数平均 | 1-2位差平均 | 1-最下位差平均 |",
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for key, s in summaries.items():
        lines.append(
            f"| {SCENARIO_LABELS[key]} | {_yen(s['final_assets_max'])} | "
            f"{_yen(s['final_assets_min'])} | {_yen(s['final_assets_median'])} | "
            f"{_yen(s['final_assets_stdev'])} | {_num(s['positive_count_mean'])}人 | "
            f"{_yen(s['gap_1_2_mean'])} | {_yen(s['gap_1_last_mean'])} |",
        )
    return "\n".join(lines)


def render_label_tables(summaries: dict[str, dict]) -> str:
    blocks = ["## Botの種類別（S2〜S5）", ""]
    for key in LABEL_SCENARIOS:
        s = summaries[key]
        is_s5 = key == "S5"
        blocks.append(f"### {SCENARIO_LABELS[key]}")
        blocks.append("")
        if is_s5:
            header = "| Bot種別 | n | 平均最終資産 | 平均順位 | 1位の割合 | 利息平均（1.5%分） | 利息平均（3%分） |"
            sep = "| --- | --- | --- | --- | --- | --- | --- |"
        else:
            header = "| Bot種別 | n | 平均最終資産 | 平均順位 | 1位の割合 |"
            sep = "| --- | --- | --- | --- | --- |"
        blocks.append(header)
        blocks.append(sep)
        for label, row in sorted(s["by_label"].items()):
            if is_s5:
                blocks.append(
                    f"| {label} | {row['n']} | {_yen(row['asset_mean'])} | "
                    f"{_num(row['rank_mean'])} | {_pct(row['rank1_ratio'])} | "
                    f"{_yen(row['interest_pre_mean'])} | {_yen(row['interest_post_mean'])} |",
                )
            else:
                blocks.append(
                    f"| {label} | {row['n']} | {_yen(row['asset_mean'])} | "
                    f"{_num(row['rank_mean'])} | {_pct(row['rank1_ratio'])} |",
                )
        blocks.append("")
    return "\n".join(blocks)


def render_contract_sections(summaries: dict[str, dict]) -> str:
    blocks = ["## 契約まわり（S3・S4・S6）", ""]
    for key in CONTRACT_SCENARIOS:
        s = summaries[key]
        blocks.append(f"### {SCENARIO_LABELS[key]}")
        blocks.append("")
        blocks.append(f"- 型B 守られた本数: {s['type_b_kept']} ／ 破られた本数: {s['type_b_violated']}")
        blocks.append(
            f"- gain（違約金を引く前の、破った方が得だった額）の分布: "
            f"平均 {_yen(s['gain_mean'])}、中央 {_yen(s['gain_median'])}、"
            f"最大 {_yen(s['gain_max'])}、正の割合 {_pct(s['gain_positive_ratio'])}",
        )
        penalty_parts = ", ".join(
            f"{PENALTY_LABELS[p]}円: {_pct(s['break_even_ratio_by_penalty'][p])}"
            for p in sorted(s["break_even_ratio_by_penalty"])
        )
        blocks.append(f"- 違約金ごとの「破った方が得だった割合」: {penalty_parts}")
        blocks.append(f"- 借金の上限（1000万円）に達した人数の平均: {_num(s['debt_cap_hits_mean'])}人/試合")
        blocks.append(
            f"- 払いきれなかった額の合計: {_yen(s['shortfall_total'])}"
            f"（平均 {_yen(s['shortfall_mean_per_game'])}/試合）",
        )
        rec = s["recovery_by_position"]
        blocks.append(
            f"- 払いきれない義務者の回収率（支払額÷約束額の平均）: "
            f"1本目 {_pct(rec[1])}、2本目 {_pct(rec[2])}、3本目以降 {_pct(rec[3])}",
        )
        blocks.append("")
    return "\n".join(blocks)


def render_king_making(summaries: dict[str, dict]) -> str:
    lines = ["## 王様作りの余地（S1・S2）", ""]
    lines.append("| シナリオ | 枠>差の割合（R12開始時点。最下位の残り借入枠 > 1位と2位の差） |")
    lines.append("| --- | --- |")
    for key in KING_MAKING_SCENARIOS:
        s = summaries[key]
        lines.append(f"| {SCENARIO_LABELS[key]} | {_pct(s['king_making_ratio'])} |")
    return "\n".join(lines)


def render_judgment_points() -> str:
    lines = ["## 仕様書に書いておらず、判断が必要だった点", ""]
    for i, (title, decision) in enumerate(JUDGMENT_POINTS, start=1):
        lines.append(f"{i}. **{title}**")
        lines.append(f"   → {decision}")
    return "\n".join(lines)


def render_summary_lines(summaries: dict[str, dict], games: int) -> str:
    """冒頭の要約（10行以内）"""
    s1 = summaries.get("S1")
    s3k3 = summaries.get("S3k3")
    s4 = summaries.get("S4")
    lines = [f"- 対象: S1〜S6（S3はk=1/3/6）の計8条件、各{games}試合、シード1〜{games}。"]
    if s1:
        lines.append(
            f"- S1（全員無作為）: 少数派なしの割合 {_pct(s1['no_minority_ratio'])}、"
            f"持ち越し最大 {_yen(s1['carryover_max'])}、"
            f"プラスで終わる人数の平均 {_num(s1['positive_count_mean'])}人、"
            f"王様作りの余地 {_pct(s1['king_making_ratio'])}。",
        )
    if s3k3 and s3k3["type_b_kept"] + s3k3["type_b_violated"] > 0:
        lines.append(
            f"- S3 k=3: 型B守られた{s3k3['type_b_kept']}本/破られた{s3k3['type_b_violated']}本、"
            f"違約金100万での「破った方が得」割合 "
            f"{_pct(s3k3['break_even_ratio_by_penalty'][1_000_000])}。",
        )
    if s4 and s4["type_b_kept"] + s4["type_b_violated"] > 0:
        lines.append(
            f"- S4（裏切り混在）: 型B守られた{s4['type_b_kept']}本/破られた{s4['type_b_violated']}本。",
        )
    lines.append("- 詳細は本文の表を参照。ルールエンジンへの変更はなし。")
    return "\n".join(lines)


def render_report(summaries: dict[str, dict], games: int) -> str:
    parts = [
        "# Bot検証レポート（サイクル1.2）",
        "",
        render_summary_lines(summaries, games),
        "",
        "## 全体（S1〜S6）",
        "",
        render_overall_tables(summaries),
        "",
        render_label_tables(summaries),
        "",
        render_contract_sections(summaries),
        "",
        render_king_making(summaries),
        "",
        render_judgment_points(),
        "",
    ]
    return "\n".join(parts)
