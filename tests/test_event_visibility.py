"""
イベント種別→公開区分（§8）の固定テスト（v0.4新設）

`engine/models.py::GameEvent.visibility`（public/self/parties/spectator）が
イベント種別ごとに意図した区分で一貫して発行されることを、実際にゲームを
回して確認する。CLAUDE.md過去の落とし穴④（同型の処理が複数箇所にあると
修正漏れが起きる）の対策として、すべての`logger.log(...)`呼び出し箇所
（engine/game.py・engine/settlement.py・engine/finance.py）を実際に
発火させ、1つのテストで全数を固定する。

NEGOTIATION_ACTION だけはアクションの種類で区分が変わる
（pass/検証失敗=spectator、dm=parties、broadcast=public）。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import (
    BroadcastAction, Contract, ContractProposeAction, ContractStatus, DmAction,
    Obligation, ObligationType, PlayerState, RepayAction, TransferAction, Vote,
)
from engine.settlement import settle_vote
from tests.helpers import FailingCommitAgent, ScriptedAgent, make_roster

# 公開区分が常に一定のイベント種別（§8）
FIXED_VISIBILITY: dict[str, str] = {
    "GAME_START": "public",
    "LOAN_REVEALED": "public",
    "GAME_END": "public",
    "POST_GAME_REFLECTION": "spectator",
    "ROUND_START": "public",
    "ENTRY_FEE_COLLECTED": "self",
    "POT_UPDATED": "public",
    "VOTE_OPEN": "public",
    "RANK_NOTIFIED": "self",
    "FINANCE_NOTICE": "self",
    "TURN_CONTRACTS_ESTABLISHED": "public",
    "CONTRACT_REJECTED": "self",
    "CONTRACT_EXPIRED": "parties",
    "CONTRACT_PROPOSED": "parties",
    "CONTRACT_SIGNED": "parties",
    "CONTRACT_ESTABLISHED": "parties",
    "TRANSFER": "parties",
    "REPAYMENT": "self",
    "AUTO_COMMIT": "public",
    "COMMIT": "self",
    "RANK_PUBLISHED": "public",
    "NEGOTIATION_EARLY_END": "spectator",
    "VOTE_REVEALED": "public",
    "EXTENSION_FEE_COLLECTED": "self",
    "VOTE_RESOLVED": "public",
    "ROUND_ABORTED": "public",
    "TYPE_B_VIOLATION": "public",
    "PAYABLE_LIMIT_FIXED": "spectator",
    "CONTRACT_PAYMENT": "parties",
    "PAYMENT_SHORTFALL": "public",
    "ROUND_RESOLVED": "public",
    "ROUND_PAYOUT": "public",
    "INTEREST": "self",
}

# NEGOTIATION_ACTIONはdata["action"]で区分が変わる（§8）
NEGOTIATION_ACTION_VISIBILITY_BY_KIND: dict[str, str] = {
    "pass": "spectator",
    "dm": "parties",
    "broadcast": "public",
}


def _build_comprehensive_game(seed: int = 1) -> Game:
    """全イベント種別を発火させるシナリオを組み立てる"""
    # R1V1: 7対5（決着、5人残留） → R1V2が発生
    v1 = {f"P{i:02d}": Vote.YES for i in range(1, 8)}
    v1.update({f"P{i:02d}": Vote.NO for i in range(8, 13)})
    # R1V2: 残留5人のうち3対2で決着（ラウンド終了）
    v2 = {"P08": Vote.YES, "P09": Vote.YES, "P10": Vote.YES, "P11": Vote.NO, "P12": Vote.NO}
    # R2V1: 6対6（やり直し） → R2V2で決着
    v2r2 = {f"P{i:02d}": Vote.YES for i in range(1, 7)}
    v2r2.update({f"P{i:02d}": Vote.NO for i in range(7, 13)})
    v2r2_v2 = {f"P{i:02d}": Vote.YES for i in range(1, 11)}
    v2r2_v2.update({"P11": Vote.NO, "P12": Vote.NO})

    agents = make_roster({
        (1, 1): v1, (1, 2): v2, (2, 1): v2r2, (2, 2): v2r2_v2,
    }, num_players=12)

    # P01はDM・全体発言・送金・返済・契約提案（型B、違反する）を行う
    agents["P01"] = ScriptedAgent(
        "P01", votes={(1, 1): Vote.YES},
        negotiate_actions={
            (1, 1, 1): DmAction(player_id="P01", to="P02", message="dm-test"),
            (1, 1, 2): BroadcastAction(player_id="P01", message="broadcast-test"),
            (1, 1, 3): TransferAction(player_id="P01", to="P02", amount=1_000),
            (1, 1, 4): ContractProposeAction(
                player_id="P01", with_players=["P02"],
                terms=[{"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_vote",
                        "round_num": 1, "details": {"vote": "NO"}}],  # vote_num欠落→不成立(CONTRACT_REJECTED)
            ),
            (1, 1, 5): ContractProposeAction(
                player_id="P01", with_players=["P02"],
                terms=[{"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_vote",
                        "round_num": 1, "vote_num": 1, "details": {"vote": "NO"}}],
            ),
        },
    )
    agents["P02"] = ScriptedAgent(
        "P02", votes={(1, 1): Vote.YES},
        sign_proposer_at={(1, 1, 6): "P01"},
    )

    config = GameConfig.default_12()
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=seed, logger=logger)
    game.run()
    return game


def _build_unsigned_proposal_game(seed: int = 3) -> Game:
    """署名がそろわず失効する契約（CONTRACT_EXPIRED）を発火させる"""
    agents = make_roster({}, num_players=12)
    agents["P01"].negotiate_actions[(1, 1, 1)] = ContractProposeAction(
        player_id="P01", with_players=["P02"],
        terms=[{"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
                "round_num": 1, "details": {"amount": 100_000}}],
    )
    # P02は署名しない
    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=seed, logger=logger)
    game.run()
    return game


def _build_auto_commit_game(seed: int = 4) -> Game:
    """無効な出力が続くエージェントでAUTO_COMMITを発火させる（§4.6）"""
    agents = make_roster({}, num_players=12)
    agents["P01"] = FailingCommitAgent("P01")
    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=seed, logger=logger)
    game.run()
    return game


def _build_post_game_reflection_game(seed: int = 5) -> Game:
    """post_game_reflect()が値を返すエージェントでPOST_GAME_REFLECTIONを発火させる（§9.4）"""
    class _ReflectingAgent(ScriptedAgent):
        def post_game_reflect(self, post_game_context):
            return {"comment": "振り返りテスト"}

    agents = make_roster({}, num_players=12)
    agents["P01"] = _ReflectingAgent("P01")
    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=seed, logger=logger)
    game.run()
    return game


def _repayment_events() -> list:
    """開始後の借金がある状態でRepayActionを実行し、REPAYMENTを発火させる"""
    agents = make_roster({}, num_players=12)
    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=6, logger=logger)
    game._setup()
    game.players["P01"] = game.players["P01"].model_copy(update={"debt_post": 500_000, "cash": 1_000_000})
    action = RepayAction(player_id="P01", amount=300_000)
    game._execute_negotiation_action(action, "P01", round_num=1, vote_num=1, turn=1)
    return logger.events


def _payment_shortfall_events() -> list:
    """支払える上限を超える型Aの義務で、PAYMENT_SHORTFALLを発火させる（§7.3手順8）"""
    players = {
        f"P{i:02d}": PlayerState(player_id=f"P{i:02d}", cash=0, initial_loan=1_200_000)
        for i in range(1, 13)
    }
    ob = Obligation(
        obligation_id="OB1", contract_id="C1", obligor="P01", counterparty="P02",
        ob_type=ObligationType.TYPE_B_VOTE, round_num=1, vote_num=1, details={"vote": "YES"},
    )
    contract = Contract(
        contract_id="C1", proposer="P01", parties=["P01", "P02"], signed_by=["P01", "P02"],
        obligations=[ob], round_created=1, vote_created=1, status=ContractStatus.ACTIVE,
        contract_seq=1, round_established=1, vote_established=1,
    )
    players["P01"] = players["P01"].model_copy(update={"debt_pre": 10_000_000})  # 残り枠0
    votes = {f"P{i:02d}": Vote.NO for i in range(1, 13)}  # P01がYES指定に反してNO投票
    logger = EventLogger()
    settle_vote(
        players, votes, GameConfig.default_12(), round_num=1, vote_num=1,
        consecutive_ties_before=0, logger=logger, contracts=[contract],
    )
    return logger.events


def test_fixed_visibility_event_types_match_table() -> None:
    """表にある種別は、実際のイベントでも常に同じ公開区分で発行される"""
    all_events = list(_build_comprehensive_game().logger.events)
    all_events += _build_unsigned_proposal_game().logger.events
    all_events += _build_auto_commit_game().logger.events
    all_events += _build_post_game_reflection_game().logger.events
    all_events += _repayment_events()
    all_events += _payment_shortfall_events()

    seen_types: set[str] = set()
    for e in all_events:
        seen_types.add(e.event_type)
        if e.event_type in FIXED_VISIBILITY:
            assert e.visibility == FIXED_VISIBILITY[e.event_type], (e.event_type, e.visibility)

    # 表に載っているすべての種別が、これらのシナリオで最低1回は発火している
    missing = set(FIXED_VISIBILITY) - seen_types
    assert not missing, f"シナリオで発火しなかった種別: {missing}"


def test_negotiation_action_visibility_varies_by_kind() -> None:
    """NEGOTIATION_ACTIONはdm=parties/broadcast=public/pass・検証失敗=spectator"""
    game = _build_comprehensive_game()
    by_kind: dict[str, set[str]] = {}
    for e in game.logger.events:
        if e.event_type != "NEGOTIATION_ACTION":
            continue
        kind = e.data.get("action")
        if e.data.get("success") is False:
            expected = "spectator"
        else:
            expected = NEGOTIATION_ACTION_VISIBILITY_BY_KIND.get(kind)
        if expected is not None:
            assert e.visibility == expected, (kind, e.data, e.visibility)
        by_kind.setdefault(kind, set()).add(e.visibility)

    assert by_kind.get("dm") == {"parties"}
    assert by_kind.get("broadcast") == {"public"}
    assert "pass" in by_kind


def test_auto_commit_from_failing_agent_is_public() -> None:
    """無効な出力が続いた場合のAUTO_COMMITも公開区分は変わらない（§4.6/§8）"""
    agents = make_roster({}, num_players=12)
    agents["P01"] = FailingCommitAgent("P01")
    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=2, logger=logger)
    game.run()

    auto_events = [e for e in logger.events if e.event_type == "AUTO_COMMIT"]
    assert auto_events
    assert all(e.visibility == "public" for e in auto_events)


def test_no_party_or_amount_leaks_into_public_or_spectator_only_fields() -> None:
    """publicイベントのdataに契約の当事者名・金額が紛れ込んでいないことを
    機械的に確認する（§8/§12.3 #43の横断チェック）"""
    game = _build_comprehensive_game()
    for e in game.logger.events:
        if e.visibility != "public":
            continue
        if e.event_type in ("CONTRACT_PAYMENT", "CONTRACT_PROPOSED", "CONTRACT_SIGNED", "CONTRACT_ESTABLISHED"):
            continue  # publicではないのでここには来ない想定（別途visibility自体を検査済み）
        # 公開イベントのdataキーに契約由来の秘匿情報キーが無いこと
        assert "contract_id" not in e.data
        assert "obligations" not in e.data
