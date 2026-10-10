"""
不成立の理由を日本語の文に変換するモジュール（§7.5、受け入れ#57）

エンジン（`engine/actions.py::RejectReason`・`engine/contracts.py::
TermRejectReason`）が返す理由はコード（英語の短い識別子）と、テンプレートに
差し込む値（params）の組である。本モジュールはそのコードから日本語の文を
作る。言い回しは事実だけを書き、戦い方の助言は書かない（§7.5）。

REASON_JA に無いコードを渡すと `KeyError` で落ちる（エンジンが返しうる
理由の全種類に日本語の文を用意していることを、テストで固定する。
`tests/test_reasons.py::test_every_reject_reason_has_japanese` 参照）。
"""

from __future__ import annotations

from typing import Any

from engine.actions import RejectReason
from engine.contracts import TermRejectReason

# --- engine/actions.py::RejectReason（16種） ---
_ACTION_REASON_JA: dict[str, str] = {
    RejectReason.DM_TO_SELF.value: "DMの宛先が自分自身でした。自分以外のプレイヤーIDを指定してください。",
    RejectReason.UNKNOWN_TARGET.value: "宛先の{to}というプレイヤーはいません。",
    RejectReason.TRANSFER_AMOUNT_NOT_POSITIVE.value: (
        "送金額が{amount}でした。送金額は1円以上の整数で指定してください。"
    ),
    RejectReason.TRANSFER_TO_SELF.value: "送金の宛先が自分自身でした。",
    RejectReason.TRANSFER_INSUFFICIENT_CASH.value: (
        "送金額{amount}円に対して、現金が{cash}円しかありません。"
        "送金できるのは現金の範囲内だけです（借入枠は使えません）。"
    ),
    RejectReason.REPAY_AMOUNT_NOT_POSITIVE.value: (
        "返済額が{amount}でした。返済額は1円以上の整数で指定してください。"
    ),
    RejectReason.NO_POST_DEBT.value: (
        "開始後の借金（15%）が0円のため、返済するものがありません。"
        "開始前の借金（5%）は最後まで返済できません。"
    ),
    RejectReason.WITH_EMPTY.value: "契約の相手（with）が空でした。相手を1人以上指定してください。",
    RejectReason.WITH_INCLUDES_PROPOSER.value: (
        "契約の相手（with）に自分自身が入っていました。提案者は自動で当事者になります。"
    ),
    RejectReason.WITH_DUPLICATED.value: "契約の相手（with）に同じプレイヤーIDが重複していました。",
    RejectReason.UNKNOWN_COUNTERPARTY.value: "契約の相手の{pid}というプレイヤーはいません。",
    RejectReason.CONTRACT_NOT_FOUND.value: (
        "契約{cid}は存在しません。署名できるのは「署名待ちの契約提案」に"
        "出ている契約IDだけです。"
    ),
    RejectReason.NOT_A_PARTY.value: "契約{cid}の当事者ではないため、署名できません。",
    RejectReason.NOT_AWAITING_SIGNATURES.value: (
        "契約{cid}は署名待ちではありません（すでに成立、または失効しています）。"
    ),
    RejectReason.ALREADY_SIGNED.value: "契約{cid}にはすでに署名しています。",
    RejectReason.UNKNOWN_ACTION_TYPE.value: (
        "アクション種別の{t}はありません。使えるのは "
        "dm / broadcast / transfer / repay / pass / contract_propose / contract_sign です。"
    ),
}

