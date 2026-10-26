#!/usr/bin/env python3
"""
記事ファイル（総括1本＋手記N本）を組み立てる（サイクル6.0新設、サイクル7.3で`--date`対応）

- 手記: `doc/blog/{date}/raw/{pid}_attempt1.md`（AI本人が書いた生の本文、無修正）に、
  引用・金額の機械照合結果と、場面への直リンクを付けた「記録係の注」を追記し、
  frontmatter（title/slug/category/tags/lead/series/order/featured/status/x_text/x_hook）を
  付けて `doc/blog/{date}/posts/{order}_memoir_{pid}.md` へ保存する。
- 総括1本: `doc/blog/{date}/summary_body.md`（記録係が直接執筆）を同じ形式で保存する。

本文（AIが書いた部分）は一切書き換えない。追記するのは常に末尾の「## 記録係の注」節のみ。
タイトルには`match.title_prefix`（第1戦は空文字、第2戦以降は「【少数決 第N戦】」）を付ける。

使い方:
    uv run python scripts/build_blog_posts.py --date 20261023
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import argparse  # noqa: E402

from blog.match_config import DEFAULT_DATE, LOG_DIR, MATCHES, BlogMatch  # noqa: E402
from blog.quote_check import check_article, render_editor_note  # noqa: E402
from blog.self_memoir import build_self_memoir_material  # noqa: E402

VIEWER_BASE_URL = "https://shousuu-ketsu-viewer.devrelay.io/watch"
CATEGORY = "AI対戦ログ"
TAGS = ["shousuu-ketsu", "AI対戦", "少数決"]
HASHTAG = "#嘘八百万"

# 一部の席は1回目の生成が他席をモデル名で呼ぶ方針（けいすけさんの決定）に沿わず、
# 1回だけやり直した（サイクル7.3）。やり直し後の方を採用する。既定（未記載）はattempt1。
# 2回とも方針に沿わなかった席（P03・P11・P15）も、代筆せず2回目の出力をそのまま採用し、
# devlog・完了報告に記録する。
FINAL_ATTEMPT_OVERRIDE: dict[tuple[str, str], int] = {
    ("20261023", "P03"): 2,
    ("20261023", "P11"): 2,
    ("20261023", "P15"): 2,
}

# 1回だけやり直した後も、本文の地の文に席番号（P◯◯）がそのまま残った席（サイクル7.3）。
# 本文は書き換えず、記録係の注に事実として記録する。
NAMING_POLICY_NOTE: dict[tuple[str, str], str] = {
    ("20261023", "P03"): (
        "本文の方針について: この試合から、他席は席番号ではなくモデル名だけで呼ぶ方針にしたが、"
        "この手記は1回のやり直しでも地の文に席番号（P04・P13等）がそのまま残った。本文は書き換えず、"
        "事実として記録する。"
    ),
    ("20261023", "P11"): (
        "本文の方針について: この試合から、他席は席番号ではなくモデル名だけで呼ぶ方針にしたが、"
        "この手記は1回のやり直しでも地の文に席番号（P01・P13等）がそのまま残った。本文は書き換えず、"
        "事実として記録する。"
    ),
    ("20261023", "P15"): (
        "本文の方針について: この試合から、他席は席番号ではなくモデル名だけで呼ぶ方針にしたが、"
        "この手記は1回のやり直しでも地の文に席番号（P08・P13等）がそのまま残った。本文は書き換えず、"
        "事実として記録する。"
    ),
    ("20261023", "P07"): (
        "本文の方針について: 他席はモデル名だけで呼ぶ方針だが、この手記では「モデル名（P◯◯）」の"
        "形で席番号が一部補足として残っている（モデル名自体は正しく使われている）。本文は書き換えず、"
        "事実として記録する。"
    ),
    ("20261023", "P12"): (
        "本文の方針について: 他席はモデル名だけで呼ぶ方針だが、この手記では地の文に席番号が2箇所"
        "そのまま残っている。本文は書き換えず、事実として記録する。"
    ),
}


def _slug_model(short_label: str) -> str:
    """モデルの短縮表示名からURLスラグ用の文字列を作る（談合カードの命名規則を踏襲:
    claude接頭辞は落とす・日付サフィックスは落とす・ドットは除く）。
    """
    s = short_label
    if s.startswith("claude-"):
        s = s[len("claude-"):]
    # "claude-haiku-4-5-20251001" の日付サフィックス(-YYYYMMDD)を落とす
    parts = s.rsplit("-", 1)
    if len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit():
        s = parts[0]
    return s.replace(".", "")


# ---------------------------------------------------------------------------
# 場面への直リンク（scene_links から要所3〜6件を機械的に選ぶ）
# ---------------------------------------------------------------------------

_MONEY_LABEL_KEYWORDS = ("山の受け取り", "契約の支払い", "契約の受け取り")
_MESSAGE_LABEL_KEYWORDS = ("全体発言", "DM")


def pick_scene_links(scene_links: list, max_items: int = 6) -> list[tuple[str, int]]:
    """「山の受け取り・契約の支払い/受け取り」（money系）→「全体発言・DM」（message系、
    交渉の山場に飛べる）→ その他（参加費・利息等）の順で優先し、重複行番号を除いて
    最大max_items件選ぶ（決定的: 常に素材の出現順）。
    """
    seen_lines: set[int] = set()
    money_items: list[tuple[str, int]] = []
    message_items: list[tuple[str, int]] = []
    other_items: list[tuple[str, int]] = []
    for link in scene_links:
        if link.line in seen_lines:
            continue
        seen_lines.add(link.line)
        if any(k in link.label for k in _MONEY_LABEL_KEYWORDS):
            money_items.append((link.label, link.line))
        elif any(k in link.label for k in _MESSAGE_LABEL_KEYWORDS):
            message_items.append((link.label, link.line))
        else:
            other_items.append((link.label, link.line))
    combined = money_items + message_items + other_items
    return combined[:max_items]


def build_editor_note_section(match: BlogMatch, pid: str, article_body: str, material) -> str:
    article_result = check_article(
        pid, article_body, material.quote_sources, material.system_prompt + material.user_prompt,
    )
    note = render_editor_note(article_result)
    naming_note = NAMING_POLICY_NOTE.get((match.date, pid))
    if naming_note:
        note = note + "\n\n" + naming_note
    links = pick_scene_links(material.scene_links)
    lines = ["## 記録係の注", "", note]
    if links:
        lines.append("")
        lines.append("この手記の場面へのリンク:")
        for label, line in links:
            url = f"{VIEWER_BASE_URL}?game={match.game_id}&event={line}"
            lines.append(f"- [{label}（#{line}）]({url})")
    return "\n".join(lines) + "\n"


def extract_title(article_body: str) -> str:
    for line in article_body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    raise ValueError("本文の1行目に「# タイトル」が見つかりません")


def strip_title_line(article_body: str) -> str:
    lines = article_body.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("# "):
            return "\n".join(lines[i + 1:]).strip() + "\n"
    return article_body


def build_memoir_post(match: BlogMatch, pid: str) -> str:
    order = match.order_of(pid)
    copy = match.seat_copy[pid]
    attempt = FINAL_ATTEMPT_OVERRIDE.get((match.date, pid), 1)
    raw_path = match.raw_dir / f"{pid}_attempt{attempt}.md"
    article_body = raw_path.read_text(encoding="utf-8")
    title = extract_title(article_body)
    body_without_title = strip_title_line(article_body)

    material = build_self_memoir_material(
        LOG_DIR, match.game_id, pid, name_by_model_only=(match.match_no != 1),
    )
    editor_note = build_editor_note_section(match, pid, article_body, material)

    model_label = material.model_label
    slug = f"{match.slug_prefix}-{order}-{_slug_model(model_label)}"
    featured = f"images/cards/{order}_{pid}_card.png"

    intro = f"この記事の本文は、{pid}席のAI（{model_label}）本人が試合後に書いたものです。記録係は本文に手を入れていません。\n\n"

    frontmatter = (
        "---\n"
        f"title: {match.title_prefix}{title}\n"
        f"slug: {slug}\n"
        f"category: {CATEGORY}\n"
        f"tags: [{', '.join(TAGS)}]\n"
        f"lead: {copy.lead}\n"
        f"series: {match.series}\n"
        f"order: {order}\n"
        f"featured: {featured}\n"
        "status: draft\n"
        f'x_text: "{copy.x_text}"\n'
        f'x_hook: "{copy.x_hook}"\n'
        "---\n\n"
    )
    return frontmatter + intro + body_without_title + "\n" + editor_note


def build_summary_post(match: BlogMatch) -> str:
    body = match.summary_body_path.read_text(encoding="utf-8")
    s = match.summary
    frontmatter = (
        "---\n"
        f"title: {match.title_prefix}{s.title}\n"
        f"slug: {match.summary_slug}\n"
        f"category: {CATEGORY}\n"
        f"tags: [{', '.join(TAGS)}]\n"
        f"lead: {s.lead}\n"
        f"series: {match.series}\n"
        "order: 01\n"
        "featured: images/cards/01_summary_card.png\n"
        "status: draft\n"
        f'x_text: "{s.x_text}"\n'
        f'x_hook: "{s.x_hook}"\n'
        "---\n\n"
    )
    return frontmatter + body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=DEFAULT_DATE, choices=sorted(MATCHES), help="対象試合の日付（既定: 現行サイクルの試合）")
    args = ap.parse_args()
    match = MATCHES[args.date]

    match.posts_dir.mkdir(parents=True, exist_ok=True)

    summary_text = build_summary_post(match)
    summary_path = match.posts_dir / "01_summary.md"
    summary_path.write_text(summary_text, encoding="utf-8")
    print(f"[生成] {summary_path}")

    for pid in match.seat_order:
        post_text = build_memoir_post(match, pid)
        order = match.order_of(pid)
        out_path = match.posts_dir / f"{order}_memoir_{pid}.md"
        out_path.write_text(post_text, encoding="utf-8")
        print(f"[生成] {out_path}")


if __name__ == "__main__":
    main()
