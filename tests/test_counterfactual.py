"""
sim.counterfactual.type_b_obligation_gain() のテスト（計画§4のテスト）

手計算した例で、「ほかの人の票が同じだったとして、破っていたら得だったか」の
計算（違約金を引く前）が正しいことを確認する。試合は回さず、
engine.minority.resolve_minority()（ルールエンジンそのもの）を直接呼んで
RoundSummaryを組み立てる。
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="sim.counterfactualのv0.4対応（投票単位・ラウンド単位の二重精算）はサイクル4.1",
)

from engine.config import GameConfig
from engine.models import Obligation, ObligationType, Vote
from sim.counterfactual import RoundSummary, resolve_minority, type_b_obligation_gain
from sim.metrics import PENALTY_AMOUNTS


def _votes(n_yes: int, n_no: int) -> dict[str, Vote]:
    votes: dict[str, Vote] = {}
    i = 1
    for _ in range(n_yes):
        votes[f"P{i:02d}"] = Vote.YES
        i += 1
    for _ in range(n_no):
        votes[f"P{i:02d}"] = Vote.NO
        i += 1
    return votes


def _round_summary(votes: dict[str, Vote], config: GameConfig, carryover_before: int, round_num: int) -> RoundSummary:
    outcome = resolve_minority(
        votes, config, carryover_before=carryover_before, round_num=round_num, is_final_round=False,
    )
    return RoundSummary(round_num=round_num, votes=votes, minority_outcome=outcome, interest_total=0)


def _obligation(obligor: str, vote: str, round_num: int) -> Obligation:
    return Obligation(
        obligation_id="C_TEST_OB01", contract_id="C_TEST", obligor=obligor, counterparty="P12",
        ob_type=ObligationType.TYPE_B_VOTE, round_num=round_num, details={"vote": vote},
    )


def test_gain_breaking_from_majority_into_minority_is_positive() -> None:
    """8YES-4NO、義務者は多数派のYESを約束。破ってNOに回ると少数派入りで得"""
    config = GameConfig.default_12()
    votes = _votes(8, 4)
    rs = _round_summary(votes, config, carryover_before=0, round_num=1)
    ob = _obligation("P01", "YES", round_num=1)

    gain = type_b_obligation_gain(ob, rs, config, is_final_round=False)

    # obey=YES(多数派)→受取0。break=NOに回ると7YES-5NO、少数派NO(5人)。
    # pool=7*10万=70万、配当=70万//5=14万、受取=参加費10万+14万=24万
    assert gain == 240_000
    # 24万はどの違約金額(30万/50万/100万/200万)よりも小さいので、
    # どの額でも「破った方が得」ではない
    for penalty in PENALTY_AMOUNTS:
        assert not (gain > penalty)


def test_gain_breaking_out_of_minority_is_negative() -> None:
    """8YES-4NO、義務者は少数派のNOを約束。破ってYESに回ると多数派落ちで損"""
    config = GameConfig.default_12()
    votes = _votes(8, 4)
    rs = _round_summary(votes, config, carryover_before=0, round_num=1)
    ob = _obligation("P09", "NO", round_num=1)

    gain = type_b_obligation_gain(ob, rs, config, is_final_round=False)

    # obey=NO(少数派4人)→pool=8*10万=80万、配当=80万//4=20万、受取=10万+20万=30万
    # break=YESに回ると9YES-3NO、自分は多数派→受取0
    assert gain == -300_000


def test_gain_breaking_creates_no_minority_tie_is_zero() -> None:
    """7YES-5NO、義務者は多数派のYESを約束。破ってNOに回ると6対6で少数派なし"""
    config = GameConfig.default_12()
    votes = _votes(7, 5)
    rs = _round_summary(votes, config, carryover_before=0, round_num=1)
    ob = _obligation("P01", "YES", round_num=1)

    gain = type_b_obligation_gain(ob, rs, config, is_final_round=False)

    # obey=YES(多数派)→受取0。break=NOに回ると6対6で少数派なし→自分の受取も0
    assert gain == 0


def test_gain_with_nonzero_carryover() -> None:
    """8YES-4NO・持ち越し40万あり、義務者は少数派のNOを約束。破ると多数派落ちで損"""
    config = GameConfig.default_12()
    votes = _votes(8, 4)
    rs = _round_summary(votes, config, carryover_before=400_000, round_num=1)
    ob = _obligation("P09", "NO", round_num=1)

    gain = type_b_obligation_gain(ob, rs, config, is_final_round=False)

    # obey=NO(少数派4人)→pool=8*10万+40万=120万、配当=120万//4=30万、受取=10万+30万=40万
    # break=YESに回ると9YES-3NOで多数派→受取0
    assert gain == -400_000
