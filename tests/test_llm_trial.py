"""
scripts/llm_trial.py・scripts/summarize_trial.py のテスト（§サイクル2.0、AIを呼ばない）

- 偽の応答（tests/helpers.py::FakeAdapter）で12席・12ラウンドが最後まで回ること
- 打ち切るラウンド数の指定が効くこと（ルール上のラウンド数は変わらない）
- 席の割り当てが、同じシードで同じになること
- 費用上限に達したら、その席が止まる（他席は続く）こと
- 要約が記録から正しく作れること
- max_parallel_agents=1 と >1 で結果が一致すること
- 試合後の振り返りが全席に1回だけ呼ばれること

サイクル2.1で追加:
- 実行を打ち切った試合（stop_after_round < num_rounds）では試合後の振り返りを行わないこと
  （stop_after_round == num_rounds なら従来どおり行うこと）
- 途中経過が1行ごとにflushされること（ProgressPrinter・_enable_line_buffering）
- 席ごとのモデル・借入額一覧（summarize_roster）
- 会話の書き出し（build_transcript）: 順序・本文・DM宛先・送金額・契約・投票結果、
  ラウンド指定での絞り込み、llm_calls不在時の挙動、再試行時の採用行選択
"""

import io
import json
import sys
from pathlib import Path

import pytest

from engine.config import GameConfig  # noqa: E402
from llm.costing import worst_case_cost  # noqa: E402
from llm.models import get_model  # noqa: E402
from llm.prompt_builder import build_loan_prompt, build_system_prompt  # noqa: E402
from scripts.llm_trial import (  # noqa: E402
    ProgressPrinter, _build_arg_parser, _enable_line_buffering, assign_seats, parse_roster, run_trial,
)
from scripts.summarize_trial import (  # noqa: E402
    build_transcript, load_trial_records, seat_label, short_model_label,
    summarize_contracts, summarize_final, summarize_post_game_reflections,
    summarize_roster, summarize_rounds, summarize_seats,
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


# --- 打ち切った試合では試合後の振り返りを行わないこと（サイクル2.1） ---

def test_run_trial_stop_before_num_rounds_skips_post_game_reflection(tmp_path):
    roster = "L1,L2,L3,L4"
    questions_path = _write_questions_file(tmp_path, 3)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "1", "--game-id", "stopskip",
        "--num-rounds", "3", "--stop-after-round", "1",
        "--questions", str(questions_path), "--no-preflight",
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)
    result = outcome["result"]
    assert result is not None
    assert result.post_game_reflections == {}

    events = load_trial_records("stopskip", outcome["log_dir"])["events"]
    assert [e for e in events if e["event_type"] == "POST_GAME_REFLECTION"] == []


def test_run_trial_stop_equal_num_rounds_still_reflects(tmp_path):
    """stop_after_round == num_rounds は最後まで回した扱いで、従来どおり振り返りを行う"""
    roster = "L1,L2,L3,L4"
    questions_path = _write_questions_file(tmp_path, 2)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "1", "--game-id", "stopatend",
        "--num-rounds", "2", "--stop-after-round", "2",
        "--questions", str(questions_path), "--no-preflight",
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)
    result = outcome["result"]
    assert result is not None
    assert sorted(result.post_game_reflections.keys()) == ["P01", "P02", "P03", "P04"]

    events = load_trial_records("stopatend", outcome["log_dir"])["events"]
    pids = [e["data"]["player_id"] for e in events if e["event_type"] == "POST_GAME_REFLECTION"]
    assert sorted(pids) == ["P01", "P02", "P03", "P04"]


# --- 途中経過が1行ごとにflushされること（サイクル2.1） ---

def test_progress_printer_flushes_each_line(monkeypatch):
    calls = []

    def fake_print(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("builtins.print", fake_print)
    progress = ProgressPrinter()
    progress({
        "player_id": "P01", "round_num": 1, "turn": 1, "phase": "negotiation",
        "elapsed_ms": 10.0, "ok": True, "error_type": None,
    })
    assert len(calls) == 1
    assert calls[0].get("flush") is True


def test_enable_line_buffering_real_file_returns_true(tmp_path):
    path = tmp_path / "out.log"
    with open(path, "w", encoding="utf-8") as f:
        applied = _enable_line_buffering(f)
        assert applied is True
        assert f.line_buffering is True


def test_enable_line_buffering_stringio_returns_false_without_raising():
    stream = io.StringIO()
    assert _enable_line_buffering(stream) is False


# --- 席ごとのモデル・借入額一覧（サイクル2.1） ---

def test_summarize_roster_lists_model_and_loan(tmp_path):
    roster = "L1,L2,L3,L4"
    questions_path = _write_questions_file(tmp_path, 1)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "11", "--game-id", "rostertest",
        "--num-rounds", "1", "--questions", str(questions_path), "--no-preflight",
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)
    records = load_trial_records("rostertest", outcome["log_dir"])

    result = summarize_roster(records["seat_map"], records["events"])
    assert [r["player_id"] for r in result] == ["P01", "P02", "P03", "P04"]
    for r in result:
        assert r["model_id"] == records["seat_map"][r["player_id"]]
        # FakeAdapterの既定応答(pass形式)はloan_amountを持たないため、
        # choose_loan()はconfig.loan_min（安全側）へフォールバックする
        assert r["loan"] == GameConfig().loan_min


