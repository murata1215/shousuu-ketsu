"""
借入額固定Bot（§12.1のBot検証用V9、サイクル4.1で一本化）

投票は無作為（自分のRNG）。借入額だけをコンストラクタで指定する
（繰上げ返済はしない。既定のpass）。

サイクル1.2〜1.3では「最低額120万／中間額500万／最大額1000万」の3クラス
（LoanMinBot/LoanMidHoldBot/LoanMaxHoldBot）に分かれていたが、v0.4の
Bot検証（V9）では120万・400万・1000万の3通りに変える指示を受けたため、
borrow額を自由に指定できる単一クラス LoanFixedBot に一本化した
（borrow額を変えるたびにクラスを増やす設計を避ける）。
"""

from bots.base import BotAgent
from engine.config import GameConfig
from engine.models import PlayerState, Vote


class LoanFixedBot(BotAgent):
    """指定した借入額を借り、持ち続ける（繰上げ返済なし）。投票は無作為"""

    def __init__(self, loan: int, seed: int = 0, bot_type: str | None = None) -> None:
        super().__init__(bot_type or f"Loan{loan // 10_000}万", seed=seed)
        self.loan = loan

    def choose_loan(self, config: GameConfig) -> int:
        return self.loan

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])
