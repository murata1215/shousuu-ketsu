"""
ゲームループ全体のテスト（§7）

12ラウンド完走、持ち越しの連続積み上げ、借入選択の同時性（§12.3 #24）、
自動代行（§4.4）、同一シードでの完全再現性を確認する。
"""

import inspect

from bots import BOT_REGISTRY, DEFAULT_ROSTER
from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import Vote
from engine.negotiation import PlayerAgent
from tests.helpers import FailingCommitAgent, make_roster


def _bot_roster(seed_offset: int = 0) -> dict:
    agents = {}
    for i in range(1, 13):
        pid = f"P{i:02d}"
        name = DEFAULT_ROSTER[(i - 1) % len(DEFAULT_ROSTER)]
        agents[pid] = BOT_REGISTRY[name](seed=i + seed_offset)
    return agents


def test_full_12_round_game_completes() -> None:
    config = GameConfig.default_12()
    agents = _bot_roster()
    game = Game(config=config, agents=agents, seed=7, logger=EventLogger())
    result = game.run()
    assert len(result.round_summaries) == 12
    assert set(result.final_assets.keys()) == set(agents.keys())


def test_carryover_accumulates_twice_then_paid_out() -> None:
    """持ち越しが2回続けて積み上がり、次の少数派がまとめて受け取ること"""
    split_tie_a = {f"P{i:02d}": Vote.YES for i in range(1, 7)}
    split_tie_a.update({f"P{i:02d}": Vote.NO for i in range(7, 13)})
    split_tie_b = dict(split_tie_a)  # 2回目も6対6
    split_payout = {f"P{i:02d}": Vote.YES for i in range(1, 9)}
    split_payout.update({f"P{i:02d}": Vote.NO for i in range(9, 13)})

    votes_by_round = {1: split_tie_a, 2: split_tie_b, 3: split_payout}
    agents = make_roster(votes_by_round, num_players=12)
    config = GameConfig.dev_small(num_players=12, num_rounds=3)
    game = Game(config=config, agents=agents, seed=1, logger=EventLogger())
    result = game.run()

    r1, r2, r3 = result.round_summaries
    assert r1.minority_outcome.minority_side is None
    assert r1.minority_outcome.carryover_after == 1_200_000
    assert r2.minority_outcome.minority_side is None
    assert r2.minority_outcome.carryover_before == 1_200_000
    assert r2.minority_outcome.carryover_after == 2_400_000

    assert r3.minority_outcome.carryover_before == 2_400_000
    assert len(r3.minority_outcome.minority_ids) == 4
    # (8*10万 + 240万) / 4人 = 80万
    assert r3.minority_outcome.payout_per_minority == 800_000
    assert r3.minority_outcome.carryover_after == 0


def test_acceptance_24_choose_loan_cannot_see_others() -> None:
    """#24: 借入額の選択中、全員の決定がそろうまで他のAIの借入額は見えない

    choose_loan() の引数は config のみであり、構造上 players 辞書や他人の
    借入額を一切渡さないため、見える経路自体が存在しない。
    """
    sig = inspect.signature(PlayerAgent.choose_loan)
    params = list(sig.parameters.keys())
    assert params == ["self", "config"]


def test_auto_commit_when_commit_always_fails() -> None:
    """無効な出力が続く場合、1回だけ再試行した後システムが自動代行する（§4.4）"""
    from tests.helpers import ScriptedAgent

    agents = {
        pid: (FailingCommitAgent(pid) if pid == "P01" else ScriptedAgent(pid))
        for pid in (f"P{i:02d}" for i in range(1, 13))
    }
    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=99, logger=EventLogger())
    result = game.run()

    summary = result.round_summaries[0]
    assert "P01" in summary.auto_commit_ids
    assert summary.votes["P01"] in (Vote.YES, Vote.NO)


def test_my_finance_marks_pre_debt_as_not_repayable() -> None:
    """財務通知のmy_financeに、開始前の借金が返済不可である表示が付く（§3.5 v0.3）"""
    from engine.models import PassAction
    from tests.helpers import ScriptedAgent

    class _RecordingAgent(ScriptedAgent):
        def __init__(self, player_id: str) -> None:
            super().__init__(player_id)
            self.seen_states: list[dict] = []

        def negotiate(self, player_state, round_num, turn, visible_state):
            self.seen_states.append(visible_state)
            return PassAction(player_id=self.player_id)

    recorder = _RecordingAgent("P01")
    agents = {
        pid: (recorder if pid == "P01" else ScriptedAgent(pid))
        for pid in (f"P{i:02d}" for i in range(1, 13))
    }
    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=1, logger=EventLogger())
    game.run()

    assert recorder.seen_states, "negotiateが一度も呼ばれていない"
    my_finance = recorder.seen_states[0]["my_finance"]
    assert my_finance["debt_pre_repayable"] is False
    assert "返済不可" in my_finance["debt_pre_note"]
    # 既存キーは消えていない（値ではなく表示を足すだけ、プラン§2）
    assert "debt_pre" in my_finance and "debt_post" in my_finance


def test_same_seed_produces_identical_result() -> None:
    """同じシードで2回回して、結果が完全に一致すること"""
    config = GameConfig.default_12()

    def run_once():
        agents = _bot_roster()
        game = Game(config=config, agents=agents, seed=123, logger=EventLogger())
        return game.run()

    result1 = run_once()
    result2 = run_once()
    assert result1.model_dump() == result2.model_dump()
