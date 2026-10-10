"""
契約モジュール（§6、v0.4）

v0.3の `engine/contracts.py`（dangou-card流用のB分類を少数決向けに縮小した
もの）を土台に、サイクル4.0で対象指定を(round_num, vote_num)の二重構造へ
拡張し、型Cに `wins_round`（勝ち残り条件、§6.4）とその割合指定
（share_percent）を追加した。

主な変更点（v0.3→v0.4）:
- 型B・型C(minority_side/in_minority) の対象は (round_num, vote_num) の
  組で指定する。型A・型C(wins_round) は round_num のみ（§9.3）。
- `obligations_due()` はvote_num引数で「投票単位の義務」と「ラウンド単位の
  義務」を1本の関数で振り分ける（CLAUDE.md 過去の落とし穴④: 同型の関数が
  複数あると修正漏れが起きるため、最初から1本にする）。
- 型Cの条件判定は判定材料（VoteOutcome / 勝ち残りリスト）が異なるため
  `evaluate_type_c_vote_condition`（投票の精算で使う）と
  `evaluate_type_c_round_condition`（ラウンドの精算で使う）の2関数に分ける
  （中身の判定ロジックが別物であり、1本に無理に統合するとif分岐が条件種別と
  精算段階の2軸になって読みにくくなるため。ただし「vote_num指定の有無で
  振り分ける」obligations_dueとは性質が異なる判断であることに注意）。
- `validate_terms` に (round_num, vote_num) の範囲検証（署名した時点の投票と
  それより後だけを対象にできる、§6.1）と share_percent の排他・範囲検証
  （§6.4）を追加した。

契約IDは乱数で生成する（GameRng経由。同一seedで再現、§8: 未成立の
提案件数が連番から推測できないようにするため連番にしない）。
"""

from enum import Enum
from typing import Any, NamedTuple

from engine.models import (
    Contract, ContractStatus, Obligation, ObligationType,
    ConditionType, RoundOutcome, VoteOutcome, Vote,
)


class TermRejectReason(str, Enum):
    """
    契約の条項（terms）が不正だった理由のコード（§6.1〜§6.4/§9.3）。
    サイクル4.2で新設（§7.5・受け入れ#57）。`llm/reasons.py::REASON_JA` の
    キーと1対1で対応する。
    """

    TERMS_EMPTY = "terms_empty"
    DETAILS_NOT_DICT = "details_not_dict"
    OBLIGOR_NOT_PARTY = "obligor_not_party"
    COUNTERPARTY_NOT_PARTY = "counterparty_not_party"
    OBLIGOR_EQUALS_COUNTERPARTY = "obligor_equals_counterparty"
    ROUND_NUM_NOT_INT = "round_num_not_int"
    ROUND_NUM_OUT_OF_RANGE = "round_num_out_of_range"
    VOTE_NUM_NOT_POSITIVE_INT = "vote_num_not_positive_int"
    VOTE_NUM_IN_PAST = "vote_num_in_past"
    TYPE_A_WITH_VOTE_NUM = "type_a_with_vote_num"
    TYPE_A_AMOUNT_NOT_POSITIVE = "type_a_amount_not_positive"
    TYPE_B_REQUIRES_VOTE_NUM = "type_b_requires_vote_num"
    TYPE_B_VOTE_INVALID = "type_b_vote_invalid"
    CONDITION_TYPE_INVALID = "condition_type_invalid"
    WINS_ROUND_WITH_VOTE_NUM = "wins_round_with_vote_num"
    CONDITION_REQUIRES_VOTE_NUM = "condition_requires_vote_num"
    AMOUNT_AND_SHARE_BOTH = "amount_and_share_both"
    AMOUNT_OR_SHARE_MISSING = "amount_or_share_missing"
    CONDITION_NOT_DICT = "condition_not_dict"
    MINORITY_SIDE_INVALID = "minority_side_invalid"
    SHARE_PERCENT_ONLY_WINS_ROUND = "share_percent_only_wins_round"
    TYPE_C_AMOUNT_NOT_POSITIVE = "type_c_amount_not_positive"
    TARGET_PLAYER_INVALID = "target_player_invalid"
    SHARE_PERCENT_TARGET_NOT_SELF = "share_percent_target_not_self"
    SHARE_PERCENT_OUT_OF_RANGE = "share_percent_out_of_range"
    UNKNOWN_OB_TYPE = "unknown_ob_type"


