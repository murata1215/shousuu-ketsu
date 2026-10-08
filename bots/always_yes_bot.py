"""
常にYES Bot（§12.1のBot検証用、新規実装）

動作確認用の最も単純なBot。毎ラウンド必ずYESへ投票する。
"""

from bots.base import BotAgent
from engine.models import PlayerState, Vote


class AlwaysYesBot(BotAgent):
    """毎ラウンド必ずYESへ投票するBot"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("AlwaysYes", seed=seed)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return Vote.YES
