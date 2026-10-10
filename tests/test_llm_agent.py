"""
AI席（LLMAgent）のテスト（§9・§13、AIを呼ばない。偽の応答を返す部品を使う）

- choose_loan/negotiate/commit/reflectの各フックがvote_num入りのI/F
  （engine/negotiation.py::PlayerAgent）で正しく動くこと
- 無効な応答・呼び出し失敗が試合を止めず安全側に倒れること
- commit()は内部で再試行しない（無効ならNone相当を返し、engine側の
  2回ループに委ねる）
- 偽の応答でL12R4V6（12席・4ラウンド・最大6投票）が最後まで回ること。
  無効な応答は1回の再試行の後自動代行になること。帳尻が合うこと
- 費用上限をつなぐと、上限超えでAPIを呼ばず安全側に倒れること
"""

import json

import pytest

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import PassAction, PlayerState, Vote
from llm.game_cost_budget import GameCostBudget
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from llm.models import get_model
from tests.helpers import FakeAdapter


def _pass_text(emotion="楽"):
    return json.dumps({"strategy": {"reason": "r", "emotion": emotion}, "action": {"type": "pass"}})


def _vote_text(vote="YES"):
    return json.dumps({"strategy": {"reason": "r", "emotion": "楽"}, "action": {"type": "vote_commit", "vote": vote}})


def _make_agent(player_id="P01", texts=None, model_key="H1", game_cost_budget=None, tmp_path=None, config=None):
    adapter = FakeAdapter(texts=texts)
    logger = LLMLogger(output_dir=tmp_path or "/tmp/shousuu_ketsu_test_logs", game_id="test")
    agent = LLMAgent(
        player_id, get_model(model_key), adapter, logger, config or GameConfig.default_12(),
        game_cost_budget=game_cost_budget,
    )
    return agent, adapter, logger


def _dummy_player_state(player_id="P01"):
    return PlayerState(player_id=player_id, cash=1_200_000, initial_loan=1_200_000)


def _dummy_visible_state():
    """v0.4の`_build_visible_state()`と同じキー集合を持つ最小限のダミー"""
    return {
        "round_num": 1, "vote_num": 1, "question": "テスト", "pot": 12_000_000,
        "remaining_ids": [f"P{i:02d}" for i in range(1, 13)], "eliminated_ids": [],
        "consecutive_ties": 0, "negotiation_max_turns": 10,
        "initial_loans": {"P01": 1_200_000}, "public_ranks_history": {},
        "round_contract_counts": [], "round_vote_results": [], "round_results": [],
        "last_vote_result": None, "last_round_result": None,
        "messages": [], "my_last_action_error": None,
        "my_contracts": [], "contracts_pending": [],
        "my_finance": {
            "cash": 1_200_000, "debt_pre": 1_200_000, "debt_pre_repayable": False,
            "debt_pre_note": "返済不可（開始前の借金・5%）", "debt_post": 0, "total_debt": 1_200_000,
            "remaining_credit": 8_800_000, "interest_forecast": 60_000, "obligations_due": [],
        },
        "my_rank": {"rank": 1, "tied": True, "n_players": 12},
    }


# --- choose_loan ---

def test_choose_loan_parses_valid_amount(tmp_path):
    text = json.dumps({"loan_amount": 3_000_000, "reason": "r"})
    agent, adapter, _ = _make_agent(texts=[text], tmp_path=tmp_path)
    amount = agent.choose_loan(GameConfig.default_12())
    assert amount == 3_000_000
    assert len(adapter.calls) == 1


def test_choose_loan_falls_back_to_min_on_invalid_response(tmp_path):
    agent, _, _ = _make_agent(texts=["これは壊れた応答です"], tmp_path=tmp_path)
    config = GameConfig.default_12()
    assert agent.choose_loan(config) == config.loan_min


def test_choose_loan_clamps_out_of_range_amount(tmp_path):
    text = json.dumps({"loan_amount": 99_000_000, "reason": "r"})
    agent, _, _ = _make_agent(texts=[text], tmp_path=tmp_path)
    config = GameConfig.default_12()
    assert agent.choose_loan(config) == config.loan_max


def test_choose_loan_falls_back_on_adapter_error(tmp_path):
    agent, _, _ = _make_agent(texts=[RuntimeError("boom")], tmp_path=tmp_path)
    config = GameConfig.default_12()
    assert agent.choose_loan(config) == config.loan_min


# --- negotiate（vote_num入りI/F） ---

def test_negotiate_parses_valid_action(tmp_path):
    agent, _, _ = _make_agent(texts=[_pass_text()], tmp_path=tmp_path)
    action = agent.negotiate(_dummy_player_state(), 1, 1, 1, _dummy_visible_state())
    assert isinstance(action, PassAction)
    assert agent.valid_json_count == 1
    assert agent.last_emotion == "楽"


