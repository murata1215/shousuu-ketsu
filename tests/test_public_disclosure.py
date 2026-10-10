"""
公示をプレイヤーに渡す形のテスト（§8.1/§12.3 受け入れ#51〜#54、サイクル4.1）

サイクル4.0のエンジンは公示をイベントログには残すが、`visible_state`
（プレイヤーに渡す情報）に入れていないものがあった（契約の成立本数は
一度も入らず、投票の精算で出た公示は`last_round_result`にしか入らないため
同じラウンドの次の投票では誰にも見えなかった）。本ファイルは、§8.1の表の
とおりに直った後の挙動を4本の受け入れテストと2本の横断テストで固定する。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import (
    BroadcastAction, ContractProposeAction, DmAction, TransferAction, Vote,
)
from tests.helpers import (
    FailingCommitAgent, ScriptedAgent, VisibleStateRecordingAgent, make_roster,
)
from tests.test_event_visibility import FIXED_VISIBILITY


def _negotiate_snapshots_by_key(
    recorder: VisibleStateRecordingAgent,
) -> dict[tuple[int, int, int], dict]:
    return {(r, v, t): vs for r, v, t, vs in recorder.negotiate_snapshots}


def test_acceptance_51_turn_contract_count_visible_from_next_turn_only() -> None:
    """
    #51: 契約が成立した巡の、次の手番 → 全プレイヤーに渡す情報に
    「投票番号・巡・本数」が入っている。当事者名・内容・成立順は入っていない
    """
    agents = make_roster({}, num_players=12)
    agents["P01"].negotiate_actions[(1, 1, 1)] = ContractProposeAction(
        player_id="P01", with_players=["P02"],
        terms=[{"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
                "round_num": 1, "details": {"amount": 100_000}}],
    )
    agents["P02"].sign_proposer_at[(1, 1, 2)] = "P01"

    recorder = VisibleStateRecordingAgent(agents["P03"])
    agents["P03"] = recorder

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=51, logger=EventLogger())
    game.run()

    by_turn = _negotiate_snapshots_by_key(recorder)

    # turn=1: まだ何も成立していない（前の巡が無い）
    assert by_turn[(1, 1, 1)]["round_contract_counts"] == []
    # turn=2: 署名がそろうのはturn=2の最中なので、turn=2の開始時点ではturn1分（0本）だけ
    assert by_turn[(1, 1, 2)]["round_contract_counts"] == [
        {"vote_num": 1, "turn": 1, "count": 0},
    ]
    # turn=3: turn=2で1本成立した分が「次の手番から」乗る
    assert by_turn[(1, 1, 3)]["round_contract_counts"] == [
        {"vote_num": 1, "turn": 1, "count": 0},
        {"vote_num": 1, "turn": 2, "count": 1},
    ]
    for entry in by_turn[(1, 1, 3)]["round_contract_counts"]:
        assert set(entry) == {"vote_num", "turn", "count"}  # 当事者名・内容・成立順は入れない


def _build_vote_settlement_disclosure_game(seed: int) -> tuple[Game, VisibleStateRecordingAgent]:
    """
    R1V1で型B違反者（P01）・払いきれなかった者（P01）・AUTO COMMIT（P08）を
    同時に発生させ、R1V2へ続く試合を組み立てる（#52・横断テスト共用）。

    P01: 「R1V1はYES」の型B義務を負うが実際はNOへ投票し違反する。P01は
    借入上限（1000万）まで借りた上で交渉中にほぼ全額を送金して払える上限を
    100万（違約金100万）未満に落とすため、払いきれない。
    P08: commitが常に失敗するため、型B義務（R1V1はYES）に従って自動代行
    （AUTO COMMIT）になる。
    """
    votes_by_vote = {
        (1, 1): {
            "P01": Vote.NO, "P02": Vote.YES, "P03": Vote.YES, "P04": Vote.YES,
            "P05": Vote.YES, "P06": Vote.YES, "P07": Vote.YES,
            "P09": Vote.NO, "P10": Vote.NO, "P11": Vote.NO, "P12": Vote.NO,
        },
        (1, 2): {
            "P01": Vote.YES, "P09": Vote.YES, "P10": Vote.NO, "P11": Vote.NO, "P12": Vote.NO,
        },
    }
    agents = make_roster(votes_by_vote, num_players=12)

    agents["P01"] = ScriptedAgent(
        "P01", votes=agents["P01"].votes, loan=10_000_000,
        negotiate_actions={
            (1, 1, 1): ContractProposeAction(
                player_id="P01", with_players=["P02"],
                terms=[{"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_vote",
                        "round_num": 1, "vote_num": 1, "details": {"vote": "YES"}}],
            ),
            (1, 1, 3): TransferAction(player_id="P01", to="P05", amount=8_900_000),
        },
    )
    agents["P02"].sign_proposer_at[(1, 1, 2)] = "P01"

    agents["P08"] = FailingCommitAgent(
        "P08", loan=agents["P08"].loan,
        negotiate_actions={
            (1, 1, 1): ContractProposeAction(
                player_id="P08", with_players=["P04"],
                terms=[{"obligor": "P08", "counterparty": "P04", "ob_type": "type_b_vote",
                        "round_num": 1, "vote_num": 1, "details": {"vote": "YES"}}],
            ),
        },
    )
    agents["P04"].sign_proposer_at[(1, 1, 2)] = "P08"

    recorder = VisibleStateRecordingAgent(agents["P09"])
    agents["P09"] = recorder

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=seed, logger=EventLogger())
    game.run()
    return game, recorder


def test_acceptance_52_vote_settlement_disclosures_visible_from_next_vote_open() -> None:
    """
    #52: R1V1で型B違反・払いきれなかった者・自動代行が出て、ラウンドがV2へ続く
    → R1V2のOpenで全プレイヤーに渡す情報に、その3つの名前が入っている。
    ラウンドの終わりを待たない
    """
    _game, recorder = _build_vote_settlement_disclosure_game(seed=52)
    by_turn = _negotiate_snapshots_by_key(recorder)

    # R1V1の最中（settlement前）はまだ何も渡らない
    for turn in (1, 2, 3, 4):
        key = (1, 1, turn)
        if key in by_turn:
            assert by_turn[key]["round_vote_results"] == []

    # R1V2のOpen（最初の巡）には、V1の3つの公示が入っている
    v2_turn1 = by_turn[(1, 2, 1)]
    assert len(v2_turn1["round_vote_results"]) == 1
    v1_result = v2_turn1["round_vote_results"][0]
    assert v1_result["round_num"] == 1
    assert v1_result["vote_num"] == 1
    assert v1_result["type_b_violator_ids"] == ["P01"]
    assert v1_result["payment_shortfall_ids"] == ["P01"]
    assert v1_result["auto_commit_ids"] == ["P08"]

    # ラウンドの終わりを待たない: このスナップショットを取った時点
    # （R1V2の最初の巡）では、R1の精算（_phase_finance）はまだ終わっていない
    # （round_resultsがまだ空）。それでも投票の精算の公示は既に渡っている
    assert v2_turn1["round_results"] == []


def test_acceptance_53_vote_and_round_history_accumulate_across_the_game() -> None:
    """
    #53: R1V3の交渉中。R2の交渉中 → R1V3では、R1V1とR1V2の全員の票と
    退場者が全プレイヤーに渡っている。R2では、R1の勝ち残りと受け取った額
    （打ち切りならその旨と持ち越し額）が渡っている
    """
    votes_by_vote = {
        (1, 1): {f"P{i:02d}": Vote.YES for i in range(1, 8)}
        | {f"P{i:02d}": Vote.NO for i in range(8, 13)},
        # 残り5人（P08〜P12）が全員YES（全員一致）でやり直し
        (1, 2): {f"P{i:02d}": Vote.YES for i in range(8, 13)},
        # 同じ5人で決着（P11,P12がNOの少数派で残り、ラウンド終了）
        (1, 3): {
            "P08": Vote.YES, "P09": Vote.YES, "P10": Vote.YES,
            "P11": Vote.NO, "P12": Vote.NO,
        },
    }
    agents = make_roster(votes_by_vote, num_players=12)
    recorder = VisibleStateRecordingAgent(agents["P08"])
    agents["P08"] = recorder

    config = GameConfig.dev_small(num_players=12, num_rounds=2)
    game = Game(config=config, agents=agents, seed=53, logger=EventLogger(), stop_after_round=2)
    game.run()

    by_turn = _negotiate_snapshots_by_key(recorder)

    # R1V3の交渉中: R1V1とR1V2の結果が両方入っている
    v3_turn1 = by_turn[(1, 3, 1)]
    assert len(v3_turn1["round_vote_results"]) == 2
    v1_view, v2_view = v3_turn1["round_vote_results"]
    assert v1_view["vote_num"] == 1
    assert v1_view["result"] == "decisive"
    assert v1_view["eliminated_ids"] == [f"P{i:02d}" for i in range(1, 8)]
    assert v1_view["remaining_ids"] == [f"P{i:02d}" for i in range(8, 13)]
    assert v2_view["vote_num"] == 2
    assert v2_view["result"] == "retry"
    assert v2_view["consecutive_ties_after"] == 1

    # R2の交渉中: R1の結果（勝ち残り・受け取った額）が1件入り、
    # round_vote_resultsはR2用に空へ戻っている
    r2_turn1 = by_turn[(2, 1, 1)]
    assert len(r2_turn1["round_results"]) == 1
    r1_view = r2_turn1["round_results"][0]
    assert r1_view["round_num"] == 1
    assert r1_view["aborted"] is False
    assert sorted(r1_view["winner_ids"]) == ["P11", "P12"]
    assert r1_view["payout_per_winner"] > 0
    assert len(r1_view["votes"]) == 3  # V1・V2・V3（ラウンド最後の投票の公示も含む）
    assert r2_turn1["round_vote_results"] == []


def test_acceptance_54_rejection_reason_only_to_proposer() -> None:
    """
    #54: 形式に合わない提案をした、次の手番 → 本人に渡す情報に不成立の理由
    が入っている。ほかのプレイヤーに渡す情報には入っていない
    """
    agents = make_roster({}, num_players=12)
    bad_proposal = ContractProposeAction(
        player_id="P01", with_players=["P02"],
        terms=[{"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_vote",
                "round_num": 1, "details": {"vote": "NO"}}],  # vote_num欠落→不成立
    )
    agents["P01"] = ScriptedAgent("P01", negotiate_actions={(1, 1, 1): bad_proposal})
    p01_recorder = VisibleStateRecordingAgent(agents["P01"])
    agents["P01"] = p01_recorder
    p03_recorder = VisibleStateRecordingAgent(agents["P03"])
    agents["P03"] = p03_recorder

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=54, logger=EventLogger())
    game.run()

    p01_by_turn = _negotiate_snapshots_by_key(p01_recorder)
    p03_by_turn = _negotiate_snapshots_by_key(p03_recorder)

    assert p01_by_turn[(1, 1, 1)]["my_last_action_error"] is None  # 提案前はまだ無い
    assert p01_by_turn[(1, 1, 2)]["my_last_action_error"] is not None  # 直前の不成立の理由
    assert p03_by_turn[(1, 1, 2)]["my_last_action_error"] is None  # 他人には渡らない


def test_last_action_error_clears_on_next_action_even_if_it_is_a_pass() -> None:
    """
    §7.5「直前の自分の行動が不成立だった場合」。passも「行動」なので、
    不成立の理由は次の自分の行動（passも含む）で消える（持ち越さない）。
    """
    agents = make_roster({}, num_players=12)
    bad_proposal = ContractProposeAction(
        player_id="P01", with_players=["P02"],
        terms=[{"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_vote",
                "round_num": 1, "details": {"vote": "NO"}}],  # vote_num欠落→不成立
    )
    agents["P01"] = ScriptedAgent("P01", negotiate_actions={(1, 1, 1): bad_proposal})
    # P05が turn=2 でも行動するよう仕込み、all_passedによる早期終了を turn=3 まで延ばす
    agents["P05"].negotiate_actions[(1, 1, 2)] = DmAction(
        player_id="P05", to="P06", message="dm",
    )
    recorder = VisibleStateRecordingAgent(agents["P01"])
    agents["P01"] = recorder

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=55, logger=EventLogger())
    game.run()

    by_turn = _negotiate_snapshots_by_key(recorder)
    assert by_turn[(1, 1, 2)]["my_last_action_error"] is not None  # turn1の不成立がまだ見える
    assert by_turn[(1, 1, 3)]["my_last_action_error"] is None  # turn2でpassした後は消える


def test_acceptance_55_round_conversation_carries_across_votes() -> None:
    """
    #55: R1V2の交渉中 → R1V1の交渉での全体発言と、自分が当事者のDMが、
    投票番号と巡がわかる形で本人に渡っている。他人どうしのDMは入っていない

    （v0.4.2 §11.7 #1。サイクル4.1まではエンジンが投票ごとに会話を消しており、
    V1の会話がV2の本人に渡っていなかった）
    """
    votes_by_vote = {
        (1, 1): {f"P{i:02d}": Vote.YES for i in range(1, 8)}
        | {f"P{i:02d}": Vote.NO for i in range(8, 13)},  # 7対5、NO側5人が残ってV2へ
    }
    agents = make_roster(votes_by_vote, num_players=12)
    agents["P07"].negotiate_actions[(1, 1, 1)] = BroadcastAction(
        player_id="P07", message="全員YESで合わせよう",
    )
    agents["P04"].negotiate_actions[(1, 1, 1)] = DmAction(
        player_id="P04", to="P07", message="乗る",
    )
    agents["P09"].negotiate_actions[(1, 1, 1)] = DmAction(
        player_id="P09", to="P10", message="他人には見えないはずの内緒話",
    )

    recorder = VisibleStateRecordingAgent(agents["P04"])
    agents["P04"] = recorder

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=55, logger=EventLogger())
    game.run()

    by_turn = _negotiate_snapshots_by_key(recorder)
    v2_messages = by_turn[(1, 2, 1)]["messages"]

    broadcasts = [m for m in v2_messages if m["type"] == "broadcast"]
    assert len(broadcasts) == 1
    assert broadcasts[0]["from"] == "P07"
    assert broadcasts[0]["vote_num"] == 1
    assert broadcasts[0]["turn"] == 1

    dms = [m for m in v2_messages if m["type"] == "dm"]
    assert len(dms) == 1  # P04が当事者のDMだけ。P09→P10は入らない
    assert dms[0]["from"] == "P04" and dms[0]["to"] == "P07"
    assert dms[0]["vote_num"] == 1 and dms[0]["turn"] == 1


def test_acceptance_56_reflection_sees_whole_round_and_next_round_starts_empty() -> None:
    """
    #56: R1の終わりの振り返り。R2V1の交渉中 → 振り返りには、R1の全部の投票
    の会話が渡っている。R2V1では、R1の会話は渡っていない

    （v0.4.2 §11.7 #1。ラウンドの終わりの振り返りには最後の投票の会話しか
    渡っていなかった不具合の再発防止）
    """
    votes_by_vote = {
        (1, 1): {f"P{i:02d}": Vote.YES for i in range(1, 8)}
        | {f"P{i:02d}": Vote.NO for i in range(8, 13)},  # 7対5、NO側5人がV2へ
        (1, 2): {
            "P08": Vote.YES, "P09": Vote.YES, "P10": Vote.YES,
            "P11": Vote.NO, "P12": Vote.NO,
        },  # 3対2、NO側2人が残ってラウンド終了
    }
    agents = make_roster(votes_by_vote, num_players=12)
    agents["P08"].negotiate_actions[(1, 1, 1)] = BroadcastAction(
        player_id="P08", message="V1の発言",
    )
    agents["P10"].negotiate_actions[(1, 2, 1)] = BroadcastAction(
        player_id="P10", message="V2の発言",
    )

    recorder = VisibleStateRecordingAgent(agents["P08"])
    agents["P08"] = recorder

    config = GameConfig.dev_small(num_players=12, num_rounds=2)
    game = Game(config=config, agents=agents, seed=56, logger=EventLogger(), stop_after_round=2)
    game.run()

    r1_round_num, r1_reflect_state = recorder.reflect_snapshots[0]
    assert r1_round_num == 1
    r1_broadcasts = {
        (m["vote_num"], m["turn"], m["message"])
        for m in r1_reflect_state["messages"] if m["type"] == "broadcast"
    }
    assert r1_broadcasts == {(1, 1, "V1の発言"), (2, 1, "V2の発言")}

    by_turn = _negotiate_snapshots_by_key(recorder)
    assert by_turn[(2, 1, 1)]["messages"] == []  # R2に入ったらR1の会話は渡らない


# ---------------------------------------------------------------------------
# 横断テスト: 公開（public）のイベント種別ごとに、対応する情報が
# visible_state のどの項目に入るかを表で固定する（CLAUDE.md落とし穴⑦対策。
# 公開のイベントを新設してvisible_stateへの反映を忘れると、このテストが
# 落ちる）。
# ---------------------------------------------------------------------------

PUBLIC_EVENT_DELIVERY: dict[str, tuple[str, ...]] = {
    "GAME_START": ("round_num", "vote_num"),
    "LOAN_REVEALED": ("initial_loans",),
    "ROUND_START": ("round_num",),
    "POT_UPDATED": ("pot",),
    "VOTE_OPEN": ("question", "remaining_ids", "eliminated_ids", "consecutive_ties"),
    "TURN_CONTRACTS_ESTABLISHED": ("round_contract_counts",),
    "AUTO_COMMIT": ("round_vote_results", "round_results"),
    "VOTE_REVEALED": ("round_vote_results", "round_results"),
    "VOTE_RESOLVED": ("round_vote_results", "round_results"),
    "ROUND_ABORTED": ("round_vote_results", "round_results"),
    "TYPE_B_VIOLATION": ("round_vote_results", "round_results"),
    "PAYMENT_SHORTFALL": ("round_vote_results", "round_results"),
    "ROUND_RESOLVED": ("round_results",),
    "ROUND_PAYOUT": ("round_results",),
    "RANK_PUBLISHED": ("public_ranks_history",),
    "GAME_END": ("round_results",),
    "NEGOTIATION_ACTION:broadcast": ("messages",),
}


def test_every_public_event_type_has_a_visible_state_field() -> None:
    """
    FIXED_VISIBILITY（tests/test_event_visibility.py）のpublicなイベント
    種別すべてが PUBLIC_EVENT_DELIVERY に挙げられており、挙げた項目名が
    実際に visible_state のキーとして存在することを確かめる。

    公開のイベントを足したのにこの表を更新し忘れると最初のassertで落ち、
    visible_state側の項目名を変えてうっかり対応が崩れると2番目のassertで
    落ちる（CLAUDE.md落とし穴④・今回の漏れと同じ種類の事故を機械的に拾う）。
    """
    public_types = {t for t, v in FIXED_VISIBILITY.items() if v == "public"}
    public_types.add("NEGOTIATION_ACTION:broadcast")
    assert set(PUBLIC_EVENT_DELIVERY) == public_types

    agents = make_roster({}, num_players=12)
    config = GameConfig.dev_small(num_players=12, num_rounds=2)
    game = Game(config=config, agents=agents, seed=1, logger=EventLogger(), stop_after_round=1)
    game.run()
    sample_state = game._build_visible_state(2, 1, for_player_id="P01")

    for event_type, fields in PUBLIC_EVENT_DELIVERY.items():
        for field in fields:
            assert field in sample_state, f"{event_type} -> {field!r} not in visible_state"


def test_public_event_payloads_reach_players() -> None:
    """
    実際にログへ出た公開イベントのデータが、渡し始める時点以降の
    visible_state のスナップショットの該当項目に、値まで一致して
    入っていることを突き合わせる。
    """
    game, recorder = _build_vote_settlement_disclosure_game(seed=56)
    by_turn = _negotiate_snapshots_by_key(recorder)
    logger_events = game.logger.events

    violation_event = next(
        e for e in logger_events
        if e.event_type == "TYPE_B_VIOLATION" and e.round_num == 1 and e.vote_num == 1
    )
    shortfall_event = next(
        e for e in logger_events
        if e.event_type == "PAYMENT_SHORTFALL" and e.round_num == 1 and e.vote_num == 1
    )
    auto_events = [
        e for e in logger_events
        if e.event_type == "AUTO_COMMIT" and e.round_num == 1 and e.vote_num == 1
    ]
    revealed_event = next(
        e for e in logger_events
        if e.event_type == "VOTE_REVEALED" and e.round_num == 1 and e.vote_num == 1
    )
    resolved_event = next(
        e for e in logger_events
        if e.event_type == "VOTE_RESOLVED" and e.round_num == 1 and e.vote_num == 1
    )

    v1_view = by_turn[(1, 2, 1)]["round_vote_results"][0]
    assert v1_view["type_b_violator_ids"] == violation_event.data["player_ids"]
    assert v1_view["payment_shortfall_ids"] == shortfall_event.data["player_ids"]
    assert sorted(v1_view["auto_commit_ids"]) == sorted(e.data["player_id"] for e in auto_events)
    assert sorted(v1_view["yes_ids"]) == sorted(
        pid for pid, vote in revealed_event.data["votes"].items() if vote == "YES"
    )
    assert sorted(v1_view["no_ids"]) == sorted(
        pid for pid, vote in revealed_event.data["votes"].items() if vote == "NO"
    )
    assert v1_view["eliminated_ids"] == resolved_event.data["eliminated_ids"]
    assert v1_view["remaining_ids"] == resolved_event.data["remaining_ids"]

    turn_counts = [
        e.data["count"] for e in logger_events
        if e.event_type == "TURN_CONTRACTS_ESTABLISHED" and e.round_num == 1 and e.vote_num == 1
    ]
    v2_turn_counts = by_turn[(1, 2, 1)]["round_contract_counts"]
    assert sum(turn_counts) == sum(c["count"] for c in v2_turn_counts)

    resolved_round_event = next(
        e for e in logger_events if e.event_type == "ROUND_RESOLVED" and e.round_num == 1
    )
    payout_events = [
        e for e in logger_events if e.event_type == "ROUND_PAYOUT" and e.round_num == 1
    ]
    # 単一ラウンドの試合なので、R1終了後の視点はpost_game_reflectの文脈で確かめる
    assert game.round_summaries, "R1の精算が完了していない"
    r1_summary_view = game._round_result_view(game.round_summaries[0])
    assert sorted(r1_summary_view["winner_ids"]) == sorted(resolved_round_event.data["winner_ids"])
    assert r1_summary_view["payout_per_winner"] == resolved_round_event.data["payout_per_winner"]
    assert {e.data["player_id"] for e in payout_events} == set(r1_summary_view["winner_ids"])