class TermRejection(NamedTuple):
    """
    terms検証の不成立結果（サイクル4.2で `str | None` から変更、§7.5）。

    code: 理由のコード（TermRejectReason）
    params: 日本語テンプレートに差し込む値（例: {"index": 2, "obligor": "P01"}）。
        indexは「何本目の義務か」（1始まり、§7.5「どの項目が形式に合わなかったか」）
    message_en: 英語の短い文（イベントログ・観戦者向け。互換のため残す）
    """

    code: TermRejectReason
    params: dict[str, Any]
    message_en: str


def create_contract(
    proposer: str,
    parties: list[str],
    terms: list[dict[str, Any]],
    round_created: int,
    vote_created: int,
    contract_id: str,
) -> Contract:
    """
    契約オブジェクトを生成する（署名前の状態、§6.1）

    提案者は自動で署名済みになる（dangou-card現行の扱いに合わせる）。

    Args:
        proposer: 提案者のプレイヤーID
        parties: 全当事者（提案者含む）のプレイヤーID
        terms: 義務定義のリスト（validate_termsで検証済みであること）
        round_created: 提案ラウンド
        vote_created: 提案した投票番号（§6.1: 署名がそろわなければこの
            投票の締切で失効する）
        contract_id: 呼び出し側（GameRng由来）が生成した契約ID

    Returns:
        PROPOSED状態の契約
    """
    obligations: list[Obligation] = []
    for i, term in enumerate(terms):
        obligations.append(Obligation(
            obligation_id=f"{contract_id}_OB{i + 1:02d}",
            contract_id=contract_id,
            obligor=term["obligor"],
            counterparty=term["counterparty"],
            ob_type=ObligationType(term["ob_type"]),
            round_num=term["round_num"],
            vote_num=term.get("vote_num"),
            details=dict(term.get("details", {})),
        ))

    return Contract(
        contract_id=contract_id,
        proposer=proposer,
        parties=parties,
        signed_by=[proposer],
        obligations=obligations,
        round_created=round_created,
        vote_created=vote_created,
        status=ContractStatus.PROPOSED,
    )


def sign_contract(contract: Contract, signer: str) -> tuple[Contract, bool]:
    """
    契約に署名する（§6.1）

    全当事者の署名がそろうとACTIVEになる。contract_seqの付番は呼び出し側
    （engine/game.py。成立した順に全体で1列に数える必要があるため）が行う。

    Args:
        contract: 対象契約
        signer: 署名者のプレイヤーID

    Returns:
        (署名反映後の契約, この署名で新たに成立したか)

    Raises:
        ValueError: 署名者が当事者でない、または既に署名済みの場合
    """
    if signer not in contract.parties:
        raise ValueError(f"{signer} is not a party of contract {contract.contract_id}")
    if signer in contract.signed_by:
        raise ValueError(f"{signer} already signed contract {contract.contract_id}")

    new_signed = list(contract.signed_by) + [signer]
    just_established = set(new_signed) >= set(contract.parties)
    new_status = ContractStatus.ACTIVE if just_established else contract.status

    updated = contract.model_copy(update={"signed_by": new_signed, "status": new_status})
    return updated, just_established


def obligations_due(
    contracts: list[Contract],
    round_num: int,
    vote_num: int | None,
    ob_type: ObligationType | None = None,
) -> list[Obligation]:
    """
    指定した精算（投票またはラウンド）が期限の有効な義務を取得する（§6.1/§7.3/§7.4）

    ACTIVE契約のみを対象にする（PROPOSED/EXPIREDの義務は誰も拘束しない）。
    vote_num=None なら「ラウンド単位の義務」（型A・wins_round。
    ob.vote_num is None のものだけ）、vote_num=int なら「その投票が対象の
    義務」（型B・minority_side・in_minority）を返す
    （CLAUDE.md過去の落とし穴④: 型ごとに関数を分けず1本に統合する）。

    Args:
        contracts: 全契約リスト
        round_num: 対象ラウンド
        vote_num: 対象投票番号。Noneならラウンド単位の義務を返す
        ob_type: 指定時はその型だけに絞る（Noneなら全型）

    Returns:
        (contract, obligation) ではなく obligation のリスト。契約への逆参照は
        obligation.contract_id から辿る。
    """
    result: list[Obligation] = []
    for contract in contracts:
        if contract.status != ContractStatus.ACTIVE:
            continue
        for ob in contract.obligations:
            if ob.round_num != round_num:
                continue
            if vote_num is None:
                if ob.vote_num is not None:
                    continue
            else:
                if ob.vote_num != vote_num:
                    continue
            if ob_type is not None and ob.ob_type != ob_type:
                continue
            result.append(ob)
    return result


