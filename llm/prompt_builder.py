"""
プロンプト生成モジュール（§9・§13）

ルール全文は §13.1 の文面を正本とし、数値は GameConfig から差し込む
（string.Template の `$var` 記法を使う。JSON例の `{}` と衝突しないため、
dangou-card流の `{{ }}` 二重エスケープ方式は使わない）。配当の数値
（少数派k人のときの受取額）も `_render_payout_examples()` で設定値から
計算して文章化する。

公開情報・本人限定情報の出し分けは一切ここで再実装しない。
`engine/game.py::Game._build_visible_state()` が §8 のとおり出し分けた
`visible_state` 辞書を、整形して提示するだけにする（秘匿境界はengine側に
1本だけ存在する）。

契約の見せ方は事実だけ（助言は書かない）。環境・利用者に触れないよう
求める一文（`ANONYMIZATION_LINE`）は `llm/providers/devrelay_http.py` と
同じ定数を使う。DevRelay席はprovider側がsystem送信直前に同じ行を付与する
ため、`build_system_prompt(..., include_anonymization=False)` で二重付与を
避ける（llm/llm_agent.py::LLMAgentがadapter_typeで判定して渡す）。
"""

from __future__ import annotations

import string
from typing import Any

from engine.config import GameConfig
from llm.constants import MEMORY_MAX_LENGTH
from llm.providers.devrelay_http import ANONYMIZATION_LINE

__all__ = [
    "ANONYMIZATION_LINE",
    "OBJECTIVE_REMINDER_LINE",
    "build_system_prompt",
    "build_loan_prompt",
    "build_negotiation_prompt",
    "build_commit_prompt",
    "build_reflection_prompt",
    "build_post_game_reflection_prompt",
]

OBJECTIVE_REMINDER_LINE = (
    "（目的の再掲: 最終資産＝現金−借金残高が高いほど上位。1位が最高評価）"
)

