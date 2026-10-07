"""
応答の読み取りのテスト（§9.2・§9.3・§13.2、AIを呼ばない）

各アクションと借入額が正しく読めること、壊れた出力は無効（ParseError）として
扱われることを確認する。
"""

import json

import pytest

from engine.models import (
    BroadcastAction, ContractProposeAction, ContractSignAction, DmAction,
    PassAction, RepayAction, TransferAction, Vote, VoteCommitAction,
)
from llm.response_parser import (
    ParseError, extract_json, extract_memory, make_correction_message,
    parse_loan_amount, parse_post_game_reflection, parse_response,
)


def _wrap(action: dict, strategy: dict | None = None) -> str:
    payload = {"strategy": strategy or {"reason": "test", "emotion": "楽"}, "action": action}
    return json.dumps(payload, ensure_ascii=False)


# --- 各アクションが正しく読める ---

def test_parse_pass():
    _, action = parse_response(_wrap({"type": "pass"}), "P01", "negotiation")
    assert isinstance(action, PassAction)
    assert action.player_id == "P01"


def test_parse_dm():
    _, action = parse_response(_wrap({"type": "dm", "to": "P07", "message": "やあ"}), "P01", "negotiation")
    assert isinstance(action, DmAction)
    assert action.to == "P07"
    assert action.message == "やあ"


def test_parse_broadcast():
    _, action = parse_response(_wrap({"type": "broadcast", "message": "こんにちは"}), "P01", "negotiation")
    assert isinstance(action, BroadcastAction)
    assert action.message == "こんにちは"


def test_parse_transfer():
    _, action = parse_response(
        _wrap({"type": "transfer", "to": "P07", "amount": 300000}), "P01", "negotiation",
    )
    assert isinstance(action, TransferAction)
    assert action.to == "P07"
    assert action.amount == 300000


def test_parse_repay():
    _, action = parse_response(_wrap({"type": "repay", "amount": 500000}), "P01", "negotiation")
    assert isinstance(action, RepayAction)
    assert action.amount == 500000


def test_parse_vote_commit_yes():
    _, action = parse_response(_wrap({"type": "vote_commit", "vote": "YES"}), "P01", "commit")
    assert isinstance(action, VoteCommitAction)
    assert action.vote == Vote.YES


def test_parse_vote_commit_lowercase_normalized():
    _, action = parse_response(_wrap({"type": "vote_commit", "vote": "no"}), "P01", "commit")
    assert action.vote == Vote.NO


def test_parse_vote_commit_rejected_in_negotiation_phase():
    with pytest.raises(ParseError):
        parse_response(_wrap({"type": "vote_commit", "vote": "YES"}), "P01", "negotiation")


def test_parse_contract_propose_type_a():
    terms = [
        {"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
         "round_num": 5, "details": {"amount": 300000}},
    ]
    _, action = parse_response(
        _wrap({"type": "contract_propose", "with": ["P07"], "terms": terms}), "P01", "negotiation",
    )
    assert isinstance(action, ContractProposeAction)
    assert action.with_players == ["P07"]
    assert action.terms == terms


def test_parse_contract_propose_type_b_vote():
    terms = [
        {"obligor": "P07", "counterparty": "P01", "ob_type": "type_b_vote",
         "round_num": 5, "details": {"vote": "NO"}},
    ]
    _, action = parse_response(
        _wrap({"type": "contract_propose", "with": ["P07"], "terms": terms}), "P01", "negotiation",
    )
    assert isinstance(action, ContractProposeAction)


def test_parse_contract_propose_type_c_minority_side():
    terms = [
        {"obligor": "P01", "counterparty": "P07", "ob_type": "type_c_conditional", "round_num": 5,
         "details": {"amount": 500000, "condition_type": "minority_side", "condition": {"side": "YES"}}},
    ]
    _, action = parse_response(
        _wrap({"type": "contract_propose", "with": ["P07"], "terms": terms}), "P01", "negotiation",
    )
    assert isinstance(action, ContractProposeAction)


def test_parse_contract_propose_type_c_in_minority():
    terms = [
        {"obligor": "P07", "counterparty": "P01", "ob_type": "type_c_conditional", "round_num": 5,
         "details": {"amount": 500000, "condition_type": "in_minority", "condition": {"target_player": "P03"}}},
    ]
    _, action = parse_response(
        _wrap({"type": "contract_propose", "with": ["P07"], "terms": terms}), "P01", "negotiation",
    )
    assert isinstance(action, ContractProposeAction)


def test_parse_contract_sign():
    _, action = parse_response(
        _wrap({"type": "contract_sign", "contract_id": "C_ABC12345"}), "P01", "negotiation",
    )
    assert isinstance(action, ContractSignAction)
    assert action.contract_id == "C_ABC12345"


def test_parse_strategy_with_valid_emotion_preserved():
    strategy, _ = parse_response(
        _wrap({"type": "pass"}, strategy={"reason": "r", "emotion": "喜"}), "P01", "negotiation",
    )
    assert strategy["emotion"] == "喜"


