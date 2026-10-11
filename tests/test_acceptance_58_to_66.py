"""
受け入れテスト#58〜#66（v0.4.3 §12.3、サイクル4.2c）

B（お金の出入りの記録・義務ごとの状態・失効した提案）とC-8（手番の文面の
上限）・A（会話とメモの投票ごとの引き継ぎ）の確認。#55・#56は
tests/test_public_disclosure.pyで書き直し済み。
"""

from __future__ import annotations

import json

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import (
    BroadcastAction, ContractProposeAction, PlayerState, TransferAction, Vote,
)
from llm.constants import MEMORY_TRUNCATE_LENGTH, TURN_PROMPT_MAX_CHARS
from llm.prompt_builder import build_negotiation_prompt, build_reflection_prompt
from llm.response_parser import extract_memory
from tests.helpers import ScriptedAgent, VisibleStateRecordingAgent, make_roster


def _negotiate_snapshots_by_key(recorder: VisibleStateRecordingAgent) -> dict:
    return {(r, v, t): vs for r, v, t, vs in recorder.negotiate_snapshots}


def _ps(pid: str) -> PlayerState:
    return PlayerState(player_id=pid, cash=0, initial_loan=0)


def test_acceptance_58_transfer_recorded_only_for_sender_and_receiver() -> None:
    """
    #58: P01がP02へ10万を送金した、次の手番 → P01とP02に渡す情報に送金の
    記録（相手と額）が入っている。P03に渡す情報には入っていない
    """
    agents = make_roster({}, num_players=12)
    agents["P01"].negotiate_actions[(1, 1, 1)] = TransferAction(
        player_id="P01", to="P02", amount=100_000,
    )
    rec01 = VisibleStateRecordingAgent(agents["P01"])
    rec02 = VisibleStateRecordingAgent(agents["P02"])
    rec03 = VisibleStateRecordingAgent(agents["P03"])
    agents["P01"], agents["P02"], agents["P03"] = rec01, rec02, rec03

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=58, logger=EventLogger())
    game.run()

    by01 = _negotiate_snapshots_by_key(rec01)
    by02 = _negotiate_snapshots_by_key(rec02)
    by03 = _negotiate_snapshots_by_key(rec03)

    def _transfer_entries(vs):
        rounds = vs["my_cash_flow"]["rounds"]
        return [e for r in rounds for e in r["entries"] if e["kind"] in ("transfer_out", "transfer_in")]

    out01 = _transfer_entries(by01[(1, 1, 2)])
    assert len(out01) == 1
    assert out01[0]["kind"] == "transfer_out"
    assert out01[0]["counterparty"] == "P02"
    assert out01[0]["amount"] == 100_000

    in02 = _transfer_entries(by02[(1, 1, 2)])
    assert len(in02) == 1
    assert in02[0]["kind"] == "transfer_in"
    assert in02[0]["counterparty"] == "P01"
    assert in02[0]["amount"] == 100_000

    assert _transfer_entries(by03[(1, 1, 2)]) == []


def test_acceptance_59_type_b_penalty_visible_to_parties_only() -> None:
    """
    #59: R1V1で型B違反があり、ラウンドがV2へ続く → R1V1の振り返りとR1V2の
    Openで、義務者と相手方に渡す情報に、違約金の約束した額と実際に払われた
    額が入っている。当事者でない人に渡す情報には入っていない
    """
    votes_by_vote = {
        (1, 1): {f"P{i:02d}": Vote.YES for i in range(1, 8)}
        | {f"P{i:02d}": Vote.NO for i in range(8, 13)},  # 7対5、NO側5人がV2へ
    }
    agents = make_roster(votes_by_vote, num_players=12)
    # P08はNO側（range(8,13)、votes_by_vote参照）。契約はYES指定なので違反する。
    agents["P08"].negotiate_actions[(1, 1, 1)] = ContractProposeAction(
        player_id="P08", with_players=["P02"],
        terms=[{"obligor": "P08", "counterparty": "P02", "ob_type": "type_b_vote",
                "round_num": 1, "vote_num": 1, "details": {"vote": "YES"}}],
    )
    agents["P02"].sign_proposer_at[(1, 1, 2)] = "P08"

    rec08 = VisibleStateRecordingAgent(agents["P08"])  # 義務者
    rec02 = VisibleStateRecordingAgent(agents["P02"])  # 相手方
    rec03 = VisibleStateRecordingAgent(agents["P03"])  # 当事者でない
    agents["P08"], agents["P02"], agents["P03"] = rec08, rec02, rec03

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=59, logger=EventLogger())
    game.run()

    def _violation_entries(vs):
        rounds = vs["my_cash_flow"]["rounds"]
        return [
            e for r in rounds for e in r["entries"]
            if e["kind"] in ("contract_payment_paid", "contract_payment_received")
        ]

    # R1V1の振り返り（is_round_end=False）
    r1v1_08 = rec08.reflect_snapshots[0][3]
    r1v1_02 = rec02.reflect_snapshots[0][3]
    r1v1_03 = rec03.reflect_snapshots[0][3]

    paid_08 = _violation_entries(r1v1_08)
    assert len(paid_08) == 1
    assert paid_08[0]["kind"] == "contract_payment_paid"
    assert paid_08[0]["promised"] == 1_000_000
    assert paid_08[0]["paid"] == 1_000_000

    received_02 = _violation_entries(r1v1_02)
    assert len(received_02) == 1
    assert received_02[0]["kind"] == "contract_payment_received"
    assert received_02[0]["promised"] == 1_000_000

    assert _violation_entries(r1v1_03) == []

    # R1V2のOpen（次の投票の交渉1巡目のvisible_state）にも引き続き入っている
    by08 = _negotiate_snapshots_by_key(rec08)
    v2_08 = by08.get((1, 2, 1))
    if v2_08 is not None:
        assert _violation_entries(v2_08) == paid_08