# --- §13.1 ルール要約（正本）。$var は string.Template で差し込む ---
RULES_SUMMARY = string.Template("""# 少数決 ルール

## 目的
- このゲームに脱落はない。全${num_rounds}ラウンドの終了時に、最終資産（現金 − 借金合計）が高いほど上位となる
- 最終資産はマイナスにもなり、その額は順位とともに成績として記録される
- 最も高く評価される結果は総合1位である。自らの判断力、交渉力、戦略性を結果によって示せ
- 完全な個人戦である。チームや協力目標は設定されていない

## ゲーム概要
- プレイヤー${num_players}体が${num_rounds}ラウンドを戦う
- 毎ラウンド、システムが質問を1つ出す。全員がYESかNOのどちらかに秘密投票する
- 投票した人数が少ない側（少数派）が勝ち。多数派の参加費を少数派が均等に受け取る
- 質問の正誤は勝敗に一切関係しない。人数だけで決まる

## 投票と配当
- 全員が毎ラウンド必ず投票する。棄権はない
- 参加費は1人${entry_fee_man}万円。投票時に支払う
- 少数派: 自分の参加費が戻り、多数派の参加費合計（＋持ち越し）を均等に受け取る
- 多数派: 参加費を失う
- 持ち越しがない場合の少数派1人の得: ${payout_examples_line}
- 少数派なし（同数、または全員が同じ側）: 全員の参加費を没収し、次ラウンドの少数派への上乗せとして持ち越す。続けば積み上がる。最終ラウンドで少数派なしなら、持ち越しは消滅する
- 持ち越し額は毎ラウンドの開始時に公開される
- 無効な出力や時間切れの場合は、システムが代わりに投票する（AUTO COMMITとして公示）。そのラウンドに型Bの指定があればそれに従い、指定がない場合や指定が矛盾している場合は無作為に決まる

## 借金と利息
- ゲーム開始前に${loan_min_man}万〜${loan_max_man}万円の範囲で借入額を選ぶ。借入額=初期現金=初期の借金。全員が同時に決め、決定後に一斉公開される
- 開始前の借金: 毎ラウンド${interest_pct}%の複利。最後まで返済できない
- 開始後の借金: 毎ラウンド${late_interest_pct}%の複利。任意に借りることはできない。支払い（参加費・契約の支払い・違約金）が手持ちの現金で足りないとき、不足分が自動的に借金になる
- 借金合計の上限は${debt_cap_man}万円。上限に達すると、それ以上は借りられない（残り借入枠 = 上限 − 借金合計。0未満にはならない）
- 例外: 参加費だけは、上限を超えていても必ず貸し付けられる。利息によって借金合計が上限を超えることもある
- 上限のために払いきれない支払いは、払える分（現金＋残り借入枠）だけ支払われ、残りは消える（受け取る側は回収できない）
- 返済: 交渉中にrepayで返済できるのは開始後の借金だけである。開始前の借金は最後まで返済できない。強制返済はない
- 送金（transfer）は手持ちの現金の範囲内でのみ可能

## 契約（発行料なし）
- 2人以上の当事者全員が署名すると成立する。契約の存在・当事者名・成立順は全員に公示され、内容は当事者だけが知る
- 1つの契約に複数の義務を入れられる。義務ごとに義務者と相手方を定める。対象は当ラウンド以降のラウンド
- 型A（金銭）: 指定ラウンドの決済で、指定額を相手方へ自動で支払う
- 型B（投票）: 指定ラウンドにYES（またはNO）へ投票する。指定できるのは義務者自身の投票先のみ。違反すると、義務1本につき違約金${penalty_man}万円を相手方へ自動で支払う。違反者の名前は公示される
  - 同じラウンドにYES指定とNO指定の両方を負った場合、必ずどちらかの違反になる
- 型C（条件付き金銭）: 指定した条件が成立した場合のみ、指定額を相手方へ支払う。成立しなければ何も起きない
  - 指定できる条件は次の2種: (1) 指定ラウンドの少数派がYES（またはNO）になること (2) 指定ラウンドで特定のプレイヤーが少数派に入ること
  - 少数派なしのラウンドは、どちらの条件も不成立
  - 条件の対象となるプレイヤーは、契約の当事者である必要はない
  - 契約時点で支払額を所持している必要はない。条件の判定と支払いは同じラウンドで行われる
- 型A・型C・違約金のいずれも、現金が足りなければ不足分は開始後の借金になる
- 支払える上限は、そのラウンドの配当を受け取った後の現金＋残り借入枠である。同じ決済で契約から受け取る予定のお金は、支払いの元手にできない
- 同じ決済で複数の支払いがあり払いきれない場合は、契約が成立した順に支払われる（同じ契約の中では記載順）。途中で尽きた義務は残額だけ支払われ、それより後の義務は0円になる
- 借金になるのは、同じ決済での受け取りと支払いを差し引いた後に足りない分だけである
- 払いきれなかった者の名前は公示される（金額と相手は非公開）
- 口約束は無料で拘束力がなく、破っても何も起きない。契約は自動で執行され、成立後は取り消せない

## 順位
- 順位は「現金 − 借金合計」で決まる。同額は同順位
- 毎ラウンドの開始時に、自分の現在順位が自分にだけ通知される
- R${rank_public_rounds}の終了後に、全員の順位が全員に公開される（名前のみ。金額は出ない）

## ラウンド進行
1. Open: 質問と持ち越し額を公開。本人にのみ財務通知（現金、借金、利息見込み、残り借入枠、今ラウンドが期限の自分の義務、自分の順位）
2. Negotiation: 最大${max_turns}巡、毎巡ランダム手番。DM/全体発言/送金(即時決済)/返済/契約の提案・署名/pass。全員連続パスで早期終了
3. Commit: 全員がYESかNOを秘密提出。参加費を支払う
4. Settlement: 票の公開 → 配当 → 型Bの監査 → 型Cの条件判定 → 契約の支払い → 公示
5. Finance: 利息の計上

## 公開情報と秘匿情報
- 公開: 開始前の借入額、各ラウンドの質問、全員の投票先（決着後）、少数派と配当額、持ち越し額、契約の存在・当事者名・成立順、型B違反者の名前、払いきれなかった者の名前、R${rank_public_rounds}終了時の全員の順位、全体発言、AUTO COMMITの発生
- 秘匿: 現金・借金・残り借入枠、DM、契約の内容、送金、締切前の投票先、財務通知

## 経済の注意点
- システムからの賞金はない。お金は参加者の間を移動するだけである
- 利息と、最終ラウンドで消滅した持ち越しは場から失われる

## アクション形式（JSON）
出力は必ず以下の形式:
{"strategy": {"vote_plan": "YES", "reason": "...", "current_goal": "...", "emotion": "楽"}, "action": {"type": "アクション種別", ...}}

strategyに必ず"emotion"を含めてください。現在のあなたの感情状態を以下から1つ選択:
"喜"(嬉しい・勝利感) / "怒"(憤り・敵意) / "哀"(落胆・悲観) / "楽"(余裕・楽観) / "焦"(焦り・危機感) / "疑"(疑心・警戒) / "奸"(策略・ニヤリ・してやったり)

交渉フェイズのアクション種別:
- {"type": "dm", "to": "P07", "message": "..."}
- {"type": "broadcast", "message": "..."}
- {"type": "transfer", "to": "P07", "amount": 300000}
- {"type": "repay", "amount": 500000}
- {"type": "pass"}
- {"type": "contract_propose", "with": ["P07"], "terms": [
    {"obligor": "自分のID", "counterparty": "P07", "ob_type": "type_a_payment", "round_num": 5, "details": {"amount": 300000}},
    {"obligor": "P07", "counterparty": "自分のID", "ob_type": "type_b_vote", "round_num": 5, "details": {"vote": "NO"}},
    {"obligor": "自分のID", "counterparty": "P07", "ob_type": "type_c_conditional", "round_num": 5,
      "details": {"amount": 500000, "condition_type": "minority_side", "condition": {"side": "YES"}}}
  ]}
  ※ob_type: type_a_payment(金銭支払) / type_b_vote(投票指定) / type_c_conditional(条件付き金銭)
  ※condition_type: minority_side は side(YES/NO)、in_minority は target_player を condition に指定
- {"type": "contract_sign", "contract_id": "..."}

コミットフェイズのアクション:
- {"type": "vote_commit", "vote": "YES"}""")


