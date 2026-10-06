"""
前回の少数派／多数派に乗るBot（§12.1のBot検証用、新規実装）

visible_state["last_round_result"] の minority_side を見て、前回の少数派
（または多数派）と同じ側に投票する。R1（まだ前回の結果がない）と、前回が
「少数派なし」（6対6・12対0、minority_side=None）だったラウンドは、
判断材料が無いため無作為に投票する（計画§6 判断1: Follow系Botの
前回少数派なし／R1の扱い）。
"""

from bots.base import BotAgent
from engine.models import PlayerState, Vote


def _last_minority_side(visible_state: dict) -> str | None:
    last = visible_state.get("last_round_result")
    if last is None:
        return None
    return last.get("minority_side")


class FollowMinorityBot(BotAgent):
    """前回の少数派側と同じ側に投票するBot（判断材料が無ければ無作為）"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("FollowMinority", seed=seed)

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        side = _last_minority_side(visible_state)
        if side is None:
            return self.rng.choice([Vote.YES, Vote.NO])
        return Vote(side)


class FollowMajorityBot(BotAgent):
    """前回の多数派側（少数派の反対）と同じ側に投票するBot（判断材料が無ければ無作為）"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("FollowMajority", seed=seed)

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        side = _last_minority_side(visible_state)
        if side is None:
            return self.rng.choice([Vote.YES, Vote.NO])
        # 前回の少数派の反対側（＝前回の多数派側）
        return Vote.NO if side == Vote.YES.value else Vote.YES
