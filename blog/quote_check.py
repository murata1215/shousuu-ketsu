"""
引用・金額の機械照合（サイクル6.0新設、談合カードの実際の運用（サイクル10.26）と同じ扱い）

手記の本文中の「言った・言われた」引用（「」『』" "で囲まれた10字以上の文字列）を、
その席に渡した素材の原文（`SelfMemoirMaterial.quote_sources`）と機械で突き合わせる。
一致しない引用は**本文からは落とさず**、記事末尾の「記録係の注」にその旨を記録する
（けいすけさんの決定④: 談合カードの実際の運用と同じ）。

金額（円）も同様に、素材に出てこない額が本文にあれば注に記録する（落とさない）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# 「」『』と全角/半角の二重引用符の中身を取り出す（入れ子は考慮しない簡易版）
_QUOTE_RE = re.compile(r"「([^「」]{1,500})」|『([^『』]{1,500})』|“([^”]{1,500})”|\"([^\"]{1,500})\"")
_MIN_QUOTE_LEN = 10  # ゲーム用語（「山分け」「少数決」等）を誤検出しないための下限

_AMOUNT_COMMA_RE = re.compile(r"(\d{1,3}(?:,\d{3})+)円")
# "1,000万円"のようなカンマ区切りの万単位表記と、"1320万円"のような4桁以上
# カンマ無し表記の両方を認める。素朴な"(\d{1,3}(?:,\d{3})*)万円"だと、カンマの
# 無い4桁の数（"1320万円"）が`\d{1,3}`の上限3桁で先頭の"1"を取りこぼし、
# "320万円"だけを誤って拾ってしまう事故があったため、「カンマ区切りの並び」と
# 「カンマの無い任意桁数の並び」を別の選択肢として明示的に分ける。
_AMOUNT_MAN_RE = re.compile(r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)万円")
# 素材中の契約details（Pythonの辞書表現そのまま、例: {'amount': 2000000, ...}）に
# 現れる生の数字は"円"も","も付かないため、上の2つのパターンでは拾えない。
# 素材側の金額の母集合を作るときだけ、6桁以上の生の数字（10万円以上。契約ID等の
# 英数字混在には`\D`境界で隣接しないため誤爆しない）も金額候補として additionally 拾う。
_AMOUNT_RAW_RE = re.compile(r"(?<!\d)(\d{6,})(?!\d)")

_ELLIPSIS_RE = re.compile(r"…|\.{3,}")


def _normalize(s: str) -> str:
    """空白を取り、文字幅をそろえる（NFKC正規化 + 空白除去）。"""
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", "", s)


def extract_quotes(markdown: str) -> list[str]:
    """本文（1行目のタイトル・見出しは含めてよい。呼び出し側で除外する）から、
    10字以上の引用文字列を出現順に抜き出す。
    """
    out: list[str] = []
    for m in _QUOTE_RE.finditer(markdown):
        text = next(g for g in m.groups() if g is not None)
        if len(text) >= _MIN_QUOTE_LEN:
            out.append(text)
    return out


def quote_matches(quote: str, sources: list[str]) -> bool:
    """引用が、素材の原文（sources、1行=1照合単位）のどれかに含まれるか判定する。

    `…`/`...`で区切られた省略引用は、区切られた各部分が**同じ1つの原文行**の中に
    順番どおり現れることを求める（離れた2つの原文をつなぎ合わせた偽の引用を通さない）。
    """
    norm_quote = _normalize(quote)
    parts = [p for p in _ELLIPSIS_RE.split(norm_quote) if p]
    if not parts:
        return False
    for src in sources:
        norm_src = _normalize(src)
        pos = 0
        ok = True
        for part in parts:
            idx = norm_src.find(part, pos)
            if idx == -1:
                ok = False
                break
            pos = idx + len(part)
        if ok:
            return True
    return False


def extract_amounts_yen(text: str, *, include_raw: bool = False) -> set[int]:
    """本文中の金額表記（カンマ区切り"###,###円"・"##万円"）を円の整数へ正規化して集める。

    include_raw=Trueのときは、"円"も","も付かない生の数字（6桁以上）も候補に含める。
    素材側（`§5. あなたが当事者の契約`の内容はPythonの辞書表現のまま出すため、
    "{'amount': 2000000, ...}"のように"円"無しの生の数字で金額が現れる）の母集合を
    作るときにだけ使う。本文側（article_markdown）の判定には使わない
    （本文は常に"円"付きの日本語の文章になる前提のため、生数字まで拾うと
    無関係な数字—行番号等—まで誤って「有効な金額」にしてしまう恐れがある）。
    """
    amounts: set[int] = set()
    for m in _AMOUNT_COMMA_RE.finditer(text):
        amounts.add(int(m.group(1).replace(",", "")))
    for m in _AMOUNT_MAN_RE.finditer(text):
        amounts.add(int(float(m.group(1).replace(",", "")) * 10_000))
    if include_raw:
        for m in _AMOUNT_RAW_RE.finditer(text):
            amounts.add(int(m.group(1)))
    return amounts


@dataclass
class QuoteCheckResult:
    pid: str
    unmatched_quotes: list[str] = field(default_factory=list)
    matched_count: int = 0
    unmatched_amounts: list[int] = field(default_factory=list)

    @property
    def total_quotes(self) -> int:
        return self.matched_count + len(self.unmatched_quotes)


def check_article(pid: str, article_markdown: str, quote_sources: list[str], material_text: str) -> QuoteCheckResult:
    """手記本文を引用・金額の両面で機械照合する。

    Args:
        article_markdown: AIが書いた本文（1行目のタイトル含む全文）
        quote_sources: その席の素材に実際に採用された原文（`SelfMemoirMaterial.quote_sources`）
        material_text: 金額照合の母集合（`SelfMemoirMaterial.system_prompt + user_prompt`）
    """
    result = QuoteCheckResult(pid=pid)
    for q in extract_quotes(article_markdown):
        if quote_matches(q, quote_sources):
            result.matched_count += 1
        else:
            result.unmatched_quotes.append(q)

    valid_amounts = extract_amounts_yen(material_text, include_raw=True)
    article_amounts = extract_amounts_yen(article_markdown)
    result.unmatched_amounts = sorted(article_amounts - valid_amounts)
    return result


def render_editor_note(result: QuoteCheckResult) -> str:
    """「記録係の注」セクションに追記する文面を組み立てる（0件なら簡潔な一言）。"""
    if not result.unmatched_quotes and not result.unmatched_amounts:
        return "記録と食い違う引用・金額は見つかりませんでした。"
    lines = []
    if result.unmatched_quotes:
        lines.append(f"- 引用の機械照合で原文と一致しなかった箇所（{len(result.unmatched_quotes)}件、本文は無修正）:")
        for q in result.unmatched_quotes:
            shown = q if len(q) <= 80 else q[:80] + "…"
            lines.append(f"    - 「{shown}」")
    if result.unmatched_amounts:
        lines.append(f"- 本文の金額のうち、素材の記載と一致しなかったもの（{len(result.unmatched_amounts)}件、本文は無修正）:")
        for amt in result.unmatched_amounts:
            lines.append(f"    - {amt:,}円")
    return "\n".join(lines)
