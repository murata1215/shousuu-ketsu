"""
プロンプトのテスト（§9・§13、AIを呼ばない）

ルール全文・借入文面の逐語一致は `tests/test_prompt_builder_rules.py` が固定する。
本ファイルは、`visible_state`（実際にゲームを動かして得たもの）から組み立てた
手番ごとの文面が、承認済みの見出しの順番・事実を正しく含むこと、横断表
（PROMPT_COVERAGE/PROMPT_SUPERSEDED/POST_GAME_COVERAGE）が`_build_visible_state`・
`post_game_context`のキー集合と完全一致すること、モデル名が出ないこと、
金額が1円単位・桁区切りで出ること、不成立の理由が日本語で本人にだけ出ること、
退場中の表示・交渉の巡の上限が正しいことを確認する。
"""

from __future__ import annotations

import pytest

from engine import player as player_ops
from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import (
    BroadcastAction, ContractProposeAction, PlayerState, TransferAction, Vote,
)
from llm.models import MODEL_REGISTRY
from llm.prompt_builder import (
    ANONYMIZATION_LINE,
    POST_GAME_COVERAGE,
    PROMPT_COVERAGE,
    PROMPT_SUPERSEDED,
    build_commit_prompt,
    build_loan_prompt,
    build_negotiation_prompt,
    build_post_game_reflection_prompt,
    build_reflection_prompt,
    build_system_prompt,
)
from tests.helpers import ScriptedAgent, VisibleStateRecordingAgent, make_roster

V1_YES = {"P01", "P03", "P05", "P07", "P09"}
V2_YES = {"P01", "P07"}


def _ps(pid: str) -> PlayerState:
    """build_*_promptはplayer_state.player_idしか読まないため、最小限のダミーでよい"""
    return PlayerState(player_id=pid, cash=0, initial_loan=0)


def _build_fixture_game(seed: int = 1):
    """
    R1V1で7対5決着（P01・P03・P05・P07・P09が残る）→ R1V2で3対2決着
    （P01・P07が残ってラウンド終了・勝ち残り）となる試合を組み立てる。

    - P01がV1の1巡目にP07へ型A契約（R1終了時300,000円）を提案し、
      P07がV1の2巡目に署名して成立する
    - P07がV1の1巡目に全体発言する
    - P09がV2の1巡目に手持ちを超える送金を試みて不成立になる
    - P02はV1で退場し、V2の交渉にも退場中のまま参加する

    全員（退場者を含む）が毎巡呼ばれるため（§7.2）、P01・P02・P03・P07・P09は
    同じ(round_num, vote_num, turn)の組で記録される。
    """
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
                terms=[{"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
                        "round_num": 1, "details": {"amount": 300_000}}],
            ),
        },
    )
    agents["P07"].sign_proposer_at[(1, 1, 2)] = "P01"
    agents["P07"].negotiate_actions[(1, 1, 1)] = BroadcastAction(
        player_id="P07", message="最初はYESでそろえたい",
    )
    agents["P09"] = ScriptedAgent(
        "P09", votes=agents["P09"].votes, loan=1_200_000,
        negotiate_actions={
            (1, 2, 1): TransferAction(player_id="P09", to="P01", amount=50_000_000),
        },
    )

    recorders: dict[str, VisibleStateRecordingAgent] = {}
    for pid in ("P01", "P02", "P03", "P07", "P09"):
        recorders[pid] = VisibleStateRecordingAgent(agents[pid])
        agents[pid] = recorders[pid]

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=seed, logger=EventLogger())
    result = game.run()
    snapshots = {
        pid: {(r, v, t): vs for r, v, t, vs in rec.negotiate_snapshots}
        for pid, rec in recorders.items()
    }
    reflect_snapshots = {
        pid: {r: vs for r, vs in rec.reflect_snapshots}
        for pid, rec in recorders.items()
    }
    return game, config, result, snapshots, reflect_snapshots


@pytest.fixture(scope="module")
def fixture_game():
    return _build_fixture_game()


def _post_game_context(game, result, pid: str) -> dict:
    """engine/game.py::_phase_post_game_reflectionと同じ形のcontextを組み立てる"""
    ranks = player_ops.assets_ranking(game.players.values())
    r = ranks[pid]
    round_results = [game._round_result_view(rr) for rr in game.round_summaries]
    all_final_assets = dict(sorted(result.final_assets.items()))
    all_final_ranks = [
        {"player_id": p, "rank": rk.rank, "tied": rk.tied}
        for p, rk in sorted(ranks.items(), key=lambda kv: (kv[1].rank, kv[0]))
    ]
    return {
        "own_rank": r.rank, "own_rank_tied": r.tied,
        "final_assets": result.final_assets.get(pid),
        "round_results": round_results,
        "all_final_ranks": all_final_ranks,
        "all_final_assets": all_final_assets,
    }


