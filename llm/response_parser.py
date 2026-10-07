"""
レスポンス解析モジュール（§9.2・§9.3・§13.2）

dangou-card `llm/response_parser.py`（B分類）のJSON抽出・多段サルベージ・
memory/emotion抽出・是正メッセージ生成はゲーム非依存のためほぼ逐語で流用し、
`_convert_action`（少数決の8アクション）・`_validate_type_c_term_shape`
（condition_type を minority_side / in_minority の2種へ）だけを少数決向けに
新規実装した。

出力形式は §13.1「アクション形式（JSON）」のとおり、談合カード現行と同じ
`{"strategy": {...}, "action": {...}}` の2部構成（`strategy.emotion` を含む）。
借入額（§13.2）は別形式 `{"loan_amount": int, "reason": str}` のため
`parse_loan_amount()` を分けて提供する。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from engine.models import (
    Action, BroadcastAction, ContractProposeAction, ContractSignAction,
    DmAction, PassAction, RepayAction, TransferAction, Vote, VoteCommitAction,
)
from llm.constants import MEMORY_MAX_LENGTH

VALID_EMOTIONS = {"喜", "怒", "哀", "楽", "焦", "疑", "奸"}


class ParseError(Exception):
    """JSON解析エラー（是正メッセージ付き）"""

    def __init__(self, message: str, correction_hint: str) -> None:
        super().__init__(message)
        self.correction_hint = correction_hint


def extract_json(text: str, _repaired: bool = False) -> dict[str, Any] | None:
    """
    レスポンスからJSONオブジェクトを抽出する（dangou-card/gentei-jankenから移植、ゲーム非依存）

    対応パターン:
    1. ```json ... ``` 完全なコードブロック
    1b. ```json ... （閉じフェンスなし＝打ち切られた応答）
    2. { で始まる生JSON
    3. テキスト中の最初の { ... } ペア
    4. 上記が全て失敗した場合、末尾の壊れ方を1回だけ補修して同じ4パターンを再試行する
       （_repaired ガードで無限再帰を防ぐ）
    """
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    m = re.search(r'```(?:json)?\s*\n?(.+)', text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        depth = 0
        last_close = -1
        for i, ch in enumerate(candidate):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    last_close = i
        if last_close >= 0:
            try:
                return json.loads(candidate[: last_close + 1])
            except json.JSONDecodeError:
                pass

    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    if not _repaired:
        repaired = _repair_trailing_garbage(text)
        if repaired != text:
            return extract_json(repaired, _repaired=True)

    return None


_TRAILING_GARBAGE_RE = re.compile(r'"\s*\)+\s*(?=[,}\]])')


def _repair_trailing_garbage(text: str) -> str:
    """閉じクォート直後に混入した余分な ")" を除去する（dangou-card由来の既知の壊れ方）"""
    return _TRAILING_GARBAGE_RE.sub('"', text)


def normalize_emotion(strategy: dict[str, Any]) -> dict[str, Any]:
    """emotionフィールドが未指定/不正なら削除する（寛容な扱い。リトライ対象にしない）"""
    emotion = strategy.get("emotion")
    if emotion not in VALID_EMOTIONS:
        strategy.pop("emotion", None)
    return strategy


def extract_memory(text: str) -> str | None:
    """
    LLM応答から次ラウンドへ引き継ぐメモ（memoryフィールド、§9.4）を取り出す

    他プレイヤーの可視状態・イベント・プロンプトには渡らない。本人の次ラウンドの
    プロンプトにのみ再注入される（llm/llm_agent.py::LLMAgent._memory）。
    """
    data = extract_json(text)
    if data is None:
        return None
    memory = data.get("memory")
    if not isinstance(memory, str) or not memory.strip():
        return None
    return memory.strip()[:MEMORY_MAX_LENGTH]


