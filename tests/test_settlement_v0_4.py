"""
精算（settle_vote/settle_round）のテスト（§7.3/§7.4、v0.4）

v0.3の `tests/test_settlement.py`・`tests/test_settlement_contracts.py` を
統合・全面置き換えた（サイクル4.0）。v0.4は精算が投票単位（settle_vote）と
ラウンド単位（settle_round）の2段に分かれるため、それぞれを個別に検証する。

対応する仕様書v0.4 §12.3の受け入れ番号はdocstringに明記する（網羅的な
対応表は doc/analysis/acceptance_v0_4.md）。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.models import (
    Contract, ContractStatus, Obligation, ObligationType, PlayerState, Vote,
)
from engine.settlement import settle_round, settle_vote


def _player(pid: str, cash: int, debt_pre: int = 0, debt_post: int = 0) -> PlayerState:
    return PlayerState(player_id=pid, cash=cash, debt_pre=debt_pre, debt_post=debt_post, initial_loan=1_200_000)


def _all_yes_votes(n: int = 12) -> dict[str, Vote]:
    return {f"P{i:02d}": Vote.YES for i in range(1, n + 1)}


def _contract(
    contract_id: str, seq: int, obligations: list[Obligation], parties: list[str] | None = None,
) -> Contract:
    parties = parties or sorted({ob.obligor for ob in obligations} | {ob.counterparty for ob in obligations})
    return Contract(
        contract_id=contract_id, proposer=parties[0], parties=parties,
        signed_by=list(parties), obligations=obligations, round_created=1, vote_created=1,
        status=ContractStatus.ACTIVE, contract_seq=seq, round_established=1, vote_established=1,
    )


def _ob_vote(
    obligor: str, counterparty: str, ob_type: ObligationType, round_num: int, vote_num: int,
    details: dict, ob_id: str = "OB1", contract_id: str = "C1",
) -> Obligation:
    return Obligation(
        obligation_id=ob_id, contract_id=contract_id, obligor=obligor,
        counterparty=counterparty, ob_type=ob_type, round_num=round_num, vote_num=vote_num, details=details,
    )


def _ob_round(
    obligor: str, counterparty: str, ob_type: ObligationType, round_num: int,
    details: dict, ob_id: str = "OB1", contract_id: str = "C1",
) -> Obligation:
    return Obligation(
        obligation_id=ob_id, contract_id=contract_id, obligor=obligor,
        counterparty=counterparty, ob_type=ob_type, round_num=round_num, vote_num=None, details=details,
    )


# ---------------------------------------------------------------------------
# settle_vote: 型Bの違反と部分払い（§12.3 #24）
# ---------------------------------------------------------------------------

def test_acceptance_24_type_b_violation_partial_borrow() -> None:
    """#24: 型B「R1V1はYES」でNOに投票。精算時の現金30万 → 相手方に100万。
    70万が開始後の借金。違反者名を公示"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    players["P01"] = _player("P01", cash=300_000)

    ob = _ob_vote("P01", "P02", ObligationType.TYPE_B_VOTE, 1, 1, {"vote": "YES"})
    contract = _contract("C1", 1, [ob])

    votes = {f"P{i:02d}": Vote.NO for i in range(1, 8)}
    votes.update({f"P{i:02d}": Vote.YES for i in range(8, 13)})

    logger = EventLogger()
    result = settle_vote(
        players, votes, GameConfig.default_12(), round_num=1, vote_num=1,
        consecutive_ties_before=0, logger=logger, contracts=[contract],
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


def test_acceptance_25_type_b_expires_if_eliminated_before_target_vote() -> None:
    """#25: 型B「R1V2はYES」。義務者はV1で退場 → 失効。違約金なし"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob_vote("P01", "P02", ObligationType.TYPE_B_VOTE, 1, 2, {"vote": "YES"})
    contract = _contract("C1", 1, [ob])

    # P01はV2に参加していない（退場済み想定）
    votes = {f"P{i:02d}": Vote.NO for i in range(2, 8)}
    votes.update({f"P{i:02d}": Vote.YES for i in range(8, 12)})

    logger = EventLogger()
    result = settle_vote(
        players, votes, GameConfig.default_12(), round_num=1, vote_num=2,
        consecutive_ties_before=0, logger=logger, contracts=[contract],
    )
    assert result.report.violations == []
    assert result.report.payments == []


def test_acceptance_26_type_b_expires_if_round_ends_before_target_vote() -> None:
    """#26: 型B「R1V3はNO」。ラウンドはV2で終了 → 失効。違約金なし
    （V3の精算自体が呼ばれなければ、この義務は一度も判定されない）"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob_vote("P01", "P02", ObligationType.TYPE_B_VOTE, 1, 3, {"vote": "NO"})
    contract = _contract("C1", 1, [ob])

    # V2で決着（11対1）。V3は起きない前提なので、本テストはV3を呼ばないことで
    # 「行われなかった投票の義務は判定されない」ことを確認する。
    votes_v2 = {f"P{i:02d}": Vote.YES for i in range(1, 12)}
    votes_v2["P12"] = Vote.NO
    logger = EventLogger()
    result = settle_vote(
        players, votes_v2, GameConfig.default_12(), round_num=1, vote_num=2,
        consecutive_ties_before=0, logger=logger, contracts=[contract],
    )
    assert result.outcome.round_over is True
    # V3の義務はこの投票（V2）の対象ではないため、集計に一切現れない
    assert result.report.payments == []
    assert result.report.violations == []


def test_acceptance_27_type_b_violation_applies_on_retry_vote() -> None:
    """#27: 型B「R1V1はYES」でNOに投票。V1は6対6（やり直し） → やり直しでも違反。違約金100万"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob_vote("P01", "P02", ObligationType.TYPE_B_VOTE, 1, 1, {"vote": "YES"})
    contract = _contract("C1", 1, [ob])

    votes = {f"P{i:02d}": Vote.NO for i in range(1, 7)}
    votes.update({f"P{i:02d}": Vote.YES for i in range(7, 13)})

    logger = EventLogger()
    result = settle_vote(
        players, votes, GameConfig.default_12(), round_num=1, vote_num=1,
        consecutive_ties_before=0, logger=logger, contracts=[contract],
    )
    assert result.outcome.result == "retry"
    assert result.report.violations == [("P01", "OB1")]
    assert result.report.payments[0].paid == 1_000_000


# ---------------------------------------------------------------------------
# settle_vote: 型Cのminority_side/in_minority（§12.3 #28〜#30）
# ---------------------------------------------------------------------------

def test_acceptance_28_type_c_minority_side_unmet_on_tie() -> None:
    """#28: 型C `minority_side`「R1V1の少数派がYES」。V1は6対6 → 不成立"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob_vote("P01", "P02", ObligationType.TYPE_C_CONDITIONAL, 1, 1, {
        "amount": 500_000, "condition_type": "minority_side", "condition": {"side": "YES"},
    })
    contract = _contract("C1", 1, [ob])
    votes = {f"P{i:02d}": Vote.YES for i in range(1, 7)}
    votes.update({f"P{i:02d}": Vote.NO for i in range(7, 13)})

    result = settle_vote(
        players, votes, GameConfig.default_12(), round_num=1, vote_num=1,
        consecutive_ties_before=0, logger=EventLogger(), contracts=[contract],
    )
    assert result.outcome.result == "retry"
    assert result.report.payments == []