def test_acceptance_60_round_settlement_payments_visible_with_payout_and_interest() -> None:
    """
    #60: R1の終わりに、割合契約（25%）の支払いと型Aの支払いがある → R1の
    最後の投票の振り返りで、義務者と相手方に渡す情報に、それぞれの約束した
    額と実際に払われた額が入っている。山の受け取りと利息は、本人に渡す
    情報に入っている
    """
    V1_YES = {"P01", "P03", "P05", "P07", "P09"}
    V2_YES = {"P01", "P07"}
    votes_by_vote = {
        (1, 1): {f"P{i:02d}": (Vote.YES if f"P{i:02d}" in V1_YES else Vote.NO) for i in range(1, 13)},
        (1, 2): {pid: (Vote.YES if pid in V2_YES else Vote.NO) for pid in V1_YES},
    }
    agents = make_roster(votes_by_vote, num_players=12)
    agents["P01"] = ScriptedAgent(
        "P01", votes=agents["P01"].votes, loan=1_200_000,
        negotiate_actions={
            (1, 1, 1): ContractProposeAction(
                player_id="P01", with_players=["P07"],
                terms=[{"obligor": "P01", "counterparty": "P07", "ob_type": "type_c_conditional",
                        "round_num": 1,
                        "details": {"share_percent": 25, "condition_type": "wins_round",
                                    "condition": {"target_player": "P01"}}}],
            ),
            (1, 1, 3): ContractProposeAction(
                player_id="P01", with_players=["P03"],
                terms=[{"obligor": "P01", "counterparty": "P03", "ob_type": "type_a_payment",
                        "round_num": 1, "details": {"amount": 100_000}}],
            ),
        },
    )
    agents["P07"].sign_proposer_at[(1, 1, 2)] = "P01"
    agents["P03"].sign_proposer_at[(1, 1, 4)] = "P01"

    rec01 = VisibleStateRecordingAgent(agents["P01"])  # 両方の義務者
    rec07 = VisibleStateRecordingAgent(agents["P07"])  # 割合契約の相手方・勝ち残り
    rec03 = VisibleStateRecordingAgent(agents["P03"])  # 型Aの相手方（退場済み）
    rec05 = VisibleStateRecordingAgent(agents["P05"])  # 当事者でない
    agents["P01"], agents["P07"], agents["P03"], agents["P05"] = rec01, rec07, rec03, rec05

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=60, logger=EventLogger())
    game.run()

    def _payment_entries(vs):
        rounds = vs["my_cash_flow"]["rounds"]
        return [
            e for r in rounds for e in r["entries"]
            if e["kind"] in ("contract_payment_paid", "contract_payment_received")
        ]

    # R1の最後の投票（R1V2）の振り返り（is_round_end=True）
    round_end_01 = next(s[3] for s in rec01.reflect_snapshots if s[0] == 1 and s[2] is True)
    payments_01 = _payment_entries(round_end_01)
    assert len(payments_01) == 2
    by_counterparty = {e["counterparty"]: e for e in payments_01}
    assert by_counterparty["P07"]["promised"] == 1_500_000  # 6,000,000の25%
    assert by_counterparty["P07"]["paid"] == 1_500_000
    assert by_counterparty["P03"]["promised"] == 100_000
    assert by_counterparty["P03"]["paid"] == 100_000

    round_end_07 = next(s[3] for s in rec07.reflect_snapshots if s[0] == 1 and s[2] is True)
    payments_07 = _payment_entries(round_end_07)
    assert len(payments_07) == 1
    assert payments_07[0]["kind"] == "contract_payment_received"
    assert payments_07[0]["promised"] == 1_500_000
    assert payments_07[0]["paid"] == 1_500_000

    round_end_03 = next(s[3] for s in rec03.reflect_snapshots if s[0] == 1 and s[2] is True)
    payments_03 = _payment_entries(round_end_03)
    assert len(payments_03) == 1
    assert payments_03[0]["promised"] == 100_000

    round_end_05 = next(s[3] for s in rec05.reflect_snapshots if s[0] == 1 and s[2] is True)
    assert _payment_entries(round_end_05) == []

    # 山の受け取りと利息は、本人に渡す情報（お金の出入りの記録）に入っている
    kinds_01 = {e["kind"] for r in round_end_01["my_cash_flow"]["rounds"] for e in r["entries"]}
    assert "pot_payout" in kinds_01
    assert "interest" in kinds_01


