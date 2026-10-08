"""
viewer/log_parser.py のテスト（サイクル3.0）

見本の記録（tests/fixtures/viewer_logs/）で各APIデータが揃うこと、
一覧がBot試合・動作確認・本文の無い旧形式を正しく扱うことを確認する。
"""

from pathlib import Path

from viewer.log_parser import (
    fold_assets,
    get_contracts,
    get_overview,
    get_round,
    get_seat,
    list_games,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "viewer_logs"


def test_list_games_excludes_bot_and_includes_full_seats() -> None:
    games = list_games(FIXTURES)
    ids = {g["game_id"] for g in games}
    assert "fx_bot4" not in ids  # 4席はBot試合扱いで出さない
    assert "fx_full12" in ids  # 12席は出す
    assert "fx_demo" not in ids  # fx_demoは3席なので一覧には出ない（直接アクセスは可）


def test_list_games_transcript_flag() -> None:
    games = {g["game_id"]: g for g in list_games(FIXTURES)}
    full = games["fx_full12"]
    assert full["transcript_available"] is False  # NEGOTIATION_ACTIONイベント自体が無い


def test_fold_assets_matches_game_end_for_fx_demo() -> None:
    import json
    events = [json.loads(line) for line in (FIXTURES / "fx_demo_events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    snapshots = fold_assets(events)
    final_assets = {"P01": 1300000, "P02": 850000, "P03": 1450000}
    r2 = snapshots[2]
    for pid, expected in final_assets.items():
        assert r2[pid]["asset"] == expected


def test_get_overview_basic_shape() -> None:
    overview = get_overview(FIXTURES, "fx_demo")
    assert overview is not None
    assert overview["completed"] is True
    assert overview["num_rounds"] == 2
    roster_by_pid = {r["player_id"]: r for r in overview["roster"]}
    assert roster_by_pid["P01"]["model_label"] == "claude-opus-5"
    assert roster_by_pid["P01"]["final_assets"] == 1300000
    assert roster_by_pid["P01"]["final_rank"] == 2
    assert overview["established_contracts"] == 1
    assert overview["contract_payments"] == 1
    assert overview["accidents"] == {
        "timeouts": 1, "server_errors": 1, "invalid_responses": 1, "auto_commits": 1,
    }
    r1 = overview["rounds"][0]
    assert r1["yes_count"] == 2 and r1["no_count"] == 1
    assert r1["minority_side"] == "NO"
    assert overview["asset_timeline"]["rounds"] == [1, 2]


def test_get_overview_redacts_contained_strings() -> None:
    overview = get_overview(FIXTURES, "fx_demo")
    serialized = str(overview)
    assert "taro@example.com" not in serialized


def test_get_overview_unknown_game_returns_none() -> None:
    assert get_overview(FIXTURES, "fx_does_not_exist") is None


def test_get_round_basic_shape_and_memo_attached() -> None:
    round1 = get_round(FIXTURES, "fx_demo", 1)
    assert round1 is not None
    assert round1["question"] == "水を飲むと喉が潤う"
    broadcasts = [e for e in round1["timeline"] if e["kind"] == "negotiation" and e["action"] == "broadcast"]
    assert broadcasts
    assert broadcasts[0]["memo"]["vote_plan"] == "YES"
    votes = [e for e in round1["timeline"] if e["kind"] == "vote_result"]
    assert votes and votes[0]["minority_side"] == "NO"
    payments = [e for e in round1["timeline"] if e["kind"] == "contract_payment"]
    assert payments and payments[0]["paid"] == 150000


def test_get_round_contract_established_has_obligations() -> None:
    """成立契約の行に義務の中身が付く（契約タブと同じCONTRACT_PROPOSEDの中身。サイクル3.1）"""
    round1 = get_round(FIXTURES, "fx_demo", 1)
    established = [e for e in round1["timeline"] if e["kind"] == "contract_established"]
    assert established and established[0]["contract_id"] == "C_FIX0001"
    obligations = established[0]["obligations"]
    assert obligations and obligations[0]["ob_type"] == "type_b_vote"


def test_get_round_clamps_out_of_range() -> None:
    clamped_high = get_round(FIXTURES, "fx_demo", 99)
    assert clamped_high["round_num"] == 2
    clamped_low = get_round(FIXTURES, "fx_demo", 0)
    assert clamped_low["round_num"] == 1


def test_get_round_accidents_present_in_round2() -> None:
    round2 = get_round(FIXTURES, "fx_demo", 2)
    kinds = {a["kind"] for a in round2["accidents"]}
    assert "timeout" in kinds
    assert "server_error" in kinds
    assert "invalid_response" in kinds
    auto_commit_items = [e for e in round2["timeline"] if e["kind"] == "accident" and e["accident_type"] == "auto_commit"]
    assert auto_commit_items and auto_commit_items[0]["player_id"] == "P03"


def test_get_round_redacts_message_text() -> None:
    round2 = get_round(FIXTURES, "fx_demo", 1)
    serialized = str(round2)
    assert "taro@example.com" not in serialized
    assert "/home/testuser" not in serialized


def test_get_round_unknown_game_returns_none() -> None:
    assert get_round(FIXTURES, "fx_does_not_exist", 1) is None


def test_get_contracts_outcome_classification() -> None:
    contracts = get_contracts(FIXTURES, "fx_demo")
    assert contracts is not None
    assert len(contracts["established"]) == 1
    c = contracts["established"][0]
    assert c["contract_seq"] == 1
    outcomes = {ob["obligor"]: ob["outcome"] for ob in c["obligations"]}
    assert outcomes["P03"] == "履行"  # 型B、違約金支払いイベントが無い＝履行
    assert outcomes["P02"] == "支払い発生"  # 型C、条件成立して支払われた
    assert contracts["proposed_total"] == 1
    assert contracts["expired"] == []


def test_get_seat_basic_shape() -> None:
    seat = get_seat(FIXTURES, "fx_demo", "P03")
    assert seat is not None
    assert seat["model_label"] == "claude-haiku-4-5"
    assert seat["votes"] == {1: "NO", 2: "YES"}  # R2はAUTO_COMMIT
    assert "C_FIX0001" in seat["contract_ids"]
    assert seat["final_assets"] == 1450000
    dm_received = [m for m in seat["messages"] if m["kind"] == "dm" and m["to"] == "P03"]
    assert dm_received
    assert seat["post_game_reflection"]["comment"]


def test_get_seat_unknown_player_returns_none() -> None:
    assert get_seat(FIXTURES, "fx_demo", "P99") is None


def test_get_seat_redacts_post_game_reflection() -> None:
    seat = get_seat(FIXTURES, "fx_demo", "P01")
    assert "taro@example.com" not in str(seat)
    assert "/home/testuser" not in str(seat)


def test_old_format_without_message_is_transcript_unavailable() -> None:
    overview = get_overview(FIXTURES, "fx_old_nomsg")
    assert overview is not None
    assert overview["transcript_available"] is False
