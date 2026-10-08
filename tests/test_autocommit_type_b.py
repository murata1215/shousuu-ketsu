"""
自動代行と型Bの指定のテスト（§4.6・仕様書v0.4 §12.3 #46）

時間切れ（コミット失敗）のとき、その投票に型Bの指定があればそれに従い、
無ければ（または矛盾していれば）シードから決まる乱数でYES/NOを選ぶことを
確認する。

サイクル4.0でv0.3から全面更新した。型Bの対象が(round_num, vote_num)の
二重構造になったため、termsにvote_numを追加した。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import ContractProposeAction, Vote
from tests.helpers import FailingCommitAgent, make_roster


def _roster_with_failing_commit(num_players: int = 12) -> dict:
    agents = make_roster({}, num_players=num_players)
    for pid, agent in agents.items():
        agents[pid] = FailingCommitAgent(pid, votes=agent.votes, loan=agent.loan)
    return agents


def test_acceptance_46_timeout_with_type_b_yes_instruction_auto_commits_yes() -> None:
    """
    #46: 時間切れ、その投票に型B「YES」指定あり → YESで自動代行。AUTO COMMITを公示
    """
    agents = _roster_with_failing_commit()
    terms = [{"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_vote",
              "round_num": 1, "vote_num": 1, "details": {"vote": "YES"}}]
    agents["P01"].negotiate_actions[(1, 1, 1)] = ContractProposeAction(
        player_id="P01", with_players=["P02"], terms=terms,
    )
    agents["P02"].sign_proposer_at[(1, 1, 2)] = "P01"

    logger = EventLogger()
    game = Game(config=GameConfig.default_12(), agents=agents, seed=3, logger=logger)
    result = game.run()

    assert "P01" in result.round_summaries[0].auto_commit_ids
    auto_events = [
        e for e in logger.events
        if e.event_type == "AUTO_COMMIT" and e.round_num == 1 and e.vote_num == 1
    ]
    p01_event = next(e for e in auto_events if e.data["player_id"] == "P01")
    assert p01_event.data["vote"] == "YES"
    assert p01_event.data["source"] == "type_b"


def test_contradictory_type_b_instructions_fall_back_to_seeded_random() -> None:
    """
    同じ投票にYES指定とNO指定の両方がある（矛盾）場合はシード由来の乱数
    （§4.6）。同一シードで2回実行すると同じ結果になることを確認する。
    """
    def _build_and_run(seed: int) -> str:
        agents = _roster_with_failing_commit()
        terms = [
            {"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_vote",
             "round_num": 1, "vote_num": 1, "details": {"vote": "YES"}},
            {"obligor": "P01", "counterparty": "P03", "ob_type": "type_b_vote",
             "round_num": 1, "vote_num": 1, "details": {"vote": "NO"}},
        ]
        agents["P01"].negotiate_actions[(1, 1, 1)] = ContractProposeAction(
            player_id="P01", with_players=["P02", "P03"], terms=terms,
        )
        agents["P02"].sign_proposer_at[(1, 1, 2)] = "P01"
        agents["P03"].sign_proposer_at[(1, 1, 2)] = "P01"

        logger = EventLogger()
        game = Game(config=GameConfig.default_12(), agents=agents, seed=seed, logger=logger)
        game.run()
        assert game.contracts[0].contract_seq is not None  # 成立したことを確認
        auto_event = next(
            e for e in logger.events
            if e.event_type == "AUTO_COMMIT" and e.data["player_id"] == "P01" and e.round_num == 1
        )
        assert auto_event.data["source"] == "random"
        return auto_event.data["vote"]

    v1 = _build_and_run(seed=5)
    v2 = _build_and_run(seed=5)
    assert v1 == v2  # 同一シードで完全一致
