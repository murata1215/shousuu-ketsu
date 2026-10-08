"""
本戦 l12r12_2002 の記録でのビューア確認（サイクル3.0）

logs/llm/はコミット対象外（.gitignore）のため、本戦の記録がこのマシン上に
無い環境では全てスキップする。あるときだけ、doc/analysis/l12r12_2002_facts.md
に書かれた事実（R4・R9が11対1、R8・R11が6対6、成立契約56本、型Cの支払い
13件、最終順位）が画面のデータとして正しく出ることを確認する。
"""

from pathlib import Path

import pytest

from viewer.log_parser import fold_assets, get_contracts, get_overview, get_round

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs" / "llm"
GAME_ID = "l12r12_2002"

pytestmark = pytest.mark.skipif(
    not (LOGS_DIR / f"{GAME_ID}_events.jsonl").exists(),
    reason="本戦l12r12_2002の記録がこの環境に無い（logs/はコミット対象外）",
)


@pytest.fixture(scope="module")
def overview() -> dict:
    return get_overview(LOGS_DIR, GAME_ID)


def test_overview_found_and_completed(overview: dict) -> None:
    assert overview is not None
    assert overview["completed"] is True
    assert overview["num_rounds"] == 12


def test_r4_and_r9_are_11_to_1(overview: dict) -> None:
    rounds = {r["round_num"]: r for r in overview["rounds"]}
    for rn in (4, 9):
        assert {rounds[rn]["yes_count"], rounds[rn]["no_count"]} == {11, 1}
    assert rounds[4]["minority_ids"] == ["P02"]
    assert rounds[9]["minority_ids"] == ["P02"]


def test_r8_and_r11_are_6_to_6_tie() -> None:
    round8 = get_round(LOGS_DIR, GAME_ID, 8)
    round11 = get_round(LOGS_DIR, GAME_ID, 11)
    for data in (round8, round11):
        votes = next(e for e in data["timeline"] if e["kind"] == "vote_result")["votes"]
        yes = sum(1 for v in votes.values() if v == "YES")
        no = sum(1 for v in votes.values() if v == "NO")
        assert yes == 6 and no == 6


def test_established_contracts_count_is_56(overview: dict) -> None:
    assert overview["established_contracts"] == 56


def test_type_c_payments_count_is_13() -> None:
    contracts = get_contracts(LOGS_DIR, GAME_ID)
    type_c_payments = [
        ob for c in contracts["established"] for ob in c["obligations"]
        if ob["ob_type"] == "type_c_conditional" and ob["outcome"] == "支払い発生"
    ]
    assert len(type_c_payments) == 13


def test_no_type_b_violations() -> None:
    contracts = get_contracts(LOGS_DIR, GAME_ID)
    violated = [
        ob for c in contracts["established"] for ob in c["obligations"]
        if ob["ob_type"] == "type_b_vote" and ob["outcome"] == "違約金支払い"
    ]
    assert violated == []


def test_final_ranks_match_facts_report(overview: dict) -> None:
    expected_ranks = {
        "P02": 1, "P04": 2, "P08": 3, "P10": 4, "P11": 5, "P07": 6,
        "P09": 7, "P05": 8, "P06": 9, "P01": 10, "P12": 11, "P03": 12,
    }
    actual_ranks = {r["player_id"]: r["final_rank"] for r in overview["roster"]}
    assert actual_ranks == expected_ranks


def test_final_assets_match_facts_report(overview: dict) -> None:
    expected_assets = {
        "P02": 2775251, "P04": 425251, "P08": 155251, "P10": 135251,
        "P11": -14749, "P07": -84749, "P09": -654749, "P05": -714749,
        "P06": -794749, "P01": -1194749, "P12": -1693715, "P03": -2838096,
    }
    actual_assets = {r["player_id"]: r["final_assets"] for r in overview["roster"]}
    assert actual_assets == expected_assets


def test_asset_fold_matches_game_end_exactly() -> None:
    import json
    events = [
        json.loads(line)
        for line in (LOGS_DIR / f"{GAME_ID}_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    snapshots = fold_assets(events)
    final = [e for e in events if e["event_type"] == "GAME_END"][0]["data"]["final_assets"]
    r12 = snapshots[12]
    for pid, expected in final.items():
        assert r12[pid]["asset"] == expected


def test_loan_amounts_match_facts_report(overview: dict) -> None:
    loans_by_pid = {r["player_id"]: r["loan"] for r in overview["roster"]}
    assert loans_by_pid["P03"] == 5_000_000
    assert loans_by_pid["P12"] == 6_000_000
    assert loans_by_pid["P01"] == 1_200_000
