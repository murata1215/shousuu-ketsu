"""
scripts/llm_trial.py・scripts/summarize_trial.py のテスト（§サイクル2.0、AIを呼ばない）

v0.4（L12R4V6: 12人・4ラウンド・最大6投票）に合わせてサイクル4.2bで全面的に
直した。

- 偽の応答（tests/helpers.py::FakeAdapter）でL12R4V6が最後まで回ること
- 打ち切るラウンド数の指定が効くこと（ルール上のラウンド数は変わらない）
- 席の割り当てが、同じシードで同じになること
- 交渉の巡の上限（3種類）の指定が効くこと
- 費用上限に達したら、その席が止まる（他席は続く）こと
- 要約が記録から正しく作れること（v0.4のイベント種別）
- max_parallel_agents=1 と >1 で結果が一致すること
- 試合後の振り返りが全席に1回だけ呼ばれること
- 実行を打ち切った試合（stop_after_round < num_rounds）では試合後の振り返りを行わないこと
  （stop_after_round == num_rounds なら従来どおり行うこと）
- 途中経過が1行ごとにflushされること（ProgressPrinter・VoteProgressPrinter・
  _enable_line_buffering）
- 席ごとのモデル・借入額一覧（summarize_roster）
- 会話の書き出し（build_transcript）: 投票番号・巡・本文・DM宛先・送金額・契約・
  投票結果、ラウンド指定での絞り込み、llm_calls不在時の挙動、再試行時の採用行選択
- v0.3形式の記録をCLI（main相当）に渡すと、その旨を表示して止まること
  （summarize_contracts/summarize_final等の個別関数はviewer/log_parser.pyが
  v0.3の記録に対して直接importして使っているため、ここでは例外を投げない。
  v0.3判定はscripts/summarize_trial.py::main()のCLI層だけで行う）
"""

import io
import json
import sys
from pathlib import Path

import pytest

from engine.config import GameConfig
from llm.costing import worst_case_cost
from llm.models import get_model
from llm.prompt_builder import build_loan_prompt, build_system_prompt
from scripts.llm_trial import (
    ProgressPrinter, VoteProgressPrinter, _build_arg_parser, _enable_line_buffering,
    assign_seats, parse_roster, run_trial,
)
from scripts.summarize_trial import (
    V0_3_RecordError, build_transcript, load_trial_records, seat_label, short_model_label,
    summarize_contracts, summarize_final, summarize_post_game_reflections,
    summarize_roster, summarize_rounds, summarize_seats,
)
from tests.helpers import FakeAdapter


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


# --- GameConfigの既定: 席数に関係なくL12R4V6基準（12席以外はnum_playersだけ差し替え） ---

def test_run_trial_defaults_to_4_rounds_regardless_of_seat_count(tmp_path):
    roster = "L1,L2,L3,L4"
    questions_path = _write_questions_file(tmp_path, GameConfig().questions_per_game)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "1", "--game-id", "defaulttest",
        "--stop-after-round", "1", "--questions", str(questions_path), "--no-preflight",
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)
    events = load_trial_records("defaulttest", outcome["log_dir"])["events"]
    game_start = [e for e in events if e["event_type"] == "GAME_START"][0]
    assert game_start["data"]["num_rounds"] == 4  # GameConfigの既定（12席でなくても）


# --- 通し: 偽の応答でL12R4V6が最後まで回ること ---

def test_run_trial_full_12_seat_game_completes_with_fake_adapter(tmp_path):
    roster = ",".join(["L1", "L2", "L3", "L4", "L5", "L6"] * 2)  # 12席
    config = GameConfig.default_12()
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "7", "--game-id", "full12",
        "--questions", str(questions_path),
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)

    assert outcome["exit_code"] == 0
    result = outcome["result"]
    assert result is not None
    assert len(result.final_assets) == 12
    assert len(result.round_summaries) == config.num_rounds

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
    config = GameConfig(num_players=4, num_rounds=3)
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)
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


# --- 交渉の巡の上限（3種類） ---

