"""
プロンプトのテスト（§9・§13、AIを呼ばない）

ルール全文の数値が設定と一致すること／モデル名が出ないこと／JSONを求める
文面に「JSON」の語があること／署名待ちの提案が相手の文面にcontract_id付きで
出ること／当事者でない人の文面に契約の内容が出ないこと／財務通知に
「返済不可」が出ること／投票文面に型B指定と違約金が出ること／YES・NO両指定の
事実が出ること／環境・利用者に触れない一文（ANONYMIZATION_LINE）が
provider側の定数と同一で、DevRelay席でも全席で文面が完全に同じになること、
を確認する。
"""

from engine.config import GameConfig
from engine.game import Game
from engine.models import ContractProposeAction
from engine.negotiation import StubAgent
from llm.models import MODEL_REGISTRY
from llm.prompt_builder import (
    ANONYMIZATION_LINE,
    build_commit_prompt,
    build_loan_prompt,
    build_negotiation_prompt,
    build_post_game_reflection_prompt,
    build_reflection_prompt,
    build_system_prompt,
)
from llm.providers.devrelay_http import ANONYMIZATION_LINE as PROVIDER_ANONYMIZATION_LINE


def _make_game(config=None, seed=1):
    config = config or GameConfig.default_12()
    agents = {f"P{i:02d}": StubAgent() for i in range(1, config.num_players + 1)}
    g = Game(config, agents, seed=seed)
    g._setup()
    g.current_round = 1
    g._phase_open(1)
    return g


# --- ルール全文の数値が設定と一致すること ---

def test_system_prompt_numbers_match_config():
    config = GameConfig.default_12()
    text = build_system_prompt("P01", config)
    assert f"プレイヤー{config.num_players}体" in text
    assert f"全{config.num_rounds}ラウンド" in text
    assert f"参加費は1人{config.entry_fee // 10_000}万円" in text
    assert f"{config.loan_min // 10_000}万〜{config.loan_max // 10_000}万円の範囲で借入額" in text
    assert "1.5%の複利" in text
    assert "3%の複利" in text
    assert f"{config.debt_cap // 10_000}万円" in text
    assert f"違約金{config.penalty_amount // 10_000}万円" in text
    assert f"最大{config.negotiation_max_turns}巡" in text
    # 持ち越しなしの少数派配当（12人10万円のとき+110万/+50万/+30万/+20万/+14万、多数派-10万）
    assert "1人なら+110万" in text
    assert "2人なら+50万" in text
    assert "3人なら+30万" in text
    assert "4人なら+20万" in text
    assert "5人なら+14万" in text
    assert "多数派は1人−10万" in text


def test_system_prompt_numbers_scale_with_custom_config():
    """設定を変えても配当・利率・上限の数値が追従すること（ハードコードでないことの確認）"""
    config = GameConfig(entry_fee=50_000, penalty_amount=2_000_000, debt_cap=20_000_000)
    text = build_system_prompt("P01", config)
    assert "参加費は1人5万円" in text
    assert "違約金200万円" in text
    assert "2000万円" in text
    # 12人・参加費5万のとき、少数派1人の得 = (11*5万)//1 = 55万
    assert "1人なら+55万" in text


def test_loan_prompt_numbers_match_config():
    config = GameConfig.default_12()
    text = build_loan_prompt(config)
    assert f"{config.loan_min // 10_000}万〜{config.loan_max // 10_000}万円の範囲で選べ" in text


# --- モデル名が出ないこと ---

def test_system_prompt_does_not_leak_model_id():
    config = GameConfig.default_12()
    text = build_system_prompt("P01", config)
    for key, info in MODEL_REGISTRY.items():
        assert info.model_id not in text, f"{key} の model_id が system prompt に出ている"


def test_loan_prompt_does_not_leak_model_id():
    config = GameConfig.default_12()
    text = build_loan_prompt(config)
    for key, info in MODEL_REGISTRY.items():
        assert info.model_id not in text


# --- JSONを求める文面に「JSON」の語があること（全数検査） ---

