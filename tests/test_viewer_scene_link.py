"""
場面リンクのサーバー側部分（サイクル3.0新設、サイクル4.3でevent=（行番号）
解決・vote=対応を追加）

URLパラメータの解析・組み立てそのもの（parseSceneParams/buildSceneUrl）は
watch.html内のJSなので、ここではサーバー側で場面を解決する部分
（範囲外roundのクランプ・未知のgame/player/contractの扱い・行番号から場面を
引く resolve_event）を確認する。JS関数の存在そのものはtest_viewer_frontend.py
で検査済み。
"""

from pathlib import Path

from viewer.log_parser import get_contracts, get_round, get_seat, resolve_event

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "viewer_logs"


def test_round_out_of_range_high_clamps_to_max() -> None:
    data = get_round(FIXTURES, "fx_v05", 999)
    assert data["round_num"] == data["max_round"] == 2


def test_round_out_of_range_low_clamps_to_one() -> None:
    data = get_round(FIXTURES, "fx_v05", -5)
    assert data["round_num"] == 1


def test_round_negative_and_zero_both_clamp_to_one() -> None:
    assert get_round(FIXTURES, "fx_v05", 0)["round_num"] == 1


def test_unknown_contract_id_does_not_crash_lookup() -> None:
    contracts = get_contracts(FIXTURES, "fx_v05")
    ids = {c["contract_id"] for c in contracts["established"]}
    assert "C_DOES_NOT_EXIST" not in ids  # サーバー側はそのまま一覧を返す。存在確認はクライアント側


def test_unknown_seat_returns_none_not_error() -> None:
    assert get_seat(FIXTURES, "fx_v05", "P_UNKNOWN") is None


def test_round_contains_seat_ids_for_client_side_filter_options() -> None:
    data = get_round(FIXTURES, "fx_v05", 1)
    assert data["seat_ids"] == [f"P{i:02d}" for i in range(1, 13)]


def test_resolve_event_from_line_number() -> None:
    """記録の行番号（event=）から、ラウンド・投票・巡・席・契約を解決できる"""
    resolved = resolve_event(FIXTURES, "fx_v05", 48)  # 山分け契約の提案
    assert resolved is not None
    assert resolved["scene"]["round"] == 1
    assert resolved["scene"]["vote"] == 1
    assert resolved["scene"]["contract"] == "C_FIXPOOL1"
    assert resolved["event"]["event_type"] == "CONTRACT_PROPOSED"


def test_resolve_event_unknown_line_returns_none() -> None:
    assert resolve_event(FIXTURES, "fx_v05", 10_000_000) is None


def test_resolve_event_unknown_game_returns_none() -> None:
    assert resolve_event(FIXTURES, "fx_does_not_exist", 1) is None
