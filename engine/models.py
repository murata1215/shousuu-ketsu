"""
少数決のデータモデル

§2〜§4・§7・§8 に対応するプレイヤー状態・投票・アクション・
イベント・ラウンド集計・ゲーム結果を定義する。

GameEvent は gentei-janken `engine/models.py` の同名クラスと同一構造
（A分類：逐語コピー。engine/events.py が本クラスに依存するため）。
PlayerState・Action群・各種結果モデルは少数決固有の新規実装（C分類）。

契約関連（Obligation/Contract/ObligationType/ConditionType/ContractProposeAction/
ContractSignAction）は dangou-card `engine/models.py` の同名クラスをB分類で
流用・縮小した（サイクル1.1）。型Bのdetailsをカード/市場指定からYES/NO指定に、
型Cのconditionをmarket系からminority_side/in_minorityの2種に差し替え、
is_fulfilled/is_expired（脱落失効用の状態）とContractCancelAction・発行料は
持ち込まない（本作に脱落・取り消し・発行料はない、§6.1）。
contract_seq は dangouに存在しない新設フィールド。
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


class ContractProposeAction(BaseModel):
    """
    契約提案アクション（§6.1/§9.2/§9.3）

    terms は義務定義の辞書リスト。各要素は
    {"obligor": str, "counterparty": str, "ob_type": str, "round_num": int,
     "details": dict} の形（検証は engine/actions.py が行う）。
    発行料はない（§6.1）。取り消しアクションは存在しない（§6.1・§11.1 #8）。
    """

    type: Literal["contract_propose"] = "contract_propose"
    player_id: str
    with_players: list[str] = Field(alias="with")
    terms: list[dict[str, Any]]

    model_config = {"populate_by_name": True}


class ContractSignAction(BaseModel):
    """契約署名アクション（§6.1/§9.2）"""

    type: Literal["contract_sign"] = "contract_sign"
    player_id: str
    contract_id: str


Action = Annotated[
    DmAction | BroadcastAction | TransferAction | RepayAction | PassAction
    | VoteCommitAction | ContractProposeAction | ContractSignAction,
    Field(discriminator="type"),
]


# =============================================================================
# 契約（§6）
# =============================================================================

class ObligationType(str, Enum):
    """義務の種別（§6.2）"""

    TYPE_A_PAYMENT = "type_a_payment"
    """型A: 指定ラウンドに指定額を相手方へ支払う"""

    TYPE_B_VOTE = "type_b_vote"
    """型B: 指定ラウンドにYES（またはNO）へ投票する"""

    TYPE_C_CONDITIONAL = "type_c_conditional"
    """型C: 条件が成立したときだけ指定額を相手方へ支払う"""


class ConditionType(str, Enum):
    """型Cの条件種別（§6.4）"""

    MINORITY_SIDE = "minority_side"
    """指定ラウンドの少数派がYES（またはNO）になる"""

    IN_MINORITY = "in_minority"
    """指定ラウンドで特定のプレイヤーが少数派に入る"""


class Obligation(BaseModel):
    """
    1つの義務単位（§6.1）

    義務は「義務者」と「相手方」の組で管理する。is_fulfilled/is_expired の
    ような状態は持たない。各義務は round_num のラウンドのSettlementで1度だけ
    判定され、払われなかった分はそのまま消える（次ラウンド以降に請求されない。
    §7.1手順6）ため、状態フラグを持つ必要がない（CLAUDE.md 過去の落とし穴③:
    使わない状態を持ち越して孤児化させない）。

    details の形（§9.3）:
    - 型A: {"amount": int}
    - 型B: {"vote": "YES"|"NO"}
    - 型C: {"amount": int, "condition_type": str, "condition": dict}
      condition_type="minority_side" は condition={"side": "YES"|"NO"}、
      condition_type="in_minority" は condition={"target_player": str}
      （対象は契約の当事者でなくてよい、§6.4）
    """

    obligation_id: str
    contract_id: str
    obligor: str
    counterparty: str
    ob_type: ObligationType
    round_num: int
    details: dict[str, Any]


class ContractStatus(str, Enum):
    """契約のステータス（§6.1）"""

    PROPOSED = "proposed"
    """提案中（署名待ち）"""

    ACTIVE = "active"
    """全当事者の署名がそろい成立"""

    EXPIRED = "expired"
    """提案したラウンドの終わりまでに署名がそろわず失効"""


class Contract(BaseModel):
    """
    契約（§6.1）

    2人以上の署名で成立する。契約の存在・当事者名・成立順は公示し、
    内容（義務の詳細）は当事者だけが見られる（§8）。発行料はなく、
    成立した契約は取り消せない（§6.1・§11.1 #8。取り消しアクションは
    Action unionに存在しない）。
    """

    contract_id: str
    """契約ID（試合のシードから導出した乱数で生成。推測不能な短い文字列、§8）"""

    proposer: str
    parties: list[str]
    signed_by: list[str] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    round_created: int
    """提案したラウンド"""

    status: ContractStatus = ContractStatus.PROPOSED

    contract_seq: int | None = None
    """
    成立した順に振られる通し番号（提案順ではない。§6.1）。
    全当事者の署名がそろった時点で確定し、以後変わらない。
    未成立（PROPOSED/EXPIRED）はNone。公示されるのはこの番号だけ（§8）。
    """

    round_established: int | None = None
    """成立したラウンド（未成立ならNone）"""


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

    established_contract_seqs: list[int] = Field(default_factory=list)
    """このラウンドのNegotiationで成立したcontract_seq（§6.1/§8: 成立順の公示）"""

    type_b_violator_ids: list[str] = Field(default_factory=list)
    """型Bの義務に違反した者のプレイヤーID（§6.3/§8: 名前だけ公示）"""

    payment_shortfall_ids: list[str] = Field(default_factory=list)
    """契約の支払いを払いきれなかった者のプレイヤーID（§7.1手順8/§8: 金額・相手は非公開）"""


class ObligationPayment(BaseModel):
    """1つの義務に対する支払いの内訳（§7.1手順6、内部処理・ログ用）"""

    contract_id: str
    contract_seq: int
    ob_index: int
    """契約内での義務の記載順（0始まり）。違約金は破った型B義務の記載位置（§11.4 #12）"""

    obligor: str
    counterparty: str
    ob_type: ObligationType
    promised: int
    """約束額（型Bの違約金は100万固定）"""

    paid: int
    """実際に支払われた額（0以上、promised以下）"""


class ContractSettlementReport(BaseModel):
    """Settlementの契約処理（手順3〜8）の結果（内部処理・ログ用）"""

    violations: list[tuple[str, str]] = Field(default_factory=list)
    """型Bの違反 (obligor, obligation_id) のリスト（手順3）"""

    payable_limits: dict[str, int] = Field(default_factory=dict)
    """義務者ごとの支払える上限（手順5）"""

    payments: list[ObligationPayment] = Field(default_factory=list)
    """実際に決定した支払いのリスト（手順6、contract_seq→記載順で整列済み）"""

    shortfall_ids: list[str] = Field(default_factory=list)
    """払いきれなかった者のプレイヤーID（手順8）"""


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

    post_game_reflections: dict[str, dict] = Field(default_factory=dict)
    """player_id -> post_game_reflect()の戻り値（§9.4、サイクル2.0新設）。

    取得に失敗した・そもそも実装していない席は含まれない（既定は空辞書で、
    既存のGameResult利用箇所への影響はない）。"""


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
