"""
少数決のデータモデル（v0.4: 勝ち抜き制 L12R4V6）

§2〜§4・§6〜§8 に対応するプレイヤー状態・投票・アクション・
イベント・投票集計・ラウンド集計・ゲーム結果を定義する。

サイクル4.0でv0.3から全面改訂した。最大の変更点は「ラウンド」と「投票」の
二重構造（§1.1）: v0.3のMinorityOutcome/RoundSummary（投票=ラウンド前提）を
VoteOutcome（1回の投票の判定）とRoundOutcome（1ラウンド＝複数投票＋山の
支払い）の2階層に分けた。契約のObligationにvote_num（型B・minority_side・
in_minorityが対象を投票単位で指定するため、§9.3）を追加し、ConditionTypeに
WINS_ROUND（勝ち残り条件、§6.4）を追加した。

GameEvent は gentei-janken `engine/models.py` の同名クラスを土台にしつつ、
v0.4でvote_num（投票番号）とvisibility（公開区分、§8）を追加した。
PlayerState・Action群・各種結果モデルは少数決固有の実装（C分類）。

契約関連（Obligation/Contract/ObligationType/ConditionType/ContractProposeAction/
ContractSignAction）は dangou-card `engine/models.py` の同名クラスを土台にv0.3で
導入し、v0.4で対象指定を(round_num, vote_num)の二重構造・WINS_ROUND・
share_percentに拡張した。is_fulfilled/is_expired（脱落失効用の状態）と
ContractCancelAction・発行料は持ち込まない（本作に脱落・取り消し・発行料はない、
§6.1）。
"""

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, computed_field


class Vote(str, Enum):
    """投票先（§4.2）"""

    YES = "YES"
    NO = "NO"


class PlayerState(BaseModel):
    """
    プレイヤーの状態（§3）

    借金は「開始前の借金（5%複利）」と「開始後の借金（15%複利）」の
    2種類を別々に保持する（§3.3）。最終資産 = 現金 − 借金合計（§2）。

    v0.4でも脱落（レコードからの除外）は存在しない。「退場」（そのラウンドの
    投票ができないだけ）は本モデルのフィールドではなく、Gameのラウンド内
    一時状態（remaining_ids/eliminated_ids）として持つ（§2: 次のラウンドには
    全員が戻るため、PlayerStateに永続化しない）。
    """

    player_id: str
    """プレイヤーID（"P01"〜"P12"）"""

    cash: int
    """現金残高"""

    debt_pre: int = 0
    """開始前の借金残高（利息5%、§3.1/§3.3）"""

    debt_post: int = 0
    """開始後の借金残高（利息15%、§3.2/§3.3）"""

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
        """最終資産と同じ式：現金 − 借金残高の合計（§2/§7.6）"""
        return self.cash - self.total_debt


# =============================================================================
# Negotiationフェイズのアクション（§7.2 Negotiation: dm/broadcast/transfer/
# repay/pass、契約の提案・署名）
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

    開始後の借金（15%）から先に充当する。開始前の借金（5%）は最後まで
    返済できない。
    """

    type: Literal["repay"] = "repay"
    player_id: str
    amount: int


class PassAction(BaseModel):
    """パスアクション（§7.2 Negotiation）。アクション枠を消費しない"""

    type: Literal["pass"] = "pass"
    player_id: str


class VoteCommitAction(BaseModel):
    """Commitフェイズの投票提出アクション（§4.2/§9.2）"""

    type: Literal["vote_commit"] = "vote_commit"
    player_id: str
    vote: Vote


class ContractProposeAction(BaseModel):
    """
    契約提案アクション（§6.1/§9.2/§9.3）

    terms は義務定義の辞書リスト。各要素は
    {"obligor": str, "counterparty": str, "ob_type": str, "round_num": int,
     "vote_num": int|None, "details": dict} の形（検証は engine/contracts.py
     ::validate_terms が行う）。型Aと wins_round は vote_num を持たない
     （round_num のみ、§9.3）。発行料はない（§6.1）。取り消しアクションは
     存在しない（§6.1・v0.3 §11.1 #8）。
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
    """型A: 指定ラウンドの終わりに指定額を相手方へ支払う"""

    TYPE_B_VOTE = "type_b_vote"
    """型B: 指定した投票（ラウンドと投票番号）でYES（またはNO）へ投票する"""

    TYPE_C_CONDITIONAL = "type_c_conditional"
    """型C: 条件が成立したときだけ指定額（または割合）を相手方へ支払う"""


