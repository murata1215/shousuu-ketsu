"""
契約の公開・秘匿のテスト（§8、v0.4）

v0.4は契約の当事者名も非公開になった（§1.2/§11.2暫定1）。公開するのは
「成立した」という事実と、各巡の終わりの成立本数だけである。内容
（義務・金額・相手）も当事者だけが見られる。署名待ちの提案は、相手側の
画面に contract_id と内容が必ず出ることを確認する。

サイクル4.0でv0.3から全面更新した。v0.3にあった `contracts_public`
（全員に見える契約の存在・当事者名・成立順の一覧）はv0.4で廃止した
（§8: 「当事者以外が見るイベントに、当事者名・内容・成立順が入らないこと」
§12.3 #43）。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import ContractProposeAction
from tests.helpers import make_roster


def _build_game_with_proposal_and_contract() -> Game:
    agents = make_roster({}, num_players=12)
    terms = [{"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
              "round_num": 2, "details": {"amount": 1_234_567}}]
    agents["P01"].negotiate_actions[(1, 1, 1)] = ContractProposeAction(
        player_id="P01", with_players=["P02"], terms=terms,
    )
    agents["P02"].sign_proposer_at[(1, 1, 2)] = "P01"

    game = Game(config=GameConfig.default_12(), agents=agents, seed=9, logger=EventLogger())
    game.run()
    return game


def test_acceptance_43_non_party_state_has_no_contract_trace() -> None:
    """#43: 当事者以外に渡す公開情報に、当事者名・内容・成立順が一切含まれない

    v0.4ではcontracts_publicという一覧自体が存在しない（my_contracts /
    contracts_pendingは当事者だけに入る）。
    """
    game = _build_game_with_proposal_and_contract()
    state = game._build_visible_state(4, 1, for_player_id="P09")  # 当事者ではない第三者

    assert "contracts_public" not in state
    assert state["my_contracts"] == []
    assert state["contracts_pending"] == []


def test_non_party_visible_state_has_no_amount_anywhere() -> None:
    """当事者以外の公開情報のどこにも金額（1,234,567）が出ない"""
    game = _build_game_with_proposal_and_contract()
    state = game._build_visible_state(4, 1, for_player_id="P09")
    assert "1234567" not in str(state) and "1,234,567" not in str(state)


def test_party_sees_contract_content_in_my_contracts() -> None:
    """当事者には義務の内容（金額込み）がmy_contractsに出る"""
    game = _build_game_with_proposal_and_contract()
    state = game._build_visible_state(4, 1, for_player_id="P01")
    my_contracts = state["my_contracts"]
    assert len(my_contracts) == 1
    ob = my_contracts[0]["obligations"][0]
    assert ob["details"]["amount"] == 1_234_567


def test_pending_proposal_shows_contract_id_and_content_to_counterparty() -> None:
    """
    署名待ちの提案は、相手側の画面に contract_id と内容が必ず出る（§8）
    """
    agents = make_roster({}, num_players=12)
    terms = [{"obligor": "P03", "counterparty": "P04", "ob_type": "type_a_payment",
              "round_num": 3, "details": {"amount": 777_000}}]
    agents["P03"].negotiate_actions[(1, 1, 1)] = ContractProposeAction(
        player_id="P03", with_players=["P04"], terms=terms,
    )
    # P04はあえて署名しない（提案のまま）

    game = Game(config=GameConfig.default_12(), agents=agents, seed=9, logger=EventLogger())

    # 提案直後のP04視点で、提案中の契約が見えることを確認する
    original_execute = game._execute_negotiation_action
    captured: dict = {}

    def _capture(action, pid, round_num, vote_num, turn):
        result = original_execute(action, pid, round_num, vote_num, turn)
        if pid == "P03" and round_num == 1 and vote_num == 1 and turn == 1:
            captured["state"] = game._build_visible_state(1, 1, for_player_id="P04")
        return result

    game._execute_negotiation_action = _capture
    game.run()

    assert "state" in captured
    pending = captured["state"]["contracts_pending"]
    assert len(pending) == 1
    assert pending[0]["proposer"] == "P03"
    assert pending[0]["obligations"][0]["details"]["amount"] == 777_000


def test_turn_disclosure_contains_only_count_no_names() -> None:
    """各巡の終わりの公示は成立本数だけで、当事者名を含まない（§6.1/§12.3 #43）"""
    game = _build_game_with_proposal_and_contract()
    established_events = [
        e for e in game.logger.events
        if e.event_type == "TURN_CONTRACTS_ESTABLISHED" and e.round_num == 1 and e.data.get("count", 0) > 0
    ]
    assert established_events
    for e in established_events:
        assert set(e.data.keys()) == {"turn", "count"}
        assert "P01" not in str(e.data) and "P02" not in str(e.data)
