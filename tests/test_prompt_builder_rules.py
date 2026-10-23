"""
ルール全文・借入文面の逐語一致テスト（§13）

`llm/prompt_builder.py::RULES_TEMPLATE`/`LOAN_PROMPT_TEMPLATE` が、仕様書
`doc/uso8000000_shousuu_ketsu_spec_v0_5.md` §13.1/§13.2 の本文と1行も
違わないことを機械的に確かめる。仕様書を書き換えても文面を直し忘れたら落ちる。

サイクル4.2までは「承認済みの6行」を仕様書に足して比較していたが、v0.4.3の
§13.1がその6行を吸収したため、差分の表（_APPROVED_ADDITIONS）は廃止し、
単純な逐語一致に戻した（サイクル4.2c）。

サイクル7.0（けいすけさん決定6・第2戦18体対応）: 投票がmax_votes_per_round回に
達しても決着しない場合の打ち切りルールをRULES_TEMPLATEに追加したが、仕様書
v0.5.1はClaudeが別途書く予定のため、本サイクルではdoc/*.mdを改版しない
（けいすけさん指示）。4.2c以前の「承認済みの追加分」の仕組みを
_PENDING_SPEC_ADDITIONSとして一時的に復活させ、仕様書本文に未反映のこの1行
だけを許容する。v0.5.1が書かれたら、このテーブルと一緒に元の単純一致へ戻す。
"""

from __future__ import annotations

import re
from pathlib import Path

from llm.prompt_builder import LOAN_PROMPT_TEMPLATE, RULES_TEMPLATE

_SPEC_PATH = Path(__file__).resolve().parent.parent / "doc" / "uso8000000_shousuu_ketsu_spec_v0_5.md"

# サイクル7.0: 仕様書v0.5 §13.1にはまだ無いが、RULES_TEMPLATE側には
# すでに反映した行（old→newの対応）。v0.5.1が書かれたらこのテーブルを空にする。
_PENDING_SPEC_ADDITIONS: dict[str, str] = {
    "- やり直しの連続回数は、決着があれば0に戻る。1ラウンドの投票は最大{max_votes_per_round}回": (
        "- やり直しの連続回数は、決着があれば0に戻る。1ラウンドの投票は最大"
        "{max_votes_per_round}回。この回数に達しても決着しなければ、同じ扱い"
        "（勝ち残りなしで終わり、山は次のラウンドに持ち越し。最終ラウンドなら消滅）"
        "でそのラウンドは打ち切りになる"
    ),
}


def _extract_fenced_block(spec_text: str, heading_pattern: str) -> str:
    """仕様書の指定見出しの後、最初に現れる ```text ... ``` ブロックの中身を取り出す"""
    m = re.search(heading_pattern + r"\n.*?```text\n(.*?)\n```", spec_text, re.DOTALL)
    assert m is not None, f"仕様書に見出しパターン {heading_pattern!r} が見つからない"
    return m.group(1)


def test_rules_template_matches_spec_verbatim() -> None:
    spec_text = _SPEC_PATH.read_text(encoding="utf-8")
    body = _extract_fenced_block(spec_text, r"### 13\.1 ルール要約")
    for old, new in _PENDING_SPEC_ADDITIONS.items():
        assert old in body, f"仕様書側の置き換え対象の行が見つからない: {old!r}"
        body = body.replace(old, new)
    assert body == RULES_TEMPLATE


def test_loan_prompt_template_matches_spec_verbatim() -> None:
    spec_text = _SPEC_PATH.read_text(encoding="utf-8")
    body = _extract_fenced_block(spec_text, r"### 13\.2 借入額を聞く文面[^\n]*")
    assert body == LOAN_PROMPT_TEMPLATE
