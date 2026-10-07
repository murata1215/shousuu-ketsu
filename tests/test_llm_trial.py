"""
scripts/llm_trial.py・scripts/summarize_trial.py のテスト（§サイクル2.0、AIを呼ばない）

- 偽の応答（tests/helpers.py::FakeAdapter）で12席・12ラウンドが最後まで回ること
- 打ち切るラウンド数の指定が効くこと（ルール上のラウンド数は変わらない）
- 席の割り当てが、同じシードで同じになること
- 費用上限に達したら、その席が止まる（他席は続く）こと
- 要約が記録から正しく作れること
- max_parallel_agents=1 と >1 で結果が一致すること
- 試合後の振り返りが全席に1回だけ呼ばれること
"""

import json
import sys
from pathlib import Path

import pytest

from llm.costing import worst_case_cost  # noqa: E402
from llm.models import get_model  # noqa: E402
from llm.prompt_builder import build_loan_prompt, build_system_prompt  # noqa: E402
from scripts.llm_trial import (  # noqa: E402
    _build_arg_parser, assign_seats, parse_roster, run_trial,
)
from scripts.summarize_trial import (  # noqa: E402
    load_trial_records, summarize_contracts, summarize_final,
    summarize_post_game_reflections, summarize_rounds, summarize_seats,
)
from tests.helpers import FakeAdapter  # noqa: E402


def _fake_adapter_factory(model_info):
    return FakeAdapter()


def _write_questions_file(tmp_path: Path, n: int, name: str = "questions.txt") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(f"質問{i}" for i in range(1, n + 1)) + "\n", encoding="utf-8")
    return path


def _make_args(tmp_path: Path, argv: list[str]):
    parser = _build_arg_parser()
    base = [
        "--log-dir", str(tmp_path / "logs"),
        "--question-history-path", str(tmp_path / "question_history.jsonl"),
    ]
    return parser.parse_args(base + argv)


# --- 席の割り当て ---

def test_parse_roster_expands_count_syntax():
    assert parse_roster("L1,L1,L3:3") == ["L1", "L1", "L3", "L3", "L3"]


def test_assign_seats_deterministic_same_seed():
    keys = ["L1", "L2", "L3", "L4"]
    a = assign_seats(keys, seed=123)
    b = assign_seats(keys, seed=123)
    assert a == b
    assert set(a.keys()) == {"P01", "P02", "P03", "P04"}
    assert sorted(a.values()) == sorted(keys)


def test_assign_seats_different_seed_usually_differs():
    keys = ["L1", "L2", "L3", "L4", "L5", "L6"]
    a = assign_seats(keys, seed=1)
    b = assign_seats(keys, seed=2)
    assert a != b


# --- 通し: 偽の応答で12席・12ラウンドが最後まで回ること ---

def test_run_trial_full_12_seat_game_completes_with_fake_adapter(tmp_path):
    roster = ",".join(["L1", "L2", "L3", "L4", "L5", "L6"] * 2)  # 12席
    questions_path = _write_questions_file(tmp_path, 12)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "7", "--game-id", "full12",
        "--questions", str(questions_path),
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)

    assert outcome["exit_code"] == 0
    result = outcome["result"]
    assert result is not None
    assert len(result.final_assets) == 12
    assert len(result.round_summaries) == 12

    # 疎通確認も実際に走り、FakeAdapterの既定応答がpass判定を通ること
    assert outcome["preflight_results"] is not None
    assert all(r["ok"] for r in outcome["preflight_results"])

    # 記録ファイルが残っていること
    log_dir = outcome["log_dir"]
    assert (log_dir / "full12_events.jsonl").exists()
    assert (log_dir / "full12_llm_calls.jsonl").exists()
    assert (log_dir / "full12_seat_map.json").exists()
    assert (log_dir / "full12_questions.json").exists()


# --- 打ち切るラウンド数の指定 ---

def test_run_trial_stop_after_round_limits_execution_but_not_rule_rounds(tmp_path):
    roster = "L1,L2,L3,L4"
    questions_path = _write_questions_file(tmp_path, 3)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "1", "--game-id", "stoptest",
        "--num-rounds", "3", "--stop-after-round", "1",
        "--questions", str(questions_path), "--no-preflight",
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)
    result = outcome["result"]
    assert result is not None
    # 実行は1ラウンドで打ち切られる
    assert len(result.round_summaries) == 1
    # ルール上のラウンド数(3)自体は変わらない（is_final判定はR3基準のまま）
    events = load_trial_records("stoptest", outcome["log_dir"])["events"]
    game_start = [e for e in events if e["event_type"] == "GAME_START"][0]
    assert game_start["data"]["num_rounds"] == 3


# --- 費用上限に達した席だけが止まる ---