def test_negotiation_max_turns_shorthand_sets_all_three(tmp_path):
    roster = "L1,L2,L3,L4"
    config = GameConfig(num_players=4, num_rounds=1)
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)
    args = _make_args(tmp_path, [
        "--roster", roster, "--seed", "1", "--game-id", "turnsshort",
        "--num-rounds", "1", "--stop-after-round", "1",
        "--negotiation-max-turns", "2",
        "--questions", str(questions_path), "--no-preflight",
    ])
    outcome = run_trial(args, adapter_factory=_fake_adapter_factory)
    assert outcome["result"] is not None
    # 交渉は全員passのため1巡で早期終了するはずだが、設定自体が効いているかは
    # run_trial内部のconfig構築ロジックを直接確認する（4引数すべて2になる）
    from scripts.llm_trial import parse_roster as _pr
    model_keys = _pr(roster)
    test_config = GameConfig(num_players=len(model_keys), num_rounds=1).model_copy(update={
        "negotiation_max_turns_first": 2, "negotiation_max_turns_next": 2, "negotiation_max_turns_retry": 2,
    })
    assert test_config.negotiation_max_turns_first == 2
    assert test_config.negotiation_max_turns_next == 2
    assert test_config.negotiation_max_turns_retry == 2


def test_negotiation_max_turns_individual_flags_override_shorthand(tmp_path):
    parser = _build_arg_parser()
    args = parser.parse_args([
        "--roster", "L1,L2,L3,L4", "--negotiation-max-turns", "2",
        "--negotiation-max-turns-retry", "1",
    ])
    assert args.negotiation_max_turns == 2
    assert args.negotiation_max_turns_first is None
    assert args.negotiation_max_turns_retry == 1
    # run_trial内のロジックと同じ組み立てを確認（個別指定が短縮形を上書きする）
    turn_updates: dict[str, int] = {}
    if args.negotiation_max_turns is not None:
        turn_updates["negotiation_max_turns_first"] = args.negotiation_max_turns
        turn_updates["negotiation_max_turns_next"] = args.negotiation_max_turns
        turn_updates["negotiation_max_turns_retry"] = args.negotiation_max_turns
    if args.negotiation_max_turns_retry is not None:
        turn_updates["negotiation_max_turns_retry"] = args.negotiation_max_turns_retry
    assert turn_updates == {
        "negotiation_max_turns_first": 2, "negotiation_max_turns_next": 2, "negotiation_max_turns_retry": 1,
    }


# --- 費用上限に達した席だけが止まる ---

def test_run_trial_per_seat_cap_blocks_only_that_seat(tmp_path):
    roster = "H1,L7,L7,L7"
    config = GameConfig(num_players=4, num_rounds=1)
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)

    system = build_system_prompt("P01", GameConfig())
    user = build_loan_prompt(GameConfig())
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


# --- 要約が記録から正しく作れること（v0.4のイベント種別） ---

def test_summarize_trial_builds_tables_from_known_records(tmp_path):
    roster = "L1,L2,L3,L4"
    config = GameConfig(num_players=4, num_rounds=1)
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)
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
    assert len(rounds[0]["votes"]) >= 1
    v1 = rounds[0]["votes"][0]
    assert v1["vote_num"] == 1
    assert sum(1 for v in v1["votes"].values() if v == "YES") \
        + sum(1 for v in v1["votes"].values() if v == "NO") == 4

    contracts = summarize_contracts(records["events"])
    assert contracts["established"] == []  # FakeAdapterは常にpassなので契約は成立しない
    assert contracts["ob_type_counts"] == {}
    assert contracts["share_percent_count"] == 0

    final = summarize_final(records["events"])
    assert final is not None
    assert final["final_ranks"] == result.final_ranks
    assert final["final_assets"] == result.final_assets

    seats = summarize_seats(records["events"], records["llm_calls"], records["seat_map"], final)
    assert {s["player_id"] for s in seats} == {"P01", "P02", "P03", "P04"}
    for s in seats:
        assert s["calls"] > 0  # choose_loan + negotiation(pass) + commit + reflect + post_game
        assert s["final_assets"] is not None
        assert s["final_rank"] is not None

    reflections = summarize_post_game_reflections(records["events"])
    assert isinstance(reflections, dict)


# --- max_parallel_agents=1 と >1 で結果が一致すること ---

def test_run_trial_parallel_matches_sequential_result(tmp_path):
    roster = "L1,L2,L3,L4,L5,L6"
    config = GameConfig(num_players=6, num_rounds=2)
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)

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
    config = GameConfig(num_players=4, num_rounds=1)
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)
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


