"""
契約モジュール（§6）

dangou-card `engine/contracts.py`（B分類）の `create_contract`/`sign_contract`/
`audit_type_b`/`evaluate_type_c_condition`/`get_active_type_*_obligations` を
少数決向けに縮小・統合して流用した。

主な変更点:
- 型Bの判定はカード/市場の照合ではなく「votes[pid] と details['vote'] の比較」
  （§6.2/§6.3）。
- 型Cの条件は minority_side / in_minority の2種のみ（§6.4）。
- dangouの `get_active_type_{a,b,c}_obligations` 3関数を `obligations_due()` 1本に
  統合した（CLAUDE.md 過去の落とし穴④: 同型の関数が複数あると修正漏れが起きるため、
  最初から1本にする）。
- 義務に is_fulfilled/is_expired の状態を持たせないため、`fulfill_obligations` /
  `expire_obligations` に相当する関数はない。
- `can_cancel_contract` / `request_cancel`（全当事者合意の解除）、
  `count_committed_type_b_cards` / `is_card_tradable`（カードトレード）、
  `execute_type_a_atomic`（全額払えなければ不履行＝脱落）は持ち込まない
  （本作に取り消し・カード・脱落はない、§6.1/CLAUDE.md 守ること）。

contract_id は乱数で生成する（GameRng経由。同一seedで再現、§8: 未成立の
提案件数が連番から推測できないようにするため連番にしない）。
"""

from typing import Any

from engine.models import (
    Contract, ContractStatus, MinorityOutcome, Obligation, ObligationType,
    ConditionType, PlayerState, Vote,
)


def create_contract(
    proposer: str,
    parties: list[str],
    terms: list[dict[str, Any]],
    round_created: int,
    contract_id: str,
) -> Contract:
    """
    契約オブジェクトを生成する（署名前の状態、§6.1）

    提案者は自動で署名済みになる（dangou-card現行の扱いに合わせる。
    CLAUDE.md「守ること」: 談合カードの現行の扱いを調べて合わせる）。

    Args:
        proposer: 提案者のプレイヤーID
        parties: 全当事者（提案者含む）のプレイヤーID
        terms: 義務定義のリスト（validate_termsで検証済みであること）
        round_created: 提案ラウンド
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
            details=dict(term.get("details", {})),
        ))

    return Contract(
        contract_id=contract_id,
        proposer=proposer,
        parties=parties,
        signed_by=[proposer],
        obligations=obligations,
        round_created=round_created,
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
    ob_type: ObligationType | None = None,
) -> list[Obligation]:
    """
    指定ラウンドが期限の有効な義務を取得する（§6.1/§7.1手順3〜6）

    ACTIVE契約のみを対象にする（PROPOSED/EXPIREDの義務は誰も拘束しない）。
    dangouの型A/B/C別3関数をここで1本に統合している。

    Args:
        contracts: 全契約リスト
        round_num: 対象ラウンド
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
            if ob_type is not None and ob.ob_type != ob_type:
                continue
            result.append(ob)
    return result


def audit_type_b(
    contracts: list[Contract],
    votes: dict[str, Vote],
    round_num: int,
) -> list[Obligation]:
    """
    型Bの監査: 指定ラウンドの型B義務のうち、実際の投票と一致しないものを返す
    （§6.2/§6.3/§7.1手順3）

    義務者がそのラウンドに投票していない（あり得ないが防御的に）場合は
    監査をスキップする。

    Args:
        contracts: 全契約リスト
        votes: player_id -> Vote（§7.1手順1でRevealされた全員分）
        round_num: 対象ラウンド

    Returns:
        違反した型B義務のリスト（破った義務1本=違約金100万1本、§6.3）
    """
    violations: list[Obligation] = []
    for ob in obligations_due(contracts, round_num, ObligationType.TYPE_B_VOTE):
        actual = votes.get(ob.obligor)
        if actual is None:
            continue
        required = ob.details.get("vote")
        if actual.value != required:
            violations.append(ob)
    return violations


def evaluate_type_c_condition(ob: Obligation, outcome: MinorityOutcome) -> bool:
    """
    型Cの条件を判定する（§6.4）

    少数派なし（6対6・12対0、outcome.minority_side is None）のラウンドは
    どちらの条件も不成立とする。

    Args:
        ob: 判定対象の型C義務
        outcome: 同じラウンドの少数決判定結果（§6.4: 条件判定と支払いは同じラウンド）

    Returns:
        条件が成立したか
    """
    if outcome.minority_side is None:
        return False

    condition_type = ob.details.get("condition_type")
    condition = ob.details.get("condition", {})

    if condition_type == ConditionType.MINORITY_SIDE.value:
        return outcome.minority_side.value == condition.get("side")

    if condition_type == ConditionType.IN_MINORITY.value:
        return condition.get("target_player") in outcome.minority_ids

    return False


