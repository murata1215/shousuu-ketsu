"""LLM実績コストと事前予約コストの共通計算。"""

from typing import Any

from llm.models import ModelInfo, estimate_cost


def usage_cost(model: ModelInfo, usage: dict[str, Any]) -> float:
    """usageを正規化して実績コストを計算する。

    Anthropicだけは input_tokens と cache_read_input_tokens を別建てで
    返すため、estimate_cost()へ渡す前に合算する。

    cache_creation_input_tokens（キャッシュ書き込み、サイクル4.2c）は
    input_tokens/total_tokensとは独立に、そのままestimate_cost()へ渡す
    （入力単価の1.25倍で課金される別建ての金額。total_tokensには元々
    含まれていない——Anthropicのadapterはtotal_tokens=input+outputのみ）。
    """
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
    total_tokens = usage.get("total_tokens", 0) or 0
    if model.adapter_type == "anthropic":
        input_tokens += cache_read
        if total_tokens:
            total_tokens += cache_read
    return estimate_cost(
        model, input_tokens, output_tokens,
        cache_read_input_tokens=cache_read,
        total_tokens=total_tokens,
        cache_creation_input_tokens=cache_creation,
    )


def worst_case_cost(model: ModelInfo, system: str, user: str, max_tokens: int,
                    input_reserve_tokens: int = 0) -> float:
    """次callの保守的な予約額（入力 + max output + hidden reserve）。

    Gemini/xAIのreserveは観測ベースの安全マージンであり、provider保証の
    数学的上限ではない。既存model_smokeの算定式をそのまま共有する。
    """
    approx_input_tokens = (len(system) + len(user)) // 2 + 50 + input_reserve_tokens
    reserve = model.hidden_thinking_reserve_tokens
    return estimate_cost(
        model, approx_input_tokens, max_tokens,
        total_tokens=approx_input_tokens + max_tokens + reserve,
    )