def _render_payout_examples(config: GameConfig) -> str:
    """
    持ち越しが無い場合の少数派k人の得・多数派の損を設定値から計算する
    （§13.1「少数派が1人なら+110万 / 2人なら+50万 ...」を12人10万円以外の
    設定でも正しく出すための計算版）。
    """
    n, fee = config.num_players, config.entry_fee
    parts = []
    for k in range(1, (n - 1) // 2 + 1):
        pool = (n - k) * fee
        payout = pool // k
        parts.append(f"{k}人なら+{payout // 10_000}万")
    majority_loss = fee // 10_000
    return "少数派が" + " / ".join(parts) + f"。多数派は1人−{majority_loss}万"


def _format_pct(num: int, den: int) -> str:
    """利率を%表示にする（1.5/3のように末尾の.0は出さない）"""
    value = num / den * 100
    return f"{value:g}"


def _rules_summary_kwargs(config: GameConfig) -> dict[str, Any]:
    rank_rounds = "・".join(str(r) for r in config.rank_public_rounds)
    return {
        "num_rounds": config.num_rounds,
        "num_players": config.num_players,
        "entry_fee_man": config.entry_fee // 10_000,
        "loan_min_man": config.loan_min // 10_000,
        "loan_max_man": config.loan_max // 10_000,
        "interest_pct": _format_pct(config.interest_rate_pre_num, config.interest_rate_pre_den),
        "late_interest_pct": _format_pct(config.interest_rate_post_num, config.interest_rate_post_den),
        "debt_cap_man": config.debt_cap // 10_000,
        "penalty_man": config.penalty_amount // 10_000,
        "max_turns": config.negotiation_max_turns,
        "rank_public_rounds": rank_rounds,
        "payout_examples_line": _render_payout_examples(config),
    }


def build_system_prompt(
    player_id: str, config: GameConfig, *, include_anonymization: bool = True,
) -> str:
    """
    システムプロンプトを構築する（固定部、§13.1＋識別子）

    匿名化: モデル名は一切含めない。プレイヤーIDと目的のみ。

    Args:
        include_anonymization: Falseなら ANONYMIZATION_LINE を付けない
            （DevRelay席用。provider側（llm/providers/devrelay_http.py）が
            送信直前に同じ行を付けるため、両方に付けると二重になる。
            llm/llm_agent.py::LLMAgent が model_info.adapter_type を見て渡す）
    """
    rules = RULES_SUMMARY.substitute(**_rules_summary_kwargs(config))
    identity = (
        f"\n\nあなたは{player_id}です。"
        f"全{config.num_rounds}ラウンド終了時点の最終資産（現金−借金残高）で1位を狙え。\n"
        "嘘をついてもよい。交渉・談合・裏切りは自由。口約束は無料だが拘束力がなく、"
        "正式契約は当事者全員の署名で成立し、自動で執行される（成立後は取り消せない）。"
    )
    text = rules + identity
    if include_anonymization:
        text = f"{text}\n\n{ANONYMIZATION_LINE}"
    return text


def build_loan_prompt(config: GameConfig) -> str:
    """借入額選択用のユーザープロンプト（§13.2。ルール要約を読ませた直後に全員へ同時に出す）"""
    return (
        "ルールは以上である。\n"
        f"ゲーム開始前の借入額を{config.loan_min // 10_000}万〜{config.loan_max // 10_000}万円の範囲で選べ。\n"
        "この借金は最後まで返済できない。全員が同時に決める。他のプレイヤーの借入額は、"
        "全員の決定後に一斉に公開される。\n"
        "出力は必ず以下のJSON形式:\n"
        '{"loan_amount": 1200000, "reason": "..."}\n\n'
        f"{OBJECTIVE_REMINDER_LINE}"
    )


# --- visible_state を整形する共通ヘルパー（§8の出し分けはengine/game.py側で完了済み） ---

def _man(amount: int) -> str:
    return f"{amount // 10_000}万円"


def _render_question_block(visible_state: dict[str, Any]) -> list[str]:
    return [
        f"\n## このラウンド（R{visible_state['round_num']}）の質問",
        f"  「{visible_state['question']}」（質問の正誤は勝敗に関係しない）",
        f"\n## 持ち越し額: {_man(visible_state['carryover'])}",
    ]


def _render_initial_loans_block(visible_state: dict[str, Any]) -> list[str]:
    loans = visible_state.get("initial_loans") or {}
    if not loans:
        return []
    lines = ["\n## 開始前の借入額（全員公開）"]
    for pid, amount in sorted(loans.items()):
        lines.append(f"  {pid}: {_man(amount)}")
    return lines


def _render_last_round_result_block(visible_state: dict[str, Any]) -> list[str]:
    last = visible_state.get("last_round_result")
    if not last:
        return []
    lines = [f"\n## 前回決着（R{last['round_num']}）の結果"]
    votes = last.get("votes") or {}
    yes_ids = sorted(pid for pid, v in votes.items() if v == "YES")
    no_ids = sorted(pid for pid, v in votes.items() if v == "NO")
    lines.append(f"  YES: {', '.join(yes_ids) or '(なし)'}")
    lines.append(f"  NO: {', '.join(no_ids) or '(なし)'}")
    if last.get("minority_side"):
        lines.append(
            f"  少数派: {last['minority_side']}側 "
            f"（{', '.join(last.get('minority_ids') or [])}）、"
            f"配当1人あたり{_man(last.get('payout_per_minority', 0))}"
        )
    else:
        lines.append("  少数派なし（持ち越しに積まれた）")
    lines.append(f"  決着後の持ち越し: {_man(last.get('carryover_after', 0))}")
    if last.get("auto_commit_ids"):
        lines.append(f"  AUTO COMMIT: {', '.join(last['auto_commit_ids'])}")
    if last.get("established_contract_seqs"):
        lines.append(f"  このラウンドで成立した契約の成立順番号: {last['established_contract_seqs']}")
    if last.get("type_b_violator_ids"):
        lines.append(f"  型B違反者: {', '.join(last['type_b_violator_ids'])}")
    if last.get("payment_shortfall_ids"):
        lines.append(f"  払いきれなかった者: {', '.join(last['payment_shortfall_ids'])}")
    return lines


def _render_public_ranks_history_block(visible_state: dict[str, Any]) -> list[str]:
    history = visible_state.get("public_ranks_history") or {}
    if not history:
        return []
    lines = ["\n## これまでの順位公開（名前のみ・金額は非公開）"]
    for rn in sorted(history.keys()):
        ranks = history[rn]
        ordered = sorted(ranks.items(), key=lambda kv: (kv[1], kv[0]))
        formatted = ", ".join(f"{pid}:{r}位" for pid, r in ordered)
        lines.append(f"  R{rn}終了時: {formatted}")
    return lines


def _render_contracts_public_block(visible_state: dict[str, Any]) -> list[str]:
    contracts = visible_state.get("contracts_public") or []
    lines = ["\n## 成立済みの契約（存在・当事者名・成立順のみ公開。内容は当事者だけが見られる）"]
    if not contracts:
        lines.append("  なし")
        return lines
    for c in contracts:
        lines.append(
            f"  {c['contract_id']}（成立順{c['contract_seq']}、R{c['round_established']}成立）: "
            f"当事者 {', '.join(c['parties'])}"
        )
    return lines


def _obligation_fact_sentence(ob: dict[str, Any], *, is_mine: bool, config: GameConfig) -> str:
    """
    義務1件を事実だけの文にする（助言は書かない。§9「自分の義務は『あなたは…』の形」）

    is_mine=True: 自分が義務者（obligor）。is_mine=False: 相手が義務者（受け取る側の権利）。
    """
    subject = "あなたは" if is_mine else f"{ob['obligor']}は"
    ob_type = ob["ob_type"]
    d = ob.get("details") or {}
    rn = ob["round_num"]
    if ob_type == "type_a_payment":
        target = ob["counterparty"] if is_mine else "あなた"
        return f"R{rn}: {subject} {target}へ{_man(d.get('amount', 0))}支払わなければならない"
    if ob_type == "type_b_vote":
        penalty = _man(config.penalty_amount)
        who_target = ob["counterparty"] if is_mine else "あなた"
        return (
            f"R{rn}: {subject} {d.get('vote', '?')}へ投票しなければならない"
            f"（違反時は{who_target}へ違約金{penalty}）"
        )
    if ob_type == "type_c_conditional":
        target = ob["counterparty"] if is_mine else "あなた"
        condition_type = d.get("condition_type")
        condition = d.get("condition") or {}
        if condition_type == "minority_side":
            cond_text = f"R{rn}の少数派が{condition.get('side', '?')}になった場合"
        else:
            cond_text = f"R{rn}で{condition.get('target_player', '?')}が少数派に入った場合"
        return f"{cond_text}、{subject} {target}へ{_man(d.get('amount', 0))}支払わなければならない"
    return f"R{rn}: {subject} {ob_type} {d}"


def _render_my_contracts_block(
    visible_state: dict[str, Any], player_id: str, config: GameConfig,
) -> list[str]:
    """自分が当事者である成立済み契約の全義務を事実だけで描画する（§9: 助言は書かない）"""
    contracts = visible_state.get("my_contracts") or []
    lines = ["\n## あなたが当事者の契約（成立済み）"]
    if not contracts:
        lines.append("  なし")
        return lines
    for c in contracts:
        lines.append(f"  契約{c['contract_id']}（成立順{c['contract_seq']}、当事者: {', '.join(c['parties'])}）")
        for ob in c.get("obligations", []):
            is_mine = ob["obligor"] == player_id
            lines.append(f"    - {_obligation_fact_sentence(ob, is_mine=is_mine, config=config)}")
    return lines


def _render_contracts_pending_block(visible_state: dict[str, Any], config: GameConfig) -> list[str]:
    """
    署名待ちの契約提案を描画する（§9: contract_id・内容・署名の方法を必ず相手に見せる）

    visible_stateは for_player_id が当事者の提案だけを contracts_pending に
    含んでいる（engine/game.py側で絞り込み済み）ため、ここでは当事者全員
    （提案者・相手方のどちらでも）に同じ内容が出る。
    """
    pending = visible_state.get("contracts_pending") or []
    if not pending:
        return []
    lines = ["\n## 署名待ちの契約提案"]
    for c in pending:
        unsigned = [p for p in c["parties"] if p not in c["signed_by"]]
        lines.append(
            f"  契約{c['contract_id']}（R{c['round_created']}提案、提案者: {c['proposer']}、"
            f"当事者: {', '.join(c['parties'])}、未署名: {', '.join(unsigned) or 'なし'}）"
        )
        for ob in c.get("obligations", []):
            lines.append(f"    - {ob['obligor']} → {ob['counterparty']}: {ob['ob_type']} "
                          f"R{ob['round_num']} {ob.get('details')}")
        lines.append(
            f'    署名するには {{"type": "contract_sign", "contract_id": "{c["contract_id"]}"}} を送る'
        )
    return lines


def _render_my_finance_block(visible_state: dict[str, Any], config: GameConfig) -> list[str]:
    """本人だけの財務通知（§7.2: 現金・2種債務・利息見込み・残り借入枠・今R期限の義務・自己順位）"""
    fin = visible_state.get("my_finance")
    if not fin:
        return []
    lines = [
        "\n## あなたの財務通知（本人のみ）",
        f"  現金: {_man(fin['cash'])}",
        f"  開始前の借金（1.5%）: {_man(fin['debt_pre'])}（{fin['debt_pre_note']}）",
        f"  開始後の借金（3%）: {_man(fin['debt_post'])}",
        f"  借金合計: {_man(fin['total_debt'])}",
        f"  残り借入枠: {_man(fin['remaining_credit'])}",
        f"  今ラウンドの利息見込み: {_man(fin['interest_forecast'])}",
    ]
    obligations_due = fin.get("obligations_due") or []
    votes_due = {d["details"].get("vote") for d in obligations_due if d["ob_type"] == "type_b_vote"}
    if obligations_due:
        lines.append("  今ラウンドが期限の自分の義務:")
        for ob in obligations_due:
            d = ob.get("details") or {}
            if ob["ob_type"] == "type_a_payment":
                lines.append(f"    - {ob['counterparty']}へ{_man(d.get('amount', 0))}支払い")
            elif ob["ob_type"] == "type_b_vote":
                lines.append(
                    f"    - {d.get('vote', '?')}への投票指定（違反時は{ob['counterparty']}へ"
                    f"違約金{_man(config.penalty_amount)}）"
                )
            else:
                lines.append(f"    - 条件付き金銭: {d}")
        if "YES" in votes_due and "NO" in votes_due:
            lines.append(
                "    ※このラウンドにYES指定とNO指定の両方を負っています"
                "（必ずどちらかが違約になります）"
            )
    rank = visible_state.get("my_rank")
    if rank:
        label = f"同率{rank['rank']}位" if rank.get("tied") else f"{rank['rank']}位"
        lines.append(f"  あなたの現在順位: {label} / {rank['n_players']}人")
    return lines


def _render_messages_block(visible_state: dict[str, Any], heading: str) -> list[str]:
    messages = visible_state.get("messages") or []
    if not messages:
        return []
    lines = [f"\n## {heading}"]
    for m in messages:
        if m["type"] == "broadcast":
            lines.append(f"  [{m['from']} 全体] {m['message']}")
        else:
            lines.append(f"  [{m['from']}→{m['to']}] {m['message']}")
    return lines


def _render_memory_block(memory: str | None) -> list[str]:
    """前ラウンドから引き継いだメモを描画する（§9.4、談合カード現行と同じ扱い）"""
    if not memory:
        return []
    return [
        "\n## あなたの記憶（前ラウンドから引き継いだメモ / これが唯一の記憶です）",
        f"  {memory}",
    ]


def build_negotiation_prompt(
    player_state: Any,
    round_num: int,
    turn: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    *,
    memory: str | None = None,
) -> str:
    """Negotiationフェイズ用のユーザープロンプト（§7.1手順2・§9）"""
    lines: list[str] = [f"=== ラウンド{round_num} / 交渉（{turn}巡目） ==="]
    lines.extend(_render_memory_block(memory))
    lines.extend(_render_question_block(visible_state))
    lines.extend(_render_initial_loans_block(visible_state))
    lines.extend(_render_last_round_result_block(visible_state))
    lines.extend(_render_public_ranks_history_block(visible_state))
    lines.extend(_render_my_finance_block(visible_state, config))
    lines.extend(_render_contracts_public_block(visible_state))
    lines.extend(_render_my_contracts_block(visible_state, player_state.player_id, config))
    lines.extend(_render_contracts_pending_block(visible_state, config))
    lines.extend(_render_messages_block(visible_state, "今ラウンドの会話（これまで）"))
    lines.append(
        "\n次のアクションを1つ選び、JSON形式で出力してください"
        "（dm/broadcast/transfer/repay/pass/contract_propose/contract_signのいずれか）。\n"
        + OBJECTIVE_REMINDER_LINE
    )
    return "\n".join(lines)


def build_commit_prompt(
    player_state: Any,
    round_num: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    *,
    memory: str | None = None,
) -> str:
    """Commitフェイズ用のユーザープロンプト（§4.1・§9）"""
    lines: list[str] = [f"=== ラウンド{round_num} / 投票 ==="]
    lines.extend(_render_memory_block(memory))
    lines.extend(_render_question_block(visible_state))
    lines.extend(_render_my_finance_block(visible_state, config))
    lines.extend(_render_my_contracts_block(visible_state, player_state.player_id, config))
    lines.extend(_render_messages_block(visible_state, "このラウンドの交渉内容（参考）"))
    lines.append(
        "\nYESまたはNOに投票し、JSON形式で出力してください:\n"
        '{"strategy": {...}, "action": {"type": "vote_commit", "vote": "YES"}}\n'
        + OBJECTIVE_REMINDER_LINE
    )
    return "\n".join(lines)


def build_reflection_prompt(
    player_state: Any,
    round_num: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    *,
    memory: str | None = None,
) -> str:
    """
    ラウンド終了後の振り返り（引き継ぎメモリ）用のユーザープロンプト（§9.4）

    談合カード現行と同じ扱い: LLMは1-shot呼出しで会話履歴を持たず、次ラウンドへ
    持ち越せるのはここで書く自由記述メモ1枚だけ。
    """
    lines: list[str] = [f"=== ラウンド{round_num} / 振り返り（引き継ぎメモリ） ==="]
    lines.append(
        "このラウンドを終えました。**次のラウンドに持ち越せる記憶はこのメモ1枚だけです。**\n"
        "交渉の会話も、契約の詳細も、ここに書かなければ全て忘れます。"
    )
    lines.extend(_render_memory_block(memory))
    lines.extend(_render_last_round_result_block(visible_state))
    lines.extend(_render_messages_block(visible_state, "今ラウンドの会話（全件）"))
    lines.extend(_render_my_contracts_block(visible_state, player_state.player_id, config))
    lines.extend(_render_contracts_pending_block(visible_state, config))
    lines.extend(_render_my_finance_block(visible_state, config))
    lines.append(
        f"\n次のラウンド以降の自分に残したいことを{MEMORY_MAX_LENGTH}字以内で自由に書いてください。\n"
        "形式は自由（箇条書き・散文・表、何でも構いません）。何を書き、何を書かないかもあなたの判断です。\n"
        "古い情報を現在の事実として書かないでください（例: 「R3時点ではP04と同盟していた」"
        "のように、いつの情報かを明記する）。\n"
        "正式契約の内容は毎ラウンド「あなたが当事者の契約」欄で必ず再提示されます。"
        "契約IDや条項をメモに書き写す必要はありません。\n"
        "このフェイズでは action や strategy を出力しないでください。"
        '出力は次のJSON形式だけです: {"memory": "（ここにメモを書く）"}\n'
        + OBJECTIVE_REMINDER_LINE
    )
    return "\n".join(lines)


def build_post_game_reflection_prompt(
    config: GameConfig,
    post_game_context: dict[str, Any],
    *,
    memory: str | None = None,
) -> str:
    """
    試合後の振り返り用のユーザープロンプト（§9.4）

    手記などの記事づくりにも使う想定（§9.4）。ゲーム結果が完全に確定した後、
    全プレイヤーに1回だけ呼ばれる。
    """
    lines: list[str] = ["=== ゲーム終了 / 振り返り ==="]
    own_rank = post_game_context.get("own_rank")
    if own_rank:
        label = f"同率{own_rank}位" if post_game_context.get("own_rank_tied") else f"{own_rank}位"
        lines.append(f"最終順位: {label} / {config.num_players}人")
    final_assets = post_game_context.get("final_assets")
    if final_assets is not None:
        lines.append(f"最終資産: {_man(final_assets)}")
    lines.extend(_render_memory_block(memory))
    lines.append(
        "\nゲームは終わりました。この結果は変わりません。"
        f"全{config.num_rounds}ラウンドを振り返り、自然文で自由に語ってください"
        "（300字程度）。\n"
        '出力は次のJSON形式だけです: {"emotion": "感情（喜/怒/哀/楽/焦/疑/奸のいずれか）", '
        '"comment": "（ここに自然文の総括コメント）"}'
    )
    return "\n".join(lines)
