"""
sim.metrics（scripts/simulate.pyの土台）の動作確認テスト（v0.4、サイクル4.1）

各シナリオを少数の試合だけ回し、完走すること・記録の形が揃っていること・
同じ指定なら同じ数字になること（乱数はシードから）を確認する。

v0.3のS1〜S6向けテスト（summarize()の単一集計関数が前提）は、v0.4の
V1〜V10（シナリオごとに見るべき指標が大きく異なる）に合わせて全面的に
書き換えた。
"""

import pytest

from sim.metrics import (
    collect_raw, common_stats, group_stats, loan_stats, oversell_stats, type_b_pact_stats,
)
from sim.scenarios import GROUP_A, GROUP_B, GROUP_C, HUB_GROUPS, SCENARIO_KEYS

GROUP_SCENARIOS = ["V2", "V3g2", "V3g3", "V3g5", "V3g6", "V4", "V5", "V6"]
TYPE_B_SCENARIOS = ["V8keep_pen100", "V8keep_pen300", "V8keep_pen500",
                    "V8break_pen100", "V8break_pen300", "V8break_pen500"]
NO_CONTRACT_SCENARIOS = ["V1", "V9", "V9_post10"]


@pytest.mark.parametrize("scenario_key", SCENARIO_KEYS)
def test_scenario_runs_n_games_and_completes(scenario_key: str) -> None:
    raw = collect_raw(scenario_key, games=5, seed_start=1)
    assert raw["n_games"] == 5
    assert len(raw["games"]) == 5
    for record in raw["games"]:
        assert len(record["rounds"]) == 4  # L12R4V6: 4ラウンド固定
        assert set(record["final_assets"]) == {f"P{i:02d}" for i in range(1, 13)}

    stats = common_stats(raw["games"])
    assert stats["n_games"] == 5
    assert stats["final_assets_max"] >= stats["final_assets_min"]
    assert 0.0 <= stats["abort_ratio"] <= 1.0
    assert 0.0 <= stats["king_making_ratio"] <= 1.0


@pytest.mark.parametrize("scenario_key", SCENARIO_KEYS)
def test_scenario_is_reproducible_with_same_seed(scenario_key: str) -> None:
    raw1 = collect_raw(scenario_key, games=5, seed_start=10)
    raw2 = collect_raw(scenario_key, games=5, seed_start=10)
    assert raw1 == raw2


def _group_members_for(scenario_key: str) -> set[str]:
    """V3は先頭からk人（キー名の数字）が組、それ以外はGROUP_A固定"""
    if scenario_key.startswith("V3g"):
        k = int(scenario_key[len("V3g"):])
        return {f"P{i:02d}" for i in range(1, k + 1)}
    return set(GROUP_A)


@pytest.mark.parametrize("scenario_key", GROUP_SCENARIOS)
def test_group_scenarios_have_sane_group_stats(scenario_key: str) -> None:
    raw = collect_raw(scenario_key, games=10, seed_start=1)
    stats = group_stats(raw["games"], _group_members_for(scenario_key))
    assert 0.0 <= stats["group_win_round_ratio"] <= 1.0
    assert 0.0 <= stats["group_share_mean"] <= 1.0


@pytest.mark.parametrize("scenario_key", TYPE_B_SCENARIOS)
def test_type_b_scenarios_have_obligations(scenario_key: str) -> None:
    """V8は組員が毎ラウンドV1で必ず型B義務を負うため、本数が0にはならない"""
    raw = collect_raw(scenario_key, games=5, seed_start=1)
    stats = type_b_pact_stats(raw["games"], set(GROUP_A))
    assert stats["kept"] + stats["violated"] > 0


def test_v8keep_never_violates() -> None:
    raw = collect_raw("V8keep_pen100", games=20, seed_start=1)
    stats = type_b_pact_stats(raw["games"], set(GROUP_A))
    assert stats["violated"] == 0


def test_v8break_always_violates_on_vote_1() -> None:
    """P01（GROUP_Aの先頭）は毎ラウンドのV1で必ず違反する"""
    raw = collect_raw("V8break_pen100", games=20, seed_start=1)
    stats = type_b_pact_stats(raw["games"], set(GROUP_A))
    # P01分の違反（1本/ラウンド）が必ず含まれる: 20試合 x 4ラウンド = 80本以上
    assert stats["violated"] >= 20 * 4


@pytest.mark.parametrize("scenario_key", NO_CONTRACT_SCENARIOS)
def test_no_contract_scenarios_have_no_contract_payments(scenario_key: str) -> None:
    raw = collect_raw(scenario_key, games=5, seed_start=1)
    for record in raw["games"]:
        assert record["contract_payments"] == []


def test_v7_hub_and_three_groups_cover_all_12_players() -> None:
    raw = collect_raw("V7", games=5, seed_start=1)
    all_members = {"P01"} | {m for g in HUB_GROUPS for m in g} | {"P11", "P12"}
    assert all_members == {f"P{i:02d}" for i in range(1, 13)}
    for record in raw["games"]:
        assert set(record["labels"].values()) >= {"Hub", "G1", "G2", "G3", "Random"}


def test_v9_has_three_loan_labels() -> None:
    raw = collect_raw("V9", games=5, seed_start=1)
    stats = loan_stats(raw["games"])
    assert set(stats["asset_mean_by_label"]) == {"Loan120man", "Loan400man", "Loan1000man"}


def test_v10_oversell_produces_contract_payments_for_p01() -> None:
    raw = collect_raw("V10", games=30, seed_start=1)
    stats = oversell_stats(raw["games"])
    assert stats["n_games_with_payment"] > 0


def test_v5_all_three_groups_always_tie_6_against_6_and_abort() -> None:
    """
    V5は3つの4人組（各組2対2で割る）が12人全員を占めるため、毎投票必ず
    6対6の同数になり、無作為要素が無い分だけ机上計算（§12.5、100%）より
    強く「打ち切り確定」になる（§11.4の既知のリスクそのもの）。
    山分け契約（wins_round）は誰も勝ち残らないため一度も支払われない。
    """
    assert set(GROUP_A) | set(GROUP_B) | set(GROUP_C) == {f"P{i:02d}" for i in range(1, 13)}
    raw = collect_raw("V5", games=5, seed_start=1)
    stats = common_stats(raw["games"])
    assert stats["abort_ratio"] == 1.0
    for record in raw["games"]:
        assert record["contract_payments"] == []