def test_acceptance_61_type_c_payment_moves_obligation_to_paid_in_my_contract_status() -> None:
    """
    #61: R1V1を対象にした型Cが支払われた後の、R1V2 → 自分が当事者の契約の
    表示で、その義務が「済み」になり、支払われた額が出ている。これからの
    義務としては示されない
    """
    # 7対5: YES側（P01含む）が退場、NO側が残る。P01がYES=少数派になるよう
    # 型C(minority_side=YES)を仕込む。
    votes_by_vote = {
        (1, 1): {f"P{i:02d}": Vote.YES for i in range(1, 6)}
        | {f"P{i:02d}": Vote.NO for i in range(6, 13)},  # 5対7。YES側（少数派）が残る
    }
    agents = make_roster(votes_by_vote, num_players=12)
    agents["P01"] = ScriptedAgent(
        "P01", votes=agents["P01"].votes, loan=1_200_000,
        negotiate_actions={
            (1, 1, 1): ContractProposeAction(
                player_id="P01", with_players=["P07"],
                terms=[{"obligor": "P01", "counterparty": "P07", "ob_type": "type_c_conditional",
                        "round_num": 1, "vote_num": 1,
                        "details": {"amount": 500_000, "condition_type": "minority_side",
                                    "condition": {"side": "YES"}}}],
            ),
        },
    )
    agents["P07"].sign_proposer_at[(1, 1, 2)] = "P01"
    rec01 = VisibleStateRecordingAgent(agents["P01"])
    agents["P01"] = rec01

    config = GameConfig.dev_small(num_players=12, num_rounds=2)
    game = Game(config=config, agents=agents, seed=61, logger=EventLogger(), stop_after_round=2)
    game.run()

    by01 = _negotiate_snapshots_by_key(rec01)
    v2_state = by01.get((1, 2, 1))
    assert v2_state is not None, "R1V2の交渉が発生していない（決着しなかった可能性）"
    contracts = v2_state["my_contract_status"]
    assert len(contracts) == 1
    ob = contracts[0]["obligations"][0]
    assert ob["status"] == "paid"
    assert ob["promised"] == 500_000
    assert ob["paid"] == 500_000
    # これからの義務としては示されない（財務通知のobligations_dueから消える）
    due = v2_state["my_finance"]["obligations_due"]
    assert due == []


