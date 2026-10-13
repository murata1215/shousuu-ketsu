"""
本戦 l12r12_2002（v0.3形式）の記録でのビューア確認（サイクル3.0、サイクル4.3で
v0.3形式の扱い案Bに合わせて作り直し）

logs/llm/はコミット対象外（.gitignore）のため、本戦の記録がこのマシン上に
無い環境では全てスキップする。v0.3形式の記録はサイクル4.3以降、詳細
（ラウンド・契約・お金の推移）は返さず、席・モデル・最終資産・順位だけを
返す（§v0.3の試合の扱い案B）。本テストはその最小限の事実
（doc/analysis/l12r12_2002_facts.md）が画面のデータとして正しく出ることを
確認する。v0.4/v0.5形式の本番試合の詳細な確認は
tests/test_viewer_real_match_v0_4.py を参照。
"""

from pathlib import Path

import pytest

from viewer.log_parser import get_contracts, get_overview, get_round

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs" / "llm"
GAME_ID = "l12r12_2002"

pytestmark = pytest.mark.skipif(
    not (LOGS_DIR / f"{GAME_ID}_events.jsonl").exists(),
    reason="本戦l12r12_2002の記録がこの環境に無い（logs/はコミット対象外）",
)


@pytest.fixture(scope="module")
def overview() -> dict:
    return get_overview(LOGS_DIR, GAME_ID)


def test_overview_detected_as_legacy_v0_3_format(overview: dict) -> None:
    assert overview is not None
    assert overview["format"] == "v0.3"
    assert overview["completed"] is True
    assert "legacy_notice" in overview
    assert "rounds" not in overview  # 詳細は返さない（案B）


def test_get_round_and_get_contracts_return_none_for_legacy(overview: dict) -> None:
    assert get_round(LOGS_DIR, GAME_ID, 4) is None
    assert get_contracts(LOGS_DIR, GAME_ID) is None


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


def test_loan_amounts_match_facts_report(overview: dict) -> None:
    loans_by_pid = {r["player_id"]: r["loan"] for r in overview["roster"]}
    assert loans_by_pid["P03"] == 5_000_000
    assert loans_by_pid["P12"] == 6_000_000
    assert loans_by_pid["P01"] == 1_200_000