def test_negotiate_retries_once_on_invalid_then_succeeds(tmp_path):
    agent, adapter, _ = _make_agent(texts=["壊れています", _pass_text()], tmp_path=tmp_path)
    action = agent.negotiate(_dummy_player_state(), 1, 1, 1, _dummy_visible_state())
    assert isinstance(action, PassAction)
    assert agent.action_correction_count == 1
    assert len(adapter.calls) == 2
    # 2回目の呼び出しは是正指示を含む
    assert "前回の回答にエラーがありました" in adapter.calls[1]["messages"][0]["content"]


def test_negotiate_falls_back_to_pass_after_exhausting_retries(tmp_path):
    agent, _, _ = _make_agent(texts=["壊れ1", "壊れ2", "壊れ3"], tmp_path=tmp_path)
    action = agent.negotiate(_dummy_player_state(), 1, 1, 1, _dummy_visible_state())
    assert isinstance(action, PassAction)


def test_negotiate_falls_back_to_pass_on_adapter_error(tmp_path):
    agent, _, _ = _make_agent(texts=[RuntimeError("boom")], tmp_path=tmp_path)
    action = agent.negotiate(_dummy_player_state(), 1, 1, 1, _dummy_visible_state())
    assert isinstance(action, PassAction)
    assert agent.last_emotion is None


def test_negotiate_logs_vote_num_on_log_call(tmp_path):
    """L12R4V6のR2V3の交渉でも、記録（*_llm_calls.jsonl）にvote_numが残る"""
    agent, _, logger = _make_agent(texts=[_pass_text()], tmp_path=tmp_path)
    agent.negotiate(_dummy_player_state(), 2, 3, 1, _dummy_visible_state())
    entry = logger.entries[-1]
    assert entry["round_num"] == 2
    assert entry["vote_num"] == 3


def test_negotiate_invalid_response_logs_vote_num(tmp_path):
    agent, _, logger = _make_agent(texts=["壊れ1", "壊れ2", "壊れ3"], tmp_path=tmp_path)
    agent.negotiate(_dummy_player_state(), 2, 3, 1, _dummy_visible_state())
    invalid_entries = [e for e in logger.entries if e.get("invalid_response")]
    assert invalid_entries
    assert all(e["vote_num"] == 3 for e in invalid_entries)


# --- commit（内部で再試行しない、vote_num入りI/F） ---

def test_commit_parses_valid_vote(tmp_path):
    agent, adapter, _ = _make_agent(texts=[_vote_text("NO")], tmp_path=tmp_path)
    vote = agent.commit(_dummy_player_state(), 1, 1, _dummy_visible_state())
    assert vote == Vote.NO
    assert len(adapter.calls) == 1  # 1回だけ（内部で再試行しない）


def test_commit_returns_non_vote_on_invalid_response_without_retrying(tmp_path):
    agent, adapter, _ = _make_agent(texts=["壊れています"], tmp_path=tmp_path)
    result = agent.commit(_dummy_player_state(), 1, 1, _dummy_visible_state())
    assert not isinstance(result, Vote)
    assert len(adapter.calls) == 1


def test_commit_returns_non_vote_on_adapter_error(tmp_path):
    agent, _, _ = _make_agent(texts=[RuntimeError("boom")], tmp_path=tmp_path)
    result = agent.commit(_dummy_player_state(), 1, 1, _dummy_visible_state())
    assert not isinstance(result, Vote)


def test_commit_logs_vote_num_on_log_call(tmp_path):
    agent, _, logger = _make_agent(texts=[_vote_text("YES")], tmp_path=tmp_path)
    agent.commit(_dummy_player_state(), 1, 4, _dummy_visible_state())
    entry = logger.entries[-1]
    assert entry["round_num"] == 1
    assert entry["vote_num"] == 4


# --- reflect（ラウンド単位。vote_numは持たない） ---

def test_reflect_updates_memory(tmp_path):
    text = json.dumps({"memory": "次は様子を見る"})
    agent, _, _ = _make_agent(texts=[text], tmp_path=tmp_path)
    agent.reflect(_dummy_player_state(), 1, _dummy_visible_state())
    assert agent._memory == "次は様子を見る"


def test_reflect_keeps_old_memory_on_failure(tmp_path):
    agent, _, _ = _make_agent(texts=["壊れています"], tmp_path=tmp_path)
    agent._memory = "以前のメモ"
    agent.reflect(_dummy_player_state(), 1, _dummy_visible_state())
    assert agent._memory == "以前のメモ"


def test_reflect_logs_none_vote_num(tmp_path):
    text = json.dumps({"memory": "m"})
    agent, _, logger = _make_agent(texts=[text], tmp_path=tmp_path)
    agent.reflect(_dummy_player_state(), 2, _dummy_visible_state())
    entry = logger.entries[-1]
    assert entry["vote_num"] is None


