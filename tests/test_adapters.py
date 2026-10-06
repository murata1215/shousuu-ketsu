"""
llm/adapters.py のテスト

サイクル2.3の疎通スモーク（scripts/smoke_providers.sh）でL1/M1/H1（Anthropic
3種）が1コールも発行できていない実害が発覚した。原因は`AnthropicAdapter.
_get_client()`がhttpx.Timeout(...)をanthropic SDKに渡していたことで、
anthropic 1.8.0は内部でhttpx2を使うため
「httpx.Timeout is from the httpx package, but this SDK uses httpx2」という
TypeErrorになっていた（サイクル1.0のhttpx/httpx2問題の残り半分。実APIには
一度も投入していなかったため今回まで発覚しなかった）。

サイクル2.5では、httpx修正後の再スモークでL1（Claude Haiku 4.5）だけが
別の原因で無通電のままだったことを追加で検証する。anthropic 1.8.0の
Messages.create()はtemperature/top_pを引数として持たず、
`supports_temperature=True`のモデルが`temperature=...`を渡すと
`TypeError: Messages.create() got an unexpected keyword argument 'temperature'`
になる。既存のtemperatureフォールバック機構はエラー文に"deprecated"が
含まれる場合しか拾わず、このケースを取りこぼしていた。

本ファイルは実APIを一切呼ばず、クライアント生成時のtimeout引数の型・
`complete()`内のフォールバック分岐の両方を、`_get_client()`を差し替えた
フェイククライアントで検証する。
"""

import dataclasses

import pytest

from llm.adapters import AnthropicAdapter, AdapterError
from llm.models import ModelInfo, get_model


class _FakeContentBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 5) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.output_tokens_details = None
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0

    def model_dump(self):
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


class _FakeResponse:
    def __init__(self, text: str = "ok") -> None:
        self.content = [_FakeContentBlock(text)]
        self.usage = _FakeUsage()
        self.stop_reason = "end_turn"
        self.model = "claude-haiku-4-5-20251001"


class _FakeMessagesEndpoint:
    """
    anthropic.Anthropic().messages 相当のフェイク。

    create()の呼び出し履歴（kwargs）を全件記録するので、テスト側で
    「何回呼ばれたか」「temperatureキーが2回目に消えているか」を検証できる。
    side_effects に例外またはレスポンスを順番に並べて注入する。
    """

    def __init__(self, side_effects: list) -> None:
        self._side_effects = list(side_effects)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        effect = self._side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class _FakeAnthropicClient:
    def __init__(self, messages: _FakeMessagesEndpoint) -> None:
        self.messages = messages


def _anthropic_model(**overrides) -> ModelInfo:
    """Anthropic系ModelInfoのコピーにoverridesを適用して返す（実API非依存のテスト用）"""
    return dataclasses.replace(get_model("L1"), **overrides)


@pytest.fixture(autouse=True)
def _anthropic_api_key(monkeypatch):
    """get_model("L1"/"M1"/"H1").env_key は全て CLAUDE_API_KEY"""
    monkeypatch.setenv("CLAUDE_API_KEY", "dummy-test-key-not-a-real-secret")


@pytest.mark.parametrize("model_key", ["L1", "M1", "H1"])
def test_anthropic_client_receives_plain_number_timeout_not_httpx_timeout(model_key):
    """
    サイクル2.3の実害の再発防止線。httpx.Timeoutを渡すとanthropic SDKが
    TypeErrorを投げるため、_get_client()が例外なく完走し、かつ生成された
    クライアントのtimeoutがhttpx.Timeoutインスタンスではなく素の数値である
    ことを確認する（実APIは一切呼ばない、クライアント生成のみ）。
    """
    import httpx

    model = get_model(model_key)
    adapter = AnthropicAdapter(model)
    client = adapter._get_client()
    assert not isinstance(client.timeout, httpx.Timeout)
    assert client.timeout == model.timeout_seconds


