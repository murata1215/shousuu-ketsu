#!/usr/bin/env python3
"""
連載記事の公開の段取り（サイクル6.0新設、`~/dangou-card/scripts/publish_next.py`の
設計を少数決の1試合連載に合わせて作り直したもの）

使い方:
    uv run python scripts/publish_blog.py --preflight          # 公開前の読み合わせ用プレビューを出力（ネット不使用）
    uv run python scripts/publish_blog.py --dry-run             # 実公開せず流れを確認（GETのみ）
    uv run python scripts/publish_blog.py --dry-run --order 01  # orderを明示指定して確認
    uv run python scripts/publish_blog.py --check-lengths       # 13本のtweet_text/x_hook加重文字数を検算
    uv run python scripts/publish_blog.py --order 01            # 実公開（本番、1本だけ）
    uv run python scripts/publish_blog.py --replay 01           # 公開済みorderの投稿セットを再表示のみ

挙動概要:
  1. `doc/blog/20261013/plan.md`の「## 投稿状態」節を走査し、状態が「原稿済（未投稿）」の
     最小orderを1本選ぶ（--orderで明示指定も可）。
  2. frontmatterのtitle/slug/category/tagsで記事を公開する。featured（キャラカード）は
     本文画像とは別枠でアップロードしてfeatured_media_urlに使う。本文には画像を含めない
     （決定③）。
  3. `x_text`の末尾に半角スペース＋`#嘘八百万`を付加した文字列をtweet_textに、
     `lead`をそのままexcerptに設定する。公開前にexcerptへのハッシュタグ/URL混入と、
     tweet_text(+URL)の加重文字数280超過を検証する。
  4. 公開後、plan.mdの該当行とdoc/blog/INDEX.mdを更新し、投稿セットを
     doc/blog/20261013/post_sets.mdへ追記する。
  5. 二重公開ガード: plan.mdが「公開済」のorderは対象にしない。実公開直前にGET /posts で
     同一タイトルの既存記事が無いか確認する。

このスクリプトは、--dry-run / --replay / --preflight / --check-lengths 時には
PixBlogの書き込みAPI（POST/PATCH/画像アップロード）を一切呼ばない。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from blog.pixblog_client import PixBlogClient, PixBlogError  # noqa: E402

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

DATE = "20261013"
BLOG_ROOT = PROJECT_ROOT / "doc" / "blog"
DATE_DIR = BLOG_ROOT / DATE
POSTS_DIR = DATE_DIR / "posts"
PLAN_PATH = DATE_DIR / "plan.md"
INDEX_PATH = BLOG_ROOT / "INDEX.md"
POST_SETS_PATH = DATE_DIR / "post_sets.md"
PREFLIGHT_OUT_PATH = PROJECT_ROOT / ".devrelay-output" / "blog_20261013_preflight.md"

STATUS_DRAFT_DONE = "原稿済（未投稿）"
STATUS_PUBLISHED_PREFIX = "公開済"

DEFAULT_TAGS = ["shousuu-ketsu", "AI対戦", "少数決"]
DEFAULT_CATEGORY = "AI対戦ログ"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((images/[^)]+)\)")
STATUS_ROW_STRICT_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$")

X_URL_WEIGHT = 23
X_MAX_LEN = 280
X_TWEET_FIELD = "tweet_text"
X_EXCERPT_FIELD = "excerpt"
X_HASHTAG = "#嘘八百万"


# ---------------------------------------------------------------------------
# X 加重文字数
# ---------------------------------------------------------------------------

def x_weighted_len(s: str) -> int:
    total = 0
    for ch in s:
        o = ord(ch)
        if (0x0000 <= o <= 0x10FF) or (0x2000 <= o <= 0x200D) or (0x2010 <= o <= 0x201F) or (0x2032 <= o <= 0x2037):
            total += 1
        else:
            total += 2
    return total


def build_tweet_text(x_text: str) -> str:
    return f"{x_text} {X_HASHTAG}"


def tweet_draft_len(tweet_text: str) -> int:
    return x_weighted_len(tweet_text) + 1 + X_URL_WEIGHT


def hook_len(x_hook: str) -> int:
    return x_weighted_len(x_hook)


def validate_excerpt(lead: str) -> list[str]:
    problems = []
    if "#" in lead:
        problems.append("ハッシュタグ（#）が含まれています")
    if re.search(r"https?://", lead):
        problems.append("URL（http:// または https://）が含まれています")
    return problems


# ---------------------------------------------------------------------------
# frontmatter / 本文パース
# ---------------------------------------------------------------------------

def parse_frontmatter(md_text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(md_text)
    if not m:
        raise ValueError("フロントマターが見つかりません")
    fm_text, body = m.group(1), m.group(2)
    fm: dict = {}
    for line in fm_text.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            fm[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        elif len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            fm[key] = value[1:-1]
        else:
            fm[key] = value
    return fm, body


def find_images(body: str) -> list[tuple[str, str]]:
    return IMAGE_RE.findall(body)


def discover_all_posts() -> list[dict]:
    entries = []
    for path in sorted(POSTS_DIR.glob("*.md")):
        fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        entries.append({"path": path, "fm": fm, "body": body})
    entries.sort(key=lambda e: int(e["fm"].get("order", 0)))
    return entries


# ---------------------------------------------------------------------------
# plan.md の「## 投稿状態」節パース・更新
# ---------------------------------------------------------------------------

def parse_status_table(plan_text: str) -> list[dict]:
    marker = "## 投稿状態"
    idx = plan_text.find(marker)
    if idx == -1:
        raise ValueError(f"'{marker}' 節が見つかりません")
    rows = []
    in_table = False
    for line in plan_text[idx:].splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if in_table:
                break
            continue
        m = STATUS_ROW_STRICT_RE.match(s)
        if not m:
            in_table = True
            continue
        order, file_, status, url = m.groups()
        if order == "order":
            continue
        in_table = True
        rows.append({"order": order, "file": file_.strip("`"), "status": status, "url": url})
    return rows


def select_next(order_override: str | None):
    plan_text = PLAN_PATH.read_text(encoding="utf-8")
    rows = parse_status_table(plan_text)
    rows.sort(key=lambda r: int(r["order"]))
    for row in rows:
        if order_override is not None and row["order"] != order_override:
            continue
        if row["status"].startswith(STATUS_PUBLISHED_PREFIX):
            print(f"[中断] order={row['order']} は既に「{row['status']}」です（二重公開ガード）。")
            sys.exit(1)
        if order_override is not None or row["status"] == STATUS_DRAFT_DONE:
            return plan_text, row
    print(f"[中断] 対象の記事が見つかりません（order指定: {order_override!r}）。")
    sys.exit(1)


def update_plan_status(plan_text: str, order: str, new_status: str, url: str) -> None:
    marker = "## 投稿状態"
    idx = plan_text.find(marker)
    if idx == -1:
        raise ValueError(f"'{marker}' 節が見つかりません")
    next_heading = re.search(r"\n## ", plan_text[idx + len(marker):])
    end = idx + len(marker) + next_heading.start() + 1 if next_heading else len(plan_text)
    head, section, tail = plan_text[:idx], plan_text[idx:end], plan_text[end:]
    lines = section.splitlines(keepends=False)
    out = []
    for line in lines:
        m = STATUS_ROW_STRICT_RE.match(line.strip())
        if m and m.group(1) == order:
            file_ = m.group(2)
            out.append(f"| {order} | {file_} | {new_status} | {url} |")
        else:
            out.append(line)
    new_section = "\n".join(out)
    if section.endswith("\n"):
        new_section += "\n"
    PLAN_PATH.write_text(head + new_section + tail, encoding="utf-8")


def update_index_status(published_count: int, total_count: int) -> None:
    if not INDEX_PATH.exists():
        return
    text = INDEX_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    out = []
    for line in lines:
        if "| 2026-10-13 |" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) == 3:
                cells[2] = f"公開中（{published_count}/{total_count}）" if published_count < total_count else "公開完了"
                out.append("| " + " | ".join(cells) + " |")
                continue
        out.append(line)
    INDEX_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 投稿セット（画像添付フック文 + リプライ用リンク）
# ---------------------------------------------------------------------------

def resolve_featured_path(fm: dict) -> Path | None:
    featured_rel = fm.get("featured")
    if not featured_rel:
        return None
    candidate = DATE_DIR / featured_rel
    return candidate if candidate.exists() else None


def featured_relpath_str(fm: dict) -> str:
    """投稿セットに載せる画像の場所（置き場からの相対パス。ホームのパスを含まない）。"""
    featured_rel = fm.get("featured")
    if not featured_rel:
        return "（見つかりません）"
    rel_to_root = f"doc/blog/{DATE}/{featured_rel}"
    return rel_to_root


def build_post_set(order: str, x_hook: str, title: str, url: str, fm: dict) -> str:
    image_line = featured_relpath_str(fm)
    return (
        f"===== 本日の投稿セット（order {order}） =====\n"
        "【ツイート①】↓コピーして、下の画像を添付して投稿（リンクは入れない）\n"
        f"{x_hook}\n"
        "\n"
        f"  添付画像: {image_line}\n"
        "\n"
        "【ツイート②】↓①に自分でリプライして投稿\n"
        f"▶ {title}\n"
        f"{url}\n"
        "========================================="
    )


def append_post_set(post_set: str) -> None:
    header = "# post_sets.md（サイクル6.0・13本の投稿セット記録）\n"
    if not POST_SETS_PATH.exists():
        POST_SETS_PATH.write_text(header, encoding="utf-8")
    with open(POST_SETS_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n```\n{post_set}\n```\n")


# ---------------------------------------------------------------------------
# 公開ペイロード組み立て
# ---------------------------------------------------------------------------

def build_body_with_public_urls(body: str, url_map: dict[str, str]) -> str:
    def _sub(m: re.Match) -> str:
        alt, rel = m.group(1), m.group(2)
        return f"![{alt}]({url_map.get(rel, rel)})"
    return IMAGE_RE.sub(_sub, body)


def build_publish_payload(fm: dict, body_with_urls: str, featured_url: str | None) -> dict:
    payload = {
        "title": fm.get("title", ""),
        "body": body_with_urls,
        "content_format": "markdown",
        "status": "published",
        "tags": fm.get("tags", DEFAULT_TAGS),
        "category": fm.get("category", DEFAULT_CATEGORY),
        "slug": fm.get("slug"),
    }
    if featured_url:
        payload["featured_media_url"] = featured_url
    if fm.get("x_text"):
        payload[X_TWEET_FIELD] = build_tweet_text(fm["x_text"])
    if fm.get("lead"):
        payload[X_EXCERPT_FIELD] = fm["lead"]
    return payload


def try_upload_image(client: PixBlogClient, image_path: Path) -> str | None:
    try:
        res = client.upload_image(image_path)
        url = client.extract_image_url(res)
        if url:
            print(f"  画像アップロード成功: {image_path.name} -> {url}")
        else:
            print(f"  [警告] 画像アップロード: URLを取得できませんでした（{image_path.name}）")
        return url
    except PixBlogError as e:
        print(f"  [警告] 画像アップロード失敗（{image_path.name}）: {e}")
        return None


def find_existing_post_by_title(client: PixBlogClient, title: str) -> dict | None:
    page = 1
    while True:
        res = client._request("GET", f"/posts?page={page}")
        for post in res.get("posts", []):
            if post.get("title") == title:
                return post
        pagination = res.get("pagination", {})
        total_pages = int(pagination.get("total_pages", 1))
        if page >= total_pages:
            return None
        page += 1


# ---------------------------------------------------------------------------
# サブコマンド: --check-lengths
# ---------------------------------------------------------------------------

def cmd_check_lengths() -> None:
    entries = discover_all_posts()
    print(f"{'order':>5} | {'file':<24} | {'x_text':>7} | {'tweet_text':>10} | {'+URL':>5} | 判定 | {'x_hook':>6} | hook判定 | excerpt")
    print("-" * 110)
    overflow, excerpt_ng, hook_overflow = [], [], []
    for e in entries:
        x_text, lead, x_hook = e["fm"].get("x_text"), e["fm"].get("lead"), e["fm"].get("x_hook")
        order, name = e["fm"].get("order", "?"), e["path"].name

        excerpt_col = "—"
        if lead:
            problems = validate_excerpt(lead)
            excerpt_col = "OK" if not problems else "NG:" + "/".join(problems)
            if problems:
                excerpt_ng.append((order, name, problems))

        hw, hook_verdict = None, "未設定"
        if x_hook:
            hw = hook_len(x_hook)
            hook_verdict = "OK" if hw <= X_MAX_LEN else "★超過"
            if hw > X_MAX_LEN:
                hook_overflow.append((order, name, hw))

        if not x_text:
            hw_col = f"{hw:>6}" if hw is not None else f"{'—':>6}"
            print(f"{order:>5} | {name:<24} | {'—':>7} | {'—':>10} | {'—':>5} | x_text未設定 | {hw_col} | {hook_verdict:<6} | {excerpt_col}")
            continue

        tw = build_tweet_text(x_text)
        w, tw_w = x_weighted_len(x_text), x_weighted_len(tw)
        d = tweet_draft_len(tw)
        verdict = "OK" if d <= X_MAX_LEN else "★超過"
        if d > X_MAX_LEN:
            overflow.append((order, name, d))
        hw_col = f"{hw:>6}" if hw is not None else f"{'—':>6}"
        print(f"{order:>5} | {name:<24} | {w:>7} | {tw_w:>10} | {d:>5} | {verdict:<4} | {hw_col} | {hook_verdict:<6} | {excerpt_col}")
    print("-" * 110)
    print(f"[報告] tweet_text+URL超過: {len(overflow)}件" if overflow else "全13本、tweet_text+URLで280文字以内。")
    print(f"[報告] excerptにハッシュタグ/URL混入: {len(excerpt_ng)}件" if excerpt_ng else "全13本、excerptにハッシュタグ・URLの混入なし。")
    print(f"[報告] x_hook超過: {len(hook_overflow)}件" if hook_overflow else "全13本、x_hookで280文字以内。")


# ---------------------------------------------------------------------------
# サブコマンド: --preflight（ネット不使用、人が読むための一括出力）
# ---------------------------------------------------------------------------

def cmd_preflight() -> None:
    entries = discover_all_posts()
    lines = [
        "# 公開前プレビュー（サイクル6.0・13本、ネット未使用・全文読み合わせ用）",
        "",
        f"生成日時: {datetime.now(timezone.utc).astimezone().isoformat()}",
        f"対象本数: {len(entries)}",
        "",
    ]
    for e in entries:
        fm, body = e["fm"], e["body"]
        order = fm.get("order", "?")
        lines.append(f"\n{'=' * 80}\n# order {order}: {fm.get('title', '')}\n{'=' * 80}\n")
        lines.append("## frontmatter")
        for k in ("title", "slug", "category", "tags", "lead", "series", "order", "featured", "x_text", "x_hook"):
            lines.append(f"- {k}: {fm.get(k)}")
        if fm.get("x_text"):
            tw = build_tweet_text(fm["x_text"])
            lines.append(f"- tweet_text(+URL)加重文字数: {tweet_draft_len(tw)} / {X_MAX_LEN}")
        if fm.get("x_hook"):
            lines.append(f"- x_hook加重文字数: {hook_len(fm['x_hook'])} / {X_MAX_LEN}")
        featured_path = resolve_featured_path(fm)
        lines.append(f"- featured画像の実在: {'OK' if featured_path else '見つかりません'}")
        lines.append("")
        lines.append("## 本文")
        lines.append(body)
    PREFLIGHT_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFLIGHT_OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[出力] {PREFLIGHT_OUT_PATH}（{len(entries)}本、ネットワーク未使用）")


# ---------------------------------------------------------------------------
# メインフロー（--dry-run / 実公開 共通）
# ---------------------------------------------------------------------------

def run_publish(order_override: str | None, dry_run: bool, force: bool = False) -> None:
    plan_text, row = select_next(order_override)
    article_path = POSTS_DIR / Path(row["file"]).name
    print(f"[選定] order={row['order']} file={article_path.name} status={row['status']!r}")

    md_text = article_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(md_text)
    images = find_images(body)
    print(f"[画像] 本文参照 {len(images)} 枚: {[rel for _, rel in images]}")

    x_text, lead = fm.get("x_text"), fm.get("lead")
    if lead:
        problems = validate_excerpt(lead)
        if problems:
            print(f"[中断] excerpt（lead）にハッシュタグ/URLが含まれています: {', '.join(problems)}")
            sys.exit(1)
        print("[確認] excerpt（lead）にハッシュタグ・URLの混入なし。")

    tweet_text: str | None = None
    if x_text:
        tweet_text = build_tweet_text(x_text)
        d = tweet_draft_len(tweet_text)
        if d > X_MAX_LEN:
            if force:
                print(f"[警告] tweet_text+URLが{d}文字で上限{X_MAX_LEN}を超えています（--forceにより続行）。")
            else:
                print(f"[中断] tweet_text+URLが{d}文字で上限{X_MAX_LEN}を超えています。--forceで続行できます。")
                sys.exit(1)
        else:
            print(f"[確認] tweet_text+URL = {d}文字（上限{X_MAX_LEN}以内）。")

    client = PixBlogClient()
    try:
        existing = find_existing_post_by_title(client, fm.get("title", ""))
    except PixBlogError as e:
        print(f"[警告] 既存記事チェックに失敗しました（続行し、実公開直前に再確認します）: {e}")
        existing = None
    if existing:
        print(f"[中断] 同一タイトルの記事が既にAPI上に存在します: post_id={existing.get('id')} url={existing.get('url')}")
        sys.exit(1)
    print("[確認] 同一タイトルの既存記事なし。")

    if dry_run:
        print("\n===== dry-run: 実公開・画像アップロードは行いません =====")
        featured_path = resolve_featured_path(fm)
        print(f"featured: {fm.get('featured')} -> {'存在確認OK' if featured_path else '[警告]見つかりません'}")
        draft_url = "https://pixblog.net/u/uso8m/(公開後に確定するURL・dry-runのため未確定)"
        x_hook = fm.get("x_hook")
        if x_hook:
            post_set = build_post_set(row["order"], x_hook, fm.get("title", ""), draft_url, fm)
            print()
            print(post_set)
        return

    # ------------------------------------------------------------------
    # 実公開経路
    # ------------------------------------------------------------------
    if len(images) > 0:
        print(f"[中断] 本文に画像参照があります（{len(images)}枚）。この連載は本文画像を使わない設計です（決定③）。")
        sys.exit(1)

    featured_rel = fm.get("featured")
    featured_url = None
    if featured_rel:
        featured_path = DATE_DIR / featured_rel
        featured_url = try_upload_image(client, featured_path)
        if not featured_url:
            print(f"[中断] featured画像のアップロードに失敗しました。POSTは行いません: {featured_rel}")
            sys.exit(1)

    body_with_urls = build_body_with_public_urls(body, {})
    payload = build_publish_payload(fm, body_with_urls, featured_url)

    res = client._request("POST", "/posts", payload)
    post_id = res.get("id") or res.get("post_id")
    public_url = res.get("url") or (f"https://pixblog.net/u/uso8m/{res.get('slug')}" if res.get("slug") else None)

    if post_id:
        pub_res = client.publish(post_id)
        public_url = pub_res.get("url") or public_url
        print(f"[公開完了] post_id={post_id} status={pub_res.get('status')} url={public_url}")
    else:
        print(f"[警告] post_idを取得できず、publishedへの遷移PATCHをスキップしました: {res}")

    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    new_status = f"{STATUS_PUBLISHED_PREFIX}（{today}・{public_url}）"
    update_plan_status(plan_text, row["order"], new_status, public_url or "")

    rows_after = parse_status_table(PLAN_PATH.read_text(encoding="utf-8"))
    published = sum(1 for r in rows_after if r["status"].startswith(STATUS_PUBLISHED_PREFIX))
    update_index_status(published, len(rows_after))

    print()
    x_hook = fm.get("x_hook")
    if x_hook:
        post_set = build_post_set(row["order"], x_hook, fm.get("title", ""), public_url or "", fm)
        print(post_set)
        append_post_set(post_set)
        print(f"[出力] {POST_SETS_PATH} に追記しました。")


# ---------------------------------------------------------------------------
# サブコマンド: --replay NN
# ---------------------------------------------------------------------------

def cmd_replay(order: str) -> None:
    order = order.zfill(2)
    rows = parse_status_table(PLAN_PATH.read_text(encoding="utf-8"))
    for row in rows:
        if row["order"] != order:
            continue
        if not row["status"].startswith(STATUS_PUBLISHED_PREFIX):
            print(f"[中断] order={order} は公開済みなしです（状態: {row['status']!r}）。")
            sys.exit(1)
        article_path = POSTS_DIR / Path(row["file"]).name
        fm, body = parse_frontmatter(article_path.read_text(encoding="utf-8"))
        url = row["url"] if row["url"] and row["url"] != "—" else "（公開URL不明）"
        x_hook = fm.get("x_hook")
        print(f"[再表示] order={order} file={article_path.name} status={row['status']!r}")
        if x_hook:
            print(build_post_set(order, x_hook, fm.get("title", ""), url, fm))
        else:
            print(f"{fm.get('title', '')} {url}")
        return
    print(f"[中断] order={order} は公開済みなしです。")
    sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--order", type=str, default=None)
    parser.add_argument("--check-lengths", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--replay", type=str, default=None)
    args = parser.parse_args()

    if args.check_lengths:
        cmd_check_lengths()
        return
    if args.preflight:
        cmd_preflight()
        return
    if args.replay is not None:
        cmd_replay(args.replay)
        return

    order_override = args.order.zfill(2) if args.order else None
    run_publish(order_override, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
