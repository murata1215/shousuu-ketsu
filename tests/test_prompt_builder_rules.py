"""
ルール全文・借入文面の逐語一致テスト（§13）

`llm/prompt_builder.py::RULES_TEMPLATE`/`LOAN_PROMPT_TEMPLATE` が、仕様書
`doc/uso8000000_shousuu_ketsu_spec_v0_4_3.md` §13.1/§13.2 の本文と1行も
違わないことを機械的に確かめる。仕様書を書き換えても文面を直し忘れたら落ちる。

サイクル4.2までは「承認済みの6行」を仕様書に足して比較していたが、v0.4.3の
§13.1がその6行を吸収したため、差分の表（_APPROVED_ADDITIONS）は廃止し、
単純な逐語一致に戻した（サイクル4.2c）。
"""

from __future__ import annotations

import re
from pathlib import Path

from llm.prompt_builder import LOAN_PROMPT_TEMPLATE, RULES_TEMPLATE

_SPEC_PATH = Path(__file__).resolve().parent.parent / "doc" / "uso8000000_shousuu_ketsu_spec_v0_4_3.md"


def _extract_fenced_block(spec_text: str, heading_pattern: str) -> str:
    """仕様書の指定見出しの後、最初に現れる ```text ... ``` ブロックの中身を取り出す"""
    m = re.search(heading_pattern + r"\n.*?```text\n(.*?)\n```", spec_text, re.DOTALL)
    assert m is not None, f"仕様書に見出しパターン {heading_pattern!r} が見つからない"
    return m.group(1)


def test_rules_template_matches_spec_verbatim() -> None:
    spec_text = _SPEC_PATH.read_text(encoding="utf-8")
    body = _extract_fenced_block(spec_text, r"### 13\.1 ルール要約")
    assert body == RULES_TEMPLATE


def test_loan_prompt_template_matches_spec_verbatim() -> None:
    spec_text = _SPEC_PATH.read_text(encoding="utf-8")
    body = _extract_fenced_block(spec_text, r"### 13\.2 借入額を聞く文面[^\n]*")
    assert body == LOAN_PROMPT_TEMPLATE