def test_short_model_label_strips_vendor_prefix():
    assert short_model_label("devrelay/claude-opus-5") == "claude-opus-5"
    assert short_model_label("gpt-4.1-mini") == "gpt-4.1-mini"
    assert short_model_label(None) == ""


def test_seat_label_adds_model_name():
    seat_map = {"P08": "devrelay/claude-opus-5"}
    assert seat_label("P08", seat_map) == "P08(claude-opus-5)"
    assert seat_label("P09", seat_map) == "P09"  # seat_mapに無い席はIDのみ


# --- 会話の書き出し（build_transcript、サイクル2.1） ---

def _make_round_open(round_num, question, carryover=0):
    return {"event_type": "ROUND_OPEN", "round_num": round_num, "phase": "open",
            "data": {"question": question, "carryover": carryover}}


def _make_negotiation_action(round_num, pid, action, turn, **extra):
    data = {"player_id": pid, "action": action, "turn": turn, **extra}
    return {"event_type": "NEGOTIATION_ACTION", "round_num": round_num, "phase": "negotiation", "data": data}


def test_build_transcript_orders_entries_and_includes_message_from_event():
    """イベント自体にmessageがあれば、それをそのまま使う（新しい記録の経路）"""
    events = [
        _make_round_open(1, "質問A", carryover=0),
        _make_negotiation_action(1, "P01", "broadcast", turn=1, message="全体へ"),
        _make_negotiation_action(1, "P02", "dm", turn=1, to="P03", message="DM本文"),
        {"event_type": "TRANSFER", "round_num": 1, "phase": "negotiation",
         "data": {"player_id": "P01", "to": "P02", "amount": 300000, "turn": 2}},
        {"event_type": "VOTE_REVEALED", "round_num": 1, "phase": "settlement",
         "data": {"votes": {"P01": "YES", "P02": "NO"}}},
        {"event_type": "MINORITY_RESOLVED", "round_num": 1, "phase": "settlement",
         "data": {"minority_side": "YES", "minority_ids": ["P01"], "payout_per_minority": 100000,
                   "carryover_after": 0}},
    ]
    transcript = build_transcript(events, llm_calls=[], rounds=None)
    assert len(transcript) == 1
    r = transcript[0]
    assert r["question"] == "質問A"
    kinds = [e["kind"] for e in r["entries"]]
    assert kinds == ["broadcast", "dm", "transfer", "vote_result"]
    assert r["entries"][0]["message"] == "全体へ"
    assert r["entries"][1] == {"turn": 1, "kind": "dm", "from": "P02", "to": "P03", "message": "DM本文"}
    assert r["entries"][2]["amount"] == 300000
    assert r["entries"][3]["minority_side"] == "YES"
    assert r["entries"][3]["payout_per_minority"] == 100000


def test_build_transcript_filters_by_round():
    events = [
        _make_round_open(1, "質問1"),
        _make_negotiation_action(1, "P01", "broadcast", turn=1, message="R1発言"),
        _make_round_open(2, "質問2"),
        _make_negotiation_action(2, "P01", "broadcast", turn=1, message="R2発言"),
    ]
    transcript = build_transcript(events, llm_calls=[], rounds=[2])
    assert [r["round_num"] for r in transcript] == [2]
    assert transcript[0]["entries"][0]["message"] == "R2発言"


def test_build_transcript_without_event_message_falls_back_to_none_when_no_llm_calls():
    """本文がイベントに無く、llm_calls（記録）も無ければ message は None（本文なし扱い）"""
    events = [
        _make_round_open(1, "質問A"),
        _make_negotiation_action(1, "P01", "broadcast", turn=1),  # messageキー無し＝古い記録
    ]
    transcript = build_transcript(events, llm_calls=[], rounds=None)
    assert transcript[0]["entries"][0]["message"] is None


def test_build_transcript_recovers_message_from_llm_calls_for_old_records():
    """messageがイベントに無い古い記録は、llm_callsのレスポンス本文から復元する"""
    events = [
        _make_round_open(1, "質問A"),
        _make_negotiation_action(1, "P01", "broadcast", turn=1),
    ]
    llm_calls = [{
        "phase": "negotiation", "round_num": 1, "player_id": "P01", "turn": 1,
        "response_text": json.dumps({
            "strategy": {"reason": "r"}, "action": {"type": "broadcast", "message": "復元された本文"},
        }),
    }]
    transcript = build_transcript(events, llm_calls, rounds=None)
    assert transcript[0]["entries"][0]["message"] == "復元された本文"


def test_build_transcript_recovers_last_matching_retry():
    """同じ(round, pid, turn)に複数行ある場合は、行為種別が一致する最後の行を採る"""
    events = [
        _make_round_open(1, "質問A"),
        _make_negotiation_action(1, "P09", "broadcast", turn=10),
    ]
    llm_calls = [
        {"phase": "negotiation", "round_num": 1, "player_id": "P09", "turn": 10,
         "invalid_response": True, "response_text": None},
        {"phase": "negotiation", "round_num": 1, "player_id": "P09", "turn": 10,
         "response_text": json.dumps({"action": {"type": "vote_commit", "vote": "YES"}})},
        {"phase": "negotiation", "round_num": 1, "player_id": "P09", "turn": 10,
         "response_text": json.dumps({"action": {"type": "broadcast", "message": "採用された本文"}})},
    ]
    transcript = build_transcript(events, llm_calls, rounds=None)
    assert transcript[0]["entries"][0]["message"] == "採用された本文"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