def test_all_json_requesting_prompts_contain_json_word():
    config = GameConfig.default_12()
    g = _make_game(config)
    ps = g.players["P01"]
    vs = g._build_visible_state(1, for_player_id="P01")

    prompts = {
        "system": build_system_prompt("P01", config),
        "loan": build_loan_prompt(config),
        "negotiation": build_negotiation_prompt(ps, 1, 1, vs, config),
        "commit": build_commit_prompt(ps, 1, vs, config),
        "reflection": build_reflection_prompt(ps, 1, vs, config),
        "post_game": build_post_game_reflection_prompt(
            config, {"own_rank": 1, "own_rank_tied": False, "final_assets": 1_200_000},
        ),
    }
    for name, text in prompts.items():
        assert "JSON" in text, f"{name} プロンプトにJSONの語が無い"


# --- 署名待ちの提案が相手の文面にcontract_idつきで出ること ---

def test_pending_contract_shown_to_counterparty_with_contract_id_and_sign_method():
    config = GameConfig.default_12()
    g = _make_game(config)
    terms = [
        {"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
         "round_num": 5, "details": {"amount": 300000}},
    ]
    action = ContractProposeAction(player_id="P01", with_players=["P07"], terms=terms)
    g._execute_negotiation_action(action, "P01", 1, 1)
    contract_id = g.contracts[0].contract_id

    vs07 = g._build_visible_state(1, for_player_id="P07")
    text = build_negotiation_prompt(g.players["P07"], 1, 2, vs07, config)
    assert contract_id in text
    assert "300000" in text or "30万円" in text
    assert "contract_sign" in text


# --- 当事者でない人の文面に契約の内容が出ないこと ---

def test_contract_content_hidden_from_non_party():
    config = GameConfig.default_12()
    g = _make_game(config)
    terms = [
        {"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
         "round_num": 5, "details": {"amount": 300000}},
        {"obligor": "P07", "counterparty": "P01", "ob_type": "type_b_vote",
         "round_num": 5, "details": {"vote": "NO"}},
    ]
    action = ContractProposeAction(player_id="P01", with_players=["P07"], terms=terms)
    g._execute_negotiation_action(action, "P01", 1, 1)

    vs03 = g._build_visible_state(1, for_player_id="P03")
    text = build_negotiation_prompt(g.players["P03"], 1, 2, vs03, config)
    assert "300000" not in text
    assert "30万円" not in text
    assert "type_a_payment" not in text
    assert "type_b_vote" not in text
    # 存在と当事者名・成立順は公開情報として出てよい
    assert g.contracts[0].contract_id not in text  # 未成立（署名待ち）はcontract_idも非公開


def test_established_contract_public_facts_visible_to_non_party():
    """成立済み契約は、存在・当事者名・成立順だけが非当事者にも公開される"""
    config = GameConfig.default_12()
    g = _make_game(config)
    terms = [
        {"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
         "round_num": 5, "details": {"amount": 300000}},
    ]
    action = ContractProposeAction(player_id="P01", with_players=["P07"], terms=terms)
    g._execute_negotiation_action(action, "P01", 1, 1)
    sign = __import__("engine.models", fromlist=["ContractSignAction"]).ContractSignAction(
        player_id="P07", contract_id=g.contracts[0].contract_id,
    )
    g._execute_negotiation_action(sign, "P07", 1, 2)
    assert g.contracts[0].status.value == "active"

    vs03 = g._build_visible_state(1, for_player_id="P03")
    text = build_negotiation_prompt(g.players["P03"], 1, 3, vs03, config)
    assert g.contracts[0].contract_id in text  # 成立済みは存在が公開される
    assert "P01" in text and "P07" in text  # 当事者名は公開
    assert "300000" not in text  # 金額（内容）は非公開


# --- 財務通知に「返済不可」が出ること ---

def test_finance_notice_shows_non_repayable():
    config = GameConfig.default_12()
    g = _make_game(config)
    vs = g._build_visible_state(1, for_player_id="P01")
    text = build_negotiation_prompt(g.players["P01"], 1, 1, vs, config)
    assert "返済不可" in text


# --- 投票文面に型B指定と違約金が出ること・YES/NO両指定の事実が出ること ---

