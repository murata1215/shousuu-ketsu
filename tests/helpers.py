"""
テスト用の固定エージェント

本サイクルはAI呼び出しを行わないため、ラウンドごとの投票・交渉行動を
事前に指定できる ScriptedAgent と、常に無効な応答を返す
FailingCommitAgent（自動代行のテスト用）を提供する。
"""

from engine.config import GameConfig
from engine.models import Action, PassAction, PlayerState, Vote
from engine.negotiation import PlayerAgent


class ScriptedAgent(PlayerAgent):
    """
    ラウンドごとの投票・交渉行動をあらかじめ指定できるエージェント

    Args:
        player_id: プレイヤーID
        votes: round_num -> Vote。指定の無いラウンドは既定でYES
        loan: choose_loan() が返す借入額
        negotiate_actions: (round_num, turn) -> Action。指定の無い巡はpass
    """

    def __init__(
        self,
        player_id: str,
        votes: dict[int, Vote] | None = None,
        loan: int = 1_200_000,
        negotiate_actions: dict[tuple[int, int], Action] | None = None,
    ) -> None:
        self.player_id = player_id
        self.votes = votes or {}
        self.loan = loan
        self.negotiate_actions = negotiate_actions or {}

    def choose_loan(self, config: GameConfig) -> int:
        return self.loan

    def negotiate(
        self, player_state: PlayerState, round_num: int, turn: int, visible_state: dict,
    ) -> Action:
        return self.negotiate_actions.get((round_num, turn), PassAction(player_id=self.player_id))

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        return self.votes.get(round_num, Vote.YES)


class FailingCommitAgent(ScriptedAgent):
    """
    commit() が常に無効な応答（例外）を返すエージェント（§4.4 自動代行のテスト用）

    negotiate/choose_loan はScriptedAgentと同じ。
    """

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        raise RuntimeError("invalid output (simulated)")


def make_roster(
    votes_by_round: dict[int, dict[str, Vote]], num_players: int = 12, loan: int = 1_200_000,
) -> dict[str, ScriptedAgent]:
    """
    round_num -> {player_id: Vote} の表から、全プレイヤー分のScriptedAgentを作る

    指定の無い (round_num, player_id) はScriptedAgent既定のYESになる。
    """
    player_ids = [f"P{i:02d}" for i in range(1, num_players + 1)]
    per_player: dict[str, dict[int, Vote]] = {pid: {} for pid in player_ids}
    for round_num, votes in votes_by_round.items():
        for pid, vote in votes.items():
            per_player[pid][round_num] = vote
    return {
        pid: ScriptedAgent(pid, votes=per_player[pid], loan=loan)
        for pid in player_ids
    }
