"""
受け入れテスト#69〜#78（v0.5 §12.3、サイクル5.0）

型Cに4つ目の条件「山分け」（pool_share）を足したことの確認。#69〜#76は
ラウンドの精算（settle_round）を直接呼ぶ単体テスト（tests/test_settlement_v0_4.py
の#31〜#34と同じ形）。#77・#78は提案の形式検証（validate_terms）を直接呼ぶ
単体テスト（tests/test_contracts.py の#36と同じ形）。

最後に、提案→署名→精算→本人に届く文面（義務の表示とお金の出入り）までが
Game経由で通ることを確かめる1本（tests/test_acceptance_58_to_68.py の#60と
同じ形）を足す（受け入れ番号の対象外、実装の配線そのものの確認）。
"""

from __future__ import annotations

from engine.config import GameConfig
from engine.contracts import TermRejectReason, validate_terms
from engine.events import EventLogger
from engine.game import Game
from engine.models import (
    Contract, ContractProposeAction, ContractStatus, Obligation, ObligationType, PlayerState, Vote,
    VoteOutcome,
)
from engine.settlement import settle_round
from llm.prompt_builder import build_negotiation_prompt
from tests.helpers import ScriptedAgent, VisibleStateRecordingAgent, make_roster


def _player(pid: str, cash: int, debt_pre: int = 0, debt_post: int = 0) -> PlayerState:
    return PlayerState(player_id=pid, cash=cash, debt_pre=debt_pre, debt_post=debt_post, initial_loan=1_200_000)


def _pool_share_ob(
    share_percent: int, ob_id: str = "OB1", contract_id: str = "C1",
) -> Obligation:
    """山分け（pool_share）の義務（obligor/counterparty/round_numを持たない、§6.4）"""
    return Obligation(
        obligation_id=ob_id, contract_id=contract_id, ob_type=ObligationType.TYPE_C_CONDITIONAL,
        details={"share_percent": share_percent, "condition_type": "pool_share"},
    )


def _contract(
    contract_id: str, seq: int, obligations: list[Obligation], parties: list[str],
    round_established: int = 1,
) -> Contract:
    return Contract(
        contract_id=contract_id, proposer=parties[0], parties=parties,
        signed_by=list(parties), obligations=obligations, round_created=1, vote_created=1,
        status=ContractStatus.ACTIVE, contract_seq=seq,
        round_established=round_established, vote_established=1,
    )


def _make_vote_outcome(round_num: int, vote_num: int, remaining_ids: list[str]) -> VoteOutcome:
    return VoteOutcome(
        round_num=round_num, vote_num=vote_num, yes_ids=[], no_ids=[],
        result="decisive", eliminated_ids=[], remaining_ids=remaining_ids,
        consecutive_ties_before=0, consecutive_ties_after=0,
        extension_fee_collected=0, round_over=True,
    )


# ---------------------------------------------------------------------------
# settle_round: 山分け（pool_share、§12.3 #69〜#76）
# ---------------------------------------------------------------------------

def test_acceptance_69_pool_share_single_winner() -> None:
    """#69: 4人（P03・P05・P08・P09）がX=60の山分けを結んでいる。P03が1人で
    勝ち残り、2,520万を受け取る → P03は1,512万を払い、P05・P08・P09が
    504万ずつ受け取る。P03の手元には山のうち1,008万が残る"""
    players = {pid: _player(pid, cash=1_000_000) for pid in ("P03", "P05", "P08", "P09")}
    ob = _pool_share_ob(60)
    contract = _contract("C1", 1, [ob], parties=["P03", "P05", "P08", "P09"])

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P03"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=25_200_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    assert result.players["P03"].cash == 1_000_000 + 25_200_000 - 15_120_000
    for pid in ("P05", "P08", "P09"):
        assert result.players[pid].cash == 1_000_000 + 5_040_000


