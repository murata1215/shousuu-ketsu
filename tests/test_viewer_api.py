"""
viewer/server.py のテスト（サイクル3.0新設、サイクル4.3でv0.4/v0.5対応・
複数置き場・新しいエンドポイントに対応）

FastAPIのTestClientで、/・/watch・各APIが開けること、見本記録のデータが
出ること、未知の試合/席で404になることを確認する（AIは呼ばない）。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import viewer.server as server_module

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "viewer_logs"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(server_module, "LOGS_DIR", [FIXTURES])
    monkeypatch.setattr(server_module, "TOKEN", "")
    return TestClient(server_module.app)


def test_index_page_ok(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_watch_page_ok(client: TestClient) -> None:
    res = client.get("/watch")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_api_games_excludes_bot_matches(client: TestClient) -> None:
    res = client.get("/api/games")
    assert res.status_code == 200
    ids = {g["game_id"] for g in res.json()}
    assert "fx_bot4" not in ids
    assert "fx_full12" in ids
    assert "fx_v05" in ids


def test_api_overview_ok_for_known_game(client: TestClient) -> None:
    res = client.get("/api/games/fx_v05/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["num_rounds"] == 2
    assert len(data["roster"]) == 12


def test_api_overview_404_for_unknown_game(client: TestClient) -> None:
    res = client.get("/api/games/not_a_game/overview")
    assert res.status_code == 404


def test_api_round_ok_and_clamped(client: TestClient) -> None:
    res = client.get("/api/games/fx_v05/rounds/1")
    assert res.status_code == 200
    assert res.json()["round_num"] == 1

    res_clamped = client.get("/api/games/fx_v05/rounds/999")
    assert res_clamped.status_code == 200
    assert res_clamped.json()["round_num"] == 2


def test_api_round_404_for_unknown_game(client: TestClient) -> None:
    res = client.get("/api/games/not_a_game/rounds/1")
    assert res.status_code == 404


def test_api_contracts_ok(client: TestClient) -> None:
    res = client.get("/api/games/fx_v05/contracts")
    assert res.status_code == 200
    assert len(res.json()["established"]) == 3


def test_api_money_ok_and_reconciled(client: TestClient) -> None:
    res = client.get("/api/games/fx_v05/money")
    assert res.status_code == 200
    assert res.json()["reconciled"] is True


def test_api_highlights_ok(client: TestClient) -> None:
    res = client.get("/api/games/fx_v05/highlights")
    assert res.status_code == 200
    data = res.json()
    assert data["solo_wins"]


def test_api_seat_ok(client: TestClient) -> None:
    res = client.get("/api/games/fx_v05/seats/P06")
    assert res.status_code == 200
    assert res.json()["player_id"] == "P06"


def test_api_seat_404_for_unknown_player(client: TestClient) -> None:
    res = client.get("/api/games/fx_v05/seats/P99")
    assert res.status_code == 404


def test_api_event_resolves_scene(client: TestClient) -> None:
    res = client.get("/api/games/fx_v05/events/48")  # pool contract propose
    assert res.status_code == 200
    data = res.json()
    assert data["scene"]["round"] == 1
    assert data["scene"]["vote"] == 1
    assert data["scene"]["contract"] == "C_FIXPOOL1"


def test_api_event_404_for_unknown_line(client: TestClient) -> None:
    res = client.get("/api/games/fx_v05/events/999999")
    assert res.status_code == 404


def test_api_responses_never_leak_email_or_home_path(client: TestClient) -> None:
    for path in (
        "/api/games/fx_v05/overview",
        "/api/games/fx_v05/rounds/1",
        "/api/games/fx_v05/rounds/2",
        "/api/games/fx_v05/contracts",
        "/api/games/fx_v05/money",
        "/api/games/fx_v05/highlights",
        "/api/games/fx_v05/seats/P01",
    ):
        body = client.get(path).text
        assert "taro@example.com" not in body
        assert "/home/testuser" not in body


def test_token_protection_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_module, "LOGS_DIR", [FIXTURES])
    monkeypatch.setattr(server_module, "TOKEN", "secret123")
    client = TestClient(server_module.app)

    res_no_token = client.get("/api/games")
    assert res_no_token.status_code == 401

    res_query_token = client.get("/api/games", params={"token": "secret123"})
    assert res_query_token.status_code == 200

    res_header_token = client.get("/api/games", headers={"X-Viewer-Token": "secret123"})
    assert res_header_token.status_code == 200


def test_default_log_roots_include_v0_4_and_v0_3(monkeypatch: pytest.MonkeyPatch) -> None:
    """既定の置き場はlogs/llm_v0_4とlogs/llmの両方（VIEWER_LOG_ROOTS未設定時）"""
    monkeypatch.delenv("VIEWER_LOG_ROOTS", raising=False)
    monkeypatch.delenv("VIEWER_LOG_ROOT", raising=False)
    roots = server_module._default_log_roots()
    names = [r.name for r in roots]
    assert names == ["llm_v0_4", "llm"]


def test_viewer_log_roots_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIEWER_LOG_ROOTS", "/tmp/a,/tmp/b")
    roots = server_module._default_log_roots()
    assert [str(r) for r in roots] == ["/tmp/a", "/tmp/b"]


def test_legacy_viewer_log_root_env_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIEWER_LOG_ROOTS", raising=False)
    monkeypatch.setenv("VIEWER_LOG_ROOT", "/tmp/legacy")
    roots = server_module._default_log_roots()
    assert [str(r) for r in roots] == ["/tmp/legacy"]