def test_commit_prompt_shows_type_b_obligation_and_penalty():
    config = GameConfig.default_12()
    g = _make_game(config)
    terms = [
        {"obligor": "P01", "counterparty": "P07", "ob_type": "type_b_vote",
         "round_num": 1, "details": {"vote": "YES"}},
    ]
    action = ContractProposeAction(player_id="P01", with_players=["P07"], terms=terms)
    g._execute_negotiation_action(action, "P01", 1, 1)
    sign = __import__("engine.models", fromlist=["ContractSignAction"]).ContractSignAction(
        player_id="P07", contract_id=g.contracts[0].contract_id,
    )
    g._execute_negotiation_action(sign, "P07", 1, 2)

    vs01 = g._build_visible_state(1, for_player_id="P01")
    text = build_commit_prompt(g.players["P01"], 1, vs01, config)
    assert "YES" in text
    assert f"{config.penalty_amount // 10_000}万円" in text


def test_commit_prompt_shows_both_yes_and_no_obligation_fact():
    config = GameConfig.default_12()
    g = _make_game(config)
    terms = [
        {"obligor": "P01", "counterparty": "P07", "ob_type": "type_b_vote",
         "round_num": 1, "details": {"vote": "YES"}},
        {"obligor": "P01", "counterparty": "P09", "ob_type": "type_b_vote",
         "round_num": 1, "details": {"vote": "NO"}},
    ]
    action1 = ContractProposeAction(player_id="P01", with_players=["P07"], terms=terms[:1])
    g._execute_negotiation_action(action1, "P01", 1, 1)
    sign1 = __import__("engine.models", fromlist=["ContractSignAction"]).ContractSignAction(
        player_id="P07", contract_id=g.contracts[0].contract_id,
    )
    g._execute_negotiation_action(sign1, "P07", 1, 2)

    action2 = ContractProposeAction(player_id="P01", with_players=["P09"], terms=terms[1:])
    g._execute_negotiation_action(action2, "P01", 1, 3)
    sign2 = __import__("engine.models", fromlist=["ContractSignAction"]).ContractSignAction(
        player_id="P09", contract_id=g.contracts[1].contract_id,
    )
    g._execute_negotiation_action(sign2, "P09", 1, 4)

    vs01 = g._build_visible_state(1, for_player_id="P01")
    text = build_commit_prompt(g.players["P01"], 1, vs01, config)
    assert "YES指定とNO指定の両方" in text


# --- 環境・利用者に触れない一文（ANONYMIZATION_LINE）が全席で完全に同じになること ---

def test_anonymization_line_matches_provider_constant():
    assert ANONYMIZATION_LINE == PROVIDER_ANONYMIZATION_LINE


def test_api_seat_system_prompt_ends_with_anonymization_line_once():
    config = GameConfig.default_12()
    text = build_system_prompt("P01", config, include_anonymization=True)
    assert text.endswith(ANONYMIZATION_LINE)
    assert text.count(ANONYMIZATION_LINE) == 1


def test_devrelay_seat_system_prompt_excludes_anonymization_line():
    config = GameConfig.default_12()
    text = build_system_prompt("P01", config, include_anonymization=False)
    assert ANONYMIZATION_LINE not in text


def test_devrelay_and_api_seats_receive_byte_identical_final_system_text():
    """
    DevRelay席はprovider側（llm/providers/devrelay_http.py::_system_with_notices）が
    送信直前にANONYMIZATION_LINEを付与するため、build_system_prompt側では
    include_anonymization=Falseで付けない。最終的にモデルへ渡る文面は
    API席（include_anonymization=True）とバイト単位で完全に一致すること。
    """
    config = GameConfig.default_12()
    api_final = build_system_prompt("P01", config, include_anonymization=True)
    devrelay_base = build_system_prompt("P01", config, include_anonymization=False)
    # provider._system_with_notices()と同じ連結方法（Codex専用行は対象外のケース）
    devrelay_final = f"{devrelay_base}\n\n{ANONYMIZATION_LINE}"
    assert devrelay_final == api_final