def test_anthropic_sdk_rejects_httpx_timeout_argument_directly():
    """
    修正の前提そのものが将来変わっていないかを確認する回帰センサー。
    anthropic SDKがhttpx.Timeoutを受け付けるようになったら、この検証済みの
    前提（サイクル1.0のhttpx/httpx2問題）自体が過去のものになったことを示す。
    """
    import anthropic
    import httpx

    with pytest.raises(TypeError):
        anthropic.Anthropic(api_key="dummy", timeout=httpx.Timeout(60))


def test_anthropic_client_missing_env_key_raises_adapter_error(monkeypatch):
    """env_key未設定時はAdapterErrorを投げる（既存の分岐が壊れていないことの確認）"""
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    model = get_model("L1")
    adapter = AnthropicAdapter(model)
    with pytest.raises(AdapterError):
        adapter._get_client()


def test_l1_no_longer_declares_temperature_support():
    """
    サイクル2.5の実害の再発防止線。L1がsupports_temperature=Trueのままだと、
    complete()が毎コールtemperatureを送りTypeErrorを引いてから
    フォールバックする無駄が発生する（フォールバック自体は②で直したが、
    実態が「Anthropic系は誰もtemperatureを受け付けない」である以上、M1/H1と
    同じくデータ側でも明示するのが正しい）。
    """
    assert get_model("L1").supports_temperature is False


# --- complete()内のtemperatureフォールバック（サイクル2.5） ---


def test_temperature_fallback_retries_on_unexpected_keyword_argument(monkeypatch):
    """
    サイクル2.5の本命の実害の再発防止線。anthropic 1.8.0のMessages.create()は
    temperature引数自体を持たず「got an unexpected keyword argument
    'temperature'」というTypeErrorになる。既存のフォールバック条件は
    "deprecated"の有無しか見ておらずこのケースを拾えていなかった
    （supports_temperature=Trueのモデルが1コールも発行できずに全滅していた）。
    """
    fake_messages = _FakeMessagesEndpoint(side_effects=[
        TypeError("Messages.create() got an unexpected keyword argument 'temperature'"),
        _FakeResponse("hello"),
    ])
    model = _anthropic_model(supports_temperature=True)
    adapter = AnthropicAdapter(model)
    monkeypatch.setattr(adapter, "_get_client", lambda: _FakeAnthropicClient(fake_messages))

    text, usage = adapter.complete(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    assert text == "hello"
    assert len(fake_messages.calls) == 2
    assert "temperature" in fake_messages.calls[0]
    assert "temperature" not in fake_messages.calls[1]


def test_temperature_fallback_retries_on_deprecated_message(monkeypatch):
    """従来のフォールバック条件（サーバ側が"deprecated"として拒否する場合）の後方互換を確認する"""
    fake_messages = _FakeMessagesEndpoint(side_effects=[
        Exception("temperature is deprecated for this model"),
        _FakeResponse("hello"),
    ])
    model = _anthropic_model(supports_temperature=True)
    adapter = AnthropicAdapter(model)
    monkeypatch.setattr(adapter, "_get_client", lambda: _FakeAnthropicClient(fake_messages))

    text, _usage = adapter.complete(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    assert text == "hello"
    assert len(fake_messages.calls) == 2


def test_temperature_fallback_does_not_swallow_unrelated_type_errors(monkeypatch):
    """
    フォールバック条件を広げすぎていないことの担保。temperature以外の
    未知キーワード引数エラー（例: 呼び出し側のバグで"foo"を渡した場合）は
    リトライされず、そのままAdapterErrorとして最終的に投げられることを確認する
    （1回しか呼ばれていない＝temperature抜きでの再試行が発生していないこと）。
    """
    fake_messages = _FakeMessagesEndpoint(side_effects=[
        TypeError("Messages.create() got an unexpected keyword argument 'foo'"),
    ])
    model = _anthropic_model(supports_temperature=True)
    adapter = AnthropicAdapter(model, max_retries=0)
    monkeypatch.setattr(adapter, "_get_client", lambda: _FakeAnthropicClient(fake_messages))

    with pytest.raises(AdapterError):
        adapter.complete(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    assert len(fake_messages.calls) == 1
