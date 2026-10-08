"""
前回の投票の少数派／多数派に乗るBot（§12.1のBot検証用、新規実装）

v0.4で「ラウンド」と「投票」が別の単位になったため（§1.1）、
visible_state["last_vote_result"]（直前の1回の投票の結果）の minority_side
を見て、直前の投票の少数派（または多数派）と同じ側に投票するよう改めた
（サイクル4.0。v0.3はvisible_state["last_round_result"]を見ていたが、
v0.4のlast_round_result はラウンド全体の勝ち残り結果であり投票ごとの
少数派情報を持たない）。
V1（まだ直前の投票がない）と、直前がやり直し・打ち切り（少数派なし、
minority_side=None）の場合は、判断材料が無いため無作為に投票する。
"""

from bots.base import BotAgent
from engine.models import PlayerState, Vote


def _last_minority_side(visible_state: dict) -> str | None:
    last = visible_state.get("last_vote_result")
    if last is None:
        return None
    return last.get("minority_side")


class FollowMinorityBot(BotAgent):
    """前回の少数派側と同じ側に投票するBot（判断材料が無ければ無作為）"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("FollowMinority", seed=seed)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        side = _last_minority_side(visible_state)
        if side is None:
            return self.rng.choice([Vote.YES, Vote.NO])
        return Vote(side)


class FollowMajorityBot(BotAgent):
    """前回の多数派側（少数派の反対）と同じ側に投票するBot（判断材料が無ければ無作為）"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("FollowMajority", seed=seed)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        side = _last_minority_side(visible_state)
        if side is None:
            return self.rng.choice([Vote.YES, Vote.NO])
        # 前回の少数派の反対側（＝前回の多数派側）
        return Vote.NO if side == Vote.YES.value else Vote.YES