def audit_type_b(
    contracts: list[Contract],
    votes: dict[str, Vote],
    round_num: int,
    vote_num: int,
) -> list[Obligation]:
    """
    型Bの監査: 指定した投票の型B義務のうち、実際の投票と一致しないものを返す
    （§6.2/§6.3/§7.3手順3）

    義務者がこの投票に参加していない（退場済み、§6.3「対象の投票の時点で
    義務者が退場している場合も、義務は失効する」）場合は監査をスキップする。

    Args:
        contracts: 全契約リスト
        votes: player_id -> Vote（§7.3手順1でRevealされた、この投票に
            参加した全員分）
        round_num: 対象ラウンド
        vote_num: 対象投票番号

    Returns:
        違反した型B義務のリスト（破った義務1本=違約金100万1本、§6.3）
    """
    violations: list[Obligation] = []
    for ob in obligations_due(contracts, round_num, vote_num, ObligationType.TYPE_B_VOTE):
        actual = votes.get(ob.obligor)
        if actual is None:
            # 退場済み、またはこの投票自体が行われなかった（§6.3）→ 失効
            continue
        required = ob.details.get("vote")
        if actual.value != required:
            violations.append(ob)
    return violations


def evaluate_type_c_vote_condition(ob: Obligation, outcome: VoteOutcome) -> bool:
    """
    型Cの投票単位の条件（minority_side/in_minority）を判定する（§6.4/§7.3手順4）

    決着した投票でのみ成立しうる。やり直し・打ち切りの投票では不成立
    （§6.4: 「やり直しになった投票と、行われなかった投票では不成立」）。

    Args:
        ob: 判定対象の型C義務（condition_typeがminority_side/in_minority）
        outcome: 対象の投票のVoteOutcome

    Returns:
        条件が成立したか
    """
    if outcome.result != "decisive":
        return False

    condition_type = ob.details.get("condition_type")
    condition = ob.details.get("condition", {})

    if condition_type == ConditionType.MINORITY_SIDE.value:
        return outcome.minority_side is not None and outcome.minority_side.value == condition.get("side")

    if condition_type == ConditionType.IN_MINORITY.value:
        return condition.get("target_player") in outcome.remaining_ids

    return False


def evaluate_type_c_round_condition(ob: Obligation, winner_ids: list[str]) -> bool:
    """
    型Cのラウンド単位の条件（wins_round）を判定する（§6.4/§7.4手順2）

    打ち切りのラウンドでは不成立（winner_idsが空のため自然に不成立になる、
    §6.4: 「打ち切りのラウンドでは不成立」）。

    Args:
        ob: 判定対象の型C義務（condition_typeがwins_round）
        winner_ids: このラウンドの勝ち残り（打ち切りなら空）

    Returns:
        条件が成立したか
    """
    condition_type = ob.details.get("condition_type")
    if condition_type != ConditionType.WINS_ROUND.value:
        return False
    condition = ob.details.get("condition", {})
    return condition.get("target_player") in winner_ids


def resolve_type_c_amount(ob: Obligation, *, payout_per_winner: int = 0) -> int:
    """
    型Cの義務の支払額を確定する（§6.4）

    固定額（amount）はそのまま。割合（share_percent）は
    「義務者がそのラウンドで受け取った山の額 × 割合 ÷ 100」（1円未満切り捨て）。
    割合はwins_roundでtarget_player==obligorのときだけ使えるため、
    payout_per_winnerは呼び出し側（§7.4のラウンド精算）でのみ渡す。

    Args:
        ob: 型C義務
        payout_per_winner: 義務者が受け取った山の額（share_percent指定時のみ使用）

    Returns:
        支払額（円）
    """
    share_percent = ob.details.get("share_percent")
    if share_percent is not None:
        return payout_per_winner * share_percent // 100
    return ob.details.get("amount", 0)


