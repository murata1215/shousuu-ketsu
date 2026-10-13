"""
viewer/money_trail.py のテスト（サイクル4.3新設）

記録イベントだけからの現金・借金の復元が、本人向け財務通知
（FINANCE_NOTICE）・最終資産（GAME_END）と1円単位で一致すること、
精算での借入（SETTLEMENT_BORROWING相当）を正しく補うことを確認する。
"""

import json
from pathlib import Path

from viewer.money_trail import asset_timeline_from_trail, build_money_trail

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


def test_build_money_trail_reconciles_fixture() -> None:
    trail = build_money_trail(_load_events("fx_v05"))
    assert trail["mismatches"] == []
    assert len(trail["snapshots"]) > 0


def test_ledger_has_initial_loan_entry() -> None:
    trail = build_money_trail(_load_events("fx_v05"))
    p01_entries = trail["ledger"]["P01"]
    assert p01_entries[0]["kind"] == "initial_loan"
    assert p01_entries[0]["cash_delta"] == 1200000


def test_ledger_pool_share_payment_recorded_for_both_sides() -> None:
    trail = build_money_trail(_load_events("fx_v05"))
    paid = [e for e in trail["ledger"]["P06"] if e["kind"] == "contract_payment_paid"]
    received = [e for e in trail["ledger"]["P09"] if e["kind"] == "contract_payment_received"]
    assert paid and paid[0]["condition_type"] == "pool_share"
    assert received and received[0]["counterparty"] == "P06"
    assert paid[0]["amount"] == received[0]["amount"]


def test_asset_timeline_from_trail_uses_round_end_snapshots() -> None:
    trail = build_money_trail(_load_events("fx_v05"))
    seat_ids = [f"P{i:02d}" for i in range(1, 13)]
    timeline = asset_timeline_from_trail(trail, seat_ids)
    assert timeline["rounds"] == [1, 2]
    assert timeline["by_seat"]["P06"][0] is not None


def test_settlement_borrowing_covers_negative_cash() -> None:
    """契約の支払いで現金が足りなくなった場合、精算での借入を自動で補う"""
    events = [
        {"_line": 1, "event_type": "LOAN_REVEALED", "round_num": 0, "data": {"loans": {"P01": 0, "P02": 0}}},
        {
            "_line": 2, "event_type": "CONTRACT_PAYMENT", "round_num": 1, "vote_num": 1,
            "phase": "settlement",
            "data": {
                "contract_id": "C1", "contract_seq": 1, "ob_index": 0, "obligor": "P01",
                "counterparty": "P02", "ob_type": "type_b_vote", "condition_type": None,
                "promised": 1000000, "paid": 1000000,
            },
        },
        {"_line": 3, "event_type": "VOTE_RESOLVED", "round_num": 1, "vote_num": 1, "phase": "settlement", "data": {}},
    ]
    trail = build_money_trail(events)
    p01_entries = trail["ledger"]["P01"]
    assert any(e["kind"] == "settlement_borrowing" and e["amount"] == 1000000 for e in p01_entries)
