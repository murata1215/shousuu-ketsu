"""
契約モジュールのテスト（§6.1〜§6.4）

engine/contracts.py の各関数と、Game経由の提案→署名→成立・失効の流れ、
取り消しアクションが存在しないことを確認する（§12.3 #20ほか）。
"""

from engine.config import GameConfig
from engine.contracts import (
    audit_type_b, create_contract, evaluate_type_c_condition, obligations_due,
    sign_contract, validate_terms, vote_constraint_for_round,
)
from engine.events import EventLogger
from engine.game import Game
from engine.minority import resolve_minority
from engine.models import (
    Action, Contract, ContractProposeAction, ContractSignAction, ContractStatus,
    Obligation, ObligationType, PlayerState, Vote,
)
from tests.helpers import ScriptedAgent, make_roster


def _votes_7_5() -> dict[str, Vote]:
    ids = [f"P{i:02d}" for i in range(1, 13)]
    return {**{pid: Vote.YES for pid in ids[:7]}, **{pid: Vote.NO for pid in ids[7:]}}


# ---------------------------------------------------------------------------
# create_contract / sign_contract（§6.1）
# ---------------------------------------------------------------------------

def test_proposer_is_auto_signed() -> None:
    """提案者は自動で署名済みになる（dangou-card現行の扱いに合わせる）"""
    contract = create_contract(
        "P01", ["P01", "P07"],
        terms=[{"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
                "round_num": 3, "details": {"amount": 100_000}}],
        round_created=1, contract_id="C_TEST0001",
    )
    assert contract.signed_by == ["P01"]
    assert contract.status == ContractStatus.PROPOSED


def test_sign_contract_establishes_on_last_signature() -> None:
    """全当事者の署名がそろうとACTIVEになる。それまではPROPOSEDのまま"""
    contract = create_contract(
        "P01", ["P01", "P07", "P09"], terms=[], round_created=1, contract_id="C_TEST0002",
    )
    contract, established = sign_contract(contract, "P07")
    assert established is False
    assert contract.status == ContractStatus.PROPOSED

    contract, established = sign_contract(contract, "P09")
    assert established is True
    assert contract.status == ContractStatus.ACTIVE


def test_sign_contract_rejects_non_party_or_duplicate() -> None:
    contract = create_contract(
        "P01", ["P01", "P07"], terms=[], round_created=1, contract_id="C_TEST0003",
    )
    try:
        sign_contract(contract, "P02")
        assert False, "non-party signer should raise"
    except ValueError:
        pass

    try:
        sign_contract(contract, "P01")
        assert False, "already-signed signer should raise"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# validate_terms（§6.1〜§6.4）
# ---------------------------------------------------------------------------

def test_validate_terms_rejects_past_round() -> None:
    """過去のラウンドを対象にした義務は成立しない"""
    terms = [{"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
              "round_num": 2, "details": {"amount": 100_000}}]
    error = validate_terms(terms, {"P01", "P07"}, {"P01", "P07"}, round_num=3, num_rounds=12)
    assert error is not None and "range" in error


def test_validate_terms_rejects_non_positive_amount() -> None:
    """金額が0以下の義務は成立しない"""
    terms = [{"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
              "round_num": 5, "details": {"amount": 0}}]
    error = validate_terms(terms, {"P01", "P07"}, {"P01", "P07"}, round_num=1, num_rounds=12)
    assert error is not None


def test_validate_terms_rejects_non_party_obligor_or_counterparty() -> None:
    """当事者でない人を義務者・相手方にした義務は成立しない"""
    terms = [{"obligor": "P01", "counterparty": "P99", "ob_type": "type_a_payment",
              "round_num": 5, "details": {"amount": 100_000}}]
    error = validate_terms(terms, {"P01", "P07"}, {"P01", "P07", "P99"}, round_num=1, num_rounds=12)
    assert error is not None and "party" in error


def test_validate_terms_allows_in_minority_target_not_a_party() -> None:
    """型Cのin_minority対象は契約の当事者でなくてよい（§6.4）"""
    terms = [{"obligor": "P01", "counterparty": "P07", "ob_type": "type_c_conditional",
              "round_num": 5,
              "details": {"amount": 500_000, "condition_type": "in_minority",
                           "condition": {"target_player": "P03"}}}]
    error = validate_terms(
        terms, {"P01", "P07"}, {"P01", "P03", "P07"}, round_num=1, num_rounds=12,
    )
    assert error is None


# ---------------------------------------------------------------------------
# audit_type_b / evaluate_type_c_condition（§6.2〜§6.4）
# ---------------------------------------------------------------------------

def _active_contract(contract_id: str, obligations: list[Obligation], seq: int) -> Contract:
    return Contract(
        contract_id=contract_id, proposer="P01", parties=["P01", "P07"],
        signed_by=["P01", "P07"], obligations=obligations, round_created=1,
        status=ContractStatus.ACTIVE, contract_seq=seq, round_established=1,
    )


