"""
借入3通りBot（§12.1のBot検証用、新規実装）

投票は無作為（自分のRNG）。借入額だけが異なる3種、いずれも持ち続け
（繰上げ返済はしない。既定のpass）:
- LoanMinBot: 最低額120万を借りる。
- LoanMidHoldBot: 中間額500万を借りる。
- LoanMaxHoldBot: 最大額1000万を借りる。

v0.3（§3.5）で返済が開始後の借金（3%）にしか効かなくなり、「1000万を借りて
R1に880万（開始前の借金分）を返す」役は成り立たなくなった（返済が不成立に
なるだけで LoanMaxHoldBot と同じ挙動になる）ため、サイクル1.2にあった
LoanMaxRepayBot は削除し、中間額保有のLoanMidHoldBotに置き換えた
（500万という額に仕様書上の定数はないため、120万〜1000万の中間として
Bot側に直書きした。判断点として報告する）。500万は GameConfig に定数を
増やさず、このファイル内に直書きする（CLAUDE.md「守ること」:
返済ルール以外のエンジンの規則は変えない）。
"""

from bots.base import BotAgent
from engine.config import GameConfig
from engine.models import PlayerState, Vote

LOAN_MID: int = 5_000_000
"""借入3通りBot検証用の中間額（500万円）。仕様書に定数はなく、ここで判断して定めた"""


class LoanMinBot(BotAgent):
    """最低額120万を借りる。投票は無作為。繰上げ返済はしない"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("LoanMin", seed=seed)

    def choose_loan(self, config: GameConfig) -> int:
        return config.loan_min

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])


class LoanMidHoldBot(BotAgent):
    """中間額500万を借り、持ち続ける（繰上げ返済なし）。投票は無作為"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("LoanMid", seed=seed)

    def choose_loan(self, config: GameConfig) -> int:
        return LOAN_MID

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])


class LoanMaxHoldBot(BotAgent):
    """1000万を借り、持ち続ける（繰上げ返済なし）。投票は無作為"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("LoanMaxHold", seed=seed)

    def choose_loan(self, config: GameConfig) -> int:
        return config.loan_max

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])
