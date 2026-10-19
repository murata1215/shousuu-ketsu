"""
本人執筆手記の素材構築（`blog/self_memoir.py`）のテスト（サイクル6.0新設）

`tests/fixtures/viewer_logs/fx_blog_*`（4席・2ラウンド、P02が型Bの違反と型Cの
「少数派」判定の両方を持つ契約の当事者、山分け契約C_POOL1が2ラウンド連続で発火する）
で、他席の内心・DM・契約の漏洩が無いこと・予算内に収まること・決定的であることを
確認する。本番試合（12席）での予算超過が無いことは`logs/`がある環境でのみ追加確認する
（CLAUDE.md過去の落とし穴⑲: 検査だけでなく実際に使う形まで通す）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blog.self_memoir import MAX_TOTAL_CHARS_DEFAULT, build_self_memoir_material

FX_DIR = Path(__file__).resolve().parent / "fixtures" / "viewer_logs"
FX_GAME = "fx_blog"

REAL_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs" / "llm_v0_4"
REAL_GAME = "t12_r4_4501c"
REAL_SEATS = [f"P{i:02d}" for i in range(1, 13)]


def test_material_within_budget_for_all_fixture_seats() -> None:
    for pid in ["P01", "P02", "P03", "P04"]:
        m = build_self_memoir_material(FX_DIR, FX_GAME, pid)
        assert m.total_chars <= MAX_TOTAL_CHARS_DEFAULT, f"{pid}: {m.total_chars} > {MAX_TOTAL_CHARS_DEFAULT}"


def test_material_is_deterministic() -> None:
    m1 = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    m2 = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    assert m1.user_prompt == m2.user_prompt
    assert m1.system_prompt == m2.system_prompt
    assert m1.stats == m2.stats


def test_own_identity_and_model_name_revealed() -> None:
    """決定①②: 本人には自分の席番号・モデル名を明示する。"""
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    assert "P01" in m.system_prompt
    assert "claude-opus-5" in m.system_prompt


def test_other_seats_model_names_present_in_roster() -> None:
    """決定②: 他席の呼び方（席番号＋モデル名）のために、全席の名簿を素材へ入れる。"""
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    assert "P02（gpt-4.1-mini）" in m.user_prompt
    assert "P03（grok-4.3）" in m.user_prompt
    assert "P04（gemini-3.5-flash-lite）" in m.user_prompt


def test_no_leakage_of_other_players_internal_memory() -> None:
    """他席の内心メモ・引き継ぎメモは一切含めない。"""
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    assert "P01には見せない" not in m.user_prompt  # P02の内心メモ・引き継ぎメモに含まれる目印


def test_no_leakage_of_dm_not_involving_pid() -> None:
    """自分が当事者でないDMは一切含めない（P02→P03のDMはP01の素材に出ない）。"""
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    assert "P02からP03へのDM" not in m.user_prompt


def test_public_broadcast_from_other_seat_is_visible() -> None:
    """全体発言（broadcast）は公開情報のため、発言者が自分以外でも素材に含める。"""
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P03")
    assert "P01の全体発言その1" in m.user_prompt
    assert "P01の全体発言（公開）" in m.user_prompt


def test_own_dm_received_is_visible() -> None:
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P02")
    assert "P01からP02へのDM（V1）" in m.user_prompt
    assert "受け取ったDM" in m.user_prompt


def test_contract_not_involving_pid_is_excluded() -> None:
    """P04は当事者でないC_POOL1・C_MINOR1の中身を見てはいけない。"""
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P04")
    assert "C_POOL1" not in m.user_prompt
    assert "C_MINOR1" not in m.user_prompt
    # 自分が当事者のC_VOTE1は見える
    assert "C_VOTE1" in m.user_prompt


def test_pool_share_amount_rendered_for_party() -> None:
    """山分け（pool_share）の実額が、当事者の素材に入ること。"""
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    assert "山分け" in m.user_prompt
    assert "2,200,000円" in m.user_prompt  # R1の山分け支払い
    assert "2,000,000円" in m.user_prompt  # R2の山分け支払い


def test_omission_note_appears_when_over_budget() -> None:
    """予算が極端に小さいと、6〜8章の可変部が削られ、省略の注記が付く
    （項目は丸ごと落とすか残すかの二択。固定部(§1-5,9)は上限なしの設計のため切り詰めない）。
    """
    m_tight = build_self_memoir_material(FX_DIR, FX_GAME, "P02", max_total_chars=3500)
    m_default = build_self_memoir_material(FX_DIR, FX_GAME, "P02")
    assert "省略" in m_tight.user_prompt
    assert m_tight.total_chars < m_default.total_chars
    total_omitted_tight = (
        m_tight.stats["strategy_omitted"] + m_tight.stats["message_omitted"] + m_tight.stats["memory_omitted"]
    )
    total_omitted_default = (
        m_default.stats["strategy_omitted"] + m_default.stats["message_omitted"] + m_default.stats["memory_omitted"]
    )
    assert total_omitted_tight > total_omitted_default


def test_rules_block_extracted_from_real_record_not_hardcoded() -> None:
    """ルール説明は、engine.configの既定値からの再構成ではなく、実際のllm_calls.jsonlの
    system_promptから抜き出す（CLAUDE.md過去の落とし穴⑦と同型の事故を避ける）。
    """
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    assert "# 少数決 ルール" in m.system_prompt
    assert "参加費100万円" in m.system_prompt  # 実際の試合のレコードに書かれた実測値
    assert "## アクション形式" not in m.system_prompt  # 行動の出力形式は手記に不要


def test_scene_links_point_to_real_event_lines() -> None:
    """scene_linksの行番号が、実際にevents.jsonlの行として解決できること。"""
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    assert m.scene_links
    events_path = FX_DIR / f"{FX_GAME}_events.jsonl"
    with open(events_path, encoding="utf-8") as f:
        total_lines = sum(1 for _ in f)
    for link in m.scene_links:
        assert 1 <= link.line <= total_lines, f"{link.label}: line={link.line} is out of range"


def test_quote_sources_contain_only_own_material() -> None:
    m = build_self_memoir_material(FX_DIR, FX_GAME, "P01")
    joined = "\n".join(m.quote_sources)
    assert "P01の内心理由V1" in joined
    assert "P02の内心理由V1" not in joined


# ---------------------------------------------------------------------------
# 本番試合（12席）での非回帰確認
# ---------------------------------------------------------------------------

pytestmark_real = pytest.mark.skipif(
    not (REAL_LOGS_DIR / f"{REAL_GAME}_events.jsonl").exists(),
    reason="本番試合t12_r4_4501cの記録がこの環境に無い（logs/はコミット対象外）",
)


@pytestmark_real
def test_real_game_all_12_seats_within_budget() -> None:
    for pid in REAL_SEATS:
        m = build_self_memoir_material(REAL_LOGS_DIR, REAL_GAME, pid)
        assert m.total_chars <= MAX_TOTAL_CHARS_DEFAULT, f"{pid}: {m.total_chars} > {MAX_TOTAL_CHARS_DEFAULT}"


@pytestmark_real
def test_real_game_pool_share_winner_material_has_payout_and_pool_share() -> None:
    """P06はR1・R3で単独勝ちし契約の支払い・受け取りが0件の席。P05はR2で山分けにより
    手取りがマイナスになった席（doc/analysis/t12_r4_4501c_facts.md参照）。
    """
    m06 = build_self_memoir_material(REAL_LOGS_DIR, REAL_GAME, "P06")
    assert "13,200,000円" in m06.user_prompt

    m05 = build_self_memoir_material(REAL_LOGS_DIR, REAL_GAME, "P05")
    assert "山分け" in m05.user_prompt
    assert "3,000,000円" in m05.user_prompt