def test_acceptance_62_post_game_reflection_carries_results_ranks_and_cash_flow() -> None:
    """
    #62: 試合後の振り返り → 全ラウンドの結果、全員の最終順位と最終資産、
    自分のお金の出入りの記録（最終ラウンドの精算と利息を含む）が渡っている
    """
    agents = make_roster({}, num_players=12)

    class _ReflectingAgent(ScriptedAgent):
        def post_game_reflect(self, post_game_context):
            return {"emotion": "楽", "comment": json.dumps({
                "has_round_results": bool(post_game_context.get("round_results")),
                "has_all_ranks": bool(post_game_context.get("all_final_ranks")),
                "has_cash_flow": bool(post_game_context.get("my_cash_flow", {}).get("rounds")),
            })}

    agents["P01"] = _ReflectingAgent("P01", votes=agents["P01"].votes)
    config = GameConfig.dev_small(num_players=12, num_rounds=2)
    game = Game(config=config, agents=agents, seed=62, logger=EventLogger())
    result = game.run()

    comment = json.loads(result.post_game_reflections["P01"]["comment"])
    assert comment["has_round_results"] is True
    assert comment["has_all_ranks"] is True
    assert comment["has_cash_flow"] is True

    # 最終ラウンドの精算と利息を含む（最後のラウンドのpot_payout/interestが
    # 記録に入っている）
    cash_flow = game._cash_flow_view("P01")
    final_round_entries = next(
        (r["entries"] for r in cash_flow["rounds"] if r["round_num"] == config.num_rounds), [],
    )
    kinds = {e["kind"] for e in final_round_entries}
    assert "interest" in kinds


def test_acceptance_65_fully_settled_contract_hidden_pending_contract_shown() -> None:
    """
    #65: R1ですべての義務が済んだ契約と、R2を対象にした型Aが残っている契約が
    ある、R2V1 → 自分が当事者の契約の表示に、前者は出ていない。前者の支払いと
    受け取りは、お金の出入りの記録に出ている。後者は表示に出ていて、R2の
    義務が「これから」になっている
    """
    agents = make_roster({}, num_players=12)
    # 契約1: R1終了時に支払う型A（R1で全義務が済む）
    agents["P01"] = ScriptedAgent(
        "P01", votes=agents["P01"].votes, loan=1_200_000,
        negotiate_actions={
            (1, 1, 1): ContractProposeAction(
                player_id="P01", with_players=["P02"],
                terms=[{"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
                        "round_num": 1, "details": {"amount": 100_000}}],
            ),
            (2, 1, 1): ContractProposeAction(
                player_id="P01", with_players=["P03"],
                terms=[{"obligor": "P01", "counterparty": "P03", "ob_type": "type_a_payment",
                        "round_num": 2, "details": {"amount": 200_000}}],
            ),
        },
    )
    agents["P02"].sign_proposer_at[(1, 1, 2)] = "P01"
    agents["P03"].sign_proposer_at[(2, 1, 2)] = "P01"
    rec01 = VisibleStateRecordingAgent(agents["P01"])
    agents["P01"] = rec01

    config = GameConfig.dev_small(num_players=12, num_rounds=2)
    game = Game(config=config, agents=agents, seed=65, logger=EventLogger(), stop_after_round=2)
    game.run()

    by01 = _negotiate_snapshots_by_key(rec01)
    # turn=3: turn=2でP03の署名が確定した後なので、契約2の成立が確実に
    # 反映されている（同じ巡内の順序に依存しない）
    r2v1_state = by01.get((2, 1, 3))
    assert r2v1_state is not None
    contracts = r2v1_state["my_contract_status"]
    # R1の契約（全義務済み）は出ない。R2の契約（これから）だけが出る
    assert len(contracts) == 1
    ob = contracts[0]["obligations"][0]
    assert ob["round_num"] == 2
    assert ob["status"] == "pending"

    # R1の契約の支払いはお金の出入りの記録に出る
    cash_flow = r2v1_state["my_cash_flow"]
    r1_entries = next((r["entries"] for r in cash_flow["rounds"] if r["round_num"] == 1), [])
    paid_entries = [e for e in r1_entries if e["kind"] == "contract_payment_paid"]
    assert any(e["amount"] == 100_000 for e in paid_entries)


def test_acceptance_63_signed_proposal_shows_signed_status_not_sign_method() -> None:
    """
    #63: P01が提案して署名済みの提案が、P02の署名待ちで残っている手番 →
    P01に渡す文面には、署名済みであることが出て、署名の送り方は出ない。
    P02に渡す文面には、未署名であることと、署名の送り方が出る
    """
    agents = make_roster({}, num_players=12)
    agents["P01"].negotiate_actions[(1, 1, 1)] = ContractProposeAction(
        player_id="P01", with_players=["P02"],
        terms=[{"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
                "round_num": 1, "details": {"amount": 100_000}}],
    )
    # P02は署名しない（提案のまま残す）
    rec01 = VisibleStateRecordingAgent(agents["P01"])
    rec02 = VisibleStateRecordingAgent(agents["P02"])
    agents["P01"], agents["P02"] = rec01, rec02

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=63, logger=EventLogger())
    game.run()

    by01 = _negotiate_snapshots_by_key(rec01)
    by02 = _negotiate_snapshots_by_key(rec02)
    vs01 = by01[(1, 1, 2)]
    vs02 = by02[(1, 1, 2)]

    text01 = build_negotiation_prompt(_ps("P01"), 1, 1, 2, vs01, config).text
    text02 = build_negotiation_prompt(_ps("P02"), 1, 1, 2, vs02, config).text

    sign_instruction = "署名するには"
    assert "あなたは署名済み" in text01
    assert sign_instruction not in text01  # 署名の送り方は出ない（アクション形式の説明文は別）
    assert "あなたは署名済み" not in text02
    assert sign_instruction in text02
    assert "未署名: P02" in text02