# --- ルール全文・借入文面の数値が設定と一致すること ---

def test_system_prompt_numbers_match_config():
    config = GameConfig.default_12()
    text = build_system_prompt("P01", config)
    assert f"プレイヤー{config.num_players}体" in text
    assert f"全{config.num_rounds}ラウンド" in text
    assert f"参加費{config.entry_fee // 10_000}万円" in text
    assert f"延長料{config.extension_fee // 10_000}万円" in text
    assert f"{config.loan_min // 10_000}万〜{config.loan_max // 10_000}万円の範囲で借入額" in text
    assert "5%の複利" in text
    assert "15%の複利" in text
    assert f"{config.debt_cap // 10_000}万円" in text
    assert f"違約金{config.penalty_amount // 10_000}万円" in text
    assert f"{config.negotiation_max_turns_first}" in text
    assert f"{config.negotiation_max_turns_next}" in text
    assert f"{config.negotiation_max_turns_retry}" in text
    assert "あなたはP01です" in text


def test_system_prompt_numbers_scale_with_custom_config():
    """設定を変えても利率・上限の数値が追従すること（ハードコードでないことの確認）"""
    config = GameConfig(
        interest_rate_pre_num=7, interest_rate_pre_den=100,
        interest_rate_post_num=20, interest_rate_post_den=100,
        penalty_amount=2_000_000, debt_cap=20_000_000,
    )
    text = build_system_prompt("P01", config)
    assert "7%の複利" in text
    assert "20%の複利" in text
    assert "違約金200万円" in text
    assert "2000万円" in text


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

def test_all_json_requesting_prompts_contain_json_word(fixture_game):
    """
    §13.2の借入文面自体には"JSON"の語が無い（仕様書どおり）。システムプロンプト
    （§13.1）には必ず含まれ、`build_json_object_request_options`
    （llm/llm_agent.py）はsystem+userを合わせて見るため、借入の呼び出しも
    実際には"json"の語を含む文脈で送信される（llm_agent.pyのsystem_promptは
    常に先に構築済み）。本テストは借入以外の単体プロンプトを確認する
    （仕様書とエンジンの食い違い#7、変更不要・報告のみ）。
    """
    _game, config, result, snapshots, reflect_snapshots = fixture_game
    vs = snapshots["P01"][(1, 1, 1)]
    post_ctx = _post_game_context(_game, result, "P01")
    prompts = {
        "negotiation": build_negotiation_prompt(_ps("P01"), 1, 1, 1, vs, config),
        "commit": build_commit_prompt(_ps("P01"), 1, 1, vs, config),
        "reflection": build_reflection_prompt(_ps("P01"), 1, reflect_snapshots["P01"][1], config),
        "post_game": build_post_game_reflection_prompt(config, post_ctx),
    }
    for name, text in prompts.items():
        assert "JSON" in text, f"{name} プロンプトにJSONの語が無い"
    assert "JSON" in build_system_prompt("P01", config)
    # 借入文面自体には無いが、システムプロンプト（必ず先に送られる）には必ずある
    assert "JSON" not in build_loan_prompt(config)


# --- 見出しの1行目（R{r}V{v}・巡・上限） ---