def test_acceptance_70_pool_share_two_mutual_winners() -> None:
    """#70: 4人組（X=60）のうちP01とP02が2人で勝ち残り、660万ずつ受け取る
    → P01・P02はそれぞれ396万を払い、ほかの3人に132万ずつ渡す。精算の後、
    P01・P02の増えた額は396万ずつ、ほかの2人は264万ずつ"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=1_000_000) for i in range(1, 5)}
    ob = _pool_share_ob(60)
    contract = _contract("C1", 1, [ob], parties=["P01", "P02", "P03", "P04"])

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01", "P02"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=13_200_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    for pid in ("P01", "P02"):
        assert result.players[pid].cash - players[pid].cash == 3_960_000
    for pid in ("P03", "P04"):
        assert result.players[pid].cash - players[pid].cash == 2_640_000


def test_acceptance_71_eliminated_party_still_receives() -> None:
    """#71: 山分けの当事者P05が、そのラウンドで退場している。ほかの当事者が
    山を受け取る → P05も分け前を受け取る"""
    players = {pid: _player(pid, cash=0) for pid in ("P01", "P05", "P08", "P09")}
    ob = _pool_share_ob(60)
    contract = _contract("C1", 1, [ob], parties=["P01", "P05", "P08", "P09"])

    # P05はこの投票のremaining_idsに含まれない（退場済み）。P01が1人で勝ち残る
    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=12_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    assert result.players["P05"].cash == 2_400_000


def test_acceptance_72_aborted_round_no_pool_share_payment() -> None:
    """#72: 山分けが成立したラウンドが打ち切りになる → そのラウンドの
    山分けの支払いは起きない"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=0) for i in range(1, 5)}
    ob = _pool_share_ob(60)
    contract = _contract("C1", 1, [ob], parties=["P01", "P02", "P03", "P04"])

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01", "P02", "P03", "P04"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=12_000_000, aborted=True, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    assert result.outcome.winner_ids == []
    for pid in ("P01", "P02", "P03", "P04"):
        assert result.players[pid].cash == 0


def test_acceptance_73_pool_share_established_at_r2_r1_receipt_not_subject() -> None:
    """#73: R2に成立した山分けの当事者が、R1で山を受け取っていた → R1の
    受け取りは対象にならない。R2以降の受け取りだけが対象"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=0) for i in range(1, 5)}
    ob = _pool_share_ob(60)
    contract = _contract("C1", 1, [ob], parties=["P01", "P02", "P03", "P04"], round_established=2)

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01"])
    result_r1 = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=12_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    # R1の受け取りは対象外（成立前）: P01が全額を保持し、ほかの3人は0円のまま
    assert result_r1.players["P01"].cash == 12_000_000
    for pid in ("P02", "P03", "P04"):
        assert result_r1.players[pid].cash == 0

    v2 = _make_vote_outcome(round_num=2, vote_num=1, remaining_ids=["P01"])
    result_r2 = settle_round(
        result_r1.players, GameConfig.default_12(), round_num=2, votes_in_round=[v2],
        carryover_in=0, pot_final=12_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    # R2の受け取りは対象（成立後）
    assert result_r2.players["P01"].cash == 12_000_000 + 12_000_000 - 7_200_000
    for pid in ("P02", "P03", "P04"):
        assert result_r2.players[pid].cash == 2_400_000


def test_acceptance_74_pool_share_stacking_two_contracts_causes_debt() -> None:
    """#74: P05が2つの山分け（どちらも4人・X=60）に入り、1人勝ちで1,200万を
    受け取る。ほかの現金0、残り枠880万 → 720万ずつ計1,440万を、契約の成立順に
    払う。現金0、開始後の借金240万"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=0, debt_pre=1_200_000) for i in range(1, 9)}
    players["P05"] = _player("P05", cash=0, debt_pre=1_200_000)
    ob_a = _pool_share_ob(60, ob_id="OBA", contract_id="CA")
    ob_b = _pool_share_ob(60, ob_id="OBB", contract_id="CB")
    contract_a = _contract("CA", 1, [ob_a], parties=["P05", "P02", "P03", "P04"])
    contract_b = _contract("CB", 2, [ob_b], parties=["P05", "P06", "P07", "P08"])

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P05"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=12_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract_a, contract_b],
    )
    total_paid = sum(p.paid for p in result.report.payments)
    assert total_paid == 14_400_000
    p05 = result.players["P05"]
    assert p05.cash == 0
    assert p05.debt_post == 2_400_000
    for pid in ("P02", "P03", "P04", "P06", "P07", "P08"):
        assert result.players[pid].cash == 2_400_000


