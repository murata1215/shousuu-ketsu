"""
`scripts/build_blog_posts.py` のテスト（サイクル7.3新設）

最も重要な固定点: 第1戦（`t12_r4_4501c`）の設定を`blog/match_config.py`経由で再構築しても、
既に公開済みの`doc/blog/20261013/posts/*.md`と1バイトも変わらないこと
（`blog/match_config.py::BlogMatch.title_prefix`等が match_no==1 で空文字になる設計の
回帰テスト）。`logs/`はコミット対象外のため、この環境に無い場合はskipする。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from blog.match_config import MATCHES  # noqa: E402

M1 = MATCHES["20261013"]
REAL_LOGS_DIR = PROJECT_ROOT / "logs" / "llm_v0_4"

pytestmark_real = pytest.mark.skipif(
    not (REAL_LOGS_DIR / f"{M1.game_id}_events.jsonl").exists(),
    reason="本番試合t12_r4_4501cの記録がこの環境に無い（logs/はコミット対象外）",
)


@pytestmark_real
def test_rebuild_summary_is_byte_identical() -> None:
    from scripts.build_blog_posts import build_summary_post

    built = build_summary_post(M1)
    existing = (M1.posts_dir / "01_summary.md").read_text(encoding="utf-8")
    assert built == existing


@pytestmark_real
@pytest.mark.parametrize("pid", ["P07", "P06"])
def test_rebuild_memoir_is_byte_identical(pid) -> None:
    from scripts.build_blog_posts import build_memoir_post

    built = build_memoir_post(M1, pid)
    order = M1.order_of(pid)
    existing = (M1.posts_dir / f"{order}_memoir_{pid}.md").read_text(encoding="utf-8")
    assert built == existing


def test_title_prefix_applied_once_for_match2_and_absent_for_match1() -> None:
    from scripts.build_blog_posts import build_summary_post

    m2 = MATCHES["20261023"]
    if not (REAL_LOGS_DIR / f"{m2.game_id}_events.jsonl").exists():
        pytest.skip("本番試合t17_r4_7002の記録がこの環境に無い（logs/はコミット対象外）")
    built2 = build_summary_post(m2)
    assert built2.count("【少数決 第2戦】") == 1

    if not (REAL_LOGS_DIR / f"{M1.game_id}_events.jsonl").exists():
        pytest.skip("本番試合t12_r4_4501cの記録がこの環境に無い（logs/はコミット対象外）")
    built1 = build_summary_post(M1)
    assert "【少数決" not in built1
