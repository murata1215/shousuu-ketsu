"""
viewer/server.py のテスト（サイクル3.0）

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
    monkeypatch.setattr(server_module, "LOGS_DIR", FIXTURES)
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


def test_api_overview_ok_for_known_game(client: TestClient) -> None:
    res = client.get("/api/games/fx_demo/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["num_rounds"] == 2
    assert len(data["roster"]) == 3


def test_api_overview_404_for_unknown_game(client: TestClient) -> None:
    res = client.get("/api/games/not_a_game/overview")
    assert res.status_code == 404


def test_api_round_ok_and_clamped(client: TestClient) -> None:
    res = client.get("/api/games/fx_demo/rounds/1")
    assert res.status_code == 200
    assert res.json()["round_num"] == 1

    res_clamped = client.get("/api/games/fx_demo/rounds/999")
    assert res_clamped.status_code == 200
    assert res_clamped.json()["round_num"] == 2


def test_api_round_404_for_unknown_game(client: TestClient) -> None:
    res = client.get("/api/games/not_a_game/rounds/1")
    assert res.status_code == 404


def test_api_contracts_ok(client: TestClient) -> None:
    res = client.get("/api/games/fx_demo/contracts")
    assert res.status_code == 200
    assert len(res.json()["established"]) == 1


def test_api_seat_ok(client: TestClient) -> None:
    res = client.get("/api/games/fx_demo/seats/P03")
    assert res.status_code == 200
    assert res.json()["player_id"] == "P03"


def test_api_seat_404_for_unknown_player(client: TestClient) -> None:
    res = client.get("/api/games/fx_demo/seats/P99")
    assert res.status_code == 404


def test_api_responses_never_leak_email_or_home_path(client: TestClient) -> None:
    for path in (
        "/api/games/fx_demo/overview",
        "/api/games/fx_demo/rounds/1",
        "/api/games/fx_demo/rounds/2",
        "/api/games/fx_demo/contracts",
        "/api/games/fx_demo/seats/P01",
    ):
        body = client.get(path).text
        assert "taro@example.com" not in body
        assert "/home/testuser" not in body


def test_token_protection_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_module, "LOGS_DIR", FIXTURES)
    monkeypatch.setattr(server_module, "TOKEN", "secret123")
    client = TestClient(server_module.app)

    res_no_token = client.get("/api/games")
    assert res_no_token.status_code == 401

    res_query_token = client.get("/api/games", params={"token": "secret123"})
    assert res_query_token.status_code == 200

    res_header_token = client.get("/api/games", headers={"X-Viewer-Token": "secret123"})
    assert res_header_token.status_code == 200