class ConditionType(str, Enum):
    """型Cの条件種別（§6.4）"""

    MINORITY_SIDE = "minority_side"
    """指定した投票の少数派がYES（またはNO）になる（投票の精算で判定、§7.3）"""

    IN_MINORITY = "in_minority"
    """指定した投票で特定のプレイヤーが少数派に入る（投票の精算で判定、§7.3）"""

    WINS_ROUND = "wins_round"
    """指定したラウンドで特定のプレイヤーが勝ち残る（ラウンドの精算で判定、§7.4）。
    v0.4で新設（v0.3との違い、§1.2）"""


class Obligation(BaseModel):
    """
    1つの義務単位（§6.1）

    義務は「義務者」と「相手方」の組で管理する。is_fulfilled/is_expired の
    ような状態は持たない。各義務はround_num（・vote_num）が指す精算で1度だけ
    判定され、払われなかった分はそのまま消える（次の精算に請求されない）ため、
    状態フラグを持つ必要がない（CLAUDE.md 過去の落とし穴③: 使わない状態を
    持ち越して孤児化させない）。

    対象の指定（§9.3/§6.2/§6.4）:
    - 型A（type_a_payment）: round_num のみ（vote_num=None）。そのラウンドの
      終わりの精算（§7.4）で支払う。
    - 型B（type_b_vote）: round_num + vote_num。その投票の精算（§7.3）で
      違反を判定する。
    - 型C（type_c_conditional）:
      - minority_side / in_minority: round_num + vote_num（§7.3で判定）
      - wins_round: round_num のみ、vote_num=None（§7.4で判定）

    details の形（§9.3/§6.4）:
    - 型A: {"amount": int}
    - 型B: {"vote": "YES"|"NO"}
    - 型C: {"condition_type": str, "condition": dict, "amount": int}
           または {"condition_type": "wins_round", "condition": {...},
                   "share_percent": int}（amountとshare_percentは排他。
           share_percentが使えるのはwins_roundでtarget_player==obligorの
           ときだけ、§6.4）
      condition_type="minority_side" は condition={"side": "YES"|"NO"}、
      condition_type="in_minority"/"wins_round" は
      condition={"target_player": str}
      （対象は契約の当事者でなくてよい、share_percent指定を除く。§6.4）
    """

    obligation_id: str
    contract_id: str
    obligor: str
    counterparty: str
    ob_type: ObligationType
    round_num: int
    vote_num: int | None = None
    """対象の投票番号（型Aとwins_roundはNone、§9.3）"""
    details: dict[str, Any]


class ContractStatus(str, Enum):
    """契約のステータス（§6.1）"""

    PROPOSED = "proposed"
    """提案中（署名待ち）"""

    ACTIVE = "active"
    """全当事者の署名がそろい成立"""

    EXPIRED = "expired"
    """提案した投票の締切（Negotiation終了）までに署名がそろわず失効"""


class Contract(BaseModel):
    """
    契約（§6.1）

    2人以上の署名で成立する。公示されるのは「成立した」という事実と、
    各巡の終わりの成立本数だけである。当事者名・内容・成立順は当事者だけが
    見られる（v0.4で非公開に変更、§1.2/§8/§11.2 暫定1）。発行料はなく、
    成立した契約は取り消せない（§6.1。取り消しアクションはAction unionに
    存在しない）。
    """

    contract_id: str
    """契約ID（試合のシードから導出した乱数で生成。推測不能な短い文字列、§8）"""

    proposer: str
    parties: list[str]
    signed_by: list[str] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    round_created: int
    """提案したラウンド"""

    vote_created: int
    """提案した投票番号（§6.1: 署名がそろわなければこの投票の締切で失効）"""

    status: ContractStatus = ContractStatus.PROPOSED

    contract_seq: int | None = None
    """
    成立した順に振られる通し番号（提案順ではない。§6.1）。
    全当事者の署名がそろった時点で確定し、以後変わらない。
    未成立（PROPOSED/EXPIRED）はNone。当事者にのみ明かされる（§8）。
    """

    round_established: int | None = None
    """成立したラウンド（未成立ならNone）"""

    vote_established: int | None = None
    """成立した投票番号（未成立ならNone）"""


# =============================================================================
# 投票・ラウンド集計・ゲーム結果（§4/§7）
# =============================================================================

VoteResult = Literal["decisive", "retry", "abort"]
"""1回の投票の判定結果（§4.3/§4.4）。
decisive=決着（多数派退場）、retry=やり直し（同数・全員一致）、
abort=やり直しが連続max_consecutive_ties回に達し打ち切り"""


