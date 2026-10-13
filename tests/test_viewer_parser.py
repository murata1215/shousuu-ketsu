"""
viewer/log_parser.py のテスト（サイクル3.0新設、サイクル4.3でv0.4/v0.5対応に作り直し）

v0.4/v0.5形式の見本（tests/fixtures/viewer_logs/fx_v05_*、12席・2ラウンド）で
各APIデータが揃うこと、一覧がBot試合・動作確認・旧形式（v0.3）を正しく扱う
ことを確認する。旧形式（fx_demo・fx_old_nomsg）は「席・モデル・最終資産・
順位だけ返す」案Bの動作を確認する。
"""

from pathlib import Path

from viewer.log_parser import (
    get_contracts,
    get_highlights,
    get_money,
    get_overview,
    get_round,
    get_seat,
    is_v0_3_format,
    list_games,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "viewer_logs"


def test_list_games_excludes_bot_and_includes_full_seats() -> None:
    games = list_games(FIXTURES)
    ids = {g["game_id"] for g in games}
    assert "fx_bot4" not in ids  # 4席はBot試合扱いで出さない
    assert "fx_full12" in ids  # 12席は出す
    assert "fx_v05" in ids
    assert "fx_demo" not in ids  # fx_demoは3席なので一覧には出ない（直接アクセスは可）


def test_list_games_transcript_flag() -> None:
    games = {g["game_id"]: g for g in list_games(FIXTURES)}
    full = games["fx_full12"]
    assert full["transcript_available"] is False  # NEGOTIATION_ACTIONイベント自体が無い


def test_list_games_marks_legacy_format_but_keeps_it_listed() -> None:
    """v0.3形式の試合は一覧から外さず、format=v0.3の札を付ける（§v0.3の試合の扱い案B）"""
    games = {g["game_id"]: g for g in list_games(FIXTURES)}
    assert "fx_v0_3_legacy" in games  # 12席のv0.3形式見本（fx_demo等は3席のため一覧に出ない）
    assert games["fx_v0_3_legacy"]["format"] == "v0.3"
    assert games["fx_v05"]["format"] == "v0.4"


def test_is_v0_3_format_detects_legacy_markers() -> None:
    import json

    legacy_events = [json.loads(line) for line in (FIXTURES / "fx_demo_events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    v04_events = [json.loads(line) for line in (FIXTURES / "fx_v05_events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert is_v0_3_format(legacy_events) is True
    assert is_v0_3_format(v04_events) is False


# =============================================================================
# 概要タブ
# =============================================================================

def test_get_overview_legacy_format_returns_roster_only() -> None:
    """旧形式（v0.3）は席・モデル・最終資産・順位だけを返す（§v0.3の試合の扱い案B）"""
    overview = get_overview(FIXTURES, "fx_demo")
    assert overview is not None
    assert overview["format"] == "v0.3"
    assert "legacy_notice" in overview
    assert "rounds" not in overview  # 詳細は返さない
    roster_by_pid = {r["player_id"]: r for r in overview["roster"]}
    assert roster_by_pid["P01"]["model_label"] == "claude-opus-5"
    assert roster_by_pid["P01"]["final_assets"] == 1300000


def test_get_overview_basic_shape() -> None:
    overview = get_overview(FIXTURES, "fx_v05")
    assert overview is not None
    assert overview["format"] == "v0.4"
    assert overview["completed"] is True
    assert overview["num_rounds"] == 2
    assert overview["money_reconciled"] is True
    roster_by_pid = {r["player_id"]: r for r in overview["roster"]}
    assert roster_by_pid["P06"]["model_label"] == "deepseek-v3"
    assert roster_by_pid["P06"]["pot_payout_count"] == 1
    assert overview["established_contracts"] == 3
    r1 = overview["rounds"][0]
    assert [v["vote_num"] for v in r1["votes"]] == [1, 2]
    assert r1["votes"][0]["result"] == "retry"
    assert r1["winner_ids"] == ["P06"]
    assert overview["asset_timeline"]["rounds"] == [1, 2]


def test_get_overview_redacts_contained_strings() -> None:
    overview = get_overview(FIXTURES, "fx_v05")
    serialized = str(overview)
    assert "taro@example.com" not in serialized


def test_get_overview_unknown_game_returns_none() -> None:
    assert get_overview(FIXTURES, "fx_does_not_exist") is None


# =============================================================================
# ラウンドタブ（v0.4: ラウンド→投票の二重構造）
# =============================================================================

def test_get_round_has_nested_votes() -> None:
    round1 = get_round(FIXTURES, "fx_v05", 1)
    assert round1 is not None
    assert [v["vote_num"] for v in round1["votes"]] == [1, 2]
    v1 = round1["votes"][0]
    assert v1["question"] == "傘をさすと雨に濡れにくくなる"
    broadcasts = [e for e in v1["timeline"] if e["kind"] == "negotiation" and e["action"] == "broadcast"]
    assert broadcasts
    assert broadcasts[0]["memo"]["vote_plan"] == "NO"


def test_get_round_vote_result_has_lied_ids() -> None:
    """V1でP02が予定票NOと言いながらYESを出したことがvote_resultに出る"""
    round1 = get_round(FIXTURES, "fx_v05", 1)
    v1 = round1["votes"][0]
    vote_results = [e for e in v1["timeline"] if e["kind"] == "vote_result"]
    assert vote_results
    assert "P02" in vote_results[0]["lied_ids"]


def test_get_round_single_winner_in_round_settlement() -> None:
    round1 = get_round(FIXTURES, "fx_v05", 1)
    rs = round1["round_settlement"]
    assert rs["winner_ids"] == ["P06"]
    assert rs["aborted"] is False
    pool_payments = [p for p in rs["payments"] if p["condition_type"] == "pool_share"]
    assert pool_payments and pool_payments[0]["obligor"] == "P06"


def test_get_round_aborted_round_has_destroyed_pot() -> None:
    """最終ラウンドの打ち切りは山を没収する（持ち越しなし）"""
    round2 = get_round(FIXTURES, "fx_v05", 2)
    rs = round2["round_settlement"]
    assert rs["aborted"] is True
    assert rs["winner_ids"] == []
    assert rs["destroyed_pot"] == rs["pot_final"]
    assert rs["carryover_out"] == 0


def test_get_round_contract_established_has_obligations() -> None:
    """
    署名(contract_sign)と成立(contract_established)はAPI上は別イベントのまま返す
    （「同じ署名が2行出る」のを防ぐ統合=mergeSignEstablished()はwatch.html側の
    表示用処理であり、APIは記録そのものの粒度を保つ）。
    """
    round1 = get_round(FIXTURES, "fx_v05", 1)
    v1 = round1["votes"][0]
    established = [e for e in v1["timeline"] if e["kind"] == "contract_established"]
    assert established
    contract_ids = {e["contract_id"] for e in established}
    assert "C_FIXPOOL1" in contract_ids
    pool = next(e for e in established if e["contract_id"] == "C_FIXPOOL1")
    assert pool["obligations"][0]["ob_type"] == "type_c_conditional"


def test_get_round_clamps_out_of_range() -> None:
    clamped_high = get_round(FIXTURES, "fx_v05", 99)
    assert clamped_high["round_num"] == 2
    clamped_low = get_round(FIXTURES, "fx_v05", 0)
    assert clamped_low["round_num"] == 1


def test_get_round_accidents_present() -> None:
    round1 = get_round(FIXTURES, "fx_v05", 1)
    kinds = {a["kind"] for a in round1["accidents"]}
    assert "timeout" in kinds
    assert "invalid_response" in kinds


def test_get_round_redacts_message_text() -> None:
    round1 = get_round(FIXTURES, "fx_v05", 1)
    serialized = str(round1)
    assert "taro@example.com" not in serialized


def test_get_round_unknown_game_returns_none() -> None:
    assert get_round(FIXTURES, "fx_does_not_exist", 1) is None


def test_get_round_legacy_format_returns_none() -> None:
    """旧形式（v0.3）はget_roundの詳細対象外（案B）"""
    assert get_round(FIXTURES, "fx_demo", 1) is None


# =============================================================================
# 契約タブ
# =============================================================================

def test_get_contracts_pool_share_shown_separately() -> None:
    contracts = get_contracts(FIXTURES, "fx_v05")
    assert contracts is not None
    pool_contract = next(c for c in contracts["established"] if c["contract_id"] == "C_FIXPOOL1")
    ob = pool_contract["obligations"][0]
    assert ob["pool_share_label"] is not None
    assert "山分け" in ob["pool_share_label"]
    assert ob["outcome"] == "これから（毎ラウンド評価）"  # 山分けは常にPENDING扱い
    assert len(ob["payments"]) == 1


def test_get_contracts_outcome_classification() -> None:
    contracts = get_contracts(FIXTURES, "fx_v05")
    typeb_contract = next(c for c in contracts["established"] if c["contract_id"] == "C_FIXTYPEB")
    ob = typeb_contract["obligations"][0]
    assert ob["outcome"] == "違約金支払い"  # P05はNOを約束したがYESを出した


def test_get_contracts_expired_and_rejected() -> None:
    contracts = get_contracts(FIXTURES, "fx_v05")
    assert len(contracts["expired"]) == 1
    assert contracts["expired"][0]["contract_id"] == "C_FIXUNSIGN"
    assert len(contracts["rejected"]) == 1
    assert contracts["rejected"][0]["player_id"] == "P11"


def test_get_contracts_legacy_format_returns_none() -> None:
    assert get_contracts(FIXTURES, "fx_demo") is None


# =============================================================================
# お金タブ
# =============================================================================

def test_get_money_reconciled() -> None:
    money = get_money(FIXTURES, "fx_v05")
    assert money is not None
    assert money["reconciled"] is True
    assert money["mismatches"] == []
    assert len(money["snapshots"]) > 0
    assert "P06" in money["ledger"]


def test_get_money_ledger_has_pool_share_entry() -> None:
    money = get_money(FIXTURES, "fx_v05")
    kinds = {e["kind"] for e in money["ledger"]["P06"]}
    assert "contract_payment_paid" in kinds


def test_get_money_legacy_format_returns_none() -> None:
    assert get_money(FIXTURES, "fx_demo") is None


# =============================================================================
# 見どころタブ
# =============================================================================

def test_get_highlights_all_categories_present() -> None:
    hl = get_highlights(FIXTURES, "fx_v05")
    assert hl is not None
    assert len(hl["lies"]) >= 1
    assert any(h["player_id"] == "P02" for h in hl["lies"])
    assert len(hl["solo_wins"]) == 1
    assert hl["solo_wins"][0]["player_id"] == "P06"
    assert len(hl["type_b_violations"]) == 1
    assert len(hl["pool_share_payouts"]) == 1
    assert len(hl["retries_and_aborts"]) == 3  # retry×2 + abort×1


def test_get_highlights_legacy_format_returns_none() -> None:
    assert get_highlights(FIXTURES, "fx_demo") is None


# =============================================================================
# 席タブ
# =============================================================================

def test_get_seat_basic_shape() -> None:
    seat = get_seat(FIXTURES, "fx_v05", "P06")
    assert seat is not None
    assert seat["model_label"] == "deepseek-v3"
    votes_by_key = {(v["round_num"], v["vote_num"]): v["vote"] for v in seat["votes"]}
    assert votes_by_key[(1, 1)] == "YES"
    assert "C_FIXPOOL1" in seat["contract_ids"]
    assert seat["final_assets"] == 307000
    assert seat["cash_flow"]  # お金の出入りの記録が付く


def test_get_seat_unknown_player_returns_none() -> None:
    assert get_seat(FIXTURES, "fx_v05", "P99") is None


def test_get_seat_redacts_post_game_reflection() -> None:
    seat = get_seat(FIXTURES, "fx_v05", "P01")
    assert "taro@example.com" not in str(seat)
    assert "/home/testuser" not in str(seat)


def test_get_seat_legacy_format_returns_none() -> None:
    assert get_seat(FIXTURES, "fx_demo", "P01") is None


def test_old_format_without_message_is_transcript_unavailable() -> None:
    overview = get_overview(FIXTURES, "fx_old_nomsg")
    assert overview is not None
    assert overview["transcript_available"] is False
