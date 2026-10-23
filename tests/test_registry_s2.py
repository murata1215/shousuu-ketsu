"""
サイクル7.0: 第2戦（18体）ロスター（S2_*キー・NAMED_ROSTERS["L18_S2"]）のテスト

既存のH1〜L7・DR_*・TERRA・K25_EVAL・M3_G37_EVALは一切変更していないことを
前提にするため、このファイルではS2_*キーとNAMED_ROSTERS・parse_roster()の
組み合わせのみを検証する（既存キーの回帰は他のテストファイルが担当）。

CLAUDE.md過去の落とし穴④「同型バグの片側だけ直す事故」の再発防止として、
Anthropicの4席（S2_AN_*）が「考えさせない」の書き方を取り違えていないかを
1件ずつ機械的に確認する（Fable 5.1/Opus 5.5にthinking.type="disabled"を
送ると400になることを実測済みのため、ここに紛れ込んでいないことを固定する）。
"""

import pytest

from llm.models import MODEL_REGISTRY, NAMED_ROSTERS, get_model
from scripts.llm_trial import parse_roster


S2_KEYS = (
    "S2_AN_H1", "S2_AN_H2", "S2_AN_M", "S2_AN_L",
    "S2_OA_H", "S2_OA_M", "S2_OA_L",
    "S2_GO_H", "S2_GO_M", "S2_GO_L",
    "S2_XA_H", "S2_XA_M", "S2_XA_L",
    "S2_MO_H", "S2_MO_M", "S2_MO_L",
    "S2_DS_H", "S2_DS_L",
)

# 社ごとの階級の顔ぶれ（指示書の表どおり。DeepSeekのみミドルが無く2席）
EXPECTED_SHAPE = {
    "Anthropic": {"H": 2, "M": 1, "L": 1},  # ハイが2席（Fable 5.1 + Opus 5.5）
    "OpenAI": {"H": 1, "M": 1, "L": 1},
    "Google": {"H": 1, "M": 1, "L": 1},
    "xAI": {"H": 1, "M": 1, "L": 1},
    "Moonshot": {"H": 1, "M": 1, "L": 1},
    "DeepSeek": {"H": 1, "L": 1},
}

# 400実測済み: この3モデルにthinking.type="disabled"を送るとAPIが拒否する
# （Fable 5.1/Opus 5.5は"adaptive"+effort、Sonnet 5.5は"between_tools"のみ可）。
_REJECTS_THINKING_DISABLED = {"claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5"}

# 400実測済み: このモデルはthinking.type="enabled"以外を拒否する
# （無効化不可。けいすけさん決定4でこのまま採用）。
_REQUIRES_THINKING_ENABLED = {"kimi-k2.7-code"}


def test_exactly_18_seats_registered():
    assert len(S2_KEYS) == 18
    for key in S2_KEYS:
        assert key in MODEL_REGISTRY, f"{key} が MODEL_REGISTRY に無い"


def test_named_roster_l18_s2_matches_s2_keys():
    assert NAMED_ROSTERS["L18_S2"] == list(S2_KEYS)


def test_parse_roster_expands_named_roster():
    assert parse_roster("L18_S2") == list(S2_KEYS)
    # 直書きのカンマ区切りも今までどおり通る（名前解決は完全一致のときだけ）
    assert parse_roster("S2_AN_H1,S2_AN_H2") == ["S2_AN_H1", "S2_AN_H2"]


def test_vendor_tier_shape_matches_instructed_lineup():
    shape: dict[str, dict[str, int]] = {}
    for key in S2_KEYS:
        info = MODEL_REGISTRY[key]
        shape.setdefault(info.provider, {}).setdefault(info.tier, 0)
        shape[info.provider][info.tier] += 1
    assert shape == EXPECTED_SHAPE


def test_all_prices_are_nonnegative_and_cache_not_above_input():
    for key in S2_KEYS:
        info = MODEL_REGISTRY[key]
        assert info.input_price >= 0.0, key
        assert info.output_price >= 0.0, key
        if info.cached_input_price is not None:
            assert info.cached_input_price <= info.input_price, key