def parse_response(text: str, player_id: str, phase: str) -> tuple[dict[str, Any] | None, Action]:
    """
    §13.1のアクション形式 {"strategy": {...}, "action": {...}} を解析する

    Args:
        text: LLMの生応答テキスト
        player_id: 応答したプレイヤーのID（Actionのplayer_idに補う）
        phase: "negotiation" | "commit"（投票はcommitでのみ有効、交渉中のvote_commitは拒否）

    Returns:
        (strategy辞書 または None, Action)

    Raises:
        ParseError: JSON抽出失敗・actionキー欠落・action_type不正・必須フィールド欠落の場合
    """
    data = extract_json(text)
    if data is None:
        raise ParseError(
            "JSONが見つかりません",
            '回答はJSON形式で返してください。例: {"strategy": {...}, "action": {"type": "pass"}}',
        )

    strategy = data.get("strategy")
    action_data = data.get("action")
    if action_data is None:
        if "type" in data:
            action_data = data
        else:
            raise ParseError(
                "actionキーが見つかりません",
                '回答に"action"キーを含めてください。例: {"strategy": {...}, "action": {"type": "pass"}}',
            )

    if isinstance(strategy, dict):
        strategy = normalize_emotion(strategy)

    action = _convert_action(action_data, player_id, phase)
    return strategy, action