class VoteOutcome(BaseModel):
    """
    1回の投票の判定結果（§4.3/§4.4/§7.3）

    v0.3のMinorityOutcomeに相当するが、「配当」はラウンド単位（§4.5/§7.4）に
    移ったため本モデルには持たない。本モデルが持つのは「誰が退場したか」
    「やり直しの連続回数」「このラウンドが終わるか」だけである。
    """

    round_num: int
    vote_num: int
    yes_ids: list[str]
    no_ids: list[str]

    result: VoteResult
    eliminated_ids: list[str]
    """決着時に退場する多数派（§4.3）。やり直し・打ち切りなら空"""

    remaining_ids: list[str]
    """この投票の後も「残っている人」（決着なら少数派、やり直し・打ち切りなら
    変わらず全員。§4.3）"""

    minority_side: Vote | None = None
    """決着時の少数派の投票先（型C minority_side条件の判定用、§6.4）。
    決着でなければNone"""

    consecutive_ties_before: int
    consecutive_ties_after: int
    """やり直しの連続回数（決着で0に戻る、§4.4）"""

    extension_fee_collected: int
    """この投票でやり直しのため徴収した延長料の合計（決着なら0、§4.4）"""

    round_over: bool
    """このラウンドがこの投票で終わるか（決着して残りがsurvivors_max以下、
    または打ち切り。§4.5/§4.4）"""

    type_b_violator_ids: list[str] = Field(default_factory=list)
    """この投票の精算（§7.3手順3）で型Bの義務に違反した者のプレイヤーID。
    resolve_vote（純粋関数）では空のまま作られ、game.pyが精算後に
    model_copyで埋める（§8.1: 投票の精算で出た公示）"""

    payment_shortfall_ids: list[str] = Field(default_factory=list)
    """この投票の精算（§7.3手順8）で契約の支払いを払いきれなかった者の
    プレイヤーID。resolve_voteでは空のまま作られ、game.pyが精算後に
    model_copyで埋める（§8.1: 投票の精算で出た公示）"""

    auto_commit_ids: list[str] = Field(default_factory=list)
    """この投票でAUTO COMMITになったプレイヤーID（§4.6）。resolve_voteでは
    空のまま作られ、game.pyが精算後にmodel_copyで埋める（§8.1）"""


class RoundOutcome(BaseModel):
    """1ラウンドの公開情報サマリ（§4.1/§4.5/§7.4/§8）"""

    round_num: int
    votes: list[VoteOutcome] = Field(default_factory=list)
    """このラウンドで行われた投票（やり直しも含む、最大6回）"""

    carryover_in: int
    """このラウンド開始時点の持ち越し額（§4.1）"""

    pot_final: int
    """このラウンドの山の最終額（参加費1,200万＋延長料＋持ち越し、§4.5）"""

    aborted: bool
    """打ち切りで終わったか（やり直しが連続max_consecutive_ties回、§4.4）"""

    winner_ids: list[str] = Field(default_factory=list)
    """勝ち残り（1人か2人）。打ち切りなら空（§4.5）"""

    payout_per_winner: int = 0
    forfeited_remainder: int = 0
    """山が割り切れなかった場合の没収端数（§4.5）"""

    carryover_out: int = 0
    """次ラウンドへの持ち越し額（打ち切り時のみ発生。§4.4）"""

    destroyed_pot: int = 0
    """R4（最終ラウンド）で打ち切りだった場合に没収された山（§4.4）"""

    auto_commit_ids: list[str] = Field(default_factory=list)
    """このラウンドの全投票でAUTO COMMITになったプレイヤーID（§4.6/§8）"""

    established_contract_seqs: list[int] = Field(default_factory=list)
    """このラウンドの全Negotiationで成立したcontract_seq（内部集計用。
    公示は本数のみ、§6.1/§8）"""

    type_b_violator_ids: list[str] = Field(default_factory=list)
    """型Bの義務に違反した者のプレイヤーID（§6.3/§8: 名前だけ公示）"""

    payment_shortfall_ids: list[str] = Field(default_factory=list)
    """このラウンドの投票精算・ラウンド精算のいずれかで契約の支払いを
    払いきれなかった者のプレイヤーID（§7.3手順8・§7.4手順7/§8）。
    投票精算分と合算したunion（既存互換）。ラウンド精算分だけは
    round_settlement_shortfall_idsを見る（§8.1）"""

    round_settlement_shortfall_ids: list[str] = Field(default_factory=list)
    """このラウンドの精算（§7.4手順7）だけで契約の支払いを払いきれなかった者
    のプレイヤーID（投票精算分は含まない。§8.1「ラウンドの精算で払いきれ
    なかった者」）"""

    public_ranks: dict[str, int] | None = None
    """R2終了後のみ設定される全員の順位（名前と順位のみ、§7.6）"""

    interest_total: int = 0
    """このラウンドのFinanceで計上された利息の合計（2種合算）"""