def test_acceptance_29_type_c_in_minority_unmet_if_eliminated() -> None:
    """#29: 型C `in_minority`「R1V2でP03が少数派に入る」。P03はV1で退場 → 不成立"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob_vote("P01", "P02", ObligationType.TYPE_C_CONDITIONAL, 1, 2, {
        "amount": 500_000, "condition_type": "in_minority", "condition": {"target_player": "P03"},
    })
    contract = _contract("C1", 1, [ob])
    # P03はV2に参加していない（退場済み）
    votes = {f"P{i:02d}": Vote.YES for i in range(1, 9) if i != 3}
    votes.update({f"P{i:02d}": Vote.NO for i in range(9, 12)})

    result = settle_vote(
        players, votes, GameConfig.default_12(), round_num=1, vote_num=2,
        consecutive_ties_before=0, logger=EventLogger(), contracts=[contract],
    )
    assert result.report.payments == []


def test_acceptance_30_type_c_in_minority_met() -> None:
    """#30: 型C `in_minority`「R1V1でP03が少数派に入る」。V1は7対5でP03は5人の側 → 成立"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob_vote("P01", "P02", ObligationType.TYPE_C_CONDITIONAL, 1, 1, {
        "amount": 500_000, "condition_type": "in_minority", "condition": {"target_player": "P03"},
    })
    contract = _contract("C1", 1, [ob])
    # NO（少数派、5人）: P03・P09・P10・P11・P12 / YES（多数派、7人）: 残り
    minority_no = {"P03", "P09", "P10", "P11", "P12"}
    votes = {f"P{i:02d}": (Vote.NO if f"P{i:02d}" in minority_no else Vote.YES) for i in range(1, 13)}

    result = settle_vote(
        players, votes, GameConfig.default_12(), round_num=1, vote_num=1,
        consecutive_ties_before=0, logger=EventLogger(), contracts=[contract],
    )
    assert "P03" in result.outcome.remaining_ids
    payment = result.report.payments[0]
    assert payment.promised == 500_000 and payment.paid == 500_000


