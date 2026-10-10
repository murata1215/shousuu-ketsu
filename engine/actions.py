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

サイクル4.2で `RejectReason`（不成立理由のコード）を追加した（§7.5・
受け入れ#57）。`reason`（英語の短い文、イベントログ・観戦者向け）は
互換のため残し、`reason_code`/`reason_params` を本人向けの日本語文面
（`llm/reasons.py::reject_reason_ja`）の元データとして新設した。
"""

from enum import Enum
from typing import Any

from engine.config import GameConfig
from engine.contracts import validate_terms
from engine.models import (
    Action, BroadcastAction, Contract, ContractProposeAction, ContractSignAction,
    ContractStatus, DmAction, PassAction, PlayerState, RepayAction, TransferAction,
    VoteCommitAction,
)


class RejectReason(str, Enum):
    """
    不成立理由のコード（§7.5）。`engine/actions.py` が返す17種。

    値は `llm/reasons.py::REASON_JA` のキーと1対1で対応する。日本語文面を
    新設する際はこのenumとREASON_JAの両方に項目を足し、
    `tests/test_reasons.py::test_every_reject_reason_has_japanese` で
    漏れを機械的に検出する。
    """

    DM_TO_SELF = "dm_to_self"
    UNKNOWN_TARGET = "unknown_target"
    TRANSFER_AMOUNT_NOT_POSITIVE = "transfer_amount_not_positive"
    TRANSFER_TO_SELF = "transfer_to_self"
    TRANSFER_INSUFFICIENT_CASH = "transfer_insufficient_cash"
    REPAY_AMOUNT_NOT_POSITIVE = "repay_amount_not_positive"
    NO_POST_DEBT = "no_post_debt"
    WITH_EMPTY = "with_empty"
    WITH_INCLUDES_PROPOSER = "with_includes_proposer"
    WITH_DUPLICATED = "with_duplicated"
    UNKNOWN_COUNTERPARTY = "unknown_counterparty"
    CONTRACT_NOT_FOUND = "contract_not_found"
    NOT_A_PARTY = "not_a_party"
    NOT_AWAITING_SIGNATURES = "not_awaiting_signatures"
    ALREADY_SIGNED = "already_signed"
    UNKNOWN_ACTION_TYPE = "unknown_action_type"


class ActionResult:
    """
    アクション検証結果

    success: 成立するか
    reason: 不成立理由（成功時はNone。英語の短い文、イベントログ・観戦者向け）
    reason_code: 不成立理由のコード（成功時はNone。RejectReason、または
        engine/contracts.py::TermRejection.code。本人向け日本語文面の元データ、
        §7.5・受け入れ#57）
    reason_params: reason_codeのテンプレートに差し込む値（例: {"to": "P07"}）
    consumes_action: アクション枠を消費するか（passは消費しない、§7.1 Negotiation）
    """

    def __init__(
        self,
        success: bool,
        reason: str | None = None,
        consumes_action: bool = True,
        *,
        reason_code: "RejectReason | str | None" = None,
        reason_params: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.reason = reason
        self.consumes_action = consumes_action
        self.reason_code = reason_code.value if isinstance(reason_code, RejectReason) else reason_code
        self.reason_params = reason_params or {}


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
            return ActionResult(
                False, "Cannot DM self", reason_code=RejectReason.DM_TO_SELF,
            )
        if action.to not in players:
            return ActionResult(
                False, f"Unknown target {action.to}",
                reason_code=RejectReason.UNKNOWN_TARGET, reason_params={"to": action.to},
            )
        return ActionResult(True)

    if isinstance(action, BroadcastAction):
        return ActionResult(True)

    if isinstance(action, TransferAction):
        # 送金: 手持ちの現金まで（§3.2。借金は差し引かない）
        if action.amount <= 0:
            return ActionResult(
                False, "Transfer amount must be positive",
                reason_code=RejectReason.TRANSFER_AMOUNT_NOT_POSITIVE,
                reason_params={"amount": action.amount},
            )
        if action.to == player.player_id:
            return ActionResult(
                False, "Cannot transfer to self", reason_code=RejectReason.TRANSFER_TO_SELF,
            )
        if action.to not in players:
            return ActionResult(
                False, f"Unknown target {action.to}",
                reason_code=RejectReason.UNKNOWN_TARGET, reason_params={"to": action.to},
            )
        if action.amount > player.cash:
            return ActionResult(
                False, "Insufficient cash for transfer",
                reason_code=RejectReason.TRANSFER_INSUFFICIENT_CASH,
                reason_params={"amount": action.amount, "cash": player.cash},
            )
        return ActionResult(True)

    if isinstance(action, RepayAction):
        # 返済（§3.5、v0.3）: 開始後の借金（3%）にだけ充てられる。開始前の
        # 借金（1.5%）は最後まで返済できない。実行額は実際には
        # min(指定額, 現金, 開始後の借金残高) にクランプされる
        # （engine/player.py::repay()）。ここでは「正の額の申告」と
        # 「開始後の借金が残っていること」を検証する（§12.3 #26・#27）。
        if action.amount <= 0:
            return ActionResult(
                False, "Repay amount must be positive",
                reason_code=RejectReason.REPAY_AMOUNT_NOT_POSITIVE,
                reason_params={"amount": action.amount},
            )
        if player.debt_post <= 0:
            return ActionResult(
                False, "No post-start debt to repay", reason_code=RejectReason.NO_POST_DEBT,
            )
        return ActionResult(True)

    if isinstance(action, VoteCommitAction):
        # 投票提出自体の形式はVoteの型で保証済み（Commitフェイズの検証はgame.pyが行う）
        return ActionResult(True)

    if isinstance(action, ContractProposeAction):
        # 契約提案: 発行料なし（§6.1）。with_players・terms の形式検証のみ。
        if not action.with_players:
            return ActionResult(
                False, "with_players must not be empty", reason_code=RejectReason.WITH_EMPTY,
            )
        if action.player_id in action.with_players:
            return ActionResult(
                False, "with_players must not include the proposer",
                reason_code=RejectReason.WITH_INCLUDES_PROPOSER,
            )
        if len(set(action.with_players)) != len(action.with_players):
            return ActionResult(
                False, "with_players must not contain duplicates",
                reason_code=RejectReason.WITH_DUPLICATED,
            )
        for target_pid in action.with_players:
            if target_pid not in players:
                return ActionResult(
                    False, f"Unknown counterparty {target_pid}",
                    reason_code=RejectReason.UNKNOWN_COUNTERPARTY, reason_params={"pid": target_pid},
                )

        parties = {action.player_id, *action.with_players}
        rejection = validate_terms(
            action.terms, parties, set(players.keys()),
            round_num, vote_num, config.num_rounds,
        )
        if rejection is not None:
            return ActionResult(
                False, rejection.message_en,
                reason_code=rejection.code, reason_params=rejection.params,
            )
        return ActionResult(True)

    if isinstance(action, ContractSignAction):
        # 契約署名: 対象契約の存在・当事者性・ステータスのみ検証（資金チェックなし）
        target: Contract | None = None
        for c in (contracts or []):
            if c.contract_id == action.contract_id:
                target = c
                break
        if target is None:
            return ActionResult(
                False, f"Contract {action.contract_id} not found",
                reason_code=RejectReason.CONTRACT_NOT_FOUND,
                reason_params={"cid": action.contract_id},
            )
        if action.player_id not in target.parties:
            return ActionResult(
                False, f"You are not a party of {action.contract_id}",
                reason_code=RejectReason.NOT_A_PARTY, reason_params={"cid": action.contract_id},
            )
        if target.status != ContractStatus.PROPOSED:
            return ActionResult(
                False, f"Contract {action.contract_id} is not awaiting signatures",
                reason_code=RejectReason.NOT_AWAITING_SIGNATURES,
                reason_params={"cid": action.contract_id},
            )
        if action.player_id in target.signed_by:
            return ActionResult(
                False, f"You already signed {action.contract_id}",
                reason_code=RejectReason.ALREADY_SIGNED, reason_params={"cid": action.contract_id},
            )
        return ActionResult(True)

    return ActionResult(
        False, f"Unknown action type {getattr(action, 'type', action)}",
        reason_code=RejectReason.UNKNOWN_ACTION_TYPE,
        reason_params={"t": getattr(action, "type", action)},
    )