def test_negotiation_heading_shows_round_vote_turn_and_max_turns_first(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P01"][(1, 1, 1)]
    text = build_negotiation_prompt(_ps("P01"), 1, 1, 1, vs, config)
    assert "=== R1V1 / 交渉（1巡目 / この投票の上限10巡） ===" in text.splitlines()[0]


def test_negotiation_heading_shows_max_turns_next_after_decisive_vote(fixture_game):
    """V1が決着したあとのV2は「決着の後の投票」の上限（既定6巡）を使う"""
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P01"][(1, 2, 1)]
    assert vs["negotiation_max_turns"] == config.negotiation_max_turns_next
    text = build_negotiation_prompt(_ps("P01"), 1, 2, 1, vs, config)
    assert f"この投票の上限{config.negotiation_max_turns_next}巡" in text.splitlines()[0]


def test_commit_heading_shows_round_and_vote(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P01"][(1, 1, 3)]
    text = build_commit_prompt(_ps("P01"), 1, 1, vs, config)
    assert text.splitlines()[0] == "=== R1V1 / 投票（Commit） ==="


def test_reflection_heading_shows_round_only(fixture_game):
    _game, config, _result, _s, reflect_snapshots = fixture_game
    text = build_reflection_prompt(_ps("P01"), 1, reflect_snapshots["P01"][1], config)
    assert text.splitlines()[0] == "=== R1 / 振り返り（引き継ぎメモ） ==="


# --- 退場中の表示（承認済み決定6） ---

def test_retired_player_situation_shows_fixed_sentence(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P02"][(1, 2, 1)]
    assert "P02" in vs["eliminated_ids"]
    text = build_negotiation_prompt(_ps("P02"), 1, 2, 1, vs, config)
    assert (
        "あなた（P02）はこのラウンド（R1）で退場しています。"
        "このラウンドの投票はできません。次のラウンドには戻ります。"
    ) in text


def test_remaining_player_situation_has_no_retired_sentence(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P01"][(1, 2, 1)]
    assert "P01" not in vs["eliminated_ids"]
    text = build_negotiation_prompt(_ps("P01"), 1, 2, 1, vs, config)
    assert "退場しています" not in text


# --- 不成立の理由が日本語で本人にだけ出ること（§7.5、受け入れ#57の文面段階確認） ---

def test_last_action_error_shown_in_japanese_to_actor_only(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs09 = snapshots["P09"][(1, 2, 2)]
    assert vs09["my_last_action_error"] is not None
    text09 = build_negotiation_prompt(_ps("P09"), 1, 2, 2, vs09, config)
    assert "不成立:" in text09
    assert "送金できるのは現金の範囲内だけです" in text09
    assert "Insufficient cash" not in text09
    assert "50,000,000円" in text09  # 桁区切り（E2と同じ書式）
    assert "200,000円" in text09

    # 他プレイヤーには出ない
    vs01 = snapshots["P01"][(1, 2, 2)]
    text01 = build_negotiation_prompt(_ps("P01"), 1, 2, 2, vs01, config)
    assert "不成立:" not in text01


def test_no_action_error_shows_fact_not_blank(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P01"][(1, 1, 1)]
    assert vs["my_last_action_error"] is None
    text = build_negotiation_prompt(_ps("P01"), 1, 1, 1, vs, config)
    assert "## 直前の行動の結果\n  まだありません" in text


# --- 契約: 署名待ちの提案がcontract_id付きで相手に出ること／成立後は当事者双方に出ること ---

def test_pending_contract_shown_to_counterparty_with_contract_id_and_sign_method(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P07"][(1, 1, 2)]
    text = build_negotiation_prompt(_ps("P07"), 1, 1, 2, vs, config)
    contract_id = vs["contracts_pending"][0]["contract_id"]
    assert contract_id in text
    assert "300,000円" in text
    assert "contract_sign" in text


def test_established_contract_shown_to_both_parties(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs01 = snapshots["P01"][(1, 1, 3)]
    vs07 = snapshots["P07"][(1, 1, 3)]
    text01 = build_negotiation_prompt(_ps("P01"), 1, 1, 3, vs01, config)
    text07 = build_negotiation_prompt(_ps("P07"), 1, 1, 3, vs07, config)
    assert "あなたは P07へ300,000円支払わなければならない" in text01
    assert "P01は あなたへ300,000円支払わなければならない" in text07


def test_contract_fully_hidden_from_non_party(fixture_game):
    """v0.4は契約の存在そのものが非公開（§1.2/§8）。当事者以外には一切出ない"""
    _game, config, _result, snapshots, _r = fixture_game
    vs03 = snapshots["P03"][(1, 1, 3)]
    text03 = build_negotiation_prompt(_ps("P03"), 1, 1, 3, vs03, config)
    assert "300,000円" not in text03
    assert "type_a_payment" not in text03
    contract_id = snapshots["P01"][(1, 1, 3)]["my_contracts"][0]["contract_id"]
    assert contract_id not in text03


# --- 財務通知: 返済不可の表記が重ならないこと（承認済み決定7） ---

def test_finance_notice_shows_non_repayable_rate_once(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P01"][(1, 1, 1)]
    text = build_negotiation_prompt(_ps("P01"), 1, 1, 1, vs, config)
    assert "開始前の借金（5%・返済不可）" in text
    assert text.count("返済不可") == 1
    assert "開始後の借金（15%）" in text


def test_finance_notice_includes_current_rank(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P01"][(1, 1, 1)]
    text = build_negotiation_prompt(_ps("P01"), 1, 1, 1, vs, config)
    assert "あなたの現在順位" in text
    assert "/ 12人" in text


# --- 投票文面に型B指定・違約金が出ること ---

def test_commit_prompt_shows_type_b_obligation_and_penalty():
    votes_by_vote = {(1, 1): {f"P{i:02d}": Vote.YES for i in range(1, 13)}}
    agents = make_roster(votes_by_vote, num_players=12)
    agents["P01"] = ScriptedAgent(
        "P01", votes=agents["P01"].votes, loan=1_200_000,
        negotiate_actions={
            (1, 1, 1): ContractProposeAction(
                player_id="P01", with_players=["P07"],
                terms=[{"obligor": "P01", "counterparty": "P07", "ob_type": "type_b_vote",
                        "round_num": 1, "vote_num": 1, "details": {"vote": "YES"}}],
            ),
        },
    )
    agents["P07"].sign_proposer_at[(1, 1, 2)] = "P01"
    recorder = VisibleStateRecordingAgent(agents["P01"])
    agents["P01"] = recorder

    config = GameConfig.dev_small(num_players=12, num_rounds=1)
    game = Game(config=config, agents=agents, seed=2, logger=EventLogger())
    game.run()
    snaps = {(r, v, t): vs for r, v, t, vs in recorder.negotiate_snapshots}
    vs = snaps[(1, 1, 3)]
    text = build_commit_prompt(_ps("P01"), 1, 1, vs, config)
    assert "YESへ投票しなければならない" in text
    assert f"{config.penalty_amount:,}円" in text


# --- 横断テスト1: PROMPT_COVERAGE/PROMPT_SUPERSEDEDがvisible_stateのキー集合と完全一致 ---

def test_prompt_coverage_matches_visible_state_keys_exactly(fixture_game):
    game, _config, _result, _s, _r = fixture_game
    sample_state = game._build_visible_state(1, 1, for_player_id="P01")
    covered = set(PROMPT_COVERAGE) | set(PROMPT_SUPERSEDED)
    assert covered == set(sample_state), (
        f"visible_stateのキーとPROMPT_COVERAGE/PROMPT_SUPERSEDEDの対応が崩れている: "
        f"visible_stateのみ={set(sample_state) - covered}, 表のみ={covered - set(sample_state)}"
    )
    # supersededの値は全てcoverageのキー（別の項目が上位集合として含む先）
    assert set(PROMPT_SUPERSEDED.values()) <= set(PROMPT_COVERAGE)


# --- 横断テスト2: 各見出しと代表値が実際の文面に現れること ---

def test_prompt_coverage_headings_and_representative_values_appear(fixture_game):
    _game, config, _result, snapshots, _r = fixture_game
    vs = snapshots["P01"][(1, 1, 3)]
    text = build_negotiation_prompt(_ps("P01"), 1, 1, 3, vs, config)

    for key, heading in PROMPT_COVERAGE.items():
        assert heading in text, f"{key} の見出し {heading!r} が文面に無い"

    # 代表値
    assert "開始前の借入額（全員公開）" in text
    for pid in vs["initial_loans"]:
        assert pid in text
    assert vs["my_contracts"][0]["contract_id"] in text


# --- post_game_contextの横断テスト ---

def test_post_game_coverage_matches_context_keys_exactly(fixture_game):
    game, _config, result, _s, _r = fixture_game
    ctx = _post_game_context(game, result, "P01")
    assert set(POST_GAME_COVERAGE) == set(ctx)


def test_post_game_reflection_prompt_includes_round_results_and_all_final_ranks(fixture_game):
    game, config, result, _s, _r = fixture_game
    ctx = _post_game_context(game, result, "P01")
    text = build_post_game_reflection_prompt(config, ctx)
    assert "## これまでのラウンドの結果" in text
    assert "## 全員の最終順位と最終資産" in text
    for pid, amount in ctx["all_final_assets"].items():
        assert pid in text
        assert f"{amount:,}円" in text


# --- 環境・利用者に触れない一文（ANONYMIZATION_LINE）が全席で完全に同じになること ---

def test_anonymization_line_matches_provider_constant():
    from llm.providers.devrelay_http import ANONYMIZATION_LINE as PROVIDER_ANONYMIZATION_LINE
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
    devrelay_final = f"{devrelay_base}\n\n{ANONYMIZATION_LINE}"
    assert devrelay_final == api_final