# ---------------------------------------------------------------------------
# settle_round: wins_round・型A・割合（§12.3 #31〜#36）
# ---------------------------------------------------------------------------

def test_acceptance_31_type_c_wins_round_fixed_amount() -> None:
    """#31: 型C `wins_round` 固定額「R1でP01が勝ち残ったらP01がP02へ300万」。
    P01が1人で勝ち残る → R1のラウンドの精算で300万を支払う"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob_round("P01", "P02", ObligationType.TYPE_C_CONDITIONAL, 1, {
        "amount": 3_000_000, "condition_type": "wins_round", "condition": {"target_player": "P01"},
    })
    contract = _contract("C1", 1, [ob])

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=12_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    assert result.outcome.winner_ids == ["P01"]
    assert result.outcome.payout_per_winner == 12_000_000
    payment = [p for p in result.report.payments if p.ob_type == ObligationType.TYPE_C_CONDITIONAL][0]
    assert payment.paid == 3_000_000
    assert result.players["P02"].cash == players["P02"].cash + 3_000_000


def test_acceptance_32_type_c_wins_round_share_percent() -> None:
    """#32: 型C `wins_round` 割合25%。義務者P01が2人勝ちで600万を受け取る → 150万を支払う"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob_round("P01", "P07", ObligationType.TYPE_C_CONDITIONAL, 1, {
        "share_percent": 25, "condition_type": "wins_round", "condition": {"target_player": "P01"},
    })
    contract = _contract("C1", 1, [ob], parties=["P01", "P07"])

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01", "P02"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=12_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    assert result.outcome.payout_per_winner == 6_000_000
    payment = result.report.payments[0]
    assert payment.promised == 1_500_000 and payment.paid == 1_500_000


def test_acceptance_33_type_c_share_percent_stacking() -> None:
    """#33: P01が割合50%の契約を3本持ち、1人勝ちで1,200万を受け取る。
    ほかの現金0、残り枠880万 → 計1,800万を支払う。現金0、開始後の借金600万"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    players["P01"] = _player("P01", cash=0, debt_pre=1_200_000, debt_post=0)  # 残り枠880万

    obs = [
        _ob_round("P01", f"P{i:02d}", ObligationType.TYPE_C_CONDITIONAL, 1, {
            "share_percent": 50, "condition_type": "wins_round", "condition": {"target_player": "P01"},
        }, ob_id=f"OB{i}", contract_id=f"C{i}")
        for i in (2, 3, 4)
    ]
    contracts = [_contract(f"C{i+2}", i + 1, [obs[i]], parties=["P01", f"P{i+2:02d}"]) for i in range(3)]

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=12_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=contracts,
    )
    assert result.outcome.payout_per_winner == 12_000_000
    total_paid = sum(p.paid for p in result.report.payments)
    assert total_paid == 18_000_000
    p01 = result.players["P01"]
    assert p01.cash == 0
    assert p01.debt_post == 6_000_000


def test_acceptance_34_type_c_share_percent_stacking_with_shortfall() -> None:
    """#34: 33と同じ条件で、残り枠が300万 → 支払える上限は1,500万。
    成立順に600万・600万・300万を支払い、3本目の残り300万は取りはぐれ"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    players["P01"] = _player("P01", cash=0, debt_pre=7_000_000, debt_post=0)  # 残り枠300万

    obs = [
        _ob_round("P01", f"P{i:02d}", ObligationType.TYPE_C_CONDITIONAL, 1, {
            "share_percent": 50, "condition_type": "wins_round", "condition": {"target_player": "P01"},
        }, ob_id=f"OB{i}", contract_id=f"C{i}")
        for i in (2, 3, 4)
    ]
    contracts = [_contract(f"C{i+2}", i + 1, [obs[i]], parties=["P01", f"P{i+2:02d}"]) for i in range(3)]

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=12_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=contracts,
    )
    assert result.outcome.payout_per_winner == 12_000_000
    paid_in_order = [p.paid for p in result.report.payments]
    assert paid_in_order == [6_000_000, 6_000_000, 3_000_000]
    assert result.report.shortfall_ids == ["P01"]