def test_acceptance_75_received_pool_share_not_re_split_or_used_as_funding() -> None:
    """#75: P01が、山分けでP02から分け前を受け取る。P01は別の山分けにも
    入っている → 受け取った分け前は、別の山分けの対象にならない。支払いの
    元手にもならない"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=0) for i in (1, 2, 3, 4, 9)}
    ob_x = _pool_share_ob(50, ob_id="OBX", contract_id="CX")
    ob_y = _pool_share_ob(60, ob_id="OBY", contract_id="CY")
    contract_x = _contract("CX", 1, [ob_x], parties=["P02", "P01"])
    contract_y = _contract("CY", 2, [ob_y], parties=["P01", "P03", "P04", "P09"])

    # P02が1人で勝ち残り、山2,000,000を受け取る。CXでP01へ半分(1,000,000)を渡す。
    # P01はCYの当事者でもあるが、山そのものを受け取ったわけではないので、
    # CYの支払いは発生しない（対象は「山の受け取り」だけ、§6.4）
    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P02"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=2_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract_x, contract_y],
    )
    assert result.players["P01"].cash == 1_000_000
    for pid in ("P03", "P04", "P09"):
        assert result.players[pid].cash == 0


def test_acceptance_76_pool_share_floor_division() -> None:
    """#76: 受取額1,000,000円、X=50、当事者4人 → ほかの3人に166,666円ずつ
    払う（1円未満は切り捨て）"""
    players = {f"P{i:02d}": _player(f"P{i:02d}", cash=0) for i in range(1, 5)}
    ob = _pool_share_ob(50)
    contract = _contract("C1", 1, [ob], parties=["P01", "P02", "P03", "P04"])

    v1 = _make_vote_outcome(round_num=1, vote_num=1, remaining_ids=["P01"])
    result = settle_round(
        players, GameConfig.default_12(), round_num=1, votes_in_round=[v1],
        carryover_in=0, pot_final=1_000_000, aborted=False, logger=EventLogger(),
        is_final_round=False, contracts=[contract],
    )
    for pid in ("P02", "P03", "P04"):
        assert result.players[pid].cash == 166_666
    assert result.players["P01"].cash == 1_000_000 - 3 * 166_666


# ---------------------------------------------------------------------------
# validate_terms: 山分けの形式検査（§12.3 #77〜#78）
# ---------------------------------------------------------------------------

def test_acceptance_77_pool_share_duplicated_rejected() -> None:
    """#77-a: 1つの契約にpool_shareが2本ある → 提案は不成立"""
    term = {"ob_type": "type_c_conditional", "details": {"share_percent": 50, "condition_type": "pool_share"}}
    error = validate_terms(
        [term, term], {"P01", "P02", "P03"}, {"P01", "P02", "P03"}, round_num=1, vote_num=1, num_rounds=4,
    )
    assert error is not None and error.code == TermRejectReason.POOL_SHARE_DUPLICATED


def test_acceptance_77_pool_share_amount_rejected() -> None:
    """#77-b: pool_shareにamountを指定した → 提案は不成立"""
    term = {
        "ob_type": "type_c_conditional",
        "details": {"share_percent": 50, "amount": 100, "condition_type": "pool_share"},
    }
    error = validate_terms(
        [term], {"P01", "P02", "P03"}, {"P01", "P02", "P03"}, round_num=1, vote_num=1, num_rounds=4,
    )
    assert error is not None and error.code == TermRejectReason.POOL_SHARE_AMOUNT_NOT_ALLOWED


def test_acceptance_77_pool_share_share_percent_zero_rejected() -> None:
    """#77-c: share_percentが0 → 提案は不成立"""
    term = {"ob_type": "type_c_conditional", "details": {"share_percent": 0, "condition_type": "pool_share"}}
    error = validate_terms(
        [term], {"P01", "P02", "P03"}, {"P01", "P02", "P03"}, round_num=1, vote_num=1, num_rounds=4,
    )
    assert error is not None and error.code == TermRejectReason.SHARE_PERCENT_OUT_OF_RANGE


def test_acceptance_77_pool_share_share_percent_101_rejected() -> None:
    """#77-d: share_percentが101 → 提案は不成立"""
    term = {"ob_type": "type_c_conditional", "details": {"share_percent": 101, "condition_type": "pool_share"}}
    error = validate_terms(
        [term], {"P01", "P02", "P03"}, {"P01", "P02", "P03"}, round_num=1, vote_num=1, num_rounds=4,
    )
    assert error is not None and error.code == TermRejectReason.SHARE_PERCENT_OUT_OF_RANGE


