"""
sim.metrics（scripts/simulate.pyの土台）の動作確認テスト（計画§4のテスト）

各シナリオを10試合だけ回し、完走すること・集計キーが揃っていること・
同じ指定なら同じ数字になること（乱数はシードから）を確認する。
"""

import pytest

from engine.config import GameConfig
from sim.metrics import collect_raw, summarize
from sim.scenarios import SCENARIO_KEYS

LABEL_SCENARIOS = ["S2", "S3k1", "S3k3", "S3k6", "S4", "S5"]
CONTRACT_SCENARIOS = ["S3k1", "S3k3", "S3k6", "S4"]


@pytest.mark.parametrize("scenario_key", SCENARIO_KEYS)
def test_scenario_runs_10_games_and_completes(scenario_key: str) -> None:
    config = GameConfig.default_12()
    raw = collect_raw(scenario_key, games=10, seed_start=1, config=config)
    assert raw["n_games"] == 10
    assert raw["n_rounds_total"] == 10 * 12

    summary = summarize(raw)
    assert summary["n_games"] == 10
    assert 0.0 <= summary["no_minority_ratio"] <= 1.0
    assert summary["final_assets_max"] >= summary["final_assets_min"]
    assert 0.0 <= summary["king_making_ratio"] <= 1.0
    for penalty_ratio in summary["break_even_ratio_by_penalty"].values():
        assert 0.0 <= penalty_ratio <= 1.0


@pytest.mark.parametrize("scenario_key", SCENARIO_KEYS)
def test_scenario_is_reproducible_with_same_seed(scenario_key: str) -> None:
    config = GameConfig.default_12()
    raw1 = collect_raw(scenario_key, games=5, seed_start=10, config=config)
    raw2 = collect_raw(scenario_key, games=5, seed_start=10, config=config)
    assert summarize(raw1) == summarize(raw2)


@pytest.mark.parametrize("scenario_key", LABEL_SCENARIOS)
def test_label_scenarios_have_bot_type_breakdown(scenario_key: str) -> None:
    config = GameConfig.default_12()
    raw = collect_raw(scenario_key, games=10, seed_start=1, config=config)
    summary = summarize(raw)
    assert summary["by_label"]
    total_n = sum(row["n"] for row in summary["by_label"].values())
    assert total_n == 10 * 12  # 12人 x 10試合


@pytest.mark.parametrize("scenario_key", CONTRACT_SCENARIOS)
def test_pair_scenarios_have_type_b_obligations(scenario_key: str) -> None:
    """S3/S4はペアBotが毎ラウンド必ず型B契約を結ぶため、義務が0本にはならない"""
    config = GameConfig.default_12()
    raw = collect_raw(scenario_key, games=10, seed_start=1, config=config)
    summary = summarize(raw)
    assert summary["type_b_kept"] + summary["type_b_violated"] > 0


def test_s4_has_more_violations_than_s3k3_with_same_seeds() -> None:
    """S4は各組の片方が20%裏切るため、S3(k=3、必ず守る)より違反本数が多い"""
    config = GameConfig.default_12()
    raw_s3 = collect_raw("S3k3", games=30, seed_start=1, config=config)
    raw_s4 = collect_raw("S4", games=30, seed_start=1, config=config)
    assert summarize(raw_s3)["type_b_violated"] == 0
    assert summarize(raw_s4)["type_b_violated"] > 0


def test_s1_and_s2_have_no_contracts() -> None:
    config = GameConfig.default_12()
    for scenario_key in ["S1", "S2", "S5"]:
        raw = collect_raw(scenario_key, games=10, seed_start=1, config=config)
        summary = summarize(raw)
        assert summary["type_b_kept"] == 0
        assert summary["type_b_violated"] == 0
