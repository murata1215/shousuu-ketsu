"""
アクション検証モジュール（§7.1 Negotiation）

dangou-card `engine/actions.py`（B分類）の `ActionResult`/`validate_action`の
枠組みを、Negotiationで使える6種（dm/broadcast/transfer/repay/pass/
vote_commit）だけに絞って流用。契約・市場・カードトレード・報奨・匿名通信の
検証は全て削除した（少数決の仕様に存在しないため）。

transfer の基準は §3.2「手持ちの現金まで。借金は差し引かない」のため、
dangou の free_cash（現金−借金）ではなく現金そのものを使う
（doc/analysis/reuse_investigation.md §5-6 の指摘に対する仕様書の回答）。
"""

from engine.config import GameConfig
from engine.models import (
    Action, BroadcastAction, DmAction, PassAction, PlayerState,
    RepayAction, TransferAction, VoteCommitAction,
)


class ActionResult:
    """
    アクション検証結果

    success: 成立するか
    reason: 不成立理由（成功時はNone）
    consumes_action: アクション枠を消費するか（passは消費しない、§7.1 Negotiation）
    """

    def __init__(self, success: bool, reason: str | None = None, consumes_action: bool = True) -> None:
        self.success = success
        self.reason = reason
        self.consumes_action = consumes_action


def validate_action(
    action: Action,
    player: PlayerState,
    config: GameConfig,
    players: dict[str, PlayerState],
) -> ActionResult:
    """
    Negotiation中のアクションを検証する

    Args:
        action: 検証するアクション
        player: アクション実行者の状態
        config: ゲーム設定
        players: 全プレイヤーの状態辞書

    Returns:
        ActionResult
    """
    if isinstance(action, PassAction):
        # passは常に成功、アクション枠非消費（§7.1 Negotiation）
        return ActionResult(True, consumes_action=False)

    if isinstance(action, DmAction):
        if action.to == player.player_id:
            return ActionResult(False, "Cannot DM self")
        if action.to not in players:
            return ActionResult(False, f"Unknown target {action.to}")
        return ActionResult(True)

    if isinstance(action, BroadcastAction):
        return ActionResult(True)

    if isinstance(action, TransferAction):
        # 送金: 手持ちの現金まで（§3.2。借金は差し引かない）
        if action.amount <= 0:
            return ActionResult(False, "Transfer amount must be positive")
        if action.to == player.player_id:
            return ActionResult(False, "Cannot transfer to self")
        if action.to not in players:
            return ActionResult(False, f"Unknown target {action.to}")
        if action.amount > player.cash:
            return ActionResult(False, "Insufficient cash for transfer")
        return ActionResult(True)

    if isinstance(action, RepayAction):
        # 返済: 実行額は実際には min(指定額, 現金, 借金合計) にクランプされる
        # （engine/player.py::repay()）。ここでは「正の額の申告」だけを検証する。
        if action.amount <= 0:
            return ActionResult(False, "Repay amount must be positive")
        return ActionResult(True)

    if isinstance(action, VoteCommitAction):
        # 投票提出自体の形式はVoteの型で保証済み（Commitフェイズの検証はgame.pyが行う）
        return ActionResult(True)

    return ActionResult(False, f"Unknown action type {getattr(action, 'type', action)}")