def test_acceptance_35_aborted_round_skips_wins_round_but_pays_type_a() -> None:
    """#35: 打ち切りのラウンドに、wins_roundの契約と型Aがある
    → wins_roundは不成立。型Aは支払う"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob_wins = _ob_round("P01", "P02", ObligationType.TYPE_C_CONDITIONAL, 1, {
        "amount": 500_000, "condition_type": "wins_round", "condition": {"target_player": "P01"},
    }, ob_id="OB1", contract_id="C1")
    ob_a = _ob_round("P03", "P04", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 200_000}, ob_id="OB1", contract_id="C2")
    contracts = [
        _contract("C1", 1, [ob_wins], parties=["P01", "P02"]),
        _contract("C2", 2, [ob_a], parties=["P03", "P04"]),
    ]

    v1 = _make_vote_outcome(round_num=1, vote_num=3, remaining_ids=[f"P{i:02d}" for i in range(1, 13)], result="abort")
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=15_600_000, aborted=True, logger=EventLogger(),
        is_final_round=False, contracts=contracts,
    )
    assert result.outcome.winner_ids == []
    assert result.outcome.carryover_out == 15_600_000
    payments = {p.contract_id: p for p in result.report.payments}
    assert "C1" not in payments  # wins_roundは不成立
    assert payments["C2"].paid == 200_000


def test_acceptance_37_type_a_paid_at_round_end_not_mid_round() -> None:
    """#37: 型A「R2に200万」 → R2のラウンドの精算で支払う。R2の途中の投票では支払わない"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    ob = _ob_round("P01", "P02", ObligationType.TYPE_A_PAYMENT, 2, {"amount": 2_000_000})
    contract = _contract("C1", 1, [ob])

    # R2V1の投票の精算では支払われない（型Aはvote_num=Noneのため、settle_voteの
    # 収集対象には含まれない）
    v1_result = settle_vote(
        players, _all_yes_votes(), GameConfig.default_12(), round_num=2, vote_num=1,
        consecutive_ties_before=0, logger=EventLogger(), contracts=[contract],
    )
    assert v1_result.report.payments == []

    v1 = _make_vote_outcome(round_num=2, vote_num=1, remaining_ids=["P01"])
    round_result = settle_round(
        players, GameConfig.default_12(), round_num=2, votes_in_round=[v1],
        carryover_in=0, pot_final=12_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    assert round_result.report.payments[0].paid == 2_000_000


# ---------------------------------------------------------------------------
# 受け渡しの一斉反映（§12.3 #38〜#42）
# ---------------------------------------------------------------------------

def test_acceptance_38_no_debt_when_credit_sufficient() -> None:
    """#38: 同じ決済で200万受け取り・150万支払い。現金0、残り枠は十分
    → 借金は発生しない。現金50万"""
    players = {
        "P01": _player("P01", cash=0),
        "P02": _player("P02", cash=1_000_000),
    }
    ob_recv = _ob_round("P02", "P01", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 2_000_000}, ob_id="OB1", contract_id="C1")
    ob_pay = _ob_round("P01", "P02", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 1_500_000}, ob_id="OB1", contract_id="C2")
    contracts = [
        _contract("C1", 1, [ob_recv], parties=["P01", "P02"]),
        _contract("C2", 2, [ob_pay], parties=["P01", "P02"]),
    ]
    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=0, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=contracts,
    )
    # P01は山を受け取らない（勝ち残りはP01だがpot_final=0なので受取額0）。
    # 代わりにC1経由で200万を受け取り、150万を支払う。
    assert result.players["P01"].cash == 500_000
    assert result.players["P01"].debt_post == 0


def test_acceptance_39_receivable_not_usable_as_funding_source() -> None:
    """#39: 同じ決済で200万受け取り・150万支払い。現金0、残り枠0
    → 支払いは0。150万は取りはぐれ。現金200万
    （「支払える上限の固定」は同じ決済の受け取りを元手にしない、§7.4手順4）"""
    players = {
        "P01": _player("P01", cash=0, debt_pre=10_000_000, debt_post=0),  # 残り枠0
        "P02": _player("P02", cash=1_000_000),
    }
    ob_recv = _ob_round("P02", "P01", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 2_000_000}, ob_id="OB1", contract_id="C1")
    ob_pay = _ob_round("P01", "P02", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 1_500_000}, ob_id="OB1", contract_id="C2")
    contracts = [
        _contract("C1", 1, [ob_recv], parties=["P01", "P02"]),
        _contract("C2", 2, [ob_pay], parties=["P01", "P02"]),
    ]
    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=[])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=0, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=contracts,
    )
    pay_payment = [p for p in result.report.payments if p.contract_id == "C2"][0]
    assert pay_payment.paid == 0
    assert result.players["P01"].cash == 2_000_000
    assert "P01" in result.report.shortfall_ids