# --- post_game_reflect ---

def test_post_game_reflect_logs_none_vote_num(tmp_path):
    text = json.dumps({"emotion": "楽", "comment": "良い試合だった"})
    agent, _, logger = _make_agent(texts=[text], tmp_path=tmp_path)
    context = {
        "own_rank": 1, "own_rank_tied": False, "final_assets": 6_000_000,
        "round_results": [], "all_final_ranks": [], "all_final_assets": {},
    }
    comment = agent.post_game_reflect(context)
    assert comment is not None
    entry = logger.entries[-1]
    assert entry["vote_num"] is None


# --- DevRelay席のsystem promptに匿名化行が重複しないこと ---

def test_devrelay_seat_system_prompt_excludes_anonymization_line(tmp_path):
    from llm.providers.devrelay_http import ANONYMIZATION_LINE
    agent, _, _ = _make_agent(model_key="DR_HAIKU", tmp_path=tmp_path)
    assert ANONYMIZATION_LINE not in agent._system_prompt


def test_api_seat_system_prompt_includes_anonymization_line_once(tmp_path):
    from llm.providers.devrelay_http import ANONYMIZATION_LINE
    agent, _, _ = _make_agent(model_key="H1", tmp_path=tmp_path)
    assert agent._system_prompt.count(ANONYMIZATION_LINE) == 1


# --- 費用上限 ---

def test_budget_block_prevents_api_call_and_falls_back_safely(tmp_path):
    logger = EventLogger()
    budget = GameCostBudget(per_player_cap_usd=0.0, game_cap_usd=100.0, event_logger=logger)
    agent, adapter, _ = _make_agent(
        model_key="H1", game_cost_budget=budget, tmp_path=tmp_path,
    )
    config = GameConfig.default_12()
    amount = agent.choose_loan(config)
    assert amount == config.loan_min
    assert len(adapter.calls) == 0  # 予約でブロックされAPIを呼んでいない
    assert budget.blocks  # ブロックが記録されている


def test_budget_block_event_carries_vote_num(tmp_path):
    """LLM_BUDGET_BLOCKEDイベント（engine/events.py）にvote_numが残る"""
    logger = EventLogger()
    budget = GameCostBudget(per_player_cap_usd=0.0, game_cap_usd=100.0, event_logger=logger)
    agent, _, _ = _make_agent(model_key="H1", game_cost_budget=budget, tmp_path=tmp_path)
    agent.negotiate(_dummy_player_state(), 2, 3, 1, _dummy_visible_state())
    block_events = [e for e in logger.events if e.event_type == "LLM_BUDGET_BLOCKED"]
    assert block_events
    assert block_events[-1].vote_num == 3


# --- 通し: 偽の応答でL12R4V6が最後まで回ること ---

def test_full_game_with_fake_llm_agents_completes_and_balances(tmp_path):
    config = GameConfig.default_12()
    llm_logger = LLMLogger(output_dir=tmp_path, game_id="full")
    agents = {}
    for i in range(1, 13):
        pid = f"P{i:02d}"
        loan_text = json.dumps({"loan_amount": config.loan_min, "reason": "safe"})
        negotiate_text = _pass_text()
        commit_text = _vote_text("YES" if i % 2 == 0 else "NO")
        reflect_text = json.dumps({"memory": ""})
        # choose_loan 1回 + (交渉・commit・reflectの繰り返し) 十分な数を積む
        # （default_textでも同じ内容が繰り返される）
        texts = [loan_text] + [negotiate_text, commit_text, reflect_text] * 400
        adapter = FakeAdapter(texts=texts, default_text=negotiate_text)
        agents[pid] = LLMAgent(pid, get_model("H1"), adapter, llm_logger, config)

    # P01だけ、commitで常に無効な応答を返すよう上書きする
    # （§4.4: 1回だけ再試行→自動代行を確認するため）
    broken_commit_agent = agents["P01"]
    broken_commit_agent.commit = lambda ps, rn, vn, vs: None  # type: ignore[method-assign]

    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=7, logger=logger)
    result = game.run()

    assert len(result.round_summaries) == config.num_rounds

    # P01は少なくとも1回AUTO COMMITになっているはず
    auto_commit_events = [e for e in logger.events if e.event_type == "AUTO_COMMIT" and e.data.get("player_id") == "P01"]
    assert len(auto_commit_events) >= 1

    # 帳尻（tests/test_invariants.pyと同じ検算、v0.4のフィールド名）
    expected_total = -(
        result.total_interest + result.total_destroyed_pot + result.total_forfeited_remainder
    )
    assert sum(result.final_assets.values()) == expected_total
    assert all(p.cash >= 0 for p in result.final_players.values())