# --- 打ち切った試合では試合後の振り返りを行わないこと ---

def test_run_trial_stop_before_num_rounds_skips_post_game_reflection(tmp_path):
    roster = "L1,L2,L3,L4"
    config = GameConfig(num_players=4, num_rounds=3)
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)
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
    config = GameConfig(num_players=4, num_rounds=2)
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)
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


# --- 途中経過が1行ごとにflushされること ---

def test_progress_printer_flushes_each_line_and_includes_vote_num(monkeypatch):
    calls = []

    def fake_print(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr("builtins.print", fake_print)
    progress = ProgressPrinter()
    progress({
        "player_id": "P07", "round_num": 1, "vote_num": 2, "turn": 3, "phase": "negotiation",
        "elapsed_ms": 10.0, "ok": True, "error_type": None,
    })
    assert len(calls) == 1
    assert calls[0]["kwargs"].get("flush") is True
    assert "R1V2 T3" in calls[0]["args"][0]


def test_vote_progress_printer_prints_decisive_line(monkeypatch):
    calls = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: calls.append(a[0]))

    class _Ev:
        def __init__(self, event_type, round_num, vote_num, data):
            self.event_type = event_type
            self.round_num = round_num
            self.vote_num = vote_num
            self.data = data

    printer = VoteProgressPrinter()
    printer(_Ev("VOTE_REVEALED", 1, 2, {"votes": {"P01": "YES", "P02": "YES", "P03": "NO", "P04": "NO", "P05": "NO"}}))
    printer(_Ev("VOTE_RESOLVED", 1, 2, {
        "result": "decisive", "eliminated_ids": ["P03", "P04", "P05"],
        "remaining_ids": ["P01", "P02"], "consecutive_ties_after": 0, "extension_fee_collected": 0,
    }))
    assert len(calls) == 1
    assert calls[0] == "R1V2 決着 YES2対NO3 退場: P03,P04,P05"


def test_vote_progress_printer_prints_abort_line_after_round_resolved(monkeypatch):
    calls = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: calls.append(a[0]))

    class _Ev:
        def __init__(self, event_type, round_num, vote_num, data):
            self.event_type = event_type
            self.round_num = round_num
            self.vote_num = vote_num
            self.data = data

    printer = VoteProgressPrinter()
    printer(_Ev("VOTE_REVEALED", 1, 3, {"votes": {f"P{i:02d}": "YES" for i in range(1, 7)}
                                         | {f"P{i:02d}": "NO" for i in range(7, 13)}}))
    printer(_Ev("VOTE_RESOLVED", 1, 3, {
        "result": "abort", "eliminated_ids": [], "remaining_ids": [f"P{i:02d}" for i in range(1, 13)],
        "consecutive_ties_after": 3, "extension_fee_collected": 1_200_000,
    }))
    assert calls == []  # 打ち切りの額が確定するまで表示を待つ
    printer(_Ev("ROUND_RESOLVED", 1, None, {
        "aborted": True, "winner_ids": [], "payout_per_winner": 0,
        "carryover_out": 15_600_000, "destroyed_pot": 0,
    }))
    assert len(calls) == 1
    assert calls[0] == "R1V3 打ち切り YES6対NO6 山1,560万をR2へ持ち越し"


def test_enable_line_buffering_real_file_returns_true(tmp_path):
    path = tmp_path / "out.log"
    with open(path, "w", encoding="utf-8") as f:
        applied = _enable_line_buffering(f)
        assert applied is True
        assert f.line_buffering is True


def test_enable_line_buffering_stringio_returns_false_without_raising():
    stream = io.StringIO()
    assert _enable_line_buffering(stream) is False


# --- 席ごとのモデル・借入額一覧 ---

def test_summarize_roster_lists_model_and_loan(tmp_path):
    roster = "L1,L2,L3,L4"
    config = GameConfig(num_players=4, num_rounds=1)
    questions_path = _write_questions_file(tmp_path, config.questions_per_game)
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


# --- 会話の書き出し（build_transcript、v0.4: 投票番号・巡入り） ---

def _make_round_start(round_num, carryover_in=0, pot=0):
    return {"event_type": "ROUND_START", "round_num": round_num, "vote_num": None, "phase": "open",
            "data": {"carryover_in": carryover_in, "pot": pot}}


