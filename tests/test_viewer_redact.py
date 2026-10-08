"""
viewer/redact.py のテスト（サイクル3.0）

メールアドレス・実行環境のパス・利用者名が、画面に出す前に確実に伏せられることを
機械的に確認する。DevRelay席の応答に混ざることがあるため（§データの扱い）。
"""

import getpass

from viewer.redact import redact_payload, redact_text, redact_value


def test_redact_text_masks_email() -> None:
    assert "taro@example.com" not in redact_text("連絡はtaro@example.comまで")
    assert "[伏せ字]" in redact_text("連絡はtaro@example.comまで")


def test_redact_text_masks_unix_home_path() -> None:
    out = redact_text("/home/testuser/shousuu-ketsu で検証した")
    assert "/home/testuser" not in out
    assert "[伏せ字]" in out


def test_redact_text_masks_username_standalone() -> None:
    username = getpass.getuser()
    out = redact_text(f"ユーザー{username}が実行した")
    assert username not in out


def test_redact_text_none_passthrough() -> None:
    assert redact_text(None) is None


def test_redact_text_leaves_normal_text_untouched() -> None:
    normal = "R1はNOで揃えよう。少数派を狙う。"
    assert redact_text(normal) == normal


def test_redact_value_recurses_into_dict_and_list() -> None:
    value = {
        "message": "連絡はtaro@example.comまで",
        "nested": {"path": "/home/testuser/shousuu-ketsu"},
        "list": ["/home/testuser/x", "通常の文字列"],
        "number": 12345,
        "none": None,
    }
    out = redact_value(value)
    assert "taro@example.com" not in out["message"]
    assert "/home/testuser" not in out["nested"]["path"]
    assert "/home/testuser" not in out["list"][0]
    assert out["list"][1] == "通常の文字列"
    assert out["number"] == 12345
    assert out["none"] is None


def test_redact_payload_is_alias_for_redact_value() -> None:
    assert redact_payload("taro@example.com") == redact_text("taro@example.com")
