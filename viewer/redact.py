"""
伏せる処理（サイクル3.0新設）

DevRelay席の応答には、たまにメールアドレス・実行環境のパス・利用者名が
混ざる（CLAUDE.md「過去の落とし穴」②のhttpx系とは別件だが、同じ「他プロジェクト
／実行環境の情報が出力に混ざる」という事故の型）。画面に出す前に必ず通す。

キー自体を欠落させる方式（gentei-janken `viewer/log_parser.py`のDM本文方式）とは
別に、ここは「値の文字列を書き換える」方式を取る——発言・内心メモ・振り返りは
本文そのものが表示対象であり、キーごと消すと機能が失われるため。
"""

from __future__ import annotations

import getpass
import re
from typing import Any

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# /home/<name>/... ・ /Users/<name>/... ・ C:\Users\<name>\...
_UNIX_HOME_RE = re.compile(r"/(home|Users)/[^/\s]+(/[^\s'\"]*)?")
_WIN_HOME_RE = re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+(\\\\[^\s'\"]*)?")

_MASK = "[伏せ字]"


def _os_username() -> str:
    """OS利用者名（例: uso8m）。取得できなければ空文字"""
    try:
        return getpass.getuser()
    except Exception:
        return ""


_OS_USERNAME = _os_username()


def redact_text(text: str | None) -> str | None:
    """文字列中のメール・パス・利用者名を伏せる。Noneはそのまま返す

    利用者名は\\b等の単語境界ではなく単純な部分文字列一致で置換する
    （PythonのUnicode正規表現では日本語文字も\\wに含まれ、
    「ユーザーuso8mが」のように隣が日本語だと\\bが境界と認識しないため）。
    """
    if text is None:
        return None
    out = _EMAIL_RE.sub(_MASK, text)
    out = _UNIX_HOME_RE.sub(_MASK, out)
    out = _WIN_HOME_RE.sub(_MASK, out)
    if _OS_USERNAME and len(_OS_USERNAME) >= 3:
        out = out.replace(_OS_USERNAME, _MASK)
    return out


def redact_value(value: Any) -> Any:
    """文字列ならredact_text、dict/listなら再帰的に処理する。それ以外はそのまま返す"""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value


def redact_payload(payload: Any) -> Any:
    """APIレスポンス全体（dict/list/文字列）に再帰的にredact_valueを適用する公開関数"""
    return redact_value(payload)
