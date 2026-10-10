"""
プロンプト生成モジュール（§9・§13）

ルール全文は §13.1 の本文を逐語で使い、承認済みの6行だけを足す（サイクル4.2b、
下記 `RULES_TEMPLATE` のdocstring参照）。数値は GameConfig から差し込む
（`str.format` の `{var}` 記法。§13.1 のJSON例はすでに `{{ }}` で二重エスケープ
されているため、そのまま `str.format` に使える）。
`tests/test_prompt_builder_rules.py` で仕様書ファイルと逐語一致することを固定する。

公開情報・本人限定情報の出し分けは一切ここで再実装しない。
`engine/game.py::Game._build_visible_state()` が §8 のとおり出し分けた
`visible_state` 辞書を、整形して提示するだけにする（秘匿境界はengine側に
1本だけ存在する）。

手番ごとの文面（交渉・投票・ラウンドの終わりの振り返り）は、状態の描き方を
`_render_state_body()` の1本にまとめ、見出しの行と末尾の指示だけを
呼び出し側（`build_negotiation_prompt`/`build_commit_prompt`/
`build_reflection_prompt`）で変える（CLAUDE.md落とし穴④: 同じ形の関数を
複数本に分けると片側だけ直す事故が起きる）。見出しの順番は
`tests/test_prompt_builder.py::PROMPT_COVERAGE` が `visible_state` の
全項目との対応を機械的に固定する。事実だけを書き、助言・例・評価は書かない。

契約の見せ方も事実だけ。環境・利用者に触れないよう求める一文
（`ANONYMIZATION_LINE`）は `llm/providers/devrelay_http.py` と同じ定数を
使う。DevRelay席はprovider側がsystem送信直前に同じ行を付与するため、
`build_system_prompt(..., include_anonymization=False)` で二重付与を
避ける（llm/llm_agent.py::LLMAgentがadapter_typeで判定して渡す）。
"""

from __future__ import annotations

from typing import Any

from engine.config import GameConfig
from llm.constants import MEMORY_MAX_LENGTH
from llm.formatting import pct, yen
from llm.providers.devrelay_http import ANONYMIZATION_LINE
from llm.reasons import reject_reason_ja

