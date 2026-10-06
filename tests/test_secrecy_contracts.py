"""
契約の公開・秘匿のテスト（§8）

公開するのは契約の存在・当事者名・成立順だけで、内容（義務・金額・相手）は
当事者だけが見られること。署名待ちの提案は、相手側の画面に contract_id と
内容が必ず出ることを確認する。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import ContractProposeAction
from tests.helpers import make_roster


def _build_game_with_proposal_and_contract() -> Game:
    agents = make_roster({r: {} for r in range(1, 13)}, num_players=12)
    terms = [{"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
              "round_num": 3, "details": {"amount": 1_234_567}}]
    agents["P01"].negotiate_actions[(1, 1)] = ContractProposeAction(
        player_id="P01", with_players=["P02"], terms=terms,
    )
    agents["P02"].sign_proposer_at[(1, 2)] = "P01"

    game = Game(config=GameConfig.default_12(), agents=agents, seed=9, logger=EventLogger())
    game.run()
    return game


def test_established_contract_is_public_but_contentless() -> None:
    """成立した契約は存在・当事者名・成立順だけが全員に見える（内容は出ない）"""
    game = _build_game_with_proposal_and_contract()
    state = game._build_visible_state(12, for_player_id="P09")  # 当事者ではない第三者
    public = state["contracts_public"]
    assert len(public) == 1
    entry = public[0]
    assert set(entry.keys()) == {"contract_id", "parties", "contract_seq", "round_established"}
    assert entry["parties"] == ["P01", "P02"]
    assert entry["contract_seq"] == 1

    # 第三者には my_contracts/contracts_pending に当該契約が出ない
    assert state["my_contracts"] == []
    assert state["contracts_pending"] == []


def test_non_party_visible_state_has_no_amount_anywhere() -> None:
    """当事者以外の公開情報のどこにも金額（1,234,567）が出ない"""
    game = _build_game_with_proposal_and_contract()
    state = game._build_visible_state(12, for_player_id="P09")
    assert "1234567" not in str(state) and "1,234,567" not in str(state)


def test_party_sees_contract_content_in_my_contracts() -> None:
    """当事者には義務の内容（金額込み）がmy_contractsに出る"""
    game = _build_game_with_proposal_and_contract()
    state = game._build_visible_state(12, for_player_id="P01")
    my_contracts = state["my_contracts"]
    assert len(my_contracts) == 1
    ob = my_contracts[0]["obligations"][0]
    assert ob["details"]["amount"] == 1_234_567


def test_pending_proposal_shows_contract_id_and_content_to_counterparty() -> None:
    """
    署名待ちの提案は、相手側の画面に contract_id と内容が必ず出る（§8）
    """
    agents = make_roster({r: {} for r in range(1, 13)}, num_players=12)
    terms = [{"obligor": "P03", "counterparty": "P04", "ob_type": "type_a_payment",
              "round_num": 5, "details": {"amount": 777_000}}]
    agents["P03"].negotiate_actions[(1, 1)] = ContractProposeAction(
        player_id="P03", with_players=["P04"], terms=terms,
    )
    # P04はあえて署名しない（提案のまま）

    game = Game(config=GameConfig.default_12(), agents=agents, seed=9, logger=EventLogger())

    # 提案直後のP04視点で、提案中の契約が見えることを確認する
    # （まだ署名していないので my_contracts には出ず、contracts_pending に出る）
    original_execute = game._execute_negotiation_action
    captured: dict = {}

    def _capture(action, pid, round_num, turn):
        original_execute(action, pid, round_num, turn)
        if pid == "P03" and round_num == 1 and turn == 1:
            captured["state"] = game._build_visible_state(1, for_player_id="P04")

    game._execute_negotiation_action = _capture
    game.run()

    assert "state" in captured
    pending = captured["state"]["contracts_pending"]
    assert len(pending) == 1
    assert pending[0]["proposer"] == "P03"
    assert pending[0]["obligations"][0]["details"]["amount"] == 777_000


def test_contracts_public_excludes_proposed_and_expired() -> None:
    """未成立（PROPOSED）・失効（EXPIRED）の契約は contracts_public に出ない"""
    agents = make_roster({r: {} for r in range(1, 13)}, num_players=12)
    terms = [{"obligor": "P05", "counterparty": "P06", "ob_type": "type_a_payment",
              "round_num": 3, "details": {"amount": 100_000}}]
    agents["P05"].negotiate_actions[(1, 1)] = ContractProposeAction(
        player_id="P05", with_players=["P06"], terms=terms,
    )
    # P06は署名しない → ラウンド末にEXPIREDになる

    game = Game(config=GameConfig.default_12(), agents=agents, seed=9, logger=EventLogger())
    game.run()

    state = game._build_visible_state(12, for_player_id="P01")
    assert state["contracts_public"] == []