def test_parse_strategy_with_invalid_emotion_dropped():
    strategy, _ = parse_response(
        _wrap({"type": "pass"}, strategy={"reason": "r", "emotion": "不明"}), "P01", "negotiation",
    )
    assert "emotion" not in strategy


# --- 借入額（§13.2） ---

def test_parse_loan_amount():
    text = json.dumps({"loan_amount": 1200000, "reason": "安全に"}, ensure_ascii=False)
    assert parse_loan_amount(text) == 1200000


def test_parse_loan_amount_rejects_negative():
    text = json.dumps({"loan_amount": -1, "reason": "x"})
    with pytest.raises(ParseError):
        parse_loan_amount(text)


def test_parse_loan_amount_rejects_missing():
    text = json.dumps({"reason": "x"})
    with pytest.raises(ParseError):
        parse_loan_amount(text)


def test_parse_loan_amount_rejects_non_numeric():
    text = json.dumps({"loan_amount": "たくさん"})
    with pytest.raises(ParseError):
        parse_loan_amount(text)


# --- 壊れた出力は無効として扱われる ---

def test_broken_json_raises_parse_error():
    with pytest.raises(ParseError):
        parse_response("これはJSONではありません", "P01", "negotiation")


def test_missing_action_key_raises_parse_error():
    text = json.dumps({"strategy": {"reason": "x"}})
    with pytest.raises(ParseError):
        parse_response(text, "P01", "negotiation")


def test_unknown_action_type_raises_parse_error():
    with pytest.raises(ParseError):
        parse_response(_wrap({"type": "market_commit"}), "P01", "negotiation")


def test_contract_propose_missing_terms_raises_parse_error():
    with pytest.raises(ParseError):
        parse_response(_wrap({"type": "contract_propose", "with": ["P07"]}), "P01", "negotiation")


def test_contract_propose_invalid_ob_type_raises_parse_error():
    terms = [{"obligor": "P01", "counterparty": "P07", "ob_type": "market_bet",
              "round_num": 5, "details": {"amount": 1}}]
    with pytest.raises(ParseError):
        parse_response(
            _wrap({"type": "contract_propose", "with": ["P07"], "terms": terms}), "P01", "negotiation",
        )


def test_contract_propose_type_c_bad_condition_type_raises_parse_error():
    terms = [{"obligor": "P01", "counterparty": "P07", "ob_type": "type_c_conditional", "round_num": 5,
              "details": {"amount": 1, "condition_type": "market_winner", "condition": {}}}]
    with pytest.raises(ParseError):
        parse_response(
            _wrap({"type": "contract_propose", "with": ["P07"], "terms": terms}), "P01", "negotiation",
        )


def test_contract_sign_missing_contract_id_raises_parse_error():
    with pytest.raises(ParseError):
        parse_response(_wrap({"type": "contract_sign"}), "P01", "negotiation")


def test_transfer_missing_amount_raises_parse_error():
    with pytest.raises(ParseError):
        parse_response(_wrap({"type": "transfer", "to": "P07"}), "P01", "negotiation")


def test_vote_commit_invalid_vote_raises_parse_error():
    with pytest.raises(ParseError):
        parse_response(_wrap({"type": "vote_commit", "vote": "MAYBE"}), "P01", "commit")


def test_make_correction_message_contains_hint():
    err = ParseError("壊れています", "こうしてください")
    msg = make_correction_message(err)
    assert "壊れています" in msg
    assert "こうしてください" in msg


# --- JSON抽出の多段サルベージ ---

def test_extract_json_from_fenced_code_block():
    text = '```json\n{"type": "pass"}\n```'
    assert extract_json(text) == {"type": "pass"}


def test_extract_json_from_plain_text_with_prefix():
    text = 'ここに回答します: {"type": "pass"} 以上です'
    assert extract_json(text) == {"type": "pass"}


def test_extract_json_returns_none_for_garbage():
    assert extract_json("completely not json at all") is None


def test_extract_memory_returns_none_for_missing_field():
    assert extract_memory('{"type": "pass"}') is None


def test_extract_memory_returns_value():
    assert extract_memory('{"memory": "次は気をつける"}') == "次は気をつける"


# --- 試合後の振り返り（§9.4） ---

def test_parse_post_game_reflection_normal():
    text = json.dumps({"comment": "面白かった", "emotion": "楽"}, ensure_ascii=False)
    result = parse_post_game_reflection(text, max_chars=100)
    assert result["status"] == "ok"
    assert result["comment"] == "面白かった"


def test_parse_post_game_reflection_empty_text():
    result = parse_post_game_reflection(None, max_chars=100)
    assert result["status"] == "empty"
    assert result["comment"] is None


def test_parse_post_game_reflection_plaintext_fallback():
    result = parse_post_game_reflection("JSONではないが何か言いたい", max_chars=100)
    assert result["status"] == "ok_plaintext"
    assert result["comment"] == "JSONではないが何か言いたい"


def test_parse_post_game_reflection_truncates_long_comment():
    long_comment = "あ" * 200
    text = json.dumps({"comment": long_comment}, ensure_ascii=False)
    result = parse_post_game_reflection(text, max_chars=50)
    assert result["truncated"] is True
    assert len(result["comment"]) == 50