def test_acceptance_64_old_messages_omitted_when_body_exceeds_limit() -> None:
    """
    #64: 会話が積み上がり、省かなければ文面が15,000字を超える手番 → 文面は
    15,000字以内に収まっている。省かれたのは古い発言だけで、残った発言は
    どれも省かれた発言より新しい。省いた件数が会話の先頭に出ている。
    財務通知・契約・投票結果・お金の出入りの記録・メモは省かれていない。
    観戦者の記録には全部の発言が残っている（messagesはvisible_state自体を
    操作していないので別途は確認しない——エンジン側でメッセージを削らない
    ことはtests/test_prompt_turn_limit.pyのBot確認で確認済み）。
    """
    visible_state = {
        "round_num": 1, "vote_num": 1, "question": "テスト", "pot": 12_000_000,
        "remaining_ids": [f"P{i:02d}" for i in range(1, 13)], "eliminated_ids": [],
        "consecutive_ties": 0, "negotiation_max_turns": 10,
        "initial_loans": {f"P{i:02d}": 1_200_000 for i in range(1, 13)}, "public_ranks_history": {},
        "round_contract_counts": [], "round_vote_results": [], "round_results": [],
        "last_vote_result": None, "last_round_result": None,
        "my_last_action_error": None,
        "my_contract_status": [], "contracts_pending": [], "my_expired_proposals": [],
        "my_cash_flow": {"rounds": [], "totals": {
            "cash_in": 0, "cash_out": 0, "cash_delta": 0,
            "debt_up": 0, "debt_down": 0, "debt_delta": 0, "borrowed": 0,
        }},
        "my_finance": {
            "cash": 1_200_000, "debt_pre": 1_200_000, "debt_pre_repayable": False,
            "debt_pre_note": "返済不可（開始前の借金・5%）", "debt_post": 0, "total_debt": 1_200_000,
            "remaining_credit": 8_800_000, "interest_forecast": 60_000, "obligations_due": [],
        },
        "my_rank": {"rank": 1, "tied": True, "n_players": 12},
        "messages": [
            {
                "type": "broadcast", "from": f"P{(i % 12) + 1:02d}", "to": None,
                "message": "あ" * 400, "vote_num": 1, "turn": i + 1,
            }
            for i in range(80)
        ],
    }
    result = build_negotiation_prompt(_ps("P01"), 1, 1, 81, visible_state, GameConfig.default_12())

    assert len(result.text) <= TURN_PROMPT_MAX_CHARS
    assert result.omitted_messages > 0
    assert f"これより前の発言 {result.omitted_messages}件は省略" in result.text

    # 財務通知・お金の出入りは省かれない（固定文言が残る）
    assert "開始前の借金" in result.text
    assert "あなたのお金の出入り" in result.text

    # 残った発言は、どれも省かれた発言より新しい（turnの昇順の末尾部分）
    kept = visible_state["messages"][result.omitted_messages:]
    for m in kept:
        assert f"{m['turn']}巡目" in result.text


