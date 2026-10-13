"""
観戦ビューア FastAPI サーバー（サイクル3.0新設、dangou-card viewer/server.pyを
縮小・流用。サイクル4.3でv0.4/v0.5対応・複数置き場対応）

神視点のみ（§作るもの「すべて神視点」）のため、dangou-card/gentei-jankenに
あるVIEWER_GOD_TOKEN・view=god/publicの切替は持ち込まない
（CLAUDE.md「過去の落とし穴③見た目だけ移して機能が置き去り」対策:
使わない仕組みをコピーしない）。

環境変数で設定可能:
  VIEWER_HOST       バインドアドレス（既定: 127.0.0.1）
  VIEWER_PORT       ポート（既定: 9028）
  VIEWER_ROOT_PATH  サブパス配信用（既定: 空）
  VIEWER_LOG_ROOTS  ログディレクトリ（カンマ区切り、複数指定可。既定:
                     logs/llm_v0_4,logs/llm。サイクル4.3新設:
                     v0.4/v0.5の記録とv0.3の記録を両方見る）
  VIEWER_LOG_ROOT   旧名（単一指定、互換のため残す。VIEWER_LOG_ROOTS未設定時
                     のみ使う）
  VIEWER_TOKEN      簡易認証トークン（未設定: 認証なし）
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from viewer.log_parser import (
    get_contracts, get_highlights, get_money, get_overview, get_round, get_seat,
    list_games, resolve_event,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

HOST = os.environ.get("VIEWER_HOST", "127.0.0.1")
PORT = int(os.environ.get("VIEWER_PORT", "9028"))
ROOT_PATH = os.environ.get("VIEWER_ROOT_PATH", "")


def _default_log_roots() -> list[Path]:
    """
    既定の置き場2つ（v0.4/v0.5の記録・v0.3の記録）。`VIEWER_LOG_ROOTS`
    （カンマ区切り）→ 旧名`VIEWER_LOG_ROOT`（単一）→ 既定の順で決める。
    """
    roots_env = os.environ.get("VIEWER_LOG_ROOTS")
    if roots_env:
        return [Path(p.strip()) for p in roots_env.split(",") if p.strip()]
    single_env = os.environ.get("VIEWER_LOG_ROOT")
    if single_env:
        return [Path(single_env)]
    return [_PROJECT_ROOT / "logs" / "llm_v0_4", _PROJECT_ROOT / "logs" / "llm"]


LOGS_DIR: list[Path] = _default_log_roots()
TOKEN = os.environ.get("VIEWER_TOKEN", "")

app = FastAPI(title="嘘八百万 —少数決— 観戦ビューア", version="2.0", root_path=ROOT_PATH)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


async def check_token(request: Request, token: Optional[str] = Query(None)) -> None:
    """VIEWER_TOKEN設定時: ?token= クエリまたは X-Viewer-Token ヘッダで照合。未設定時は認証なし"""
    if not TOKEN:
        return
    provided = token or request.headers.get("X-Viewer-Token", "")
    if provided != TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/")
async def index(_: None = Depends(check_token)) -> FileResponse:
    """試合一覧ページ"""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/watch")
async def watch(_: None = Depends(check_token)) -> FileResponse:
    """試合のページ本体（相対パス依存のため末尾スラッシュ無しで配信）"""
    return FileResponse(str(STATIC_DIR / "watch.html"))


@app.get("/api/games")
async def api_games(_: None = Depends(check_token)) -> list[dict]:
    """席の割り当てがある試合の一覧を返す"""
    return list_games(LOGS_DIR)


@app.get("/api/games/{game_id}/overview")
async def api_overview(game_id: str, _: None = Depends(check_token)) -> dict:
    data = get_overview(LOGS_DIR, game_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return data


@app.get("/api/games/{game_id}/rounds/{round_num}")
async def api_round(game_id: str, round_num: int, _: None = Depends(check_token)) -> dict:
    data = get_round(LOGS_DIR, game_id, round_num)
    if data is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return data


@app.get("/api/games/{game_id}/contracts")
async def api_contracts(game_id: str, _: None = Depends(check_token)) -> dict:
    data = get_contracts(LOGS_DIR, game_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return data


@app.get("/api/games/{game_id}/money")
async def api_money(game_id: str, _: None = Depends(check_token)) -> dict:
    data = get_money(LOGS_DIR, game_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return data


@app.get("/api/games/{game_id}/highlights")
async def api_highlights(game_id: str, _: None = Depends(check_token)) -> dict:
    data = get_highlights(LOGS_DIR, game_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return data


@app.get("/api/games/{game_id}/seats/{pid}")
async def api_seat(game_id: str, pid: str, _: None = Depends(check_token)) -> dict:
    data = get_seat(LOGS_DIR, game_id, pid)
    if data is None:
        raise HTTPException(status_code=404, detail="Game or seat not found")
    return data


@app.get("/api/games/{game_id}/events/{line}")
async def api_event(game_id: str, line: int, _: None = Depends(check_token)) -> dict:
    """記録ファイルの行番号から、その場面（タブ・ラウンド・投票・巡・席）を解決する（§直リンク）"""
    data = resolve_event(LOGS_DIR, game_id, line)
    if data is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return data


def main() -> None:
    """サーバー起動"""
    import uvicorn

    print("=== 嘘八百万 —少数決— 観戦ビューア ===")
    print(f"URL: http://{HOST}:{PORT}")
    if ROOT_PATH:
        print(f"Root path: {ROOT_PATH}")
    print(f"認証: {'有効（VIEWER_TOKEN設定済み）' if TOKEN else 'なし（公開アクセス可）'}")
    print(f"ログ: {', '.join(str(p) for p in LOGS_DIR)}")
    print("---")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