def test_acceptance_78_pool_share_ignores_obligor_counterparty_round_num() -> None:
    """#78: pool_shareの項にobligor・counterparty・round_numが書かれている
    → それらを無視して受け付ける。当事者全員の山分けとして成立する"""
    term = {
        "obligor": "P01", "counterparty": "P02", "round_num": 1, "vote_num": 1,
        "ob_type": "type_c_conditional",
        "details": {
            "share_percent": 60, "condition_type": "pool_share",
            "condition": {"target_player": "P01"},
        },
    }
    error = validate_terms(
        [term], {"P01", "P02", "P03"}, {"P01", "P02", "P03"}, round_num=1, vote_num=1, num_rounds=4,
    )
    assert error is None


# ---------------------------------------------------------------------------
# Game経由の配線確認（提案→署名→精算→本人に届く文面。受け入れ番号の対象外）
# ---------------------------------------------------------------------------

def test_pool_share_full_game_wiring_propose_sign_settle_and_prompt() -> None:
    """
    4人組（P01・P02・P03・P04、X=60）が山分けを結び、P01が1人で勝ち残る試合を
    Game経由で通す。精算後のお金の出入りの記録・自分が当事者の契約の表示・
    財務通知のmy_pool_sharesが崩れずに渡り、プロンプトの描画も例外を出さない
    ことを確かめる。
    """
    V1_YES = {"P01"}
    votes_by_vote = {
        (1, 1): {pid: (Vote.YES if pid in V1_YES else Vote.NO) for pid in [f"P{i:02d}" for i in range(1, 13)]},
    }
    agents = make_roster(votes_by_vote, num_players=12)
    agents["P01"] = ScriptedAgent(
        "P01", votes=agents["P01"].votes, loan=1_200_000,
        negotiate_actions={
            (1, 1, 1): ContractProposeAction(
                player_id="P01", with_players=["P02", "P03", "P04"],
                terms=[{"ob_type": "type_c_conditional",
                        "details": {"share_percent": 60, "condition_type": "pool_share"}}],
            ),
        },
    )
    agents["P02"].sign_proposer_at[(1, 1, 2)] = "P01"
    agents["P03"].sign_proposer_at[(1, 1, 2)] = "P01"
    agents["P04"].sign_proposer_at[(1, 1, 2)] = "P01"

    rec01 = VisibleStateRecordingAgent(agents["P01"])
    rec02 = VisibleStateRecordingAgent(agents["P02"])
    agents["P01"], agents["P02"] = rec01, rec02

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=6900, logger=EventLogger())
    game.run()

    round_end_01 = next(s[3] for s in rec01.reflect_snapshots if s[0] == 1 and s[2] is True)
    payments_01 = [
        e for r in round_end_01["my_cash_flow"]["rounds"] for e in r["entries"]
        if e["kind"] == "contract_payment_paid" and e.get("condition_type") == "pool_share"
    ]
    assert len(payments_01) == 3  # P02・P03・P04への3本

    round_end_02 = next(s[3] for s in rec02.reflect_snapshots if s[0] == 1 and s[2] is True)
    received_02 = [
        e for r in round_end_02["my_cash_flow"]["rounds"] for e in r["entries"]
        if e["kind"] == "contract_payment_received" and e.get("condition_type") == "pool_share"
    ]
    assert len(received_02) == 1

    # 自分が当事者の契約の表示（山分けは常にPENDING表示、§6.4）
    contract_status = [c for c in round_end_01["my_contract_status"] if c["contract_id"]]
    assert contract_status, "山分けの契約がmy_contract_statusに出ていない"
    pool_share_obs = [
        ob for c in contract_status for ob in c["obligations"]
        if ob["details"].get("condition_type") == "pool_share"
    ]
    assert pool_share_obs and all(ob["status"] == "pending" for ob in pool_share_obs)

    # プロンプトの描画: 当事者P01の文面には「山分け」の語が実際に出る
    vs_p01 = game._build_visible_state(1, 1, for_player_id="P01")
    prompt_p01 = build_negotiation_prompt(
        PlayerState(player_id="P01", cash=0, initial_loan=0), 1, 1, 1, vs_p01, config,
    )
    assert "山分け" in prompt_p01.text

    # 当事者でないP12の文面は、例外を出さずに描画できる（契約は非公開のため
    # 「山分け」が出なくてもよい。クラッシュしないことが主眼）
    vs_p12 = game._build_visible_state(1, 1, for_player_id="P12")
    build_negotiation_prompt(
        PlayerState(player_id="P12", cash=0, initial_loan=0), 1, 1, 1, vs_p12, config,
    )