def vote_constraint_for_round(
    contracts: list[Contract],
    player_id: str,
    round_num: int,
) -> Vote | None:
    """
    自動代行（§4.4）向け: そのラウンドに型Bの指定があればそれを返す

    同ラウンドにYES指定とNO指定の両方を負っている（矛盾）場合はNoneを返し、
    呼び出し側がシード由来の乱数にフォールバックする（§4.4: 「指定がない、
    または矛盾している場合はシードから決まる乱数」）。

    Args:
        contracts: 全契約リスト
        player_id: 対象プレイヤーID
        round_num: 対象ラウンド

    Returns:
        指定が一意に決まればそのVote、無指定または矛盾ならNone
    """
    votes_required: set[Vote] = set()
    for ob in obligations_due(contracts, round_num, ObligationType.TYPE_B_VOTE):
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


def validate_terms(
    terms: list[dict[str, Any]],
    parties: set[str],
    valid_player_ids: set[str],
    round_num: int,
    num_rounds: int,
) -> str | None:
    """
    契約提案のterms全体を検証する（§6.1〜§6.4）

    1条項でも不正なら提案全体を不成立にする（dangou-card方針を踏襲）。
    型Cの条件対象（target_player）は契約の当事者でなくてよいが、ゲームに
    存在するプレイヤーIDでなければならない（§6.4）。

    Args:
        terms: 義務定義のリスト
        parties: 契約の当事者（obligor/counterpartyはこの集合に含まれること）
        valid_player_ids: ゲームに存在する全プレイヤーID（型Cのtarget_player検証用）
        round_num: 提案している現在ラウンド（過去ラウンドを対象にできない、§6.1）
        num_rounds: 総ラウンド数（対象ラウンドの上限）

    Returns:
        不正ならエラーメッセージ、問題なければNone
    """
    if not terms:
        return "terms must not be empty"

    for term in terms:
        obligor = term.get("obligor")
        counterparty = term.get("counterparty")
        ob_type = term.get("ob_type")
        term_round = term.get("round_num")
        details = term.get("details", {})
        if not isinstance(details, dict):
            return f"details must be a dict, got {details!r}"

        if obligor not in parties:
            return f"obligor {obligor!r} is not a party of this contract"
        if counterparty not in parties:
            return f"counterparty {counterparty!r} is not a party of this contract"
        if obligor == counterparty:
            return "obligor and counterparty must differ"

        if not isinstance(term_round, int) or isinstance(term_round, bool):
            return f"round_num must be an int, got {term_round!r}"
        if not (round_num <= term_round <= num_rounds):
            return (
                f"round_num {term_round} is out of range ({round_num}..{num_rounds}); "
                "past rounds are not allowed"
            )

        if ob_type == ObligationType.TYPE_A_PAYMENT.value:
            amount = details.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                return f"type_a_payment amount must be a positive int, got {amount!r}"

        elif ob_type == ObligationType.TYPE_B_VOTE.value:
            vote = details.get("vote")
            if vote not in (Vote.YES.value, Vote.NO.value):
                return f"type_b_vote vote must be YES or NO, got {vote!r}"

        elif ob_type == ObligationType.TYPE_C_CONDITIONAL.value:
            amount = details.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                return f"type_c_conditional amount must be a positive int, got {amount!r}"

            condition_type = details.get("condition_type")
            if condition_type not in {c.value for c in ConditionType}:
                return (
                    f"Invalid type_c_conditional condition_type: {condition_type!r} "
                    f"(valid: {', '.join(c.value for c in ConditionType)})"
                )

            condition = details.get("condition")
            if not isinstance(condition, dict):
                return f"type_c_conditional condition must be a dict, got {condition!r}"

            if condition_type == ConditionType.MINORITY_SIDE.value:
                side = condition.get("side")
                if side not in (Vote.YES.value, Vote.NO.value):
                    return f"minority_side condition.side must be YES or NO, got {side!r}"
            elif condition_type == ConditionType.IN_MINORITY.value:
                target_player = condition.get("target_player")
                # 対象は契約の当事者でなくてよいが、ゲームに存在する必要がある（§6.4）
                if target_player not in valid_player_ids:
                    return (
                        f"Invalid type_c_conditional condition.target_player: {target_player!r}"
                    )
        else:
            return f"Unknown ob_type: {ob_type!r}"

    return None
