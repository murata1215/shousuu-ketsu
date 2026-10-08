"""
実在のメールアドレスの再混入を機械的に検知する回帰テスト（サイクル3.1新設）

サイクル3.0のコミットでtests/配下に実在のメールアドレスが混入し、公開リポジトリに
pushされた事故を踏まえる（CLAUDE.md「過去の落とし穴」参照）。gitで管理している
全ファイルをテキストとして走査し、メールアドレスらしき文字列が無いことを確認する。

許可するドメイン・ローカル部:
- `example.com` / `example.org` / `example.net`（RFC 2606のテスト用ドメイン）
- `noreply@...`（コミット属性等の定型アドレス）
- `git@github.com`（devlogに記録されたSSH remoteの記述。実際はURLでメールではない）

バイナリファイル（画像等）はデコード不能なためスキップする。
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ALLOWED_DOMAINS = ("example.com", "example.org", "example.net", "github.com")
_ALLOWED_LOCAL_PARTS = ("noreply",)


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout
    return [REPO_ROOT / p for p in out.decode("utf-8").split("\0") if p]


def _is_allowed_email(addr: str) -> bool:
    local, _, domain = addr.partition("@")
    if domain in _ALLOWED_DOMAINS:
        return True
    if local in _ALLOWED_LOCAL_PARTS:
        return True
    return False


def test_no_real_email_addresses_in_tracked_files() -> None:
    """git管理下の全ファイルに、example.com/noreply/github.com以外のメールらしき文字列が無いこと"""
    violations: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # バイナリ(画像等)はスキップ
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _EMAIL_RE.findall(line):
                if not _is_allowed_email(match):
                    # 値そのものは報告に出さない（ファイル名・行番号だけ）
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert violations == [], f"許可外のメールらしき文字列が見つかった箇所: {violations}"
