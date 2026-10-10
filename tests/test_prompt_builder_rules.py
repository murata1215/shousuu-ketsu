"""
ルール全文・借入文面の逐語一致テスト（§13、サイクル4.2b承認済み決定1・2）

`llm/prompt_builder.py::RULES_TEMPLATE`/`LOAN_PROMPT_TEMPLATE` が、仕様書
`doc/uso8000000_shousuu_ketsu_spec_v0_4_2.md` §13.1/§13.2 の本文を逐語で使い、
承認済みの6行だけを足したものと完全一致することを機械的に確かめる。
仕様書を書き換えても文面を直し忘れたら落ちる。
"""

from __future__ import annotations

import re
from pathlib import Path

from llm.prompt_builder import LOAN_PROMPT_TEMPLATE, RULES_TEMPLATE

_SPEC_PATH = Path(__file__).resolve().parent.parent / "doc" / "uso8000000_shousuu_ketsu_spec_v0_4_2.md"

# 承認済みの6行（サイクル4.2 プラン承認時 + 承認時の追加指示1点）。
# (アンカー行, 足す行) の順。アンカー行の直後に挿入する。
_APPROVED_ADDITIONS: list[tuple[str, str]] = [
    (
        "- 1つの契約に複数の義務を入れられる。義務ごとに義務者と相手方を定める。"
        "対象にできるのは、現在の投票と、それより後の投票・ラウンド",
        "- 義務者と相手方は、どちらもその契約の当事者（提案者と、with に挙げた相手）でなければならない",
    ),
    (
        "- R{rank_reveal_round}の終了後に、全員の順位が全員に公開される（名前のみ。金額は出ない）",
        "- 最終ラウンドの終了後に、全員の最終順位と最終資産が公開される",
    ),
    (
        '- {{"type": "broadcast", "message": "..."}}',
        "  ※dm と broadcast の message は500字以内。超えた分は切り捨てられる",
    ),
    (
        "- 同じ決済で複数の支払いがあり払いきれない場合は、契約が成立した順に支払われる"
        "（同じ契約の中では記載順）。途中で尽きた義務は残額だけ支払われ、それより後の義務は0円になる",
        "- 型Bの違約金は、破った型Bの義務が契約に書かれている位置の支払いとして、この順番に入る",
    ),
    (
        "退場している間の vote_plan は空文字でよい。",
        "vote_plan には、今の投票で入れるつもりの側を書く。",
    ),
    (
        "  ※金額は amount（円）で指定する。wins_round で target_player が義務者自身の場合に限り、"
        "amount の代わりに share_percent（1〜100の整数）を指定できる",
        "  ※with には、自分以外の当事者を重複なく書く。提案者は自動で当事者になる",
    ),
]


def _extract_fenced_block(spec_text: str, heading_pattern: str) -> str:
    """仕様書の指定見出しの後、最初に現れる ```text ... ``` ブロックの中身を取り出す"""
    m = re.search(heading_pattern + r"\n.*?```text\n(.*?)\n```", spec_text, re.DOTALL)
    assert m is not None, f"仕様書に見出しパターン {heading_pattern!r} が見つからない"
    return m.group(1)


def _insert_after(text: str, anchor_line: str, new_line: str) -> str:
    """anchor_line（そのままの行）の直後に new_line を1行挿入する"""
    assert text.count(anchor_line) == 1, (
        f"アンカー行が仕様書中に0回または複数回見つかった: {anchor_line!r}"
    )
    return text.replace(anchor_line, f"{anchor_line}\n{new_line}", 1)


def test_rules_template_matches_spec_verbatim_plus_6_lines() -> None:
    spec_text = _SPEC_PATH.read_text(encoding="utf-8")
    body = _extract_fenced_block(spec_text, r"### 13\.1 ルール要約")
    for anchor, addition in _APPROVED_ADDITIONS:
        body = _insert_after(body, anchor, addition)
    assert body == RULES_TEMPLATE


def test_loan_prompt_template_matches_spec_verbatim() -> None:
    spec_text = _SPEC_PATH.read_text(encoding="utf-8")
    body = _extract_fenced_block(spec_text, r"### 13\.2 借入額を聞く文面[^\n]*")
    assert body == LOAN_PROMPT_TEMPLATE
