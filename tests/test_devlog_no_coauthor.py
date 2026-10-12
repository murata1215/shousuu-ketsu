"""
devlogにCo-Authored-Byの行が無いことの検査（サイクル5.0新設）

devlog（`doc/devlog/*.md`）は、コミットメッセージとは別の人間向けの記録で
あり、Git trailerのCo-Authored-By行を書かない方針にした（CLAUDE.mdの
devlogルール参照）。2026-10-11_224741.md・2026-10-12_075200.mdに過去
1行ずつ紛れていたのを見つけ、消した（サイクル5.0）。

「Co-Authored-By」という語そのものではなく、実際のgit trailerの形
（`Co-Authored-By: 名前 <メール>`）だけを検査する。2026-10-12_075200.mdの
ように、過去の混入を訂正の文として振り返って書く場合（バッククォートで
語だけを引用する等）は、trailerの形（コロンの後にメールアドレスまで
続く）にならないため誤検知しない。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEVLOG_DIR = REPO_ROOT / "doc" / "devlog"

_COAUTHOR_TRAILER_RE = re.compile(r"Co-Authored-By:\s*\S.*<\S+@\S+>")


def test_no_coauthored_by_trailer_in_devlog() -> None:
    """doc/devlog/*.md のどのファイルにも、Co-Authored-Byのgit trailerが無いこと"""
    violations: list[str] = []
    for path in sorted(DEVLOG_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _COAUTHOR_TRAILER_RE.search(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert violations == [], f"Co-Authored-Byの行が見つかった箇所: {violations}"