class ObligationPayment(BaseModel):
    """1つの義務に対する支払いの内訳（§7.3手順6・§7.4手順5、内部処理・ログ用）"""

    contract_id: str
    contract_seq: int
    ob_index: int
    """契約内での義務の記載順（0始まり）。違約金は破った型B義務の記載位置（§6.3）"""

    obligor: str
    counterparty: str
    ob_type: ObligationType
    promised: int
    """約束額（型Bの違約金は100万固定。share_percent指定はここで確定した金額）"""

    paid: int
    """実際に支払われた額（0以上、promised以下）"""


class ContractSettlementReport(BaseModel):
    """精算（投票またはラウンド）の契約処理の結果（内部処理・ログ用）"""

    violations: list[tuple[str, str]] = Field(default_factory=list)
    """型Bの違反 (obligor, obligation_id) のリスト（§7.3手順3）"""

    payable_limits: dict[str, int] = Field(default_factory=dict)
    """義務者ごとの支払える上限（§7.3手順5・§7.4手順4）"""

    payments: list[ObligationPayment] = Field(default_factory=list)
    """実際に決定した支払いのリスト（contract_seq→記載順で整列済み）"""

    shortfall_ids: list[str] = Field(default_factory=list)
    """払いきれなかった者のプレイヤーID"""


class GameResult(BaseModel):
    """ゲーム全体の最終結果（§2/§7.6）"""

    seed: int
    final_players: dict[str, PlayerState]
    final_assets: dict[str, int]
    """player_id -> 最終資産（現金 − 借金合計）"""

    final_ranks: dict[str, int]
    """player_id -> 最終順位（同額は同順位、§7.6）"""

    round_summaries: list[RoundOutcome]
    total_interest: int
    """全ラウンド・全プレイヤーの利息合計（§2.1の場からの流出）"""

    total_destroyed_pot: int
    """R4で打ち切りだった場合に没収された山の合計（§2.1/§4.4）"""

    total_forfeited_remainder: int
    """山の配当の端数処理で没収された合計額（§4.5）"""

    post_game_reflections: dict[str, dict] = Field(default_factory=dict)
    """player_id -> post_game_reflect()の戻り値（§9.4）。

    取得に失敗した・そもそも実装していない席は含まれない（既定は空辞書で、
    既存のGameResult利用箇所への影響はない）。"""


# =============================================================================
# イベント（ログ用、§8）
# =============================================================================

Visibility = Literal["public", "self", "parties", "spectator"]
"""イベントの公開区分（§8）。
public=全員に見える／self=本人のみ／parties=契約の当事者のみ／
spectator=観戦者のみ（プレイヤーには見せない全内部状態）。
engine自体はこのタグに基づいてvisible_stateの構築を制限しない
（_build_visible_stateが別途フィルタする）。タグは「この情報は誰に
見せてよいか」の宣言であり、viewer・将来のUIが利用する。"""


class GameEvent(BaseModel):
    """
    ゲームイベント（JSONL出力用）

    全ゲームイベントを時系列で記録する。v0.4でvote_num（投票番号、§1.1）と
    visibility（公開区分、§8）を追加した。
    """

    event_type: str
    """イベント種別（例: GAME_START, VOTE_RESOLVED等）"""

    timestamp: str
    """ISO8601形式のタイムスタンプ"""

    round_num: int
    """ラウンド番号（0=ゲーム開始前）"""

    vote_num: int | None = None
    """投票番号（1〜6、ラウンド単位のイベントや開始前はNone）"""

    phase: str
    """フェイズ名（setup / open / negotiation / commit / settlement /
    round_settlement / finance）"""

    step: int | None = None
    """Settlement内のStep番号（§7.3/§7.4、Settlement以外はNone）"""

    visibility: Visibility = "spectator"
    """公開区分（§8）。既定はspectator（安全側）。公開イベントはgame.py側で
    明示的にpublicを指定する"""

    data: dict[str, Any] = Field(default_factory=dict)
    """イベント固有データ"""
