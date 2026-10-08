"""
アクション検証モジュール（§7.1 Negotiation）

dangou-card `engine/actions.py`（B分類）の `ActionResult`/`validate_action`の
枠組みを、Negotiationで使える8種（dm/broadcast/transfer/repay/pass/
vote_commit/contract_propose/contract_sign）に絞って流用。市場・カードトレード・
報奨・匿名通信・契約解除の検証は全て削除した（少数決の仕様に存在しないため。
契約解除アクションはそもそもAction unionに無い、§6.1・§11.1 #8）。

transfer の基準は §3.2「手持ちの現金まで。借金は差し引かない」のため、
dangou の free_cash（現金−借金）ではなく現金そのものを使う
（doc/analysis/reuse_investigation.md §5-6 の指摘に対する仕様書の回答）。

contract_propose/contract_sign の検証は dangou-card `engine/actions.py` の
terms検証ブロック（B分類）を少数決向けに縮小して流用した
（engine/contracts.py::validate_terms に委譲。発行料のチェックは無い＝§6.1）。
"""

from engine.config import GameConfig
from engine.contracts import validate_terms
from engine.models import (
    Action, BroadcastAction, Contract, ContractProposeAction, ContractSignAction,
    ContractStatus, DmAction, PassAction, PlayerState, RepayAction, TransferAction,
    VoteCommitAction,
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
    *,
    round_num: int = 1,
    vote_num: int = 1,
    contracts: list[Contract] | None = None,
) -> ActionResult:
    """
    Negotiation中のアクションを検証する

    Args:
        action: 検証するアクション
        player: アクション実行者の状態
        config: ゲーム設定
        players: 全プレイヤーの状態辞書
        round_num: 現在のラウンド（contract_proposeのround_num範囲検証に使う）
        vote_num: 現在の投票番号（contract_proposeのvote_num範囲検証に使う、
            v0.4新設、§9.3）
        contracts: 全契約リスト（contract_signの対象検索に使う。契約を扱わない
            アクションの検証には不要なためキーワード専用・省略可）

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
        # 返済（§3.5、v0.3）: 開始後の借金（3%）にだけ充てられる。開始前の
        # 借金（1.5%）は最後まで返済できない。実行額は実際には
        # min(指定額, 現金, 開始後の借金残高) にクランプされる
        # （engine/player.py::repay()）。ここでは「正の額の申告」と
        # 「開始後の借金が残っていること」を検証する（§12.3 #26・#27）。
        if action.amount <= 0:
            return ActionResult(False, "Repay amount must be positive")
        if player.debt_post <= 0:
            return ActionResult(False, "No post-start debt to repay")
        return ActionResult(True)

    if isinstance(action, VoteCommitAction):
        # 投票提出自体の形式はVoteの型で保証済み（Commitフェイズの検証はgame.pyが行う）
        return ActionResult(True)

    if isinstance(action, ContractProposeAction):
        # 契約提案: 発行料なし（§6.1）。with_players・terms の形式検証のみ。
        if not action.with_players:
            return ActionResult(False, "with_players must not be empty")
        if action.player_id in action.with_players:
            return ActionResult(False, "with_players must not include the proposer")
        if len(set(action.with_players)) != len(action.with_players):
            return ActionResult(False, "with_players must not contain duplicates")
        for target_pid in action.with_players:
            if target_pid not in players:
                return ActionResult(False, f"Unknown counterparty {target_pid}")

        parties = {action.player_id, *action.with_players}
        error = validate_terms(
            action.terms, parties, set(players.keys()),
            round_num, vote_num, config.num_rounds,
        )
        if error is not None:
            return ActionResult(False, error)
        return ActionResult(True)

    if isinstance(action, ContractSignAction):
        # 契約署名: 対象契約の存在・当事者性・ステータスのみ検証（資金チェックなし）
        target: Contract | None = None
        for c in (contracts or []):
            if c.contract_id == action.contract_id:
                target = c
                break
        if target is None:
            return ActionResult(False, f"Contract {action.contract_id} not found")
        if action.player_id not in target.parties:
            return ActionResult(False, f"You are not a party of {action.contract_id}")
        if target.status != ContractStatus.PROPOSED:
            return ActionResult(
                False, f"Contract {action.contract_id} is not awaiting signatures",
            )
        if action.player_id in target.signed_by:
            return ActionResult(False, f"You already signed {action.contract_id}")
        return ActionResult(True)

    return ActionResult(False, f"Unknown action type {getattr(action, 'type', action)}")