def vote_constraint_for_vote(
    contracts: list[Contract],
    player_id: str,
    round_num: int,
    vote_num: int,
) -> Vote | None:
    """
    自動代行（§4.6）向け: この投票に型Bの指定があればそれを返す

    同じ投票にYES指定とNO指定の両方を負っている（矛盾）場合はNoneを返し、
    呼び出し側がシード由来の乱数にフォールバックする（§4.6: 「指定がない、
    または矛盾している場合はシードから決まる乱数」）。

    Args:
        contracts: 全契約リスト
        player_id: 対象プレイヤーID
        round_num: 対象ラウンド
        vote_num: 対象投票番号

    Returns:
        指定が一意に決まればそのVote、無指定または矛盾ならNone
    """
    votes_required: set[Vote] = set()
    for ob in obligations_due(contracts, round_num, vote_num, ObligationType.TYPE_B_VOTE):
        if ob.obligor != player_id:
            continue
        required = ob.details.get("vote")
        try:
            votes_required.add(Vote(required))
        except ValueError:
            continue

    if len(votes_required) == 1:
        return next(iter(votes_required))
    return None


def _validate_target_timing(
    term_round: Any, term_vote: Any, *, round_num: int, vote_num: int, num_rounds: int, index: int,
) -> TermRejection | None:
    """
    対象が「署名した時点の投票と、それより後」であることを検証する（§6.1）

    Args:
        term_round: 義務のround_num（検証前）
        term_vote: 義務のvote_num（検証前。型A/wins_roundはNone）
        round_num: 現在ラウンド
        vote_num: 現在投票番号
        num_rounds: 総ラウンド数（対象ラウンドの上限）
        index: 何本目の義務か（1始まり。§7.5の日本語文面用）

    Returns:
        不正ならTermRejection、問題なければNone
    """
    if not isinstance(term_round, int) or isinstance(term_round, bool):
        return TermRejection(
            TermRejectReason.ROUND_NUM_NOT_INT, {"index": index, "value": term_round},
            f"round_num must be an int, got {term_round!r}",
        )
    if not (round_num <= term_round <= num_rounds):
        return TermRejection(
            TermRejectReason.ROUND_NUM_OUT_OF_RANGE,
            {"index": index, "value": term_round, "lo": round_num, "hi": num_rounds},
            f"round_num {term_round} is out of range ({round_num}..{num_rounds}); "
            "past rounds are not allowed",
        )
    if term_vote is not None:
        if not isinstance(term_vote, int) or isinstance(term_vote, bool) or term_vote < 1:
            return TermRejection(
                TermRejectReason.VOTE_NUM_NOT_POSITIVE_INT, {"index": index, "value": term_vote},
                f"vote_num must be a positive int, got {term_vote!r}",
            )
        if term_round == round_num and term_vote < vote_num:
            return TermRejection(
                TermRejectReason.VOTE_NUM_IN_PAST,
                {
                    "index": index, "value": term_vote,
                    "round_num": round_num, "vote_num": vote_num,
                },
                f"vote_num {term_vote} is in the past "
                f"(current R{round_num}V{vote_num}); past votes are not allowed",
            )
    return None


