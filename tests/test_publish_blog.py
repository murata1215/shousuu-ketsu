"""
公開の段取り（`scripts/publish_blog.py`）のテスト（サイクル6.0新設、サイクル7.3で
複数試合に対応）

PixBlogへのネットワークアクセスを要する経路（実公開・`--dry-run`の既存記事チェック）は
対象外にし、純粋関数（X加重文字数・frontmatterのパース・plan.mdの「## 投稿状態」節の
読み書き・投稿セットの組み立て）だけを確認する。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "publish_blog.py"

spec = importlib.util.spec_from_file_location("publish_blog", SCRIPT_PATH)
publish_blog = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(PROJECT_ROOT))
spec.loader.exec_module(publish_blog)

from blog.match_config import MATCHES  # noqa: E402

M1 = MATCHES["20261013"]  # 公開済み。postsディレクトリが常に存在する


def _configure(date: str) -> None:
    publish_blog.configure(date)


# ---------------------------------------------------------------------------
# X 加重文字数
# ---------------------------------------------------------------------------

def test_x_weighted_len_ascii_is_one_each() -> None:
    assert publish_blog.x_weighted_len("abc") == 3


def test_x_weighted_len_japanese_is_two_each() -> None:
    assert publish_blog.x_weighted_len("山分け") == 6


def test_build_tweet_text_appends_hashtag_no_url() -> None:
    tw = publish_blog.build_tweet_text("本文です")
    assert tw == "本文です #嘘八百万"
    assert "http" not in tw


def test_tweet_draft_len_includes_url_weight() -> None:
    tw = "a" * 10
    d = publish_blog.tweet_draft_len(tw)
    assert d == 10 + 1 + publish_blog.X_URL_WEIGHT


def test_validate_excerpt_detects_hashtag_and_url() -> None:
    assert publish_blog.validate_excerpt("普通の文") == []
    assert "ハッシュタグ" in publish_blog.validate_excerpt("#嘘八百万 の記事")[0]
    assert "URL" in publish_blog.validate_excerpt("https://example.com を見て")[0]


# ---------------------------------------------------------------------------
# frontmatter / 本文パース
# ---------------------------------------------------------------------------

def test_parse_frontmatter_basic() -> None:
    md = (
        "---\n"
        "title: テスト記事\n"
        "slug: sk-test-01\n"
        "tags: [a, b, c]\n"
        'x_text: "引用符つきの値"\n'
        "---\n\n"
        "本文です。\n"
    )
    fm, body = publish_blog.parse_frontmatter(md)
    assert fm["title"] == "テスト記事"
    assert fm["slug"] == "sk-test-01"
    assert fm["tags"] == ["a", "b", "c"]
    assert fm["x_text"] == "引用符つきの値"
    assert body.strip() == "本文です。"


def test_parse_frontmatter_missing_raises() -> None:
    with pytest.raises(ValueError):
        publish_blog.parse_frontmatter("本文だけでfrontmatterが無い")


def test_find_images_empty_for_no_inline_images() -> None:
    """この連載は本文に画像を使わない設計（決定③）。実際の投稿ファイルで確認する。"""
    assert publish_blog.find_images("本文中に画像参照は無い。") == []


def test_discover_all_posts_finds_all_posts_in_order() -> None:
    """公開済みの第1戦（13本）で確認する。第2戦は生成後に別途確認する。"""
    orig = publish_blog.MATCH.date
    _configure(M1.date)
    try:
        entries = publish_blog.discover_all_posts()
        assert len(entries) == M1.post_count
        orders = [int(e["fm"]["order"]) for e in entries]
        assert orders == list(range(1, M1.post_count + 1))
        for e in entries:
            assert publish_blog.find_images(e["body"]) == [], f"{e['path'].name}: 本文に画像参照がある"
    finally:
        _configure(orig)


def test_featured_relpath_str_is_relative_not_absolute() -> None:
    """投稿セットの画像の場所は相対パスで書く（ホームのパスを含まない）。"""
    orig = publish_blog.MATCH.date
    _configure(M1.date)
    try:
        fm = {"featured": "images/cards/13_P06_card.png"}
        rel = publish_blog.featured_relpath_str(fm)
        assert rel == "doc/blog/20261013/images/cards/13_P06_card.png"
        assert not rel.startswith("/")
        assert str(Path.home()) not in rel
    finally:
        _configure(orig)


# ---------------------------------------------------------------------------
# plan.md の「## 投稿状態」節パース・更新
# ---------------------------------------------------------------------------

_SAMPLE_PLAN = """# サンプルplan.md

