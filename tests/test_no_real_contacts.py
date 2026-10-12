"""
公開前の自動チェック（実在のメールアドレス・ホームのパスの再混入を機械的に
検知する回帰テスト、サイクル3.1新設・サイクル5.0でホームのパスの検査を追加）

サイクル3.0のコミットでtests/配下に実在のメールアドレスが混入し、公開リポジトリに
pushされた事故を踏まえる（CLAUDE.md「過去の落とし穴」参照）。gitで管理している
全ファイルをテキストとして走査し、メールアドレスらしき文字列が無いことを確認する。

許可するドメイン・ローカル部:
- `example.com` / `example.org` / `example.net`（RFC 2606のテスト用ドメイン）
- `noreply@...`（コミット属性等の定型アドレス）
- `git@github.com`（devlogに記録されたSSH remoteの記述。実際はURLでメールではない）

バイナリファイル（画像等）はデコード不能なためスキップする。

サイクル5.0で、実在のホームのパス（例: `/home/<利用者名>`）が書かれていないかの
検査を足した。パスはコードに直接書かず、`Path.home()`で実行時の環境から取る
（`viewer/redact.py::_os_username`と同じ流儀）。`/home/testuser`等の架空のパス
（CLAUDE.md落とし穴⑥）や、`/home/devrelay/...`のような別マシンの共有配置先
（`doc/viewer_operations.md`のCaddy設定例）は、実行時のホームと文字列が一致
しないため自然に対象外になる（本ファイル自身がこの検査に通ることが、
検査が実際に効いていることの確認でもある）。
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


def test_no_real_home_path_in_tracked_files() -> None:
    """
    git管理下の全ファイルに、実行時の環境のホームのパスがそのまま書かれていないこと
    （サイクル5.0新設）

    パスはコードに直接書かず、`Path.home()`で実行時の環境から取る。
    極端に短い・取得できない環境（ホームが"/"など）では意味のある検査に
    ならないため、その場合は検査をスキップする。
    """
    home = str(Path.home())
    if len(home) < 4:
        return
    violations: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # バイナリ(画像等)はスキップ
        for lineno, line in enumerate(text.splitlines(), start=1):
            if home in line:
                # 値そのものは報告に出さない（ファイル名・行番号だけ）
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert violations == [], f"実在のホームのパスが見つかった箇所: {violations}"
