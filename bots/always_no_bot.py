"""
常にNO Bot（§12.1のBot検証用、新規実装）

動作確認用の最も単純なBot。毎ラウンド必ずNOへ投票する。
"""

from bots.base import BotAgent
from engine.models import PlayerState, Vote


class AlwaysNoBot(BotAgent):
    """毎ラウンド必ずNOへ投票するBot"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("AlwaysNo", seed=seed)

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        return Vote.NO
