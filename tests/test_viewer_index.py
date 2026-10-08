"""
viewer/log_index.py のテスト（サイクル3.0）

索引（`logs/viewer_index/{game_id}.json`相当）が作れること、prompt本文を一切
持たないこと、mtime+sizeが変わらなければ再生成されないことを確認する。
"""

import json
from pathlib import Path

from viewer.log_index import build_entries, index_path, load_or_build_index

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "viewer_logs"


def test_build_entries_never_contains_prompt_fields() -> None:
    llm_calls = [json.loads(line) for line in (FIXTURES / "fx_demo_llm_calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = build_entries(llm_calls)
    serialized = json.dumps(entries, ensure_ascii=False)
    assert "system_prompt" not in serialized
    assert "user_prompt" not in serialized
    # 索引完成後は生のresponse_textも持たない（strategy/action/memory/commentに分解済み）
    assert "システムプロンプト本文" not in serialized
    assert "ユーザープロンプト本文" not in serialized


def test_build_entries_extracts_strategy_and_memory() -> None:
    llm_calls = [json.loads(line) for line in (FIXTURES / "fx_demo_llm_calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = build_entries(llm_calls)
    neg = [e for e in entries if e["phase"] == "negotiation" and e["player_id"] == "P01" and e["round_num"] == 1]
    assert neg
    assert neg[0]["strategy"]["vote_plan"] == "YES"
    reflect = [e for e in entries if e["phase"] == "reflect" and e["player_id"] == "P03"]
    assert reflect
    assert "少数派" in reflect[0]["memory"]


def test_build_entries_handles_invalid_and_error_rows() -> None:
    llm_calls = [json.loads(line) for line in (FIXTURES / "fx_demo_llm_calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = build_entries(llm_calls)
    timeouts = [e for e in entries if e["error_type"] == "timeout"]
    assert len(timeouts) == 1
    invalids = [e for e in entries if e["invalid_response"]]
    assert len(invalids) == 1
    assert invalids[0]["reason"] == "actionキーが見つかりません"


def test_load_or_build_index_creates_and_reuses_cache(tmp_path: Path) -> None:
    log_dir = tmp_path / "llm"
    log_dir.mkdir()
    for name in ("fx_demo_events.jsonl", "fx_demo_llm_calls.jsonl", "fx_demo_seat_map.json", "fx_demo_questions.json"):
        (log_dir / name).write_bytes((FIXTURES / name).read_bytes())

    entries1 = load_or_build_index("fx_demo", log_dir)
    assert len(entries1) == 25
    idx_file = index_path("fx_demo", log_dir)
    assert idx_file.exists()
    first_mtime = idx_file.stat().st_mtime_ns

    entries2 = load_or_build_index("fx_demo", log_dir)
    assert entries2 == entries1
    # ソースが変わっていないので索引ファイルは再書き込みされない
    assert idx_file.stat().st_mtime_ns == first_mtime


def test_load_or_build_index_rebuilds_when_source_changes(tmp_path: Path) -> None:
    log_dir = tmp_path / "llm"
    log_dir.mkdir()
    calls_path = log_dir / "fx_demo_llm_calls.jsonl"
    calls_path.write_bytes((FIXTURES / "fx_demo_llm_calls.jsonl").read_bytes())

    entries1 = load_or_build_index("fx_demo", log_dir)
    # ソースに1行追記してサイズを変える
    with open(calls_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "round_num": 3, "player_id": "P01", "turn": 1, "phase": "negotiation",
            "model_id": "x", "response_text": None,
        }, ensure_ascii=False) + "\n")
    entries2 = load_or_build_index("fx_demo", log_dir)
    assert len(entries2) == len(entries1) + 1


def test_index_path_lives_under_logs_viewer_index(tmp_path: Path) -> None:
    p = index_path("abc", tmp_path)
    assert p == tmp_path / "viewer_index" / "abc.json"