def test_run_trial_per_seat_cap_blocks_only_that_seat(tmp_path):
    roster = "H1,L7,L7,L7"
    questions_path = _write_questions_file(tmp_path, 1)

    # H1の1コール分の予約額より少し小さいcapにする（必ずブロックさせる）
    config_for_pricing = None
    system = build_system_prompt("P01", __import__("engine.config", fromlist=["GameConfig"]).GameConfig())
    user = build_loan_prompt(__import__("engine.config", fromlist=["GameConfig"]).GameConfig())
    h1_reserve = worst_case_cost(get_model("H1"), system, user, max_tokens=1000)
    assert h1_reserve > 0.001  # H1は十分高額なはず

    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "3", "--game-id", "capblock",
        "--num-rounds", "1", "--stop-after-round", "1", "--negotiation-max-turns", "1",
        "--questions", str(questions_path), "--no-preflight",
        "--per-seat-cap-usd", str(h1_reserve / 2), "--game-cap-usd", "10",
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)
    result = outcome["result"]
    assert result is not None

    seat_map = json.loads((outcome["log_dir"] / "capblock_seat_map.json").read_text(encoding="utf-8"))
    h1_pid = [pid for pid, model_id in seat_map.items() if model_id == get_model("H1").model_id][0]

    events = load_trial_records("capblock", outcome["log_dir"])["events"]
    blocked_pids = {e["data"]["player_id"] for e in events if e["event_type"] == "LLM_BUDGET_BLOCKED"}
    assert h1_pid in blocked_pids
    # L7席はブロックされていない
    assert blocked_pids == {h1_pid}
    # 試合自体は完走する（ブロックされた席は安全側にフォールバックするだけ）
    assert len(result.round_summaries) == 1


# --- 要約が記録から正しく作れること ---

def test_summarize_trial_builds_tables_from_known_records(tmp_path):
    roster = "L1,L2,L3,L4"
    questions_path = _write_questions_file(tmp_path, 1)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "9", "--game-id", "summtest",
        "--num-rounds", "1", "--questions", str(questions_path), "--no-preflight",
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)
    result = outcome["result"]

    records = load_trial_records("summtest", outcome["log_dir"])
    rounds = summarize_rounds(records["events"])
    assert len(rounds) == 1
    assert rounds[0]["round_num"] == 1
    assert sum(1 for v in rounds[0]["votes"].values() if v == "YES") \
        + sum(1 for v in rounds[0]["votes"].values() if v == "NO") == 4

    contracts = summarize_contracts(records["events"])
    assert contracts["established"] == []  # FakeAdapterは常にpassなので契約は成立しない

    final = summarize_final(records["events"])
    assert final is not None
    assert final["final_ranks"] == result.final_ranks
    assert final["final_assets"] == result.final_assets

    seats = summarize_seats(records["events"], records["llm_calls"], records["seat_map"])
    assert {s["player_id"] for s in seats} == {"P01", "P02", "P03", "P04"}
    for s in seats:
        assert s["calls"] > 0  # choose_loan + negotiation(pass) + commit + reflect + post_game

    reflections = summarize_post_game_reflections(records["events"])
    # FakeAdapterの既定応答(pass形式)はpost_game_reflectionのcommentフィールドを持たないため
    # 最終的にparse_post_game_reflectionがplaintextとして何らかのコメントを拾うか空になる。
    # どちらでも良いが、辞書の型自体は壊れない。
    assert isinstance(reflections, dict)


# --- max_parallel_agents=1 と >1 で結果が一致すること ---

def test_run_trial_parallel_matches_sequential_result(tmp_path):
    roster = "L1,L2,L3,L4,L5,L6"
    questions_path = _write_questions_file(tmp_path, 2)

    def _run(parallel: int, game_id: str):
        args = _make_args(tmp_path, [
            "--roster", roster, "--seed", "42", "--game-id", game_id,
            "--num-rounds", "2", "--questions", str(questions_path),
            "--no-preflight", "--parallel", str(parallel),
        ])
        return run_trial(args, adapter_factory=_fake_adapter_factory)

    seq = _run(1, "par_seq")
    par = _run(6, "par_par")

    assert seq["result"].final_assets == par["result"].final_assets
    assert seq["result"].final_ranks == par["result"].final_ranks
    assert len(seq["result"].round_summaries) == len(par["result"].round_summaries) == 2


# --- 試合後の振り返りが全席に1回だけ呼ばれること ---

def test_run_trial_post_game_reflection_called_once_per_seat(tmp_path):
    roster = "L1,L2,L3,L4"
    questions_path = _write_questions_file(tmp_path, 1)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "5", "--game-id", "postref",
        "--num-rounds", "1", "--questions", str(questions_path), "--no-preflight",
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)
    events = load_trial_records("postref", outcome["log_dir"])["events"]
    post_game_events = [e for e in events if e["event_type"] == "POST_GAME_REFLECTION"]
    pids = [e["data"]["player_id"] for e in post_game_events]
    assert sorted(pids) == ["P01", "P02", "P03", "P04"]
    assert len(pids) == len(set(pids))  # 1席あたり1回だけ


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