def test_devrelay_seats_are_subscription_billed_codex():
    for key in ("S2_OA_H", "S2_OA_M", "S2_OA_L"):
        info = MODEL_REGISTRY[key]
        assert info.adapter_type == "devrelay_http"
        assert info.billing == "subscription"
        assert info.devrelay_ai == "codex"
        assert info.provider == "OpenAI"
        assert info.input_price == 0.0 and info.output_price == 0.0


def test_anthropic_seats_never_use_disabled_thinking_where_api_rejects_it():
    """落とし穴④の再発防止: 4席のうち400実測済みの3モデルにdisabledが紛れていないか"""
    for key in ("S2_AN_H1", "S2_AN_H2", "S2_AN_M", "S2_AN_L"):
        info = MODEL_REGISTRY[key]
        assert info.adapter_type == "anthropic"
        assert info.supports_temperature is False  # SDK自体にtemperature引数が無い
        thinking = (info.extra_params or {}).get("thinking")
        assert isinstance(thinking, dict), f"{key}: extra_paramsにthinkingが無い"
        if info.model_id in _REJECTS_THINKING_DISABLED:
            assert thinking.get("type") != "disabled", (
                f"{key}({info.model_id}) は thinking.type='disabled' を400で拒否する実測済みモデル"
            )


def test_anthropic_h_seats_use_adaptive_low_effort():
    for key in ("S2_AN_H1", "S2_AN_H2"):
        info = MODEL_REGISTRY[key]
        assert info.extra_params["thinking"] == {"type": "adaptive"}
        assert info.extra_params["output_config"] == {"effort": "low"}


def test_anthropic_middle_seat_uses_between_tools():
    info = MODEL_REGISTRY["S2_AN_M"]
    assert info.extra_params["thinking"] == {"type": "between_tools"}


def test_anthropic_low_seat_uses_disabled():
    info = MODEL_REGISTRY["S2_AN_L"]
    assert info.extra_params["thinking"] == {"type": "disabled"}


def test_moonshot_middle_seat_keeps_thinking_enabled_not_disabled():
    """kimi-k2.7-codeは思考を無効化できない実測済みモデル（けいすけさん決定4）"""
    info = MODEL_REGISTRY["S2_MO_M"]
    assert info.model_id in _REQUIRES_THINKING_ENABLED
    assert info.extra_params["thinking"] == {"type": "enabled"}


def test_xai_low_seat_is_the_non_reasoning_variant_not_grok_4_1_fast():
    """grok-4.1-fastは実測404のため不採用（けいすけさん決定3）"""
    info = MODEL_REGISTRY["S2_XA_L"]
    assert info.model_id == "grok-4.20-0309-non-reasoning"
    assert info.model_id != "grok-4.1-fast"


def test_xai_high_seat_uses_low_reasoning_effort():
    """grok-4.7は思考を無効化できない（xAI公式）。最小の"low"で固定する（サイクル7.0b実測）"""
    info = MODEL_REGISTRY["S2_XA_H"]
    assert info.extra_params["reasoning_effort"] == "low"


def test_no_duplicate_model_id_within_s2_roster_itself():
    """指示書の確認事項: S2の18席どうしではmodel_idが重複しないこと
    （旧ロスターH1〜L7との重複は許容。既存L4/H5/M5/L5/H6と同一モデルを
    指すS2席が複数あるのは意図的＝その旨をllm/models.pyのコメントに明記済み）。
    """
    seen: dict[str, str] = {}
    for key in S2_KEYS:
        model_id = MODEL_REGISTRY[key].model_id
        assert model_id not in seen, f"{key} と {seen.get(model_id)} が同じmodel_id={model_id!r}"
        seen[model_id] = key


@pytest.mark.parametrize("key", S2_KEYS)
def test_get_model_resolves_each_s2_key_directly(key):
    assert get_model(key) is MODEL_REGISTRY[key]
