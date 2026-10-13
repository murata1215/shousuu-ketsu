"""
本戦 t12_r4_4501c・t12_r4_4401b（v0.4/v0.5形式）の記録でのビューア確認
（サイクル4.3新設）

logs/llm_v0_4/はコミット対象外（.gitignore）のため、本番試合の記録がこの
マシン上に無い環境では全てスキップする。あるときだけ、お金の照合に
ずれが無いこと、見どころ（嘘・単独勝ち・型Bの違反・山分けの支払い・
やり直し/打ち切り）が実測値どおり拾えること、場面への直リンクの例
（`?game=t12_r4_4501c&round=3&vote=3&player=P06&event=1099`）が解決できる
ことを確認する。
"""

from pathlib import Path

import pytest

from viewer.log_parser import get_contracts, get_highlights, get_money, get_overview, resolve_event

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs" / "llm_v0_4"
GAME_4501C = "t12_r4_4501c"
GAME_4401B = "t12_r4_4401b"

pytestmark = pytest.mark.skipif(
    not (LOGS_DIR / f"{GAME_4501C}_events.jsonl").exists(),
    reason="本番試合t12_r4_4501cの記録がこの環境に無い（logs/はコミット対象外）",
)


@pytest.fixture(scope="module")
def overview_4501c() -> dict:
    return get_overview(LOGS_DIR, GAME_4501C)


@pytest.fixture(scope="module")
def highlights_4501c() -> dict:
    return get_highlights(LOGS_DIR, GAME_4501C)


def test_4501c_overview_v0_4_format_and_money_reconciled(overview_4501c: dict) -> None:
    assert overview_4501c["format"] == "v0.4"
    assert overview_4501c["completed"] is True
    assert overview_4501c["num_rounds"] == 4
    assert overview_4501c["money_reconciled"] is True
    assert overview_4501c["established_contracts"] == 22


def test_4501c_final_ranks_and_assets() -> None:
    overview = get_overview(LOGS_DIR, GAME_4501C)
    roster_by_pid = {r["player_id"]: (r["final_rank"], r["final_assets"]) for r in overview["roster"]}
    assert roster_by_pid["P06"] == (1, 21941392)  # 2回単独勝ちした1位
    assert roster_by_pid["P07"] == (12, -6355063)  # 最下位


def test_4501c_highlights_counts(highlights_4501c: dict) -> None:
    assert len(highlights_4501c["solo_wins"]) == 2
    assert all(h["player_id"] == "P06" for h in highlights_4501c["solo_wins"])
    assert len(highlights_4501c["type_b_violations"]) == 2
    assert len(highlights_4501c["pool_share_payouts"]) == 5
    assert len(highlights_4501c["retries_and_aborts"]) == 2
    assert len(highlights_4501c["lies"]) >= 9  # 最後の巡の予定票と実際の票が違う人


def test_4501c_direct_link_example_resolves_to_r3v3_p06_dm() -> None:
    """プランで示した直リンクの例（R3V3のP06のDM）が解決できることを確認する"""
    resolved = resolve_event(LOGS_DIR, GAME_4501C, 1099)
    assert resolved is not None
    assert resolved["scene"]["round"] == 3
    assert resolved["scene"]["vote"] == 3
    assert resolved["scene"]["player"] == "P06"
    assert resolved["event"]["event_type"] == "NEGOTIATION_ACTION"
    assert resolved["event"]["data"]["action"] == "dm"
    assert resolved["event"]["data"]["to"] == "P09"


def test_4501c_contracts_include_pool_share_with_pending_outcome() -> None:
    contracts = get_contracts(LOGS_DIR, GAME_4501C)
    pool_contracts = [
        c for c in contracts["established"]
        for ob in c["obligations"] if ob.get("pool_share_label")
    ]
    assert pool_contracts
    for c in pool_contracts:
        for ob in c["obligations"]:
            if ob.get("pool_share_label"):
                assert ob["outcome"] == "これから（毎ラウンド評価）"


def test_4501c_money_mismatches_empty() -> None:
    money = get_money(LOGS_DIR, GAME_4501C)
    assert money["mismatches"] == []


@pytest.mark.skipif(
    not (LOGS_DIR / f"{GAME_4401B}_events.jsonl").exists(),
    reason="本番試合t12_r4_4401bの記録がこの環境に無い（logs/はコミット対象外）",
)
def test_4401b_overview_and_highlights() -> None:
    overview = get_overview(LOGS_DIR, GAME_4401B)
    assert overview["format"] == "v0.4"
    assert overview["money_reconciled"] is True
    assert overview["established_contracts"] == 28

    roster_by_pid = {r["player_id"]: (r["final_rank"], r["final_assets"]) for r in overview["roster"]}
    assert roster_by_pid["P03"] == (1, 14417462)

    highlights = get_highlights(LOGS_DIR, GAME_4401B)
    assert len(highlights["solo_wins"]) == 1
    assert len(highlights["type_b_violations"]) == 0
    assert len(highlights["retries_and_aborts"]) == 4
