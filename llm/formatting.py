"""
金額・利率の表記を1本化するモジュール（§7.5・§9）

手番の文面（`llm/prompt_builder.py`）と不成立の理由の文面（`llm/reasons.py`）の
両方が同じ書式を使う（CLAUDE.md落とし穴④対策: 同じ形を複数箇所に書くと
修正漏れが起きる。サイクル4.2 E2で、理由の文の金額だけ桁区切りが無いことが
見つかったため新設した）。
"""

from __future__ import annotations


def yen(amount: int) -> str:
    """金額を1円単位・桁区切りで、末尾に「円」を付けて表す（例: 1,458,608円）"""
    return f"{amount:,}円"


def comma(value: int) -> str:
    """桁区切りだけを付ける（テンプレート側にすでに「円」等の単位がある場合用）"""
    return f"{value:,}"


def pct(num: int, den: int) -> str:
    """利率を%表示にする（1.5/3のように末尾の.0は出さない）"""
    value = num / den * 100
    return f"{value:g}"