def test_audit_type_b_yes_and_no_violation_counts_one() -> None:
    """YES指定1本とNO指定1本を両方負った場合、違約は破った1本分だけ（§6.3）"""
    ob_yes = Obligation(
        obligation_id="OB1", contract_id="C1", obligor="P01", counterparty="P07",
        ob_type=ObligationType.TYPE_B_VOTE, round_num=3, details={"vote": "YES"},
    )
    ob_no = Obligation(
        obligation_id="OB2", contract_id="C1", obligor="P01", counterparty="P09",
        ob_type=ObligationType.TYPE_B_VOTE, round_num=3, details={"vote": "NO"},
    )
    contract = _active_contract("C1", [ob_yes, ob_no], seq=1)
    votes = {"P01": Vote.NO}  # NOに投票 → YES指定だけ破る
    violations = audit_type_b([contract], votes, round_num=3)
    assert len(violations) == 1
    assert violations[0].obligation_id == "OB1"


def test_audit_type_b_two_counterparties_two_violations() -> None:
    """相手方が2人いる型Bを破ると、違約は2本分（200万）になる（§6.3）"""
    obs = [
        Obligation(
            obligation_id=f"OB{i}", contract_id="C1", obligor="P01",
            counterparty=cp, ob_type=ObligationType.TYPE_B_VOTE,
            round_num=3, details={"vote": "YES"},
        )
        for i, cp in enumerate(["P07", "P09"], start=1)
    ]
    contract = _active_contract("C1", obs, seq=1)
    violations = audit_type_b([contract], {"P01": Vote.NO}, round_num=3)
    assert len(violations) == 2


def test_evaluate_type_c_in_minority_for_non_party() -> None:
    """当事者でない人を条件にした型Cが正しく判定される（§6.4）"""
    ob = Obligation(
        obligation_id="OB1", contract_id="C1", obligor="P01", counterparty="P07",
        ob_type=ObligationType.TYPE_C_CONDITIONAL, round_num=5,
        details={"amount": 500_000, "condition_type": "in_minority",
                 "condition": {"target_player": "P03"}},
    )
    outcome_true = resolve_minority(
        {**{f"P{i:02d}": Vote.YES for i in range(1, 12)}, "P03": Vote.NO},
        GameConfig.default_12(), 0, 5, is_final_round=False,
    )
    assert "P03" in outcome_true.minority_ids
    assert evaluate_type_c_condition(ob, outcome_true) is True

    outcome_false = resolve_minority(
        {f"P{i:02d}": Vote.YES for i in range(1, 13)},
        GameConfig.default_12(), 0, 5, is_final_round=False,
    )
    assert evaluate_type_c_condition(ob, outcome_false) is False


def test_evaluate_type_c_no_minority_round_is_unmet() -> None:
    """少数派なし（6対6・12対0）のラウンドはどちらの条件も不成立（§6.4）"""
    ob_minority_side = Obligation(
        obligation_id="OB1", contract_id="C1", obligor="P01", counterparty="P07",
        ob_type=ObligationType.TYPE_C_CONDITIONAL, round_num=1,
        details={"amount": 500_000, "condition_type": "minority_side",
                 "condition": {"side": "YES"}},
    )
    outcome = resolve_minority(_votes_7_5(), GameConfig.default_12(), 0, 1, is_final_round=False)
    # 7対5なので少数派はNO。minority_side=YESは不成立。
    assert evaluate_type_c_condition(ob_minority_side, outcome) is False

    tie_outcome = resolve_minority(
        {f"P{i:02d}": Vote.YES for i in range(1, 7)}
        | {f"P{i:02d}": Vote.NO for i in range(7, 13)},
        GameConfig.default_12(), 0, 1, is_final_round=False,
    )
    assert tie_outcome.minority_side is None
    assert evaluate_type_c_condition(ob_minority_side, tie_outcome) is False


# ---------------------------------------------------------------------------
# vote_constraint_for_round（§4.4）
# ---------------------------------------------------------------------------

def test_vote_constraint_contradictory_returns_none() -> None:
    """同ラウンドにYES指定とNO指定の両方があれば矛盾としてNoneを返す（§4.4）"""
    obs = [
        Obligation(
            obligation_id="OB1", contract_id="C1", obligor="P01", counterparty="P07",
            ob_type=ObligationType.TYPE_B_VOTE, round_num=3, details={"vote": "YES"},
        ),
        Obligation(
            obligation_id="OB2", contract_id="C1", obligor="P01", counterparty="P09",
            ob_type=ObligationType.TYPE_B_VOTE, round_num=3, details={"vote": "NO"},
        ),
    ]
    contract = _active_contract("C1", obs, seq=1)
    assert vote_constraint_for_round([contract], "P01", 3) is None


def test_vote_constraint_single_instruction_is_followed() -> None:
    ob = Obligation(
        obligation_id="OB1", contract_id="C1", obligor="P01", counterparty="P07",
        ob_type=ObligationType.TYPE_B_VOTE, round_num=3, details={"vote": "YES"},
    )
    contract = _active_contract("C1", [ob], seq=1)
    assert vote_constraint_for_round([contract], "P01", 3) == Vote.YES


