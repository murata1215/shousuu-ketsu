"""
不成立の理由の日本語化のテスト（§7.5、サイクル4.2）

engine/actions.py::RejectReason・engine/contracts.py::TermRejectReason の
全種類に日本語テンプレートが用意されていることを機械的に確かめる
（CLAUDE.md「エンジンが返しうる理由の全種類に日本語の文を用意し、用意のない
理由が英語のままAIに渡らないことをテストで固定する」）。
"""

import re

import pytest

from engine.actions import RejectReason
from engine.config import GameConfig
from engine.contracts import TermRejectReason
from llm.reasons import REASON_JA, reject_reason_ja

_CONFIG = GameConfig.default_12()

# 桁区切りの無い5桁以上の「…円」が無いこと（E2）を確かめる正規表現
_UNFORMATTED_YEN_RE = re.compile(r"(?<!,)(?<!\d)\d{5,}円")


def test_every_reject_reason_has_japanese() -> None:
    """RejectReason・TermRejectReasonの全メンバがREASON_JAに存在する"""
    all_codes = {r.value for r in RejectReason} | {r.value for r in TermRejectReason}
    assert all_codes == set(REASON_JA)


def test_reject_reason_ja_returns_none_for_none() -> None:
    assert reject_reason_ja(None, _CONFIG) is None


def test_reject_reason_ja_raises_on_unknown_code() -> None:
    """用意のない理由コードが日本語化されずに素通りすることを防ぐ（KeyError）"""
    with pytest.raises(KeyError):
        reject_reason_ja({"code": "no_such_reason", "params": {}, "message_en": "x"}, _CONFIG)


def test_no_post_debt_uses_config_interest_rates_not_hardcoded() -> None:
    """E1: NO_POST_DEBTの利率は設定値から差し込まれ、既定以外の設定でも正しい"""
    err = {"code": RejectReason.NO_POST_DEBT.value, "params": {}, "message_en": "x"}
    custom_config = GameConfig(interest_rate_pre_num=7, interest_rate_pre_den=100,
                                interest_rate_post_num=20, interest_rate_post_den=100)
    text = reject_reason_ja(err, custom_config)
    assert text is not None
    assert "20%" in text
    assert "7%" in text
    assert "15%" not in text
    assert "5%" not in text


def test_transfer_insufficient_cash_amounts_have_comma_separators() -> None:
    """E2: 送金額・現金の金額が桁区切りで表示される"""
    err = {
        "code": RejectReason.TRANSFER_INSUFFICIENT_CASH.value,
        "params": {"amount": 1_000_000_000, "cash": 200_000},
        "message_en": "x",
    }
    text = reject_reason_ja(err, _CONFIG)
    assert text is not None
    assert "1,000,000,000円" in text
    assert "200,000円" in text
    assert not _UNFORMATTED_YEN_RE.search(text)


@pytest.mark.parametrize("code", [r.value for r in RejectReason])
def test_action_reason_renders_without_japanese_or_english_leaking(code: str) -> None:
    """
    各RejectReasonのテンプレートが、ダミーのparamsで例外なくフォーマットでき、
    英語の理由文（message_en）そのものが日本語の出力に混ざらないことを確かめる
    """
    dummy_params = {
        "to": "P07", "amount": 100, "cash": 50, "pid": "P07", "cid": "C_TEST",
        "t": "unknown_type",
    }
    err = {"code": code, "params": dummy_params, "message_en": "Cannot DM self"}
    text = reject_reason_ja(err, _CONFIG)
    assert text is not None
    assert "Cannot DM self" not in text
    assert all(ord(ch) < 128 for ch in text) is False  # 日本語の文字を含む
    assert not _UNFORMATTED_YEN_RE.search(text)


@pytest.mark.parametrize("code", [r.value for r in TermRejectReason])
def test_term_reason_renders_without_english_leaking(code: str) -> None:
    dummy_params = {
        "index": 1, "value": "x", "pid": "P07", "lo": 1, "hi": 4,
        "round_num": 1, "vote_num": 2, "condition_type": "minority_side",
        "obligor": "P01",
    }
    err = {"code": code, "params": dummy_params, "message_en": "Unknown ob_type"}
    text = reject_reason_ja(err, _CONFIG)
    assert text is not None
    assert "Unknown ob_type" not in text
    assert not _UNFORMATTED_YEN_RE.search(text)
