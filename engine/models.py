"""
少数決のデータモデル

§2〜§4・§7・§8 に対応するプレイヤー状態・投票・アクション・
イベント・ラウンド集計・ゲーム結果を定義する。

GameEvent は gentei-janken `engine/models.py` の同名クラスと同一構造
（A分類：逐語コピー。engine/events.py が本クラスに依存するため）。
PlayerState・Action群・各種結果モデルは少数決固有の新規実装（C分類）。
契約（Obligation/Contract/contract_seq）はサイクル1.1で追加する。本サイクルの
Settlement は契約フックを空リストで返すだけで、関連モデルは一切持たない
（CLAUDE.md 過去の落とし穴③: 見た目だけ先行させる孤児コードを避けるため）。
"""

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, computed_field


class Vote(str, Enum):
    """投票先（§4.1）"""

    YES = "YES"
    NO = "NO"


class PlayerState(BaseModel):
    """
    プレイヤーの状態（§3）

    借金は「開始前の借金（1.5%複利）」と「開始後の借金（3%複利）」の
    2種類を別々に保持する（§3.3）。最終資産 = 現金 − 借金合計（§2）。
    """

    player_id: str
    """プレイヤーID（"P01"〜"P12"）"""

    cash: int
    """現金残高"""

    debt_pre: int = 0
    """開始前の借金残高（利息1.5%、§3.1/§3.3）"""

    debt_post: int = 0
    """開始後の借金残高（利息3%、§3.2/§3.3）"""

    initial_loan: int
    """開始前の借入額（公開情報、§3.1/§8）"""

    @computed_field
    @property
    def total_debt(self) -> int:
        """借金残高の合計（2種の合算、§3.4）"""
        return self.debt_pre + self.debt_post

    @computed_field
    @property
    def net_assets(self) -> int:
        """最終資産と同じ式：現金 − 借金残高の合計（§2/§7.3）"""
        return self.cash - self.total_debt


# =============================================================================
# Negotiationフェイズのアクション（§7.1 Negotiation: dm/broadcast/transfer/
# repay/pass。契約の提案・署名は1.1以降）
# =============================================================================

class DmAction(BaseModel):
    """DM送信アクション（§9.2）"""

    type: Literal["dm"] = "dm"
    player_id: str
    to: str
    message: str


class BroadcastAction(BaseModel):
    """全体発言アクション（§9.2）"""

    type: Literal["broadcast"] = "broadcast"
    player_id: str
    message: str


class TransferAction(BaseModel):
    """
    送金アクション（§3.2/§9.2）

    即時決済。手持ちの現金までしか送れない（借金は差し引かない、§3.2）。
    """

    type: Literal["transfer"] = "transfer"
    player_id: str
    to: str
    amount: int


class RepayAction(BaseModel):
    """
    任意返済アクション（§3.5/§9.2）

    開始後の借金（3%）から先に充当する。
    """

    type: Literal["repay"] = "repay"
    player_id: str
    amount: int


class PassAction(BaseModel):
    """パスアクション（§7.1 Negotiation）。アクション枠を消費しない"""

    type: Literal["pass"] = "pass"
    player_id: str


class VoteCommitAction(BaseModel):
    """Commitフェイズの投票提出アクション（§4.1/§9.2）"""

    type: Literal["vote_commit"] = "vote_commit"
    player_id: str
    vote: Vote


Action = Annotated[
    DmAction | BroadcastAction | TransferAction | RepayAction | PassAction | VoteCommitAction,
    Field(discriminator="type"),
]


# =============================================================================
# ラウンド集計・ゲーム結果
# =============================================================================

class MinorityOutcome(BaseModel):
    """
    1ラウンドの少数決判定結果（§4.2/§4.3）

    少数派なし（6対6・12対0）の場合は minority_side=None、
    minority_ids=[] となり、没収額が carryover_after に積み上がる。
    """

    round_num: int
    yes_ids: list[str]
    no_ids: list[str]
    minority_side: Vote | None
    minority_ids: list[str]
    majority_ids: list[str]
    carryover_before: int
    """このラウンド開始時点の持ち越し額（§4.3）"""

    pool: int
    """多数派の参加費合計 + 持ち越し（少数派なしの場合は配当対象外）"""

    payout_per_minority: int
    """少数派1人あたりの配当（参加費の払い戻しは含まない。§4.2）"""

    forfeited_remainder: int
    """配当が割り切れなかった場合の没収端数（§4.3）"""

    carryover_after: int
    """次ラウンドへの持ち越し額（少数派なしなら積み上がる。R12消滅後は0）"""

    destroyed_carryover: int
    """R12で少数派なしだった場合に消滅した持ち越し額（それ以外は0、§4.3）"""


class RoundSummary(BaseModel):
    """1ラウンドの公開情報サマリ（§8: 公開情報の範囲に対応）"""

    round_num: int
    votes: dict[str, Vote]
    """決着後に公開される全員の投票先（§8）"""

    auto_commit_ids: list[str] = Field(default_factory=list)
    """このラウンドでAUTO COMMITになったプレイヤーID（§4.4/§8）"""

    minority_outcome: MinorityOutcome
    interest_total: int
    """このラウンドのFinanceで計上された利息の合計（2種合算）"""

    public_ranks: dict[str, int] | None = None
    """R3・R6・R9終了後のみ設定される全員の順位（名前と順位のみ、§7.3）"""


class GameResult(BaseModel):
    """ゲーム全体の最終結果（§2/§7.3）"""

    seed: int
    final_players: dict[str, PlayerState]
    final_assets: dict[str, int]
    """player_id -> 最終資産（現金 − 借金合計）"""

    final_ranks: dict[str, int]
    """player_id -> 最終順位（同額は同順位、§7.3）"""

    round_summaries: list[RoundSummary]
    total_interest: int
    """全ラウンド・全プレイヤーの利息合計（§2.1の場からの流出）"""

    total_destroyed_carryover: int
    """R12で少数派なしだった場合に消滅した持ち越し額の合計（§2.1）"""

    total_forfeited_remainder: int
    """配当の端数処理で没収された合計額（§4.3）"""


# =============================================================================
# イベント（ログ用）
# =============================================================================

class GameEvent(BaseModel):
    """
    ゲームイベント（JSONL出力用）

    全ゲームイベントを時系列で記録する。gentei-janken の GameEvent と同一構造
    （engine/events.py が本クラスに依存する）。
    """

    event_type: str
    """イベント種別（例: GAME_START, VOTE_COMMITTED, MINORITY_RESOLVED等）"""

    timestamp: str
    """ISO8601形式のタイムスタンプ"""

    round_num: int
    """ラウンド番号（0=ゲーム開始前）"""

    phase: str
    """フェイズ名（open / negotiation / commit / settlement / finance）"""

    step: int | None = None
    """Settlement内のStep番号（§7.1: 1-8、Settlement以外はNone）"""

    data: dict[str, Any] = Field(default_factory=dict)
    """イベント固有データ"""