# --- engine/contracts.py::TermRejectReason（26種、{i}本目の義務） ---
_TERM_REASON_JA: dict[str, str] = {
    TermRejectReason.TERMS_EMPTY.value: "契約の条項（terms）が空でした。義務を1本以上入れてください。",
    TermRejectReason.DETAILS_NOT_DICT.value: (
        "{index}本目の義務の details が辞書ではありませんでした（{value}）。"
    ),
    TermRejectReason.OBLIGOR_NOT_PARTY.value: (
        "{index}本目の義務の義務者{pid}が、この契約の当事者ではありません。"
        "義務者と相手方は、どちらも当事者（自分と with に挙げた相手）でなければなりません。"
    ),
    TermRejectReason.COUNTERPARTY_NOT_PARTY.value: (
        "{index}本目の義務の相手方{pid}が、この契約の当事者ではありません。"
        "義務者と相手方は、どちらも当事者でなければなりません。"
    ),
    TermRejectReason.OBLIGOR_EQUALS_COUNTERPARTY.value: (
        "{index}本目の義務で、義務者と相手方が同じ{pid}になっています。"
    ),
    TermRejectReason.ROUND_NUM_NOT_INT.value: (
        "{index}本目の義務の round_num が整数ではありませんでした（{value}）。"
    ),
    TermRejectReason.ROUND_NUM_OUT_OF_RANGE.value: (
        "{index}本目の義務の round_num が{value}でした。"
        "指定できるのは現在のR{lo}からR{hi}までです。過ぎたラウンドは指定できません。"
    ),
    TermRejectReason.VOTE_NUM_NOT_POSITIVE_INT.value: (
        "{index}本目の義務の vote_num が{value}でした。1以上の整数で指定してください。"
    ),
    TermRejectReason.VOTE_NUM_IN_PAST.value: (
        "{index}本目の義務の対象がR{round_num}V{value}でした。"
        "現在はR{round_num}V{vote_num}で、過ぎた投票は指定できません。"
    ),
    TermRejectReason.TYPE_A_WITH_VOTE_NUM.value: (
        "{index}本目の型A（type_a_payment）に vote_num が付いていました。"
        "型Aは round_num だけを指定します。"
    ),
    TermRejectReason.TYPE_A_AMOUNT_NOT_POSITIVE.value: (
        "{index}本目の型Aの amount が{value}でした。1円以上の整数で指定してください。"
    ),
    TermRejectReason.TYPE_B_REQUIRES_VOTE_NUM.value: (
        "{index}本目の型B（type_b_vote）に vote_num がありませんでした。"
        "型Bは round_num と vote_num の両方を指定します。"
    ),
    TermRejectReason.TYPE_B_VOTE_INVALID.value: (
        "{index}本目の型Bの vote が{value}でした。YES か NO を指定してください。"
    ),
    TermRejectReason.CONDITION_TYPE_INVALID.value: (
        "{index}本目の型Cの condition_type が{value}でした。"
        "指定できるのは minority_side / in_minority / wins_round です。"
    ),
    TermRejectReason.WINS_ROUND_WITH_VOTE_NUM.value: (
        "{index}本目の wins_round に vote_num が付いていました。"
        "wins_round は round_num だけを指定します。"
    ),
    TermRejectReason.CONDITION_REQUIRES_VOTE_NUM.value: (
        "{index}本目の{condition_type}には vote_num が必要です。"
        "round_num と vote_num の両方を指定します。"
    ),
    TermRejectReason.AMOUNT_AND_SHARE_BOTH.value: (
        "{index}本目の型Cに amount と share_percent の両方がありました。"
        "どちらか一方だけを指定してください。"
    ),
    TermRejectReason.AMOUNT_OR_SHARE_MISSING.value: (
        "{index}本目の型Cに amount も share_percent もありませんでした。"
        "どちらか一方を指定してください。"
    ),
    TermRejectReason.CONDITION_NOT_DICT.value: (
        "{index}本目の型Cの condition が辞書ではありませんでした（{value}）。"
    ),
    TermRejectReason.MINORITY_SIDE_INVALID.value: (
        "{index}本目の minority_side の condition.side が{value}でした。"
        "YES か NO を指定してください。"
    ),
    TermRejectReason.SHARE_PERCENT_ONLY_WINS_ROUND.value: (
        "{index}本目で share_percent を{condition_type}に使っていました。"
        "割合で指定できるのは wins_round だけです。"
    ),
    TermRejectReason.TYPE_C_AMOUNT_NOT_POSITIVE.value: (
        "{index}本目の型Cの amount が{value}でした。1円以上の整数で指定してください。"
    ),
    TermRejectReason.TARGET_PLAYER_INVALID.value: (
        "{index}本目の{condition_type}の condition.target_player が{value}でした。"
        "存在するプレイヤーIDを指定してください。"
    ),
    TermRejectReason.SHARE_PERCENT_TARGET_NOT_SELF.value: (
        "{index}本目で share_percent の対象が{value}でした。割合で指定できるのは、"
        "義務者自身（{obligor}）が勝ち残る場合だけです。"
    ),
    TermRejectReason.SHARE_PERCENT_OUT_OF_RANGE.value: (
        "{index}本目の share_percent が{value}でした。1〜100の整数で指定してください。"
    ),
    TermRejectReason.UNKNOWN_OB_TYPE.value: (
        "{index}本目の ob_type が{value}でした。"
        "使えるのは type_a_payment / type_b_vote / type_c_conditional です。"
    ),
}

REASON_JA: dict[str, str] = {**_ACTION_REASON_JA, **_TERM_REASON_JA}
"""理由コード（文字列） -> 日本語テンプレート（str.format用）の全対応表"""


def reject_reason_ja(err: dict[str, Any] | None) -> str | None:
    """
    `visible_state["my_last_action_error"]`（{"code","params","message_en"}）を
    日本語の文に変換する（§7.5）。

    Args:
        err: None、または {"code": str, "params": dict, "message_en": str}

    Returns:
        err が None なら None。それ以外は日本語の文（戦い方の助言は含まない）。

    Raises:
        KeyError: codeに対応する日本語テンプレートが無い場合（用意漏れを
            検出するため、素通りさせない。CLAUDE.md過去の落とし穴①と同じ
            「既定値を持たず、未対応なら止まる」方針）。
    """
    if err is None:
        return None
    code = err.get("code")
    template = REASON_JA[code]
    return template.format(**err.get("params", {}))