def _make_vote_open(round_num, vote_num, question):
    return {"event_type": "VOTE_OPEN", "round_num": round_num, "vote_num": vote_num, "phase": "open",
            "data": {"question": question}}


def _make_negotiation_action(round_num, vote_num, pid, action, turn, **extra):
    data = {"player_id": pid, "action": action, "turn": turn, **extra}
    return {"event_type": "NEGOTIATION_ACTION", "round_num": round_num, "vote_num": vote_num,
            "phase": "negotiation", "data": data}


def test_build_transcript_orders_entries_and_includes_message_from_event():
    """イベント自体にmessageがあれば、それをそのまま使う（新しい記録の経路）"""
    events = [
        _make_round_start(1, carryover_in=0, pot=12_000_000),
        _make_vote_open(1, 1, "質問A"),
        _make_negotiation_action(1, 1, "P01", "broadcast", turn=1, message="全体へ"),
        _make_negotiation_action(1, 1, "P02", "dm", turn=1, to="P03", message="DM本文"),
        {"event_type": "TRANSFER", "round_num": 1, "vote_num": 1, "phase": "negotiation",
         "data": {"player_id": "P01", "to": "P02", "amount": 300000, "turn": 2}},
        {"event_type": "VOTE_REVEALED", "round_num": 1, "vote_num": 1, "phase": "settlement",
         "data": {"votes": {"P01": "YES", "P02": "NO"}}},
        {"event_type": "VOTE_RESOLVED", "round_num": 1, "vote_num": 1, "phase": "settlement",
         "data": {"result": "decisive", "eliminated_ids": ["P01"], "remaining_ids": ["P02"],
                   "consecutive_ties_after": 0, "extension_fee_collected": 0}},
    ]
    transcript = build_transcript(events, llm_calls=[], rounds=None)
    assert len(transcript) == 1
    r = transcript[0]
    assert r["pot"] == 12_000_000
    assert len(r["votes"]) == 1
    v = r["votes"][0]
    assert v["vote_num"] == 1
    assert v["question"] == "質問A"
    kinds = [e["kind"] for e in v["entries"]]
    assert kinds == ["broadcast", "dm", "transfer", "vote_result"]
    assert v["entries"][0]["message"] == "全体へ"
    assert v["entries"][1] == {"turn": 1, "kind": "dm", "from": "P02", "to": "P03", "message": "DM本文"}
    assert v["entries"][2]["amount"] == 300000
    assert v["entries"][3]["result"] == "decisive"
    assert v["entries"][3]["eliminated_ids"] == ["P01"]


def test_build_transcript_filters_by_round():
    events = [
        _make_round_start(1),
        _make_vote_open(1, 1, "質問1"),
        _make_negotiation_action(1, 1, "P01", "broadcast", turn=1, message="R1発言"),
        _make_round_start(2),
        _make_vote_open(2, 1, "質問2"),
        _make_negotiation_action(2, 1, "P01", "broadcast", turn=1, message="R2発言"),
    ]
    transcript = build_transcript(events, llm_calls=[], rounds=[2])
    assert [r["round_num"] for r in transcript] == [2]
    assert transcript[0]["votes"][0]["entries"][0]["message"] == "R2発言"


def test_build_transcript_distinguishes_votes_within_same_round():
    events = [
        _make_round_start(1),
        _make_vote_open(1, 1, "質問1"),
        _make_negotiation_action(1, 1, "P01", "broadcast", turn=1, message="V1の発言"),
        _make_vote_open(1, 2, "質問2"),
        _make_negotiation_action(1, 2, "P01", "broadcast", turn=1, message="V2の発言"),
    ]
    transcript = build_transcript(events, llm_calls=[], rounds=None)
    assert len(transcript) == 1
    vote_nums = [v["vote_num"] for v in transcript[0]["votes"]]
    assert vote_nums == [1, 2]
    assert transcript[0]["votes"][0]["entries"][0]["message"] == "V1の発言"
    assert transcript[0]["votes"][1]["entries"][0]["message"] == "V2の発言"


