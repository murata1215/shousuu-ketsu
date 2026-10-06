"""
借入3通りBot（§12.1のBot検証用、新規実装）

投票は無作為（自分のRNG）。借入額だけが異なる3種:
- LoanMinBot: 最低額120万を借りる。繰上げ返済はしない（既定のpass）。
- LoanMaxRepayBot: 1000万を借り、R1の最初の自分の巡で880万を返済する
  （計画§6 判断10: R1のNegotiation最初の自分の巡で返済）。
- LoanMaxHoldBot: 1000万を借り、持ち続ける（繰上げ返済はしない）。

「既定ではBotは繰上げ返済をしない（サイクル1.0 のBotにある『R1で全額返す』
動きは、上の『借りて返す』役だけに残す）」はこの3種のうちLoanMaxRepayBotに
だけ残し、他の2種はBotAgent既定のpassのまま。
"""

from bots.base import BotAgent
from engine.config import GameConfig
from engine.models import Action, PassAction, PlayerState, RepayAction, Vote


class LoanMinBot(BotAgent):
    """最低額120万を借りる。投票は無作為。繰上げ返済はしない"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("LoanMin", seed=seed)

    def choose_loan(self, config: GameConfig) -> int:
        return config.loan_min

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])


class LoanMaxRepayBot(BotAgent):
    """1000万を借り、R1に880万を返済する。投票は無作為"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("LoanMaxRepay", seed=seed)
        self._repaid = False

    def choose_loan(self, config: GameConfig) -> int:
        return config.loan_max

    def negotiate(
        self, player_state: PlayerState, round_num: int, turn: int, visible_state: dict,
    ) -> Action:
        if round_num == 1 and not self._repaid:
            self._repaid = True
            return RepayAction(player_id=player_state.player_id, amount=8_800_000)
        return PassAction(player_id=player_state.player_id)

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])


class LoanMaxHoldBot(BotAgent):
    """1000万を借り、持ち続ける（繰上げ返済なし）。投票は無作為"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("LoanMaxHold", seed=seed)

    def choose_loan(self, config: GameConfig) -> int:
        return config.loan_max

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])
