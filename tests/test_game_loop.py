"""
ゲームループ全体のテスト（§4/§7、v0.4）

4ラウンド完走、やり直し連続3回による打ち切りと持ち越し、借入選択の
同時性（§12.3 #50）、自動代行（§4.6）、同一シードでの完全再現性を
確認する。

サイクル4.0でv0.3から全面更新した。v0.3の「6対6で持ち越しが積み上がる」
テストは、v0.4では「やり直しが連続3回続くと打ち切りになり、山が次ラウンドへ
持ち越される」（§4.4）という別の仕組みに置き換わっている。
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


def test_full_4_round_game_completes() -> None:
    config = GameConfig.default_12()
    agents = _bot_roster()
    game = Game(config=config, agents=agents, seed=7, logger=EventLogger())
    result = game.run()
    assert len(result.round_summaries) == 4
    assert set(result.final_assets.keys()) == set(agents.keys())


def test_abort_carries_pot_to_next_round_then_decisive_payout() -> None:
    """やり直しが連続3回続くと打ち切りになり、山が次ラウンドへ持ち越される
    （§4.4）。次ラウンドで決着すれば、持ち越しを含めた山を勝ち残りが受け取る
    （§4.5）"""
    tie_6_6 = {f"P{i:02d}": Vote.YES for i in range(1, 7)}
    tie_6_6.update({f"P{i:02d}": Vote.NO for i in range(7, 13)})
    # R2V1: 11対1（NO側1人が勝ち残り）
    decisive_11_1 = {f"P{i:02d}": Vote.YES for i in range(1, 12)}
    decisive_11_1["P12"] = Vote.NO

    votes_by_vote = {
        (1, 1): tie_6_6, (1, 2): tie_6_6, (1, 3): tie_6_6,
        (2, 1): decisive_11_1,
    }
    agents = make_roster(votes_by_vote, num_players=12)
    config = GameConfig.dev_small(num_players=12, num_rounds=2)
    game = Game(config=config, agents=agents, seed=1, logger=EventLogger())
    result = game.run()

    r1, r2 = result.round_summaries
    assert r1.aborted is True
    assert len(r1.votes) == 3
    assert r1.carryover_in == 0
    # 山 = 12人*100万 + 延長料12人*10万*3回 = 1,200万 + 360万 = 1,560万
    assert r1.pot_final == 15_600_000
    assert r1.carryover_out == 15_600_000
    assert r1.winner_ids == []

    assert r2.aborted is False
    assert r2.carryover_in == 15_600_000
    # 山 = 1,200万 + 1,560万 = 2,760万
    assert r2.pot_final == 27_600_000
    assert r2.winner_ids == ["P12"]
    assert r2.payout_per_winner == 27_600_000
    assert r2.carryover_out == 0


def test_acceptance_50_choose_loan_cannot_see_others() -> None:
    """#50: 借入額の選択中、全員の決定がそろうまで他のAIの借入額は見えない

    choose_loan() の引数は config のみであり、構造上 players 辞書や他人の
    借入額を一切渡さないため、見える経路自体が存在しない。
    """
    sig = inspect.signature(PlayerAgent.choose_loan)
    params = list(sig.parameters.keys())
    assert params == ["self", "config"]


def test_auto_commit_when_commit_always_fails() -> None:
    """無効な出力が続く場合、1回だけ再試行した後システムが自動代行する（§4.6）"""
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


def test_my_finance_marks_pre_debt_as_not_repayable() -> None:
    """財務通知のmy_financeに、開始前の借金が返済不可である表示が付く（§3.5）"""
    from tests.helpers import ScriptedAgent

    class _RecordingAgent(ScriptedAgent):
        def __init__(self, player_id: str) -> None:
            super().__init__(player_id)
            self.seen_states: list[dict] = []

        def negotiate(self, player_state, round_num, vote_num, turn, visible_state):
            self.seen_states.append(visible_state)
            return super().negotiate(player_state, round_num, vote_num, turn, visible_state)

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
    # 既存キーは消えていない（値ではなく表示を足すだけ）
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
