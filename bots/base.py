"""
BotAgent基底クラス

全ルールベースBotの共通基底。gentei-janken `bots/base.py`（B分類）から、
`_try_exit`（脱落判定、本作には脱落がないため削除）を除いて流用した。
`_try_full_repay` は §3.5 の任意返済に合わせて 2種債務合計 → 現金の
min を返すよう直した（gentei版は単一 `debt` フィールド基準だった）。

サイクル1.2（Bot検証）で既定の `negotiate()` を「常にpass」に変更した
（計画§1）。サイクル1.0のBotにあった「借金があれば利息を止めるため
全額返済」は、借入3通りBot（§12.1検証）のうち「借りて返す」役だけに
残していたが、サイクル1.3（仕様書v0.3、§3.5）で返済が開始後の借金
（3%）にしか効かなくなり、その役は成り立たなくなって削除した
（bots/loan_bot.py参照）。_try_full_repay はどのBotからも呼ばれなく
なったが、新ルールに合わせて直した上でヘルパとして残す（CLAUDE.md
過去の落とし穴③: 孤児コードを残さない）。
"""

import random

from engine.config import GameConfig
from engine.models import Action, PassAction, PlayerState, RepayAction
from engine.negotiation import PlayerAgent


class BotAgent(PlayerAgent):
    """
    全Botの共通基底クラス

    Args:
        bot_type: Bot種別名（例: "Random", "AlwaysYes"）
        seed: Bot固有の乱数シード
    """

    def __init__(self, bot_type: str, seed: int = 0) -> None:
        self.bot_type = bot_type
        self.rng = random.Random(seed)

    def choose_loan(self, config: GameConfig) -> int:
        """既定は最低借入額。サブクラスでオーバーライド可能"""
        return config.loan_min

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        """既定: 常にpass（繰上げ返済はしない。サイクル1.2で変更）"""
        return PassAction(player_id=player_state.player_id)

    def _try_full_repay(self, player_state: PlayerState) -> Action | None:
        """
        開始後の借金（3%）を止めるための全額任意返済（§3.5、v0.3）

        開始前の借金（1.5%）は最後まで返済できないため対象外。
        開始後の借金残高と現金の小さい方を返済額にする。開始後の借金が
        無ければ何もしない（None、§12.3 #26・#27）。
        """
        if player_state.debt_post <= 0:
            return None
        amount = min(player_state.debt_post, player_state.cash)
        if amount <= 0:
            return None
        return RepayAction(player_id=player_state.player_id, amount=amount)
