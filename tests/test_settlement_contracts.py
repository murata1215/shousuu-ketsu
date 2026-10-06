"""
契約を使ったSettlementのテスト（§7.1手順3〜8、§12.3の受け入れテスト）

execute_settlement() にACTIVE契約を直接渡し、§12.3の受け入れテスト
#8, 9, 11, 12, 13, 14, 19, 20, 21, 22 と、CLAUDE.md追加確認事項
（YES/NO両負で違約1本・相手方2人で200万・部分払い未払分は消える・
違約金が払えなくても違反者名は公示される）を確認する。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.models import (
    Contract, ContractStatus, Obligation, ObligationType, PlayerState, Vote,
)
from engine.settlement import execute_settlement


def _player(pid: str, cash: int, debt_pre: int = 0, debt_post: int = 0) -> PlayerState:
    return PlayerState(player_id=pid, cash=cash, debt_pre=debt_pre, debt_post=debt_post, initial_loan=1_200_000)


def _all_yes_votes(n: int = 12) -> dict[str, Vote]:
    return {f"P{i:02d}": Vote.YES for i in range(1, n + 1)}


def _contract(contract_id: str, seq: int, obligations: list[Obligation], parties: list[str] | None = None) -> Contract:
    parties = parties or sorted({ob.obligor for ob in obligations} | {ob.counterparty for ob in obligations})
    return Contract(
        contract_id=contract_id, proposer=parties[0], parties=parties,
        signed_by=list(parties), obligations=obligations, round_created=1,
        status=ContractStatus.ACTIVE, contract_seq=seq, round_established=1,
    )


def _ob(obligor: str, counterparty: str, ob_type: ObligationType, round_num: int, details: dict, ob_id: str = "OB1", contract_id: str = "C1") -> Obligation:
    return Obligation(
        obligation_id=ob_id, contract_id=contract_id, obligor=obligor,
        counterparty=counterparty, ob_type=ob_type, round_num=round_num, details=details,
    )


# ---------------------------------------------------------------------------
# #8: 型B「R3はYES」でNOに投票、配当後の現金30万 → 相手方に100万。70万借金。違反公示
# ---------------------------------------------------------------------------

def test_acceptance_8_type_b_violation_partial_borrow() -> None:
    """
    型B「R3はYES」でNOに投票、配当後の現金30万 → 相手方に100万（30万は現金、
    70万は新規に開始後の借金として調達）。違反者名を公示（§12.3 #8）。
    残り借入枠が十分あるため100万は全額支払われ、取りはぐれは生じない。

    P01をNO側（多数派、配当なし）に置くことで「配当後の現金」をそのまま
    テスト入力のcashとして使える（多数派は配当で変化しない）。
    """
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    players["P01"] = _player("P01", cash=300_000)  # 配当後の現金30万相当に設定

    ob = _ob("P01", "P02", ObligationType.TYPE_B_VOTE, 3, {"vote": "YES"})
    contract = _contract("C1", 1, [ob])

    # NO(多数派・配当なし): P01〜P07 / YES(少数派): P08〜P12
    votes = {f"P{i:02d}": Vote.NO for i in range(1, 8)}
    votes.update({f"P{i:02d}": Vote.YES for i in range(8, 13)})

    logger = EventLogger()
    result = execute_settlement(
        players, votes, GameConfig.default_12(), carryover_before=0, round_num=3,
        logger=logger, is_final_round=False, contracts=[contract],
    )

    assert result.report.violations == [("P01", "OB1")]
    payment = result.report.payments[0]
    assert payment.promised == 1_000_000 and payment.paid == 1_000_000
    assert result.players["P01"].cash == 0
    assert result.players["P01"].debt_post == 700_000
    assert result.players["P02"].cash == players["P02"].cash + 1_000_000
    assert "P01" not in result.report.shortfall_ids
    violation_events = [e for e in logger.events if e.event_type == "TYPE_B_VIOLATION"]
    assert violation_events and "P01" in violation_events[0].data["player_ids"]


# ---------------------------------------------------------------------------
# #9: 型A 500万、現金100万、借金残高700万 → 400万だけ支払う。100万取りはぐれ。
#     借金残高1000万。名前を公示
# ---------------------------------------------------------------------------

def test_acceptance_9_type_a_capped_by_debt_limit() -> None:
    config = GameConfig.default_12()
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    players["P01"] = _player("P01", cash=1_000_000, debt_pre=7_000_000, debt_post=0)
    players["P02"] = _player("P02", cash=0)

    ob = _ob("P01", "P02", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 5_000_000})
    contract = _contract("C1", 1, [ob])

    logger = EventLogger()
    result = execute_settlement(
        players, _all_yes_votes(), config, carryover_before=0, round_num=1,
        logger=logger, is_final_round=False, contracts=[contract],
    )

    payment = result.report.payments[0]
    assert payment.paid == 4_000_000  # 残り枠 = 1000万 - 700万 = 300万 + 現金100万 = 400万
    assert payment.promised - payment.paid == 1_000_000
    p01 = result.players["P01"]
    assert p01.total_debt == 10_000_000
    assert "P01" in result.report.shortfall_ids


# ---------------------------------------------------------------------------
# #11: 型C「R5の少数派がYES」で6対6 → 不成立。何も起きない
# ---------------------------------------------------------------------------

def test_acceptance_11_type_c_minority_side_unmet_on_tie() -> None:
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob("P01", "P02", ObligationType.TYPE_C_CONDITIONAL, 5, {
        "amount": 500_000, "condition_type": "minority_side", "condition": {"side": "YES"},
    })
    contract = _contract("C1", 1, [ob])

    votes = {**{f"P{i:02d}": Vote.YES for i in range(1, 7)}, **{f"P{i:02d}": Vote.NO for i in range(7, 13)}}
    logger = EventLogger()
    result = execute_settlement(
        players, votes, GameConfig.default_12(), carryover_before=0, round_num=5,
        logger=logger, is_final_round=False, contracts=[contract],
    )
    assert result.report.payments == []
    assert result.players["P01"].cash == players["P01"].cash
    assert result.players["P02"].cash == players["P02"].cash


# ---------------------------------------------------------------------------
# #12: 型C「R5でP03が少数派に入る」、P03が少数派 → 成立。型Aと同じ列に並べて執行
# ---------------------------------------------------------------------------

def test_acceptance_12_type_c_in_minority_met() -> None:
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob("P01", "P07", ObligationType.TYPE_C_CONDITIONAL, 5, {
        "amount": 500_000, "condition_type": "in_minority", "condition": {"target_player": "P03"},
    })
    contract = _contract("C1", 1, [ob], parties=["P01", "P07"])

    votes = {f"P{i:02d}": Vote.YES for i in range(1, 13)}
    votes["P03"] = Vote.NO  # P03だけ少数派
    logger = EventLogger()
    result = execute_settlement(
        players, votes, GameConfig.default_12(), carryover_before=0, round_num=5,
        logger=logger, is_final_round=False, contracts=[contract],
    )
    payment = result.report.payments[0]
    assert payment.paid == 500_000
    assert result.players["P01"].cash == players["P01"].cash - 500_000
    assert result.players["P07"].cash == players["P07"].cash + 500_000


# ---------------------------------------------------------------------------
# #13/#14: 同じ決済での受け取り・支払いの相殺
# ---------------------------------------------------------------------------

def test_acceptance_13_sufficient_credit_no_borrowing() -> None:
    """同じ決済で200万受け取り・150万支払い、現金0、残り枠十分 → 借金なし。現金50万"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    players["P01"] = _player("P01", cash=0)  # 配当後の現金0

    ob_pay = _ob("P01", "P02", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 1_500_000}, ob_id="OB1", contract_id="C1")
    ob_receive = _ob("P03", "P01", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 2_000_000}, ob_id="OB1", contract_id="C2")
    contracts = [
        _contract("C1", 1, [ob_pay], parties=["P01", "P02"]),
        _contract("C2", 2, [ob_receive], parties=["P03", "P01"]),
    ]
    players["P03"] = _player("P03", cash=5_000_000)

    logger = EventLogger()
    result = execute_settlement(
        players, _all_yes_votes(), GameConfig.default_12(), carryover_before=0, round_num=1,
        logger=logger, is_final_round=False, contracts=contracts,
    )
    assert result.players["P01"].cash == 500_000
    assert result.players["P01"].debt_post == 0
    assert "P01" not in result.report.shortfall_ids


