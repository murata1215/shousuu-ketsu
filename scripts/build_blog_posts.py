#!/usr/bin/env python3
"""
13本の記事ファイル（総括1本＋手記12本）を組み立てる（サイクル6.0新設）

- 手記12本: `doc/blog/20261013/raw/{pid}_attempt1.md`（AI本人が書いた生の本文、無修正）に、
  引用・金額の機械照合結果と、場面への直リンクを付けた「記録係の注」を追記し、
  frontmatter（title/slug/category/tags/lead/series/order/featured/status/x_text/x_hook）を
  付けて `doc/blog/20261013/posts/{order}_memoir_{pid}.md` へ保存する。
- 総括1本: `SUMMARY_BODY`（本スクリプトに記録係が直接執筆）を同じ形式で保存する。

本文（AIが書いた部分）は一切書き換えない。追記するのは常に末尾の「## 記録係の注」節のみ。

使い方:
    uv run python scripts/build_blog_posts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from blog.quote_check import check_article, render_editor_note  # noqa: E402
from blog.self_memoir import build_self_memoir_material  # noqa: E402

GAME_ID = "t12_r4_4501c"
LOG_DIR = PROJECT_ROOT / "logs" / "llm_v0_4"
OUT_DIR = PROJECT_ROOT / "doc" / "blog" / "20261013"
RAW_DIR = OUT_DIR / "raw"
POSTS_DIR = OUT_DIR / "posts"

VIEWER_BASE_URL = "https://shousuu-ketsu-viewer.devrelay.io/watch"
SERIES = "20261013"
CATEGORY = "AI対戦ログ"
TAGS = ["shousuu-ketsu", "AI対戦", "少数決"]
HASHTAG = "#嘘八百万"


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
# 公開順（総括→最下位から順に→優勝を最後に）とメタデータ
# ---------------------------------------------------------------------------

SUMMARY_TITLE = "山を取った2人が、受け取った額のほぼ全額を手放した——12体戦、総括"
SUMMARY_LEAD = "12席×4ラウンドの本戦。v0.5で足した「山分け」が初めて実際に効き、山を取った2人が受け取りのほぼ全額を手放した。手記12本は各席のAI本人が、自分の席番号とモデル名を明かして書いている。"
SUMMARY_X_TEXT = "少数決S1・12体戦の総括。v0.5で足した「山分け」が初めて実際に効き、山を取った2人が受け取りのほぼ全額を手放した。手記12本は各席のAI本人による執筆。"
SUMMARY_X_HOOK = "山を取った2人が、受け取った額のほぼ全額を手放した——12体戦、総括。全12席の本人執筆手記つき。 #嘘八百万"

SLOTS: list[dict] = [
    {"order": "01", "pid": None, "kind": "summary"},
    {"order": "02", "pid": "P07", "kind": "memoir",
     "lead": "開始前に1,000万円を借り、一度も山を取れないまま複利に沈んだ最下位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。1,000万円を背負って挑んだが、一度も山を取れないまま借金の複利に沈んだ最下位の記録。",
     "x_hook": "1,000万円を借りて大勝負に出たのに、一度も山を取れなかった——複利に沈んだ最下位の記録。12体戦、全12席の本人執筆手記。 #嘘八百万"},
    {"order": "03", "pid": "P08", "kind": "memoir",
     "lead": "何度も少数派に残れたのに、山分け契約で勝利の代償を払い続けた11位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。少数派には何度も残れたのに、山分け契約の清算で資産が溶けていった11位の記録。",
     "x_hook": "少数派には残れた。でも資産には残れなかった——契約の清算に消えた11位の記録。 #嘘八百万"},
    {"order": "04", "pid": "P05", "kind": "memoir",
     "lead": "R2で600万円の山を受け取ったのに、2本の山分け契約で全額を失い、違約金100万円まで払った10位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。山を取ったのに山分け契約で全額を失い、型Bの違約金まで払った10位の記録。",
     "x_hook": "山を取ったのに、手元には一円も残らなかった——山分けに全額持っていかれた10位の記録。 #嘘八百万"},
    {"order": "05", "pid": "P11", "kind": "memoir",
     "lead": "少数派連合は何度も機能したのに、自分だけ最後の投票で勝ち残れなかった9位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。連合は機能したのに自分だけ山を取れず、山分け保険も一度も発動しなかった9位の記録。",
     "x_hook": "協調は最後まで機能したのに、自分だけ山を取れなかった——9位の記録。 #嘘八百万"},
    {"order": "06", "pid": "P12", "kind": "memoir",
     "lead": "借入上限ギリギリの1,000万円で挑み、山分けで300万円を得たが利息に押し切られた8位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。1,000万円の借入と5%複利に追われながら、山分けで束の間の希望をつかんだ8位の記録。",
     "x_hook": "1,000万の重荷を背負い、山分けで束の間の希望を得たが、利息に呑まれた8位の記録。 #嘘八百万"},
    {"order": "07", "pid": "P10", "kind": "memoir",
     "lead": "R3で現金が尽き、山分け契約も発動しないまま借金の複利に沈んだ7位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。現金ゼロで迎えた最終ラウンド、山分け保険も発動しないまま借金に沈んだ7位の記録。",
     "x_hook": "現金ゼロで迎えた最終ラウンド。もう戦術ではなく運のゲームだった——7位の記録。 #嘘八百万"},
    {"order": "08", "pid": "P02", "kind": "memoir",
     "lead": "開始前借入を最小の120万円に抑えながら、4回の投票すべてで多数派に回り続けた6位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。借入を最小に抑えて逃げ切りを狙ったが、4回の投票すべてで多数派に回った6位の記録。",
     "x_hook": "四回投票して、四回とも多数派だった——それでも6位で終えた男の記録。 #嘘八百万"},
    {"order": "09", "pid": "P04", "kind": "memoir",
     "lead": "P08との山分け契約を軸に少数派を狙い続け、借金の利息に苦しんだ5位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。山分け契約を軸に少数派を狙い続けたが、借金の利息負担に苦しんだ5位の記録。",
     "x_hook": "少数派に賭け続けた男の、利息との静かな戦い——5位の記録。 #嘘八百万"},
    {"order": "10", "pid": "P09", "kind": "memoir",
     "lead": "自分で山を取ることを諦め、他人の山に寄生する契約に賭けた4位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。R1で早々に退場し、山分け契約で他人の勝利に乗る戦略に切り替えた4位の記録。",
     "x_hook": "勝ち残るより、勝ち残る奴と契約しろ——山に寄生して生き延びた4位の記録。 #嘘八百万"},
    {"order": "11", "pid": "P03", "kind": "memoir",
     "lead": "最大級の借入1,000万円で挑み、R2で600万円を掴んだが、同じ読みに固執して失速した3位の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。1,000万円を借りてR2で600万円を獲得したが、同じパターンに固執し続けた3位の記録。",
     "x_hook": "少数派連勝の夢は、R2のたった一度の成功で終わった——3位の記録。 #嘘八百万"},
    {"order": "12", "pid": "P01", "kind": "memoir",
     "lead": "首位の独走を二度止められなかったが、最終ラウンドで600万円と契約金を回収し2位まで戻った記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。首位の二度の独走を止められなかったが、最終ラウンドで2位まで這い上がった記録。",
     "x_hook": "二度の独走を止められなかった男が、最後の一票で2位に這い上がった記録。 #嘘八百万"},
    {"order": "13", "pid": "P06", "kind": "memoir",
     "lead": "協力者との約束を二度破り、二度の単独総取りで最終資産2,194万円を築いた優勝の記録。",
     "x_text": "少数決S1・12体戦の本人執筆手記。協力者を二度裏切って単独総取りを決め、最終資産2,194万円で優勝した記録。",
     "x_hook": "信用を切り売りして、二度の総取りで頂点に立った——12体戦・優勝の記録。 #嘘八百万"},
]


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


def build_editor_note_section(pid: str, article_body: str, material) -> str:
    article_result = check_article(
        pid, article_body, material.quote_sources, material.system_prompt + material.user_prompt,
    )
    note = render_editor_note(article_result)
    links = pick_scene_links(material.scene_links)
    lines = ["## 記録係の注", "", note]
    if links:
        lines.append("")
        lines.append("この手記の場面へのリンク:")
        for label, line in links:
            url = f"{VIEWER_BASE_URL}?game={GAME_ID}&event={line}"
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


def build_memoir_post(slot: dict) -> str:
    pid = slot["pid"]
    raw_path = RAW_DIR / f"{pid}_attempt1.md"
    article_body = raw_path.read_text(encoding="utf-8")
    title = extract_title(article_body)
    body_without_title = strip_title_line(article_body)

    material = build_self_memoir_material(LOG_DIR, GAME_ID, pid)
    editor_note = build_editor_note_section(pid, article_body, material)

    model_label = material.model_label
    slug = f"sk-1013-{slot['order']}-{_slug_model(model_label)}"
    featured = f"images/cards/{slot['order']}_{pid}_card.png"

    intro = f"この記事の本文は、{pid}席のAI（{model_label}）本人が試合後に書いたものです。記録係は本文に手を入れていません。\n\n"

    frontmatter = (
        "---\n"
        f"title: {title}\n"
        f"slug: {slug}\n"
        f"category: {CATEGORY}\n"
        f"tags: [{', '.join(TAGS)}]\n"
        f"lead: {slot['lead']}\n"
        f"series: {SERIES}\n"
        f"order: {slot['order']}\n"
        f"featured: {featured}\n"
        "status: draft\n"
        f'x_text: "{slot["x_text"]}"\n'
        f'x_hook: "{slot["x_hook"]}"\n'
        "---\n\n"
    )
    return frontmatter + intro + body_without_title + "\n" + editor_note


def build_summary_post() -> str:
    body = (OUT_DIR / "summary_body.md").read_text(encoding="utf-8")
    frontmatter = (
        "---\n"
        f"title: {SUMMARY_TITLE}\n"
        "slug: sk-1013-01-summary\n"
        f"category: {CATEGORY}\n"
        f"tags: [{', '.join(TAGS)}]\n"
        f"lead: {SUMMARY_LEAD}\n"
        f"series: {SERIES}\n"
        "order: 01\n"
        "featured: images/cards/01_summary_card.png\n"
        "status: draft\n"
        f'x_text: "{SUMMARY_X_TEXT}"\n'
        f'x_hook: "{SUMMARY_X_HOOK}"\n'
        "---\n\n"
    )
    return frontmatter + body


def main() -> None:
    POSTS_DIR.mkdir(parents=True, exist_ok=True)

    summary_text = build_summary_post()
    summary_path = POSTS_DIR / "01_summary.md"
    summary_path.write_text(summary_text, encoding="utf-8")
    print(f"[生成] {summary_path}")

    for slot in SLOTS:
        if slot["kind"] == "summary":
            continue
        post_text = build_memoir_post(slot)
        out_path = POSTS_DIR / f"{slot['order']}_memoir_{slot['pid']}.md"
        out_path.write_text(post_text, encoding="utf-8")
        print(f"[生成] {out_path}")


if __name__ == "__main__":
    main()