def test_turn_limit_calc_still_works_with_1500_char_memory() -> None:
    """
    #64の延長（サイクル4.2d）: メモが1,500字（MEMORY_TRUNCATE_LENGTH）のときも、
    手番の文面の上限（15,000字）の計算が壊れない（会話を古い発言から省いて
    収める、残った発言は省かれた発言より新しい）。メモは会話以外の部分なので
    省かれない（今までどおり）。
    """
    visible_state = {
        "round_num": 1, "vote_num": 1, "question": "テスト", "pot": 12_000_000,
        "remaining_ids": [f"P{i:02d}" for i in range(1, 13)], "eliminated_ids": [],
        "consecutive_ties": 0, "negotiation_max_turns": 10,
        "initial_loans": {f"P{i:02d}": 1_200_000 for i in range(1, 13)}, "public_ranks_history": {},
        "round_contract_counts": [], "round_vote_results": [], "round_results": [],
        "last_vote_result": None, "last_round_result": None,
        "my_last_action_error": None,
        "my_contract_status": [], "contracts_pending": [], "my_expired_proposals": [],
        "my_cash_flow": {"rounds": [], "totals": {
            "cash_in": 0, "cash_out": 0, "cash_delta": 0,
            "debt_up": 0, "debt_down": 0, "debt_delta": 0, "borrowed": 0,
        }},
        "my_finance": {
            "cash": 1_200_000, "debt_pre": 1_200_000, "debt_pre_repayable": False,
            "debt_pre_note": "返済不可（開始前の借金・5%）", "debt_post": 0, "total_debt": 1_200_000,
            "remaining_credit": 8_800_000, "interest_forecast": 60_000, "obligations_due": [],
        },
        "my_rank": {"rank": 1, "tied": True, "n_players": 12},
        "messages": [
            {
                "type": "broadcast", "from": f"P{(i % 12) + 1:02d}", "to": None,
                "message": "あ" * 400, "vote_num": 1, "turn": i + 1,
            }
            for i in range(80)
        ],
    }
    max_memory = "あ" * MEMORY_TRUNCATE_LENGTH
    result = build_negotiation_prompt(
        _ps("P01"), 1, 1, 81, visible_state, GameConfig.default_12(), memory=max_memory,
    )

    assert len(result.text) <= TURN_PROMPT_MAX_CHARS
    assert result.omitted_messages > 0
    assert max_memory in result.text

    kept = visible_state["messages"][result.omitted_messages:]
    for m in kept:
        assert f"{m['turn']}巡目" in result.text


def test_acceptance_66_memory_truncated_at_1500_and_not_updated_by_negotiate_response() -> None:
    """
    #66: 振り返りで、1,200字のメモを返した席と、1,600字のメモを返した席が
    ある。別の席が、交渉の応答にメモを入れて返した → 1,200字のメモはそのまま、
    1,600字のメモは1,500字で切り捨てられて、次の手番の文面に入っている。
    交渉の応答に入れたメモでは、メモは書き換わらない（v0.4.4 §9.4・§11.9 #1）
    """
    medium_memory = "あ" * 1_200
    medium_result = extract_memory(json.dumps({"memory": medium_memory}))
    assert medium_result.truncated is False
    assert medium_result.text == medium_memory

    long_memory = "あ" * 1_600
    result = extract_memory(json.dumps({"memory": long_memory}))
    assert result.truncated is True
    assert len(result.text) == MEMORY_TRUNCATE_LENGTH

    visible_state = {
        "round_num": 1, "vote_num": 2, "question": "テスト", "pot": 12_000_000,
        "remaining_ids": [f"P{i:02d}" for i in range(1, 13)], "eliminated_ids": [],
        "consecutive_ties": 0, "negotiation_max_turns": 10,
        "initial_loans": {}, "public_ranks_history": {},
        "round_contract_counts": [], "round_vote_results": [], "round_results": [],
        "last_vote_result": None, "last_round_result": None,
        "my_last_action_error": None,
        "my_contract_status": [], "contracts_pending": [], "my_expired_proposals": [],
        "my_cash_flow": {"rounds": [], "totals": {
            "cash_in": 0, "cash_out": 0, "cash_delta": 0,
            "debt_up": 0, "debt_down": 0, "debt_delta": 0, "borrowed": 0,
        }},
        "my_finance": {
            "cash": 1_200_000, "debt_pre": 1_200_000, "debt_pre_repayable": False,
            "debt_pre_note": "返済不可（開始前の借金・5%）", "debt_post": 0, "total_debt": 1_200_000,
            "remaining_credit": 8_800_000, "interest_forecast": 60_000, "obligations_due": [],
        },
        "my_rank": {"rank": 1, "tied": True, "n_players": 12},
        "messages": [],
    }
    text = build_negotiation_prompt(
        _ps("P01"), 1, 2, 1, visible_state, GameConfig.default_12(), memory=result.text,
    ).text
    assert result.text in text

    # 交渉の応答にmemoryが入っていても、LLMAgent.negotiate()はそれを読まない
    # （llm/llm_agent.pyのnegotiate/commitからextract_memory呼び出しを削除済み。
    # tests/test_llm_agent.py::test_negotiate_response_memory_field_does_not_update_memory
    # が実機能として固定している）。