# ---------------------------------------------------------------------------
# obligations_due（§6.1）
# ---------------------------------------------------------------------------

def test_obligations_due_ignores_proposed_and_wrong_round() -> None:
    active_ob = Obligation(
        obligation_id="OB1", contract_id="C1", obligor="P01", counterparty="P07",
        ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5, details={"amount": 100_000},
    )
    active = _active_contract("C1", [active_ob], seq=1)

    proposed_ob = Obligation(
        obligation_id="OB1", contract_id="C2", obligor="P01", counterparty="P07",
        ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5, details={"amount": 999_999},
    )
    proposed = Contract(
        contract_id="C2", proposer="P01", parties=["P01", "P07"], signed_by=["P01"],
        obligations=[proposed_ob], round_created=1, status=ContractStatus.PROPOSED,
    )

    due = obligations_due([active, proposed], round_num=5)
    assert len(due) == 1 and due[0].contract_id == "C1"

    assert obligations_due([active, proposed], round_num=6) == []


# ---------------------------------------------------------------------------
# Game経由: 成立順の付番・失効・取り消しアクションの不在（§12.3 #20）
# ---------------------------------------------------------------------------

def test_contract_seq_assigned_in_establishment_order_not_proposal_order() -> None:
    """
    提案はP05発が先でも、署名がそろうのが後ならcontract_seqは後になる
    （§6.1/§12.3 #20）。
    """
    agents = make_roster({r: {} for r in range(1, 13)}, num_players=12)

    terms_a = [{"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
                "round_num": 3, "details": {"amount": 100_000}}]
    terms_b = [{"obligor": "P05", "counterparty": "P06", "ob_type": "type_a_payment",
                "round_num": 3, "details": {"amount": 100_000}}]

    # P05が先（turn1）に提案、P01は後（turn2）に提案。
    agents["P05"].negotiate_actions[(1, 1)] = ContractProposeAction(
        player_id="P05", with_players=["P06"], terms=terms_b,
    )
    agents["P01"].negotiate_actions[(1, 2)] = ContractProposeAction(
        player_id="P01", with_players=["P02"], terms=terms_a,
    )
    # 署名はP01の契約（提案が後）の方が先にそろうようにする:
    # P02の署名をturn3、P06の署名をturn4に置く（turn4を空振りさせないことで、
    # 全員passによる早期終了がturn5のP06署名を飲み込まないようにする）。
    agents["P02"].sign_proposer_at[(1, 3)] = "P01"
    agents["P06"].sign_proposer_at[(1, 4)] = "P05"

    game = Game(config=GameConfig.default_12(), agents=agents, seed=1, logger=EventLogger())
    result = game.run()
    assert len(result.round_summaries) == 12

    contracts_by_proposer = {c.proposer: c for c in game.contracts if c.status == ContractStatus.ACTIVE}
    assert "P01" in contracts_by_proposer and "P05" in contracts_by_proposer
    # P01の契約（署名が先にそろう）のseqの方が小さい
    assert contracts_by_proposer["P01"].contract_seq < contracts_by_proposer["P05"].contract_seq


def test_unsigned_proposal_expires_at_round_end() -> None:
    """署名がそろわない提案は、提案したラウンドの終わりまでに失効する（§6.1）"""
    agents = make_roster({r: {} for r in range(1, 13)}, num_players=12)
    terms = [{"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
              "round_num": 3, "details": {"amount": 100_000}}]
    agents["P01"].negotiate_actions[(1, 1)] = ContractProposeAction(
        player_id="P01", with_players=["P02"], terms=terms,
    )
    # P02は署名しない（常にpass）

    game = Game(config=GameConfig.default_12(), agents=agents, seed=1, logger=EventLogger())
    game.run()

    assert len(game.contracts) == 1
    assert game.contracts[0].status == ContractStatus.EXPIRED
    assert game.contracts[0].contract_seq is None


def test_no_cancel_action_exists_in_action_union() -> None:
    """取り消しのアクションは存在しない（§6.1・§11.1 #8）"""
    import typing

    allowed_types = {
        option.model_fields["type"].default
        for option in typing.get_args(typing.get_args(Action)[0])
    }
    assert "contract_cancel" not in allowed_types
    assert allowed_types == {
        "dm", "broadcast", "transfer", "repay", "pass", "vote_commit",
        "contract_propose", "contract_sign",
    }


def test_multi_party_contract_requires_all_signatures() -> None:
    """3人契約は全員の署名がそろって初めて成立する（§6.1: 2人以上の署名）"""
    contract = create_contract(
        "P01", ["P01", "P05", "P09"], terms=[], round_created=1, contract_id="C_TEST0004",
    )
    contract, established = sign_contract(contract, "P05")
    assert established is False
    assert contract.status == ContractStatus.PROPOSED
    contract, established = sign_contract(contract, "P09")
    assert established is True
    assert contract.status == ContractStatus.ACTIVE