def test_acceptance_14_zero_credit_payment_capped_to_zero() -> None:
    """同じ決済で200万受け取り・150万支払い、現金0、残り枠0 → 支払いは0。150万取りはぐれ。現金200万"""
    config = GameConfig.default_12()
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    players["P01"] = _player("P01", cash=0, debt_pre=5_000_000, debt_post=5_000_000)  # 残り枠0

    ob_pay = _ob("P01", "P02", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 1_500_000}, ob_id="OB1", contract_id="C1")
    ob_receive = _ob("P03", "P01", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 2_000_000}, ob_id="OB1", contract_id="C2")
    contracts = [
        _contract("C1", 1, [ob_pay], parties=["P01", "P02"]),
        _contract("C2", 2, [ob_receive], parties=["P03", "P01"]),
    ]
    players["P03"] = _player("P03", cash=5_000_000)

    logger = EventLogger()
    result = execute_settlement(
        players, _all_yes_votes(), config, carryover_before=0, round_num=1,
        logger=logger, is_final_round=False, contracts=contracts,
    )
    pay_item = next(p for p in result.report.payments if p.contract_id == "C1")
    assert pay_item.paid == 0
    assert pay_item.promised - pay_item.paid == 1_500_000
    assert result.players["P01"].cash == 2_000_000
    assert "P01" in result.report.shortfall_ids