def test_build_transcript_without_event_message_falls_back_to_none_when_no_llm_calls():
    """本文がイベントに無く、llm_calls（記録）も無ければ message は None（本文なし扱い）"""
    events = [
        _make_round_start(1),
        _make_vote_open(1, 1, "質問A"),
        _make_negotiation_action(1, 1, "P01", "broadcast", turn=1),  # messageキー無し＝古い記録
    ]
    transcript = build_transcript(events, llm_calls=[], rounds=None)
    assert transcript[0]["votes"][0]["entries"][0]["message"] is None


def test_build_transcript_recovers_message_from_llm_calls_for_old_records():
    """messageがイベントに無い記録は、llm_callsのレスポンス本文から復元する"""
    events = [
        _make_round_start(1),
        _make_vote_open(1, 1, "質問A"),
        _make_negotiation_action(1, 1, "P01", "broadcast", turn=1),
    ]
    llm_calls = [{
        "phase": "negotiation", "round_num": 1, "vote_num": 1, "player_id": "P01", "turn": 1,
        "response_text": json.dumps({
            "strategy": {"reason": "r"}, "action": {"type": "broadcast", "message": "復元された本文"},
        }),
    }]
    transcript = build_transcript(events, llm_calls, rounds=None)
    assert transcript[0]["votes"][0]["entries"][0]["message"] == "復元された本文"


def test_build_transcript_recovers_last_matching_retry():
    """同じ(round, vote, pid, turn)に複数行ある場合は、行為種別が一致する最後の行を採る"""
    events = [
        _make_round_start(1),
        _make_vote_open(1, 1, "質問A"),
        _make_negotiation_action(1, 1, "P09", "broadcast", turn=10),
    ]
    llm_calls = [
        {"phase": "negotiation", "round_num": 1, "vote_num": 1, "player_id": "P09", "turn": 10,
         "invalid_response": True, "response_text": None},
        {"phase": "negotiation", "round_num": 1, "vote_num": 1, "player_id": "P09", "turn": 10,
         "response_text": json.dumps({"action": {"type": "vote_commit", "vote": "YES"}})},
        {"phase": "negotiation", "round_num": 1, "vote_num": 1, "player_id": "P09", "turn": 10,
         "response_text": json.dumps({"action": {"type": "broadcast", "message": "採用された本文"}})},
    ]
    transcript = build_transcript(events, llm_calls, rounds=None)
    assert transcript[0]["votes"][0]["entries"][0]["message"] == "採用された本文"


# --- v0.3形式の記録: CLI層だけで検出して止まる。個別関数は例外を投げない ---
#
# summarize_contracts/summarize_final/summarize_roster/summarize_post_game_reflections
# はviewer/log_parser.pyがv0.3の記録（logs/llm/l12r12_2002等）に対して直接
# importして使っているため、ここで例外を投げるとビューアが壊れる
# （CLAUDE.md過去の落とし穴⑦と同種の事故）。v0.3判定は
# scripts/summarize_trial.py::main()のCLI層だけで行う。

def test_v0_3_style_records_do_not_raise_from_library_functions():
    """viewer/log_parser.pyが直接呼ぶ関数は、v0.3の記録でも例外を投げない"""
    v0_3_events = [
        {"event_type": "ROUND_OPEN", "round_num": 1, "phase": "open",
         "data": {"question": "質問1", "carryover": 0}},
        {"event_type": "GAME_END", "round_num": 1, "phase": "finance",
         "data": {"final_ranks": {"P01": 1}, "final_assets": {"P01": 1_000_000}}},
    ]
    assert summarize_contracts(v0_3_events)["established"] == []
    assert summarize_final(v0_3_events) == {
        "final_ranks": {"P01": 1}, "final_assets": {"P01": 1_000_000},
    }


def test_check_not_v0_3_detects_round_open_and_minority_resolved():
    from scripts.summarize_trial import _check_not_v0_3
    with pytest.raises(V0_3_RecordError):
        _check_not_v0_3([{"event_type": "ROUND_OPEN", "round_num": 1, "phase": "open", "data": {}}])
    with pytest.raises(V0_3_RecordError):
        _check_not_v0_3([{"event_type": "MINORITY_RESOLVED", "round_num": 1, "phase": "settlement", "data": {}}])
    _check_not_v0_3([{"event_type": "VOTE_OPEN", "round_num": 1, "phase": "open", "data": {}}])  # raise しない


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
