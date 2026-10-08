"""
無作為投票Bot（§12.1のBot検証用、新規実装）

毎ラウンド、Bot固有のRNGでYES/NOを無作為に選ぶ。交渉は常にpass
（借金があれば全額返済、BotAgent既定の挙動）。
"""

from bots.base import BotAgent
from engine.models import PlayerState, Vote


class RandomVoteBot(BotAgent):
    """毎ラウンド無作為にYES/NOへ投票するBot"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("Random", seed=seed)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])