# ---------------------------------------------------------------------------
# #19/#20: 支払える上限400万。C01(先に成立)にP02へ300万、C05にP08へ300万
# ---------------------------------------------------------------------------

def test_acceptance_19_and_20_payable_limit_split_by_contract_seq() -> None:
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=4_000_000) for i in range(1, 13)}
    # P01の残り枠を0にして、支払える上限をちょうど現金400万に固定する
    players["P01"] = _player("P01", cash=4_000_000, debt_pre=10_000_000)
    ob_to_p02 = _ob("P01", "P02", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 3_000_000}, contract_id="C01")
    ob_to_p08 = _ob("P01", "P08", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 3_000_000}, contract_id="C05")
    contracts = [
        _contract("C01", 1, [ob_to_p02], parties=["P01", "P02"]),
        _contract("C05", 5, [ob_to_p08], parties=["P01", "P08"]),
    ]
    logger = EventLogger()
    result = execute_settlement(
        players, _all_yes_votes(), GameConfig.default_12(), carryover_before=0, round_num=1,
        logger=logger, is_final_round=False, contracts=contracts,
    )
    p02_payment = next(p for p in result.report.payments if p.counterparty == "P02")
    p08_payment = next(p for p in result.report.payments if p.counterparty == "P08")
    assert p02_payment.paid == 3_000_000
    assert p08_payment.paid == 1_000_000
    assert result.players["P02"].cash == players["P02"].cash + 3_000_000
    assert result.players["P08"].cash == players["P08"].cash + 1_000_000


# ---------------------------------------------------------------------------
# #21: 支払える上限100万。C01の型B違約金100万とC07の型A100万が同じ決済
# ---------------------------------------------------------------------------

def test_acceptance_21_type_b_penalty_takes_priority_by_seq() -> None:
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    # P01の残り枠を0にして、支払える上限をちょうど現金100万に固定する
    players["P01"] = _player("P01", cash=1_000_000, debt_pre=10_000_000)
    ob_b = _ob("P01", "P02", ObligationType.TYPE_B_VOTE, 1, {"vote": "YES"}, contract_id="C01")
    ob_a = _ob("P01", "P07", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 1_000_000}, contract_id="C07")
    contracts = [
        _contract("C01", 1, [ob_b], parties=["P01", "P02"]),
        _contract("C07", 7, [ob_a], parties=["P01", "P07"]),
    ]
    # NO(多数派・配当なし): P01〜P07 / YES(少数派): P08〜P12。
    # P01はYES指定なのでNOに投票すると違反になる。多数派に置くことで配当の
    # 影響を受けず、cashをそのまま「支払える上限」の検証に使える。
    votes = {f"P{i:02d}": Vote.NO for i in range(1, 8)}
    votes.update({f"P{i:02d}": Vote.YES for i in range(8, 13)})

    logger = EventLogger()
    result = execute_settlement(
        players, votes, GameConfig.default_12(), carryover_before=0, round_num=1,
        logger=logger, is_final_round=False, contracts=contracts,
    )
    penalty = next(p for p in result.report.payments if p.contract_id == "C01")
    type_a = next(p for p in result.report.payments if p.contract_id == "C07")
    assert penalty.paid == 1_000_000
    assert type_a.paid == 0


# ---------------------------------------------------------------------------
# #22: 1つの契約に義務が2本あり、1本目の途中で上限に届く
# ---------------------------------------------------------------------------