def validate_terms(
    terms: list[dict[str, Any]],
    parties: set[str],
    valid_player_ids: set[str],
    round_num: int,
    vote_num: int,
    num_rounds: int,
) -> TermRejection | None:
    """
    契約提案のterms全体を検証する（§6.1〜§6.4/§9.3）

    1条項でも不正なら提案全体を不成立にする（dangou-card方針を踏襲）。
    型Cの条件対象（target_player）は契約の当事者でなくてよいが、ゲームに
    存在するプレイヤーIDでなければならない（§6.4）。

    Args:
        terms: 義務定義のリスト
        parties: 契約の当事者（obligor/counterpartyはこの集合に含まれること）
        valid_player_ids: ゲームに存在する全プレイヤーID（型Cのtarget_player検証用）
        round_num: 提案している現在ラウンド
        vote_num: 提案している現在投票番号
        num_rounds: 総ラウンド数（対象ラウンドの上限）

    Returns:
        不正ならTermRejection（code・params・message_en。§7.5の日本語文面の元データ）、
        問題なければNone
    """
    if not terms:
        return TermRejection(TermRejectReason.TERMS_EMPTY, {}, "terms must not be empty")

    for i, term in enumerate(terms, start=1):
        obligor = term.get("obligor")
        counterparty = term.get("counterparty")
        ob_type = term.get("ob_type")
        term_round = term.get("round_num")
        term_vote = term.get("vote_num")
        details = term.get("details", {})
        if not isinstance(details, dict):
            return TermRejection(
                TermRejectReason.DETAILS_NOT_DICT, {"index": i, "value": details},
                f"details must be a dict, got {details!r}",
            )

        if obligor not in parties:
            return TermRejection(
                TermRejectReason.OBLIGOR_NOT_PARTY, {"index": i, "pid": obligor},
                f"obligor {obligor!r} is not a party of this contract",
            )
        if counterparty not in parties:
            return TermRejection(
                TermRejectReason.COUNTERPARTY_NOT_PARTY, {"index": i, "pid": counterparty},
                f"counterparty {counterparty!r} is not a party of this contract",
            )
        if obligor == counterparty:
            return TermRejection(
                TermRejectReason.OBLIGOR_EQUALS_COUNTERPARTY, {"index": i, "pid": obligor},
                "obligor and counterparty must differ",
            )

        if ob_type == ObligationType.TYPE_A_PAYMENT.value:
            if term_vote is not None:
                return TermRejection(
                    TermRejectReason.TYPE_A_WITH_VOTE_NUM, {"index": i},
                    "type_a_payment must not specify vote_num (round_num only, §9.3)",
                )
            rejection = _validate_target_timing(
                term_round, None, round_num=round_num, vote_num=vote_num,
                num_rounds=num_rounds, index=i,
            )
            if rejection:
                return rejection
            amount = details.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                return TermRejection(
                    TermRejectReason.TYPE_A_AMOUNT_NOT_POSITIVE, {"index": i, "value": amount},
                    f"type_a_payment amount must be a positive int, got {amount!r}",
                )

        elif ob_type == ObligationType.TYPE_B_VOTE.value:
            if term_vote is None:
                return TermRejection(
                    TermRejectReason.TYPE_B_REQUIRES_VOTE_NUM, {"index": i},
                    "type_b_vote requires vote_num (§9.3)",
                )
            rejection = _validate_target_timing(
                term_round, term_vote, round_num=round_num, vote_num=vote_num,
                num_rounds=num_rounds, index=i,
            )
            if rejection:
                return rejection
            vote = details.get("vote")
            if vote not in (Vote.YES.value, Vote.NO.value):
                return TermRejection(
                    TermRejectReason.TYPE_B_VOTE_INVALID, {"index": i, "value": vote},
                    f"type_b_vote vote must be YES or NO, got {vote!r}",
                )

        elif ob_type == ObligationType.TYPE_C_CONDITIONAL.value:
            condition_type = details.get("condition_type")
            if condition_type not in {c.value for c in ConditionType}:
                return TermRejection(
                    TermRejectReason.CONDITION_TYPE_INVALID, {"index": i, "value": condition_type},
                    f"Invalid type_c_conditional condition_type: {condition_type!r} "
                    f"(valid: {', '.join(c.value for c in ConditionType)})",
                )

            is_round_level = condition_type == ConditionType.WINS_ROUND.value
            if is_round_level and term_vote is not None:
                return TermRejection(
                    TermRejectReason.WINS_ROUND_WITH_VOTE_NUM, {"index": i},
                    "wins_round must not specify vote_num (round_num only, §9.3)",
                )
            if not is_round_level and term_vote is None:
                return TermRejection(
                    TermRejectReason.CONDITION_REQUIRES_VOTE_NUM,
                    {"index": i, "condition_type": condition_type},
                    f"{condition_type} requires vote_num (§9.3)",
                )
            rejection = _validate_target_timing(
                term_round, None if is_round_level else term_vote,
                round_num=round_num, vote_num=vote_num, num_rounds=num_rounds, index=i,
            )
            if rejection:
                return rejection

            amount = details.get("amount")
            share_percent = details.get("share_percent")
            if amount is not None and share_percent is not None:
                return TermRejection(
                    TermRejectReason.AMOUNT_AND_SHARE_BOTH, {"index": i},
                    "amount and share_percent are mutually exclusive (§6.4)",
                )
            if amount is None and share_percent is None:
                return TermRejection(
                    TermRejectReason.AMOUNT_OR_SHARE_MISSING, {"index": i},
                    "type_c_conditional requires amount or share_percent",
                )

            condition = details.get("condition")
            if not isinstance(condition, dict):
                return TermRejection(
                    TermRejectReason.CONDITION_NOT_DICT, {"index": i, "value": condition},
                    f"type_c_conditional condition must be a dict, got {condition!r}",
                )

            if condition_type == ConditionType.MINORITY_SIDE.value:
                side = condition.get("side")
                if side not in (Vote.YES.value, Vote.NO.value):
                    return TermRejection(
                        TermRejectReason.MINORITY_SIDE_INVALID, {"index": i, "value": side},
                        f"minority_side condition.side must be YES or NO, got {side!r}",
                    )
                if share_percent is not None:
                    return TermRejection(
                        TermRejectReason.SHARE_PERCENT_ONLY_WINS_ROUND,
                        {"index": i, "condition_type": condition_type},
                        "share_percent is only allowed for wins_round (§6.4)",
                    )
                if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                    return TermRejection(
                        TermRejectReason.TYPE_C_AMOUNT_NOT_POSITIVE, {"index": i, "value": amount},
                        f"type_c_conditional amount must be a positive int, got {amount!r}",
                    )

            elif condition_type == ConditionType.IN_MINORITY.value:
                target_player = condition.get("target_player")
                if target_player not in valid_player_ids:
                    return TermRejection(
                        TermRejectReason.TARGET_PLAYER_INVALID,
                        {"index": i, "condition_type": condition_type, "value": target_player},
                        f"Invalid type_c_conditional condition.target_player: {target_player!r}",
                    )
                if share_percent is not None:
                    return TermRejection(
                        TermRejectReason.SHARE_PERCENT_ONLY_WINS_ROUND,
                        {"index": i, "condition_type": condition_type},
                        "share_percent is only allowed for wins_round (§6.4)",
                    )
                if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                    return TermRejection(
                        TermRejectReason.TYPE_C_AMOUNT_NOT_POSITIVE, {"index": i, "value": amount},
                        f"type_c_conditional amount must be a positive int, got {amount!r}",
                    )

            elif condition_type == ConditionType.WINS_ROUND.value:
                target_player = condition.get("target_player")
                if target_player not in valid_player_ids:
                    return TermRejection(
                        TermRejectReason.TARGET_PLAYER_INVALID,
                        {"index": i, "condition_type": condition_type, "value": target_player},
                        f"Invalid type_c_conditional condition.target_player: {target_player!r}",
                    )
                if share_percent is not None:
                    if target_player != obligor:
                        return TermRejection(
                            TermRejectReason.SHARE_PERCENT_TARGET_NOT_SELF,
                            {"index": i, "obligor": obligor, "value": target_player},
                            "share_percent is only allowed when target_player "
                            "is the obligor themselves (§6.4)",
                        )
                    if (
                        not isinstance(share_percent, int)
                        or isinstance(share_percent, bool)
                        or not (1 <= share_percent <= 100)
                    ):
                        return TermRejection(
                            TermRejectReason.SHARE_PERCENT_OUT_OF_RANGE,
                            {"index": i, "value": share_percent},
                            f"share_percent must be an int in 1..100, got {share_percent!r}",
                        )
                else:
                    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                        return TermRejection(
                            TermRejectReason.TYPE_C_AMOUNT_NOT_POSITIVE, {"index": i, "value": amount},
                            f"type_c_conditional amount must be a positive int, got {amount!r}",
                        )
        else:
            return TermRejection(
                TermRejectReason.UNKNOWN_OB_TYPE, {"index": i, "value": ob_type},
                f"Unknown ob_type: {ob_type!r}",
            )

    return None
