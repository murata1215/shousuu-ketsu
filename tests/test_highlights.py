"""
viewer/highlights.py のテスト（サイクル4.3新設）

見どころ5種（嘘・単独勝ち・型Bの違反・山分けの支払い・やり直し/打ち切り）が
記録から正しく拾えることを確認する。
"""

import json
from pathlib import Path

from viewer.highlights import build_highlights
from viewer.log_index import build_entries

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "viewer_logs"


def _load_events(game_id: str) -> list[dict]:
    events = []
    with open(FIXTURES / f"{game_id}_events.jsonl", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            e["_line"] = i
            events.append(e)
    return events


def _load_index(game_id: str) -> list[dict]:
    llm_calls = [
        json.loads(line)
        for line in (FIXTURES / f"{game_id}_llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return build_entries(llm_calls)


def test_vote_plan_mismatch_detects_p02_lie() -> None:
    hl = build_highlights(_load_events("fx_v05"), _load_index("fx_v05"))
    lie = next(h for h in hl["lies"] if h["player_id"] == "P02")
    assert lie["round_num"] == 1 and lie["vote_num"] == 1
    assert lie["planned_vote"] == "NO"
    assert lie["actual_vote"] == "YES"


def test_solo_win_detects_single_winner() -> None:
    hl = build_highlights(_load_events("fx_v05"), _load_index("fx_v05"))
    assert len(hl["solo_wins"]) == 1
    assert hl["solo_wins"][0] == {
        "line": hl["solo_wins"][0]["line"], "round_num": 1, "player_id": "P06", "amount": 1320000,
    }


def test_type_b_violation_detected() -> None:
    hl = build_highlights(_load_events("fx_v05"), _load_index("fx_v05"))
    assert hl["type_b_violations"][0]["player_ids"] == ["P05"]


def test_pool_share_payout_detected() -> None:
    hl = build_highlights(_load_events("fx_v05"), _load_index("fx_v05"))
    payout = hl["pool_share_payouts"][0]
    assert payout["obligor"] == "P06"
    assert payout["counterparty"] == "P09"
    assert payout["paid"] == 660000


def test_retries_and_aborts_both_kinds_present() -> None:
    hl = build_highlights(_load_events("fx_v05"), _load_index("fx_v05"))
    kinds = [h["kind"] for h in hl["retries_and_aborts"]]
    assert kinds.count("retry") == 2
    assert kinds.count("abort") == 1