def test_acceptance_40_payable_limit_split_by_contract_seq() -> None:
    """#40: 支払える上限400万。成立順1でP02へ300万、成立順5でP08へ300万
    → P02に300万、P08に100万。200万は取りはぐれ"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=4_000_000) for i in range(1, 13)}
    players["P01"] = _player("P01", cash=4_000_000, debt_pre=10_000_000, debt_post=0)  # 残り枠0 → 上限=現金400万
    ob1 = _ob_round("P01", "P02", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 3_000_000}, ob_id="OB1", contract_id="C1")
    ob5 = _ob_round("P01", "P08", ObligationType.TYPE_A_PAYMENT, 1, {"amount": 3_000_000}, ob_id="OB1", contract_id="C5")
    contracts = [
        _contract("C1", 1, [ob1], parties=["P01", "P02"]),
        _contract("C5", 5, [ob5], parties=["P01", "P08"]),
    ]
    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=[])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=0, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=contracts,
    )
    by_contract = {p.contract_id: p.paid for p in result.report.payments}
    assert by_contract["C1"] == 3_000_000
    assert by_contract["C5"] == 1_000_000
    assert result.report.shortfall_ids == ["P01"]


def test_acceptance_42_partial_type_b_penalty_before_type_c_same_vote() -> None:
    """#42（v0.4.1 §12.3）: 支払える上限100万。成立順1の型B違約金100万と、
    成立順7の型C（同じ投票が対象）100万が同じ精算 → 成立順1の違約金100万
    だけ支払う。成立順7は0円

    v0.4のサイクル4.0では型Aがラウンドの精算（§7.4）、型Bの違約金が投票の
    精算（§7.3）で別々の決済になるため両者が同じ精算に来ないことを指摘し、
    「同じ投票が対象の型C」に読み替えて検証していた。v0.4.1 §11.6 #1で
    仕様書の本文そのものがこの形に直ったため、現在は読み替えではなく
    仕様書の記述どおりの検証である。
    """
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 13)}
    players["P01"] = _player("P01", cash=1_000_000, debt_pre=10_000_000, debt_post=0)  # 残り枠0 → 上限=現金100万

    ob_b = _ob_vote("P01", "P02", ObligationType.TYPE_B_VOTE, 1, 1, {"vote": "YES"}, ob_id="OB1", contract_id="C1")
    ob_c = _ob_vote("P01", "P03", ObligationType.TYPE_C_CONDITIONAL, 1, 1, {
        "amount": 1_000_000, "condition_type": "in_minority", "condition": {"target_player": "P01"},
    }, ob_id="OB1", contract_id="C7")
    contracts = [
        _contract("C1", 1, [ob_b], parties=["P01", "P02"]),
        _contract("C7", 7, [ob_c], parties=["P01", "P03"]),
    ]
    # P01がNOに投票（YES指定の違反）し、少数派（in_minority）になる票構成
    votes = {f"P{i:02d}": Vote.YES for i in range(2, 12)}
    votes["P01"] = Vote.NO
    votes["P12"] = Vote.NO

    result = settle_vote(
        players, votes, GameConfig.default_12(), round_num=1, vote_num=1,
        consecutive_ties_before=0, logger=EventLogger(), contracts=contracts,
    )
    assert result.report.violations == [("P01", "OB1")]
    by_contract = {p.contract_id: p.paid for p in result.report.payments}
    assert by_contract["C1"] == 1_000_000
    assert by_contract["C7"] == 0


# ---------------------------------------------------------------------------
# ヘルパ: ラウンド精算テスト用の最小限のVoteOutcome
# ---------------------------------------------------------------------------

def _make_vote_outcome(
    round_num: int, vote_num: int, remaining_ids: list[str], result: str = "decisive",
) -> "object":
    from engine.models import VoteOutcome
    return VoteOutcome(
        round_num=round_num, vote_num=vote_num, yes_ids=[], no_ids=[],
        result=result, eliminated_ids=[], remaining_ids=remaining_ids,
        consecutive_ties_before=0, consecutive_ties_after=0,
        extension_fee_collected=0, round_over=True,
    )
