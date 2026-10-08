"""
場面リンクのサーバー側部分（サイクル3.0）

URLパラメータの解析・組み立てそのもの（parseSceneParams/buildSceneUrl）は
watch.html内のJSなので、ここではサーバー側で場面を解決する部分
（範囲外roundのクランプ・未知のgame/player/contractの扱い）を確認する。
JS関数の存在そのものはtest_viewer_frontend.pyで検査済み。
"""

from pathlib import Path

from viewer.log_parser import get_contracts, get_round, get_seat

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "viewer_logs"


def test_round_out_of_range_high_clamps_to_max() -> None:
    data = get_round(FIXTURES, "fx_demo", 999)
    assert data["round_num"] == data["max_round"] == 2


def test_round_out_of_range_low_clamps_to_one() -> None:
    data = get_round(FIXTURES, "fx_demo", -5)
    assert data["round_num"] == 1


def test_round_negative_and_zero_both_clamp_to_one() -> None:
    assert get_round(FIXTURES, "fx_demo", 0)["round_num"] == 1


def test_unknown_contract_id_does_not_crash_lookup() -> None:
    contracts = get_contracts(FIXTURES, "fx_demo")
    ids = {c["contract_id"] for c in contracts["established"]}
    assert "C_DOES_NOT_EXIST" not in ids  # サーバー側はそのまま一覧を返す。存在確認はクライアント側


def test_unknown_seat_returns_none_not_error() -> None:
    assert get_seat(FIXTURES, "fx_demo", "P_UNKNOWN") is None


def test_round_contains_seat_ids_for_client_side_filter_options() -> None:
    data = get_round(FIXTURES, "fx_demo", 1)
    assert data["seat_ids"] == ["P01", "P02", "P03"]