def parse_loan_amount(text: str) -> int:
    """
    §13.2の借入額応答 {"loan_amount": int, "reason": "..."} を解析する

    Raises:
        ParseError: JSON抽出失敗・loan_amountが正の整数でない場合
    """
    data = extract_json(text)
    if data is None:
        raise ParseError(
            "JSONが見つかりません",
            '回答はJSON形式で返してください。例: {"loan_amount": 1200000, "reason": "..."}',
        )
    amount = data.get("loan_amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
        raise ParseError(
            f"loan_amountが不正: {amount!r}",
            "loan_amountは正の整数（円）で指定してください。",
        )
    return int(amount)


_VALID_OB_TYPES = {"type_a_payment", "type_b_vote", "type_c_conditional"}
_VALID_CONDITION_TYPES = {"minority_side", "in_minority"}


def _validate_type_c_term_shape(i: int, term: dict[str, Any]) -> None:
    """
    type_c_conditional termの形（details/condition の構造）だけを検証する（§6.4/§9.3）

    round_num範囲・target_playerの実在性チェックは、このparser層には
    現在ラウンド・プレイヤー一覧の文脈が無いため行わない
    （engine/actions.py::validate_action が担当する）。
    """
    details = term.get("details")
    if not isinstance(details, dict):
        raise ParseError(
            f"terms[{i}]のdetailsが辞書ではありません",
            'type_c_conditionalのdetailsは{"amount": int, "condition_type": '
            '"minority_side"|"in_minority", "condition": {...}} の形式である必要があります',
        )
    amount = details.get("amount")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ParseError(
            f"terms[{i}]のtype_c_conditional amountが不正: {amount!r}",
            "amountは正の整数である必要があります",
        )
    condition_type = details.get("condition_type")
    if condition_type not in _VALID_CONDITION_TYPES:
        raise ParseError(
            f"terms[{i}]のcondition_typeが無効: {condition_type!r}",
            f"有効なcondition_type: {', '.join(sorted(_VALID_CONDITION_TYPES))}",
        )
    condition = details.get("condition")
    if not isinstance(condition, dict):
        raise ParseError(
            f"terms[{i}]のconditionが辞書ではありません",
            "conditionは{side}（minority_side）または{target_player}（in_minority）の"
            "辞書である必要があります",
        )
    if condition_type == "minority_side":
        side = condition.get("side")
        if side not in ("YES", "NO"):
            raise ParseError(
                f"terms[{i}]のcondition.sideが無効: {side!r}",
                'condition_type=minority_sideにはcondition={"side": "YES"|"NO"}が必要です',
            )
    else:  # in_minority
        if not isinstance(condition.get("target_player"), str) or not condition.get("target_player"):
            raise ParseError(
                f"terms[{i}]のconditionにtarget_playerが不足",
                'condition_type=in_minorityにはcondition={"target_player": "P03"}が必要です',
            )


def _convert_action(data: dict[str, Any], player_id: str, phase: str) -> Action:
    """actionデータをengine Action型に変換する（§9.2）"""
    action_type = data.get("type", "")

    if action_type == "vote_commit" and phase == "negotiation":
        raise ParseError(
            "交渉フェイズではvote_commitは使えません。投票はコミットフェイズで行います",
            "交渉フェイズではdm/broadcast/transfer/repay/pass/contract_propose/"
            'contract_signを選択してください。例: {"strategy": {...}, "action": {"type": "pass"}}',
        )

    if action_type == "pass":
        return PassAction(player_id=player_id)

    if action_type == "dm":
        return DmAction(
            player_id=player_id,
            to=str(data.get("to", "")),
            message=str(data.get("message", ""))[:500],
        )

    if action_type == "broadcast":
        return BroadcastAction(
            player_id=player_id,
            message=str(data.get("message", ""))[:500],
        )

    if action_type == "transfer":
        amount = data.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            raise ParseError(
                "transferにamount（整数）が必要です",
                '例: {"type": "transfer", "to": "P07", "amount": 300000}',
            )
        return TransferAction(player_id=player_id, to=str(data.get("to", "")), amount=int(amount))

    if action_type == "repay":
        amount = data.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            raise ParseError(
                "repayにamount（整数）が必要です",
                '例: {"type": "repay", "amount": 500000}',
            )
        return RepayAction(player_id=player_id, amount=int(amount))

    if action_type == "vote_commit":
        vote_raw = str(data.get("vote", "")).strip().upper()
        if vote_raw not in ("YES", "NO"):
            raise ParseError(
                f"voteが無効: {data.get('vote')!r}",
                '例: {"type": "vote_commit", "vote": "YES"}',
            )
        return VoteCommitAction(player_id=player_id, vote=Vote(vote_raw))

    if action_type == "contract_propose":
        terms = data.get("terms")
        with_players = data.get("with", data.get("with_players", []))
        if not terms or not isinstance(terms, list):
            raise ParseError(
                "contract_proposeにtermsリストが必要です",
                '例: {"type": "contract_propose", "with": ["P07"], "terms": '
                '[{"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment", '
                '"round_num": 5, "details": {"amount": 500000}}]}',
            )
        if not isinstance(with_players, list) or not with_players:
            raise ParseError(
                "contract_proposeにwith（相手プレイヤーのリスト）が必要です",
                '例: {"type": "contract_propose", "with": ["P07"], "terms": [...]}',
            )
        required_keys = {"obligor", "counterparty", "ob_type", "round_num"}
        for i, term in enumerate(terms):
            if not isinstance(term, dict):
                raise ParseError(
                    f"terms[{i}]が辞書ではありません",
                    "各termは{obligor, counterparty, ob_type, round_num, details}の辞書である必要があります",
                )
            missing = required_keys - set(term.keys())
            if missing:
                raise ParseError(
                    f"terms[{i}]にキーが不足: {', '.join(sorted(missing))}",
                    f"各termには{', '.join(sorted(required_keys))}が必要です",
                )
            ob_type = term.get("ob_type")
            if ob_type not in _VALID_OB_TYPES:
                raise ParseError(
                    f"terms[{i}]のob_typeが無効: {ob_type!r}",
                    f"有効なob_type: {', '.join(sorted(_VALID_OB_TYPES))}",
                )
            if not isinstance(term.get("round_num"), int) or isinstance(term.get("round_num"), bool):
                raise ParseError(
                    f"terms[{i}]のround_numが整数ではありません",
                    "round_numは整数で指定してください",
                )
            details = term.get("details")
            if ob_type == "type_a_payment":
                if not isinstance(details, dict) or not isinstance(details.get("amount"), int) \
                        or isinstance(details.get("amount"), bool) or details.get("amount") <= 0:
                    raise ParseError(
                        f"terms[{i}]のtype_a_payment detailsが不正",
                        'type_a_paymentのdetailsは{"amount": 正の整数} の形式である必要があります',
                    )
            elif ob_type == "type_b_vote":
                vote = (details or {}).get("vote") if isinstance(details, dict) else None
                if not isinstance(vote, str) or vote.strip().upper() not in ("YES", "NO"):
                    raise ParseError(
                        f"terms[{i}]のtype_b_vote detailsが不正",
                        'type_b_voteのdetailsは{"vote": "YES"|"NO"} の形式である必要があります',
                    )
            else:  # type_c_conditional
                _validate_type_c_term_shape(i, term)

        try:
            return ContractProposeAction(
                player_id=player_id, with_players=[str(p) for p in with_players], terms=terms,
            )
        except ValidationError as e:
            raise ParseError(
                f"contract_proposeの形式が不正です: {e}",
                "with/termsの形式を仕様に合わせて修正してください",
            ) from e

    if action_type == "contract_sign":
        contract_id = data.get("contract_id")
        if not isinstance(contract_id, str) or not contract_id:
            raise ParseError(
                "contract_signにcontract_idが必要です",
                '例: {"type": "contract_sign", "contract_id": "C_XXXXXXXX"}',
            )
        return ContractSignAction(player_id=player_id, contract_id=contract_id)

    raise ParseError(
        f"不明なaction type: {action_type!r}",
        "typeは dm/broadcast/transfer/repay/pass/contract_propose/contract_sign/"
        "vote_commit のいずれかにしてください",
    )


def make_correction_message(error: ParseError) -> str:
    """リトライ用の是正指示メッセージを生成する"""
    return f"前回の回答にエラーがありました: {error}\n修正してください: {error.correction_hint}"


# --- 試合後の振り返り（§9.4）---

_COMMENT_FIELD_RE = re.compile(r'"comment"\s*:\s*"((?:[^"\\]|\\.)*)"')
_COMMENT_FIELD_OPEN_RE = re.compile(r'"comment"\s*:\s*"(.*)', re.DOTALL)


def _salvage_string(fragment: str) -> str:
    """壊れたJSON文字列断片からベストエフォートでテキストを復元する"""
    try:
        return json.loads(f'"{fragment}"')
    except json.JSONDecodeError:
        return (
            fragment.replace('\\"', '"').replace("\\n", "\n")
            .replace("\\t", "\t").replace("\\\\", "\\")
        )


def parse_post_game_reflection(text: str | None, max_chars: int) -> dict[str, Any]:
    """
    試合後の振り返り（§9.4 POST_GAME_REFLECTION）応答を解析する

    談合カード現行の多段サルベージ方針を踏襲する: 通常のparse_response()と違い、
    疑わしい応答でも空にせず、最後は素の散文でも受理する（本人の最後の言葉を
    失わないため。守るべき旧値が無いreflectionならではの安全側の設計）。

    Returns:
        {"status", "comment", "comment_chars", "truncated", "salvaged"}
        status: "ok" | "ok_recovered" | "ok_plaintext" | "empty"
    """
    stripped = (text or "").strip()
    if not stripped:
        return {"status": "empty", "comment": None, "comment_chars": 0, "truncated": False, "salvaged": False}

    data = extract_json(stripped)
    comment: str | None = None
    status: str | None = None

    if isinstance(data, dict):
        raw_comment = data.get("comment")
        if isinstance(raw_comment, str) and raw_comment.strip():
            comment = raw_comment
            status = "ok"

    salvaged = False
    if comment is None:
        m = _COMMENT_FIELD_RE.search(stripped)
        if m:
            comment = _salvage_string(m.group(1))
        else:
            m = _COMMENT_FIELD_OPEN_RE.search(stripped)
            if m:
                comment = _salvage_string(m.group(1))
        if comment and comment.strip():
            status = "ok_recovered"
            salvaged = True

    if comment is None:
        comment = stripped
        status = "ok_plaintext"

    comment = comment.strip()
    truncated = len(comment) > max_chars
    if truncated:
        comment = comment[:max_chars]

    return {
        "status": status, "comment": comment or None, "comment_chars": len(comment or ""),
        "truncated": truncated, "salvaged": salvaged,
    }