def test_acceptance_22_partial_then_zero_within_same_contract() -> None:
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    # P01の残り枠を0にして、支払える上限をちょうど現金100万に固定する
    players["P01"] = _player("P01", cash=1_000_000, debt_pre=10_000_000)
    ob1 = _ob("P01", "P02", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 800_000}, ob_id="OB1", contract_id="C1")
    ob2 = _ob("P01", "P03", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 800_000}, ob_id="OB2", contract_id="C1")
    contract = Contract(
        contract_id="C1", proposer="P01", parties=["P01", "P02", "P03"],
        signed_by=["P01", "P02", "P03"], obligations=[ob1, ob2], round_created=1,
        status=ContractStatus.ACTIVE, contract_seq=1, round_established=1,
    )
    logger = EventLogger()
    result = execute_settlement(
        players, _all_yes_votes(), GameConfig.default_12(), carryover_before=0, round_num=1,
        logger=logger, is_final_round=False, contracts=[contract],
    )
    first = next(p for p in result.report.payments if p.ob_index == 0)
    second = next(p for p in result.report.payments if p.ob_index == 1)
    assert first.paid == 800_000  # 1本目は全額（現金100万のうち80万）
    assert second.paid == 200_000  # 残り20万だけ部分払い


# ---------------------------------------------------------------------------
# CLAUDE.md追加確認事項
# ---------------------------------------------------------------------------

def test_two_counterparty_type_b_violation_doubles_penalty() -> None:
    """相手方が2人いる型Bを破ると、違約金は2本分（200万）"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=4_000_000) for i in range(1, 13)}
    ob1 = _ob("P01", "P07", ObligationType.TYPE_B_VOTE, 1, {"vote": "YES"}, ob_id="OB1", contract_id="C1")
    ob2 = _ob("P01", "P09", ObligationType.TYPE_B_VOTE, 1, {"vote": "YES"}, ob_id="OB2", contract_id="C1")
    contract = Contract(
        contract_id="C1", proposer="P01", parties=["P01", "P07", "P09"],
        signed_by=["P01", "P07", "P09"], obligations=[ob1, ob2], round_created=1,
        status=ContractStatus.ACTIVE, contract_seq=1, round_established=1,
    )
    votes = _all_yes_votes()
    votes["P01"] = Vote.NO
    votes["P02"] = Vote.NO
    votes["P03"] = Vote.NO
    votes["P04"] = Vote.NO
    votes["P05"] = Vote.NO

    logger = EventLogger()
    result = execute_settlement(
        players, votes, GameConfig.default_12(), carryover_before=0, round_num=1,
        logger=logger, is_final_round=False, contracts=[contract],
    )
    total_penalty = sum(p.paid for p in result.report.payments)
    assert total_penalty == 2_000_000
    assert result.players["P07"].cash == players["P07"].cash + 1_000_000
    assert result.players["P09"].cash == players["P09"].cash + 1_000_000


def test_partial_payment_shortfall_never_billed_again() -> None:
    """部分払いで払われなかった分は消え、次ラウンド以降に請求されない"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=500_000) for i in range(1, 13)}
    # P01の残り枠を0にして、支払える上限をちょうど現金50万に固定する
    players["P01"] = _player("P01", cash=500_000, debt_pre=10_000_000)
    ob = _ob("P01", "P02", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 2_000_000})
    contract = _contract("C1", 1, [ob], parties=["P01", "P02"])

    logger = EventLogger()
    result = execute_settlement(
        players, _all_yes_votes(), GameConfig.default_12(), carryover_before=0, round_num=1,
        logger=logger, is_final_round=False, contracts=[contract],
    )
    assert result.report.payments[0].paid == 500_000
    assert result.report.payments[0].promised - result.report.payments[0].paid == 1_500_000

    # 次ラウンド(round_num=2)には、この義務は対象外（round_num=1固定のため再請求されない）
    from engine.contracts import obligations_due
    assert obligations_due([contract], round_num=2) == []


def test_violator_name_published_even_if_penalty_cannot_be_paid() -> None:
    """違約金が上限のために払いきれない場合でも、違反者の名前は公示される"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=0, debt_pre=10_000_000) for i in range(1, 13)}
    ob = _ob("P01", "P02", ObligationType.TYPE_B_VOTE, 1, {"vote": "YES"})
    contract = _contract("C1", 1, [ob], parties=["P01", "P02"])

    # NO(多数派・配当なし): P01〜P07 / YES(少数派): P08〜P12。
    # P01を多数派に置き、配当でcashが変わらないようにする（残り枠0のまま）。
    votes = {f"P{i:02d}": Vote.NO for i in range(1, 8)}
    votes.update({f"P{i:02d}": Vote.YES for i in range(8, 13)})

    logger = EventLogger()
    result = execute_settlement(
        players, votes, GameConfig.default_12(), carryover_before=0, round_num=1,
        logger=logger, is_final_round=False, contracts=[contract],
    )
    assert result.report.payments[0].paid == 0
    assert "P01" in result.report.shortfall_ids
    violation_events = [e for e in logger.events if e.event_type == "TYPE_B_VIOLATION"]
    assert violation_events and "P01" in violation_events[0].data["player_ids"]
