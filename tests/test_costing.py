"""
LLMコスト計算のテスト（サイクル4.2c: キャッシュ書き込み課金の計上漏れ修正）

試走では直結のClaude席の費用計算がキャッシュ書き込み
（cache_creation_input_tokens）を一切課金していなかった（取得・記録は
していたが、金額に入っていなかった）。本ファイルはその修正を固定する。
"""

from llm.costing import usage_cost
from llm.models import CACHE_WRITE_PRICE_MULTIPLIER, MODEL_REGISTRY, estimate_cost


def test_cache_write_multiplier_is_1_25() -> None:
    assert CACHE_WRITE_PRICE_MULTIPLIER == 1.25


def test_m1_has_cached_input_price_set() -> None:
    """
    M1（claude-sonnet-5）のcached_input_priceが未設定（None、→入力単価と
    同値で過大計上）だった不備を直した（サイクル4.2c）。
    """
    m1 = MODEL_REGISTRY["M1"]
    assert m1.cached_input_price == 0.20


def test_l1_cached_input_price_unchanged() -> None:
    """L1（claude-haiku-4-5）は既に正しく設定済みだったので変えない"""
    l1 = MODEL_REGISTRY["L1"]
    assert l1.cached_input_price == 0.10


def test_estimate_cost_counts_cache_write_tokens() -> None:
    """キャッシュ書き込みトークンが、入力単価の1.25倍で課金される"""
    m1 = MODEL_REGISTRY["M1"]
    cost_without_cache_write = estimate_cost(m1, input_tokens=0, output_tokens=0)
    cost_with_cache_write = estimate_cost(
        m1, input_tokens=0, output_tokens=0, cache_creation_input_tokens=1_000_000,
    )
    assert cost_without_cache_write == 0.0
    # 入力単価$2.00/1Mトークン × 1.25倍 × 1Mトークン = $2.50
    assert cost_with_cache_write == m1.input_price * CACHE_WRITE_PRICE_MULTIPLIER


def test_estimate_cost_counts_cache_read_tokens_at_discount() -> None:
    """キャッシュ読みトークンは、割引単価（cached_input_price）で課金される"""
    m1 = MODEL_REGISTRY["M1"]
    cost = estimate_cost(
        m1, input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=1_000_000,
    )
    assert cost == m1.cached_input_price


def test_usage_cost_anthropic_counts_cache_write_and_read() -> None:
    """
    usage_cost()がAnthropic形式のusage辞書（cache_creation_input_tokens・
    cache_read_input_tokensを別建てで持つ）を受け取り、両方を課金に含める
    こと（実際のLLMAgent._call()の経路、llm/adapters.py::AnthropicAdapterの
    usage整形と同じ形）。
    """
    m1 = MODEL_REGISTRY["M1"]
    usage = {
        "input_tokens": 1_000,
        "output_tokens": 500,
        "total_tokens": 1_500,
        "cache_creation_input_tokens": 2_000,
        "cache_read_input_tokens": 3_000,
    }
    cost = usage_cost(m1, usage)

    # 手計算: input(1,000+3,000キャッシュ読み込み分を合算した4,000の一部)は
    # usage_cost内部でinput_tokensに合算される。期待値は直接estimate_cost
    # と同じ式で再現して比較する。
    expected = estimate_cost(
        m1,
        input_tokens=1_000 + 3_000, output_tokens=500,
        cache_read_input_tokens=3_000, total_tokens=1_500 + 3_000,
        cache_creation_input_tokens=2_000,
    )
    assert cost == expected
    assert cost > 0