__all__ = [
    "ANONYMIZATION_LINE",
    "OBJECTIVE_REMINDER_LINE",
    "RULES_TEMPLATE",
    "LOAN_PROMPT_TEMPLATE",
    "PROMPT_COVERAGE",
    "PROMPT_SUPERSEDED",
    "POST_GAME_COVERAGE",
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

# --- §13.1 ルール要約（正本）。{var} は str.format で差し込む ---
#
# 本文は仕様書 doc/uso8000000_shousuu_ketsu_spec_v0_4_2.md §13.1（799〜923行）を
# 逐語で使い、サイクル4.2のプラン承認時に足した6行だけを加える（ルールは変えない。
# 仕様書の次の版への反映は人間側の作業）。足した位置（承認済み差分）:
#   (a) 「## 契約」の「1つの契約に複数の義務を入れられる…」の次（§13.1 847行の次）
#   (b) 「## 順位」の末尾（§13.1 872行の次）
#   (c) アクション形式の broadcast の次（§13.1 904行の次）
#   (d) 「## 契約」の支払い順の次（§13.1 864行の次）
#   (e) 「退場している間の vote_plan は空文字でよい。」の次（§13.1 900行の次。
#       v0.4は1ラウンドに投票が何回もあり、vote_planがどの投票の予定かが
#       文面に書かれていないため、承認時に追加で指示された6本目の行）
#   (f) contract_propose の注記の末尾（§13.1 919行の次）
# `tests/test_prompt_builder_rules.py::test_rules_template_matches_spec_verbatim_plus_6_lines`
# が、仕様書から抽出した§13.1本文にこの6行を挿入した文字列と本定数が
# 完全一致することを機械的に確かめる（仕様書を直しても文面を直し忘れたら落ちる）。
RULES_TEMPLATE = """# 少数決 ルール

## 目的
- このゲームに脱落はない。全{num_rounds}ラウンドの終了時に、最終資産（現金 − 借金合計）が高いほど上位となる
- 最終資産はマイナスにもなり、その額は順位とともに成績として記録される
- 最も高く評価される結果は総合1位である。自らの判断力、交渉力、戦略性を結果によって示せ
- 順位は個人ごとに決まる。システムが決めるチームや協力目標はない

## ゲーム概要
- プレイヤー{num_players}体が{num_rounds}ラウンドを戦う。1ラウンドの中で投票を何回か行う
- 記号: R2V3 は「ラウンド2の3回目の投票」を指す
- ラウンドの最初に、全員が参加費{entry_fee_man}万円を出す。集まったお金を「山」と呼ぶ
- 投票では、システムが質問を1つ出し、残っている人がYESかNOのどちらかに秘密投票する
- 投票した人数が多い側は、そのラウンドから退場する。少ない側は残り、次の投票へ進む
- 残りが{survivors_max}人以下になったらラウンドは終わり、残った人（勝ち残り）が山を均等に受け取る
- 次のラウンドには、退場した人も含めて全員が戻る
- 質問の正誤は勝敗に一切関係しない。人数だけで決まる

## 投票と判定
- 残っている人は必ず投票する。棄権はない。退場者は投票できない
- 投票ごとの参加費はない
- 決着: 両方の側に1人以上いて人数が違うとき、人数の多い側が退場する
- やり直し: 同数、または全員が同じ側のとき、誰も退場しない。残っている全員が延長料{extension_fee_man}万円を山に入れ、同じ顔ぶれで新しい質問にもう一度投票する。投票番号は1つ進む
- 打ち切り: やり直しが{max_consecutive_ties}回続いたら、そのラウンドは勝ち残りなしで終わる。山は次のラウンドの山に持ち越される。最終ラウンドで打ち切りになった山は消滅する
- やり直しの連続回数は、決着があれば0に戻る。1ラウンドの投票は最大{max_votes_per_round}回
- 退場は脱落ではない。退場者は、そのラウンドの投票ができないだけで、交渉・契約・送金・受け取りは続けられる
- 締切後に、残っている全員の投票先が公開される
- 無効な出力や時間切れの場合は、システムが代わりに投票する（AUTO COMMITとして公示）。その投票に型Bの指定があればそれに従い、指定がない場合や指定が矛盾している場合は無作為に決まる

## 山の支払い
- 山 = 全員の参加費 ＋ 延長料 ＋ 前のラウンドからの持ち越し
- 勝ち残りが1人なら山の全額、2人なら半分ずつを受け取る
- 山の額は常に公開される

## 借金と利息
- ゲーム開始前に{loan_min_man}万〜{loan_max_man}万円の範囲で借入額を選ぶ。借入額=初期現金=初期の借金。全員が同時に決め、決定後に一斉公開される
- 開始前の借金: 毎ラウンド{interest_pct}%の複利。最後まで返済できない
- 開始後の借金: 毎ラウンド{late_interest_pct}%の複利。任意に借りることはできない。支払い（参加費・延長料・契約の支払い・違約金）が手持ちの現金で足りないとき、不足分が自動的に借金になる
- 利息は各ラウンドの最後に1回計上される（投票ごとではない）
- 借金合計の上限は{debt_cap_man}万円。上限に達すると、それ以上は借りられない（残り借入枠 = 上限 − 借金合計。0未満にはならない）
- 例外: 参加費と延長料は、上限を超えていても必ず貸し付けられる。利息によって借金合計が上限を超えることもある
- 上限のために払いきれない支払いは、払える分（現金＋残り借入枠）だけ支払われ、残りは消える（受け取る側は回収できない）
- 返済: 交渉中に repay で返済できるのは開始後の借金だけである。開始前の借金は最後まで返済できない。強制返済はない
- 送金（transfer）は手持ちの現金の範囲内でのみ可能

## 契約（発行料なし）
- 2人以上の当事者全員が署名すると成立する。提案者は提案した時点で署名済みになる。署名がそろわなかった提案は、その投票の締切で失効する
- 公示されるのは、各巡で成立した契約の本数だけである。当事者名と内容は当事者だけが知る
- 1つの契約に複数の義務を入れられる。義務ごとに義務者と相手方を定める。対象にできるのは、現在の投票と、それより後の投票・ラウンド
- 義務者と相手方は、どちらもその契約の当事者（提案者と、with に挙げた相手）でなければならない
- 退場者も契約を提案・署名できる
- 型A（金銭）: 指定ラウンドの終わりに、指定額を相手方へ自動で支払う
- 型B（投票）: 指定した投票（ラウンドと投票番号）でYES（またはNO）へ投票する。指定できるのは義務者自身の投票先のみ。違反すると、義務1本につき違約金{penalty_man}万円を相手方へ自動で支払う。違反者の名前は公示される
  - その投票が行われなかった場合、またはその時点で義務者が退場している場合、義務は失効する（違約金なし）
  - やり直しになった投票でも、違反の判定は行われる
  - 同じ投票にYES指定とNO指定の両方を負った場合、必ずどちらかの違反になる
- 型C（条件付き金銭）: 指定した条件が成立した場合のみ、相手方へ自動で支払う。成立しなければ何も起きない
  - 指定できる条件は次の3種: (1) 指定した投票の少数派がYES（またはNO）になること (2) 指定した投票で特定のプレイヤーが少数派に入ること (3) 指定したラウンドで特定のプレイヤーが勝ち残ること
  - (1)(2)は、やり直しになった投票や行われなかった投票では不成立。(3)は、打ち切りのラウンドでは不成立
  - 金額は固定額で指定する。(3)で対象が義務者自身の場合に限り、「義務者がそのラウンドで受け取った山の何%」という割合でも指定できる
  - 割合の合計が100%を超える約束もできる。その場合、受け取った山より多くを支払うことになる
  - 条件の対象となるプレイヤーは、契約の当事者である必要はない（割合で指定する場合を除く）
  - 契約時点で支払額を所持している必要はない
- 型Bの違約金と条件(1)(2)の支払いは、その投票の決済で行われる。型Aと条件(3)の支払いは、ラウンドの終わりの決済で行われる
- 型A・型C・違約金のいずれも、現金が足りなければ不足分は開始後の借金になる
- 支払える上限は、その決済の時点の現金＋残り借入枠である（ラウンドの終わりの決済では、山を受け取った後の現金）。同じ決済で契約から受け取る予定のお金は、支払いの元手にできない
- 同じ決済で複数の支払いがあり払いきれない場合は、契約が成立した順に支払われる（同じ契約の中では記載順）。途中で尽きた義務は残額だけ支払われ、それより後の義務は0円になる
- 型Bの違約金は、破った型Bの義務が契約に書かれている位置の支払いとして、この順番に入る
- 借金になるのは、同じ決済での受け取りと支払いを差し引いた後に足りない分だけである
- 払いきれなかった者の名前は公示される（金額と相手は非公開）
- 口約束は無料で拘束力がなく、破っても何も起きない。契約は自動で執行され、成立後は取り消せない

## 順位
- 順位は「現金 − 借金合計」で決まる。同額は同順位
- 各投票の開始時に、自分の現在順位が自分にだけ通知される
- R{rank_reveal_round}の終了後に、全員の順位が全員に公開される（名前のみ。金額は出ない）
- 最終ラウンドの終了後に、全員の最終順位と最終資産が公開される

## 進行
ラウンドの開始: 全員が残っている状態に戻る。参加費を支払う。山の額が公開される
1回の投票:
1. Open: 質問、残っている人と退場者、山の額、やり直しの連続回数を公開。本人にのみ財務通知（現金、借金、利息見込み、残り借入枠、自分の義務、自分の順位）
2. Negotiation: 全員（退場者を含む）が参加。毎巡ランダム手番。DM/全体発言/送金(即時決済)/返済/契約の提案・署名/pass。全員連続パスで早期終了。最大の巡は、ラウンド最初の投票が{max_turns_first}、決着の後の投票が{max_turns_next}、やり直しの再投票が{max_turns_retry}
3. Commit: 残っている人がYESかNOを秘密提出
4. Settlement: 票の公開 → 判定（退場、または延長料）→ 型Bの監査 → 型Cの条件判定 → 契約の支払い → 公示
ラウンドの終わり: 山の支払い（打ち切りなら持ち越し）→ 勝ち残りを条件にした型Cと型Aの支払い → 公示 → 利息の計上
提示される記録: このラウンドのこれまでの投票の結果（全員の票、退場者、公示）、これまでのラウンドの結果、このラウンドの各巡で成立した契約の本数は、手番のたびに提示される
会話の記録: 同じラウンドの中の会話（全体発言と、自分が当事者のDM）は、投票が変わっても提示される。ラウンドが変わると前のラウンドの会話は提示されず、ラウンドの終わりに自分で書くメモ1枚だけが引き継がれる
不成立: 形式に合わない行動や、実行できない行動（手持ちを超える送金など）は不成立になる。理由は次の手番に本人にだけ通知される

## 公開情報と秘匿情報
- 公開: 開始前の借入額、山の額と持ち越し額、各投票の質問、残っている人と退場者、全員の投票先（締切後）、勝ち残りと受け取った額、各巡で成立した契約の本数、型B違反者の名前、払いきれなかった者の名前、R{rank_reveal_round}終了時の全員の順位、全体発言、AUTO COMMITの発生
- 秘匿: 現金・借金・残り借入枠、DM、契約の当事者と内容、送金、締切前の投票先、財務通知

## 経済の注意点
- システムからの賞金はない。山は参加者が出したお金だけである
- 利息と、最終ラウンドで消滅した山は場から失われる

## アクション形式（JSON）
出力は必ず以下の形式:
{{"strategy": {{"vote_plan": "YES", "reason": "...", "current_goal": "...", "emotion": "楽"}}, "action": {{"type": "アクション種別", ...}}}}

strategyに必ず"emotion"を含めてください。現在のあなたの感情状態を以下から1つ選択:
"喜"(嬉しい・勝利感) / "怒"(憤り・敵意) / "哀"(落胆・悲観) / "楽"(余裕・楽観) / "焦"(焦り・危機感) / "疑"(疑心・警戒) / "奸"(策略・ニヤリ・してやったり)
退場している間の vote_plan は空文字でよい。
vote_plan には、今の投票で入れるつもりの側を書く。

交渉フェイズのアクション種別:
- {{"type": "dm", "to": "P07", "message": "..."}}
- {{"type": "broadcast", "message": "..."}}
  ※dm と broadcast の message は500字以内。超えた分は切り捨てられる
- {{"type": "transfer", "to": "P07", "amount": 300000}}
- {{"type": "repay", "amount": 500000}}
- {{"type": "pass"}}
- {{"type": "contract_propose", "with": ["P07"], "terms": [
    {{"obligor": "自分のID", "counterparty": "P07", "ob_type": "type_a_payment", "round_num": 2, "details": {{"amount": 300000}}}},
    {{"obligor": "P07", "counterparty": "自分のID", "ob_type": "type_b_vote", "round_num": 2, "vote_num": 1, "details": {{"vote": "NO"}}}},
    {{"obligor": "自分のID", "counterparty": "P07", "ob_type": "type_c_conditional", "round_num": 2, "vote_num": 1,
      "details": {{"amount": 500000, "condition_type": "minority_side", "condition": {{"side": "YES"}}}}}},
    {{"obligor": "自分のID", "counterparty": "P07", "ob_type": "type_c_conditional", "round_num": 2,
      "details": {{"share_percent": 25, "condition_type": "wins_round", "condition": {{"target_player": "自分のID"}}}}}}
  ]}}
  ※ob_type: type_a_payment(金銭支払) / type_b_vote(投票指定) / type_c_conditional(条件付き金銭)
  ※condition_type: minority_side は side(YES/NO)、in_minority と wins_round は target_player を condition に指定
  ※type_b_vote と minority_side・in_minority は round_num と vote_num を指定する。type_a_payment と wins_round は round_num だけを指定する
  ※金額は amount（円）で指定する。wins_round で target_player が義務者自身の場合に限り、amount の代わりに share_percent（1〜100の整数）を指定できる
  ※with には、自分以外の当事者を重複なく書く。提案者は自動で当事者になる
- {{"type": "contract_sign", "contract_id": "..."}}

コミットフェイズのアクション（残っている人のみ）:
- {{"type": "vote_commit", "vote": "YES"}}"""

# --- §13.2 借入額を聞く文面（正本）。ルール要約を読ませた直後に全員へ同時に出す ---
LOAN_PROMPT_TEMPLATE = """ルールは以上である。
ゲーム開始前の借入額を{loan_min_man}万〜{loan_max_man}万円の範囲で選べ。
この借金は最後まで返済できない。全員が同時に決める。他のプレイヤーの借入額は、全員の決定後に一斉に公開される。
出力は必ず以下の形式:
{{"loan_amount": 1200000, "reason": "..."}}"""


def _rules_summary_kwargs(config: GameConfig) -> dict[str, Any]:
    rank_reveal = "・".join(str(r) for r in config.rank_public_rounds)
    return {
        "num_rounds": config.num_rounds,
        "num_players": config.num_players,
        "entry_fee_man": config.entry_fee // 10_000,
        "survivors_max": config.survivors_max,
        "extension_fee_man": config.extension_fee // 10_000,
        "max_consecutive_ties": config.max_consecutive_ties,
        "max_votes_per_round": config.max_votes_per_round,
        "loan_min_man": config.loan_min // 10_000,
        "loan_max_man": config.loan_max // 10_000,
        "interest_pct": pct(config.interest_rate_pre_num, config.interest_rate_pre_den),
        "late_interest_pct": pct(config.interest_rate_post_num, config.interest_rate_post_den),
        "debt_cap_man": config.debt_cap // 10_000,
        "penalty_man": config.penalty_amount // 10_000,
        "rank_reveal_round": rank_reveal,
        "max_turns_first": config.negotiation_max_turns_first,
        "max_turns_next": config.negotiation_max_turns_next,
        "max_turns_retry": config.negotiation_max_turns_retry,
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
    rules = RULES_TEMPLATE.format(**_rules_summary_kwargs(config))
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
    body = LOAN_PROMPT_TEMPLATE.format(**_rules_summary_kwargs(config))
    return f"{body}\n\n{OBJECTIVE_REMINDER_LINE}"


# --- visible_state を整形する共通ヘルパー（§8の出し分けはengine/game.py側で完了済み） ---
#
# 見出し → visible_stateの元項目の対応表（横断テスト用）。
# `tests/test_prompt_builder.py` が、実ゲームの`_build_visible_state()`の
# キー集合がこの表（PROMPT_COVERAGEとPROMPT_SUPERSEDEDの和）と完全一致する
# ことを機械的に確かめる（対応の無い項目を足し忘れたら落ちる）。
PROMPT_COVERAGE: dict[str, str] = {
    "round_num": "=== R",
    "vote_num": "=== R",
    "negotiation_max_turns": "この投票の上限",
    "question": "## 今の場面",
    "pot": "## 今の場面",
    "remaining_ids": "## 今の場面",
    "eliminated_ids": "## 今の場面",
    "consecutive_ties": "## 今の場面",
    "my_last_action_error": "## 直前の行動の結果",
    "my_finance": "## あなたの財務通知（本人のみ）",
    "my_rank": "## あなたの財務通知（本人のみ）",
    "initial_loans": "## 開始前の借入額（全員公開）",
    "public_ranks_history": "## 公開された順位（名前のみ）",
    "round_vote_results": "## このラウンドの投票結果（これまで）",
    "round_results": "## これまでのラウンドの結果",
    "round_contract_counts": "## このラウンドで成立した契約の本数（巡ごと）",
    "my_contracts": "## あなたが当事者の契約（成立済み）",
    "contracts_pending": "## 署名待ちの契約提案",
    "messages": "## このラウンドの会話",
}

# last_vote_result/last_round_resultは、round_vote_results/round_resultsが
# 同じ事実を上位集合として含むため、文面には別途出さない（bots/follow_bot.py
# が読む互換キーとしてvisible_state自体には残る）。
PROMPT_SUPERSEDED: dict[str, str] = {
    "last_vote_result": "round_vote_results",
    "last_round_result": "round_results",
}

# post_game_contextの項目 → 試合後の振り返りの文面のどこに出るか（横断テスト用）
POST_GAME_COVERAGE: dict[str, str] = {
    "own_rank": "最終順位",
    "own_rank_tied": "最終順位",
    "final_assets": "あなたの最終資産",
    "round_results": "## これまでのラウンドの結果",
    "all_final_ranks": "## 全員の最終順位と最終資産",
    "all_final_assets": "## 全員の最終順位と最終資産",
}


def _render_memory_block(memory: str | None) -> list[str]:
    """引き継ぎのメモを描画する（§9.4。無ければ事実として「まだない」と書く）"""
    lines = ["\n## 引き継ぎのメモ"]
    if not memory:
        lines.append("  （まだありません。ラウンドの終わりに自分で書きます）")
    else:
        lines.append(f"  {memory}")
    return lines


def _render_situation_block(visible_state: dict[str, Any], player_id: str) -> list[str]:
    """今の場面（§8: 質問・山・残っている人・退場者・やり直しの連続回数・自分の在不在）"""
    remaining_ids = visible_state["remaining_ids"]
    eliminated_ids = visible_state["eliminated_ids"]
    round_num = visible_state["round_num"]
    lines = [
        "\n## 今の場面",
        f"  質問: 「{visible_state['question']}」（質問の正誤は勝敗に関係しない）",
        f"  山: {yen(visible_state['pot'])}",
        f"  残っている人（{len(remaining_ids)}人）: {', '.join(remaining_ids) or 'なし'}",
        f"  退場者（{len(eliminated_ids)}人）: {', '.join(eliminated_ids) or 'なし'}",
        f"  やり直しの連続回数: {visible_state['consecutive_ties']}",
    ]
    if player_id in eliminated_ids:
        lines.append(
            f"  あなた（{player_id}）はこのラウンド（R{round_num}）で退場しています。"
            "このラウンドの投票はできません。次のラウンドには戻ります。"
        )
    return lines


def _render_last_action_error_block(visible_state: dict[str, Any], config: GameConfig) -> list[str]:
    """直前の行動の結果（§7.5: 不成立の理由を日本語で本人にだけ渡す）"""
    err = visible_state.get("my_last_action_error")
    lines = ["\n## 直前の行動の結果"]
    if err is None:
        lines.append("  まだありません")
    else:
        lines.append(f"  不成立: {reject_reason_ja(err, config)}")
    return lines


def _render_obligations_due_lines(obligations_due: list[dict[str, Any]], config: GameConfig) -> list[str]:
    """財務通知の「自分の義務」欄（本人が義務者の義務のみ、obligorキーは持たない）"""
    if not obligations_due:
        return ["  この投票・このラウンドを対象にした自分の義務: なし"]
    lines = ["  この投票・このラウンドを対象にした自分の義務:"]
    votes_due: set[str] = set()
    for ob in obligations_due:
        d = ob.get("details") or {}
        rn, vn = ob["round_num"], ob.get("vote_num")
        label = f"R{rn}V{vn}" if vn is not None else f"R{rn}"
        if ob["ob_type"] == "type_a_payment":
            lines.append(f"    - {label}: {ob['counterparty']}へ{yen(d.get('amount', 0))}支払い")
        elif ob["ob_type"] == "type_b_vote":
            votes_due.add(str(d.get("vote")))
            lines.append(
                f"    - {label}: {d.get('vote', '?')}への投票指定"
                f"（違反時は{ob['counterparty']}へ違約金{yen(config.penalty_amount)}）"
            )
        else:
            if "share_percent" in d:
                amount_text = f"受け取った山の{d['share_percent']}%"
            else:
                amount_text = yen(d.get("amount", 0))
            lines.append(f"    - {label}: 条件付きで{ob['counterparty']}へ{amount_text}支払い")
    if "YES" in votes_due and "NO" in votes_due:
        lines.append("    ※YES指定とNO指定の両方を負っています（必ずどちらかが違約になります）")
    return lines


def _render_my_finance_block(visible_state: dict[str, Any], config: GameConfig) -> list[str]:
    """あなたの財務通知（本人のみ。§7.5。現在順位を含む、承認済み決定3）"""
    fin = visible_state.get("my_finance")
    lines = ["\n## あなたの財務通知（本人のみ）"]
    if not fin:
        lines.append("  （本人以外には届きません）")
        return lines
    pre_pct = pct(config.interest_rate_pre_num, config.interest_rate_pre_den)
    post_pct = pct(config.interest_rate_post_num, config.interest_rate_post_den)
    lines.extend([
        f"  現金: {yen(fin['cash'])}",
        f"  開始前の借金（{pre_pct}%・返済不可）: {yen(fin['debt_pre'])}",
        f"  開始後の借金（{post_pct}%）: {yen(fin['debt_post'])}",
        f"  借金合計: {yen(fin['total_debt'])}",
        f"  残り借入枠: {yen(fin['remaining_credit'])}",
        f"  今ラウンドの利息見込み: {yen(fin['interest_forecast'])}",
    ])
    lines.extend(_render_obligations_due_lines(fin.get("obligations_due") or [], config))
    rank = visible_state.get("my_rank")
    if rank:
        label = f"同率{rank['rank']}位" if rank.get("tied") else f"{rank['rank']}位"
        lines.append(f"  あなたの現在順位: {label} / {rank['n_players']}人")
    return lines


def _render_initial_loans_block(visible_state: dict[str, Any]) -> list[str]:
    """開始前の借入額（全員公開。§3.1）"""
    loans = visible_state.get("initial_loans") or {}
    lines = ["\n## 開始前の借入額（全員公開）"]
    if not loans:
        lines.append("  まだ公開されていません")
        return lines
    for pid, amount in sorted(loans.items()):
        lines.append(f"  {pid}: {yen(amount)}")
    return lines


def _render_public_ranks_history_block(visible_state: dict[str, Any], config: GameConfig) -> list[str]:
    """公開された順位（名前のみ。§7.6）"""
    history = visible_state.get("public_ranks_history") or {}
    lines = ["\n## 公開された順位（名前のみ）"]
    if not history:
        rank_reveal = "・".join(str(r) for r in config.rank_public_rounds)
        lines.append(f"  まだ公開されていません（R{rank_reveal}の終了後に公開されます）")
        return lines
    for rn in sorted(history.keys()):
        ranks = history[rn]
        ordered = sorted(ranks.items(), key=lambda kv: (kv[1], kv[0]))
        formatted = ", ".join(f"{pid}:{r}位" for pid, r in ordered)
        lines.append(f"  R{rn}終了時: {formatted}")
    return lines


_VOTE_RESULT_LABEL = {"decisive": "決着", "retry": "やり直し", "abort": "打ち切り"}


def _render_vote_result_lines(v: dict[str, Any]) -> list[str]:
    """
    1回の投票の結果を事実だけで描画する（§8.1）

    round_vote_results・round_results[].votesの両方で同じ形を使う
    （CLAUDE.md落とし穴④: engine側の_vote_result_view()と対になる1本）。
    """
    label = _VOTE_RESULT_LABEL[v["result"]]
    yes_ids, no_ids = v["yes_ids"], v["no_ids"]
    lines = [
        f"R{v['round_num']}V{v['vote_num']} {label}: "
        f"YES {len(yes_ids)}人（{', '.join(yes_ids) or 'なし'}） / "
        f"NO {len(no_ids)}人（{', '.join(no_ids) or 'なし'}）",
    ]
    if v["result"] == "decisive":
        lines.append(f"  退場: {', '.join(v['eliminated_ids']) or 'なし'}")
        lines.append(f"  残り: {', '.join(v['remaining_ids']) or 'なし'}")
        lines.append(f"  少数派: {v.get('minority_side') or '不明'}")
    else:
        lines.append(f"  延長料: {yen(v['extension_fee_collected'])}")
    lines.append(f"  やり直しの連続回数: {v['consecutive_ties_after']}")
    lines.append(
        f"  型B違反者: {', '.join(v['type_b_violator_ids']) or 'なし'} / "
        f"払いきれなかった者: {', '.join(v['payment_shortfall_ids']) or 'なし'} / "
        f"AUTO COMMIT: {', '.join(v['auto_commit_ids']) or 'なし'}",
    )
    return lines


def _render_round_vote_results_block(visible_state: dict[str, Any]) -> list[str]:
    """このラウンドの投票結果（これまで。§8.1 受け入れ#52）"""
    results = visible_state.get("round_vote_results") or []
    lines = ["\n## このラウンドの投票結果（これまで）"]
    if not results:
        lines.append("  まだありません")
        return lines
    for v in results:
        lines.extend(f"  {ln}" for ln in _render_vote_result_lines(v))
    return lines


def _render_round_results_block(visible_state: dict[str, Any]) -> list[str]:
    """これまでのラウンドの結果（§8.1 受け入れ#53。各投票の結果を含む）"""
    results = visible_state.get("round_results") or []
    lines = ["\n## これまでのラウンドの結果"]
    if not results:
        lines.append("  まだ終わったラウンドはありません")
        return lines
    for r in results:
        if r["aborted"]:
            if r["destroyed_pot"]:
                lines.append(f"  R{r['round_num']}: 打ち切り。山{yen(r['destroyed_pot'])}は消滅")
            else:
                lines.append(f"  R{r['round_num']}: 打ち切り。山{yen(r['carryover_out'])}を次のラウンドへ持ち越し")
        else:
            winners = ", ".join(r["winner_ids"])
            lines.append(f"  R{r['round_num']}: 勝ち残り {winners}（1人あたり{yen(r['payout_per_winner'])}）")
        if r.get("round_settlement_shortfall_ids"):
            lines.append(f"    払いきれなかった者（ラウンドの精算）: {', '.join(r['round_settlement_shortfall_ids'])}")
        for v in r.get("votes") or []:
            lines.extend(f"    {ln}" for ln in _render_vote_result_lines(v))
    return lines


def _render_round_contract_counts_block(visible_state: dict[str, Any]) -> list[str]:
    """このラウンドで成立した契約の本数（巡ごと。§8.1 受け入れ#51）"""
    counts = visible_state.get("round_contract_counts") or []
    round_num = visible_state["round_num"]
    lines = ["\n## このラウンドで成立した契約の本数（巡ごと）"]
    if not counts:
        lines.append("  まだありません")
        return lines
    for c in counts:
        lines.append(f"  R{round_num}V{c['vote_num']} {c['turn']}巡目: {c['count']}本")
    return lines


def _obligation_fact_sentence(ob: dict[str, Any], *, is_mine: bool, config: GameConfig) -> str:
    """
    義務1件を事実だけの文にする（助言は書かない。§9「自分の義務は『あなたは…』の形」）

    is_mine=True: 自分が義務者（obligor）。is_mine=False: 相手が義務者（受け取る側の権利）。
    """
    subject = "あなたは" if is_mine else f"{ob['obligor']}は"
    ob_type = ob["ob_type"]
    d = ob.get("details") or {}
    rn, vn = ob["round_num"], ob.get("vote_num")
    label = f"R{rn}V{vn}" if vn is not None else f"R{rn}"
    if ob_type == "type_a_payment":
        target = ob["counterparty"] if is_mine else "あなた"
        return f"{label}: {subject} {target}へ{yen(d.get('amount', 0))}支払わなければならない"
    if ob_type == "type_b_vote":
        penalty = yen(config.penalty_amount)
        who_target = ob["counterparty"] if is_mine else "あなた"
        return (
            f"{label}: {subject} {d.get('vote', '?')}へ投票しなければならない"
            f"（違反時は{who_target}へ違約金{penalty}）"
        )
    # type_c_conditional
    target = ob["counterparty"] if is_mine else "あなた"
    condition_type = d.get("condition_type")
    condition = d.get("condition") or {}
    if "share_percent" in d:
        amount_text = f"受け取った山の{d['share_percent']}%"
    else:
        amount_text = yen(d.get("amount", 0))
    if condition_type == "minority_side":
        cond_text = f"{label}の少数派が{condition.get('side', '?')}になった場合"
    elif condition_type == "in_minority":
        cond_text = f"{label}で{condition.get('target_player', '?')}が少数派に入った場合"
    else:  # wins_round
        cond_text = f"R{rn}で{condition.get('target_player', '?')}が勝ち残った場合"
    return f"{cond_text}、{subject} {target}へ{amount_text}支払わなければならない"


def _render_my_contracts_block(
    visible_state: dict[str, Any], player_id: str, config: GameConfig,
) -> list[str]:
    """あなたが当事者の契約（成立済み。§9: 助言は書かない）"""
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


def _render_contracts_pending_block(
    visible_state: dict[str, Any], player_id: str, config: GameConfig,
) -> list[str]:
    """
    署名待ちの契約提案（§9: contract_id・内容・署名の方法を必ず当事者に見せる）

    visible_stateは for_player_id が当事者の提案だけを contracts_pending に
    含んでいる（engine/game.py側で絞り込み済み）。
    """
    pending = visible_state.get("contracts_pending") or []
    lines = ["\n## 署名待ちの契約提案"]
    if not pending:
        lines.append("  なし")
        return lines
    for c in pending:
        unsigned = [p for p in c["parties"] if p not in c["signed_by"]]
        lines.append(
            f"  契約{c['contract_id']}（R{c['round_created']}V{c['vote_created']}提案、"
            f"提案者: {c['proposer']}、当事者: {', '.join(c['parties'])}、"
            f"未署名: {', '.join(unsigned) or 'なし'}）",
        )
        for ob in c.get("obligations", []):
            is_mine = ob["obligor"] == player_id
            lines.append(f"    - {_obligation_fact_sentence(ob, is_mine=is_mine, config=config)}")
        lines.append(
            f'    署名するには {{"type": "contract_sign", "contract_id": "{c["contract_id"]}"}} を送る',
        )
    return lines


def _render_messages_block(visible_state: dict[str, Any]) -> list[str]:
    """このラウンドの会話（どの投票の何巡目の発言かが分かる形。§9.4）"""
    round_num = visible_state["round_num"]
    messages = visible_state.get("messages") or []
    lines = ["\n## このラウンドの会話"]
    if not messages:
        lines.append("  まだありません")
        return lines
    for m in messages:
        tag = f"[R{round_num}V{m.get('vote_num')} {m.get('turn')}巡目]"
        if m["type"] == "broadcast":
            lines.append(f"  {tag} {m['from']} 全体: {m['message']}")
        else:
            lines.append(f"  {tag} {m['from']} → {m['to']}: {m['message']}")
    return lines


def _render_state_body(
    visible_state: dict[str, Any], player_id: str, config: GameConfig, memory: str | None,
) -> list[str]:
    """
    手番ごとの文面の共通本体（承認済み決定3の見出し順）。

    交渉・投票（Commit）・ラウンドの終わりの振り返りは、見出しの行と
    末尾の指示だけを呼び出し側で変え、本体の描画はここに1本化する。
    """
    lines: list[str] = []
    lines.extend(_render_memory_block(memory))
    lines.extend(_render_situation_block(visible_state, player_id))
    lines.extend(_render_last_action_error_block(visible_state, config))
    lines.extend(_render_my_finance_block(visible_state, config))
    lines.extend(_render_initial_loans_block(visible_state))
    lines.extend(_render_public_ranks_history_block(visible_state, config))
    lines.extend(_render_round_vote_results_block(visible_state))
    lines.extend(_render_round_results_block(visible_state))
    lines.extend(_render_round_contract_counts_block(visible_state))
    lines.extend(_render_my_contracts_block(visible_state, player_id, config))
    lines.extend(_render_contracts_pending_block(visible_state, player_id, config))
    lines.extend(_render_messages_block(visible_state))
    return lines


def build_negotiation_prompt(
    player_state: Any,
    round_num: int,
    vote_num: int,
    turn: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    *,
    memory: str | None = None,
) -> str:
    """Negotiationフェイズ用のユーザープロンプト（§7.2手順2・§9）"""
    max_turns = visible_state.get("negotiation_max_turns")
    heading = f"=== R{round_num}V{vote_num} / 交渉（{turn}巡目 / この投票の上限{max_turns}巡） ==="
    lines: list[str] = [heading]
    lines.extend(_render_state_body(visible_state, player_state.player_id, config, memory))
    lines.append(
        "\n次のアクションを1つ選び、JSON形式で出力してください"
        "（dm/broadcast/transfer/repay/pass/contract_propose/contract_signのいずれか）。\n"
        + OBJECTIVE_REMINDER_LINE,
    )
    return "\n".join(lines)


def build_commit_prompt(
    player_state: Any,
    round_num: int,
    vote_num: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    *,
    memory: str | None = None,
) -> str:
    """Commitフェイズ用のユーザープロンプト（§4.2・§9）"""
    heading = f"=== R{round_num}V{vote_num} / 投票（Commit） ==="
    lines: list[str] = [heading]
    lines.extend(_render_state_body(visible_state, player_state.player_id, config, memory))
    lines.append(
        "\nYESまたはNOに投票し、JSON形式で出力してください:\n"
        '{"strategy": {...}, "action": {"type": "vote_commit", "vote": "YES"}}\n'
        + OBJECTIVE_REMINDER_LINE,
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
    ラウンド終了後の振り返り（引き継ぎメモ）用のユーザープロンプト（§9.4）

    LLMは1-shot呼出しで会話履歴を持たず、次ラウンドへ持ち越せるのはここで
    書く自由記述メモ1枚だけ。契約・投票結果・ラウンドの結果・順位は手番の
    たびに提示されるので、メモに書き写す必要はないことを事実として伝える
    （承認済み決定8）。
    """
    heading = f"=== R{round_num} / 振り返り（引き継ぎメモ） ==="
    lines: list[str] = [heading]
    lines.extend(_render_state_body(visible_state, player_state.player_id, config, memory))
    lines.append(
        f"\nこのラウンドを終えました。次のラウンド以降の自分に残したいことを"
        f"{MEMORY_MAX_LENGTH}字以内で自由に書いてください。\n"
        "形式は自由（箇条書き・散文・表、何でも構いません）。何を書き、何を書かないかもあなたの判断です。\n"
        "古い情報を現在の事実として書かないでください（例: 「R3時点ではP04と同盟していた」"
        "のように、いつの情報かを明記する）。\n"
        "契約・投票結果・ラウンドの結果・順位は手番のたびに提示されるので、書き写す必要はありません。\n"
        "このフェイズでは action や strategy を出力しないでください。"
        '出力は次のJSON形式だけです: {"memory": "（ここにメモを書く）"}\n'
        + OBJECTIVE_REMINDER_LINE,
    )
    return "\n".join(lines)


def _render_all_final_ranks_and_assets_block(post_game_context: dict[str, Any]) -> list[str]:
    """全員の最終順位と最終資産（§7.6。最終ラウンドの終了後に全員へ公開される）"""
    ranks = post_game_context.get("all_final_ranks") or []
    assets = post_game_context.get("all_final_assets") or {}
    lines = ["\n## 全員の最終順位と最終資産"]
    if not ranks:
        lines.append("  まだありません")
        return lines
    for r in ranks:
        pid = r["player_id"]
        label = f"同率{r['rank']}位" if r.get("tied") else f"{r['rank']}位"
        lines.append(f"  {label}: {pid}（{yen(assets.get(pid, 0))}）")
    return lines


def build_post_game_reflection_prompt(
    config: GameConfig,
    post_game_context: dict[str, Any],
    *,
    memory: str | None = None,
) -> str:
    """
    試合後の振り返り用のユーザープロンプト（§9.4）

    手記などの記事づくりにも使う想定（§9.4）。ゲーム結果が完全に確定した後、
    全プレイヤーに1回だけ呼ばれる。承認済み決定9により、これまでの全ラウンド
    の結果と、全員の最終順位・最終資産（本人の分だけでなく全員分）も渡す。
    """
    lines: list[str] = ["=== ゲーム終了 / 振り返り ==="]
    own_rank = post_game_context.get("own_rank")
    if own_rank:
        label = f"同率{own_rank}位" if post_game_context.get("own_rank_tied") else f"{own_rank}位"
        lines.append(f"あなたの最終順位: {label} / {config.num_players}人")
    final_assets = post_game_context.get("final_assets")
    if final_assets is not None:
        lines.append(f"あなたの最終資産: {yen(final_assets)}")
    lines.extend(_render_memory_block(memory))
    lines.extend(_render_round_results_block(post_game_context))
    lines.extend(_render_all_final_ranks_and_assets_block(post_game_context))
    lines.append(
        "\nゲームは終わりました。この結果は変わりません。"
        f"全{config.num_rounds}ラウンドを振り返り、自然文で自由に語ってください"
        "（300字程度）。\n"
        '出力は次のJSON形式だけです: {"emotion": "感情（喜/怒/哀/楽/焦/疑/奸のいずれか）", '
        '"comment": "（ここに自然文の総括コメント）"}',
    )
    return "\n".join(lines)