## スロット表

| order | 席 |
|---|---|
| 01 | 総括 |

## 投稿状態

| order | ファイル | 状態 | 公開URL |
|---|---|---|---|
| 01 | `posts/01_summary.md` | 原稿済（未投稿） | — |
| 02 | `posts/02_memoir_P07.md` | 原稿済（未投稿） | — |

## アイキャッチ

| order | 席 | 表情 |
|---|---|---|
| 01 | 総括 | — |
"""


def test_parse_status_table_reads_rows() -> None:
    rows = publish_blog.parse_status_table(_SAMPLE_PLAN)
    assert len(rows) == 2
    assert rows[0] == {"order": "01", "file": "posts/01_summary.md", "status": "原稿済（未投稿）", "url": "—"}


def test_update_plan_status_only_touches_target_section(tmp_path) -> None:
    """談合カードで実際に起きた事故の再発防止: 「## 投稿状態」節より前・後にある
    別の表（スロット表・アイキャッチ表）の行は一切書き換えない。
    """
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(_SAMPLE_PLAN, encoding="utf-8")

    orig_plan_path = publish_blog.PLAN_PATH
    publish_blog.PLAN_PATH = plan_path
    try:
        publish_blog.update_plan_status(_SAMPLE_PLAN, "01", "公開済（2026-10-13・https://example.com/post）", "https://example.com/post")
        new_text = plan_path.read_text(encoding="utf-8")
    finally:
        publish_blog.PLAN_PATH = orig_plan_path

    # 投稿状態節の01行だけが更新されている
    assert "| 01 | `posts/01_summary.md` | 公開済（2026-10-13・https://example.com/post） | https://example.com/post |" in new_text
    # 02行は無変更
    assert "| 02 | `posts/02_memoir_P07.md` | 原稿済（未投稿） | — |" in new_text
    # スロット表・アイキャッチ表の "01" を含む行は無変更（事故の再発防止）
    assert "| 01 | 総括 |" in new_text
    assert "| 01 | 総括 | — |" in new_text


def test_update_index_status_updates_only_date_row(tmp_path) -> None:
    orig_date = publish_blog.MATCH.date
    _configure(M1.date)
    try:
        index_path = tmp_path / "INDEX.md"
        index_path.write_text(
            "# 索引\n\n| 試合日 | 試合ID | 状態 |\n|---|---|---|\n"
            f"| {M1.index_date} | {M1.game_id} | 公開中（0/13） |\n",
            encoding="utf-8",
        )
        orig_index = publish_blog.INDEX_PATH
        publish_blog.INDEX_PATH = index_path
        try:
            publish_blog.update_index_status(5, 13)
            text = index_path.read_text(encoding="utf-8")
        finally:
            publish_blog.INDEX_PATH = orig_index
        assert "公開中（5/13）" in text
    finally:
        _configure(orig_date)


# ---------------------------------------------------------------------------
# 投稿セット
# ---------------------------------------------------------------------------

def test_build_post_set_contains_hook_title_url_and_relative_image() -> None:
    orig = publish_blog.MATCH.date
    _configure(M1.date)
    try:
        fm = {"featured": "images/cards/01_summary_card.png"}
        post_set = publish_blog.build_post_set("01", "フック文 #嘘八百万", "タイトル", "https://example.com/post", fm)
        assert "フック文 #嘘八百万" in post_set
        assert "タイトル" in post_set
        assert "https://example.com/post" in post_set
        assert "doc/blog/20261013/images/cards/01_summary_card.png" in post_set
        assert str(Path.home()) not in post_set
    finally:
        _configure(orig)


# ---------------------------------------------------------------------------
# CLIサブコマンド（ネットワーク不使用のもののみ）
# ---------------------------------------------------------------------------

def test_cli_check_lengths_runs_without_network(tmp_path) -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--date", M1.date, "--check-lengths"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0
    assert "280文字以内" in res.stdout or "超過" in res.stdout


def test_cli_preflight_runs_without_network_and_writes_output() -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--date", M1.date, "--preflight"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0
    assert M1.preflight_path.exists()
    text = M1.preflight_path.read_text(encoding="utf-8")
    assert "order 01" in text
    assert f"order {M1.post_count:02d}" in text
