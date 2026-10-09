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


def test_acceptance_14_round2_start_resets_all_12_and_collects_entry_fee() -> None:
    """#14: R2の開始 → R1の退場者を含む12人全員が残っている状態に戻り、
    全員から参加費100万を徴収する"""
    # R1V1: 10対2（NO側2人が勝ち残り、8人が退場） → R1は1回の投票で終わる
    r1_split = {f"P{i:02d}": Vote.YES for i in range(1, 11)}
    r1_split.update({"P11": Vote.NO, "P12": Vote.NO})
    agents = make_roster({(1, 1): r1_split}, num_players=12)
    config = GameConfig.dev_small(num_players=12, num_rounds=2)
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=1, logger=logger)
    game.run()

    r1 = next(e for e in logger.events if e.event_type == "ROUND_START" and e.round_num == 1)
    assert r1.data["carryover_in"] == 0
    r2 = next(e for e in logger.events if e.event_type == "ROUND_START" and e.round_num == 2)
    assert r2.data["carryover_in"] == 0  # R1は決着で終わったので持ち越しはない

    entry_fee_r2 = [
        e for e in logger.events if e.event_type == "ENTRY_FEE_COLLECTED" and e.round_num == 2
    ]
    assert len(entry_fee_r2) == 12
    assert {e.data["player_id"] for e in entry_fee_r2} == set(f"P{i:02d}" for i in range(1, 13))
    assert all(e.data["paid"] == config.entry_fee for e in entry_fee_r2)

    # R2の最初の投票では12人全員が「残っている人」（R1の退場者8人も含む）
    commit_r2v1 = [
        e for e in logger.events
        if e.event_type == "COMMIT" and e.round_num == 2 and e.vote_num == 1
    ]
    assert {e.data["player_id"] for e in commit_r2v1} == set(f"P{i:02d}" for i in range(1, 13))


def test_acceptance_15_eliminated_players_skip_commit_but_can_negotiate() -> None:
    """#15: 退場者がいる投票 → 退場者にはCommitを求めない。退場者は
    Negotiationで発言・契約の提案と署名・送金・返済ができる"""
    from engine.models import BroadcastAction

    # V1: 7対5（7人が退場、5人が残る） → V2が行われる
    v1_split = {f"P{i:02d}": Vote.YES for i in range(1, 8)}
    v1_split.update({f"P{i:02d}": Vote.NO for i in range(8, 13)})
    agents = make_roster({(1, 1): v1_split}, num_players=12)
    # P01（V1で退場する側）がV2のNegotiationで全体発言する
    agents["P01"].negotiate_actions[(1, 2, 1)] = BroadcastAction(
        player_id="P01", message="退場者からの発言",
    )

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=1, logger=logger)
    game.run()

    # P01の発言が記録されている（退場者でもNegotiationのアクションは実行される）
    broadcast_events = [
        e for e in logger.events
        if e.event_type == "NEGOTIATION_ACTION" and e.data.get("action") == "broadcast"
        and e.data.get("player_id") == "P01"
    ]
    assert broadcast_events and broadcast_events[0].data["message"] == "退場者からの発言"

    # V2のCommitは残っている5人だけが対象（P01は含まれない）
    commit_v2 = [
        e for e in logger.events
        if e.event_type == "COMMIT" and e.round_num == 1 and e.vote_num == 2
    ]
    committed_ids = {e.data["player_id"] for e in commit_v2}
    assert "P01" not in committed_ids
    assert len(committed_ids) == 5


def test_acceptance_45_negotiation_turn_limits_by_vote_kind() -> None:
    """#45: 交渉の巡 → ラウンド最初の投票は最大10、決着の後の投票は最大6、
    やり直しの再投票は最大3"""
    from engine.models import VoteOutcome

    config = GameConfig.default_12()
    agents = make_roster({}, num_players=12)
    game = Game(config=config, agents=agents, seed=1, logger=EventLogger())

    assert game._negotiation_max_turns(1, []) == 10

    decisive = VoteOutcome(
        round_num=1, vote_num=1, yes_ids=[], no_ids=[], result="decisive",
        eliminated_ids=[], remaining_ids=[], consecutive_ties_before=0,
        consecutive_ties_after=0, extension_fee_collected=0, round_over=False,
    )
    assert game._negotiation_max_turns(2, [decisive]) == 6

    retry = VoteOutcome(
        round_num=1, vote_num=1, yes_ids=[], no_ids=[], result="retry",
        eliminated_ids=[], remaining_ids=[], consecutive_ties_before=0,
        consecutive_ties_after=1, extension_fee_collected=1_200_000, round_over=False,
    )
    assert game._negotiation_max_turns(2, [retry]) == 3


def test_on_question_published_called_only_for_votes_actually_used() -> None:
    """#48の前段（エンジン側の範囲）: 実際に使った投票の回数だけ
    on_question_publishedが呼ばれ、使わなかった質問は呼ばれない
    （実際の履歴ファイルへの追記はllm/questions.py側の責務、サイクル4.2）"""
    config = GameConfig.default_12()
    published: list[tuple[int, int]] = []
    agents = _bot_roster()
    game = Game(
        config=config, agents=agents, seed=7, logger=EventLogger(),
        on_question_published=lambda r, v, q: published.append((r, v)),
    )
    result = game.run()

    total_votes_used = sum(len(rs.votes) for rs in result.round_summaries)
    assert len(published) == total_votes_used
    assert len(published) < config.questions_per_game  # 24問すべては使っていない
    assert published == sorted(published)  # ラウンド・投票の順に1つずつ使う


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
