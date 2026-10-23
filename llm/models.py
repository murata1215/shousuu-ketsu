"""
モデルレジストリ

ロスター仕様書v1.0の12モデルを定義。
モデルID・アダプタ種別・単価・API設定を管理する。
L7は2026-08-16追加の負荷試験専用枠（12モデル仕様書には含まれない）。

Step 3C-prep2: モデルIDを各社APIで確認した実在IDに確定。
- OpenAI: gpt-4.1 / gpt-4.1-mini（最新世代）
- xAI: grok-4.5 / grok-4.3（実在確認）
- Gemini: gemini-3.5-flash / gemini-3.5-flash-lite（OpenAI互換エンドポイント）
- Moonshot: kimi-k2.6（api.moonshot.aiが正しいエンドポイント）
- DeepSeek: deepseek-chat（402入金待ち）
- Anthropic: claude-sonnet-5 / claude-haiku-4-5（確認済み）
"""

from dataclasses import dataclass


# --- provider 表示名 → emotion画像/資産のベンダーキー正規化 ---
_PROVIDER_TO_VENDOR: dict[str, str] = {
    "Anthropic": "anthropic",
    "OpenAI": "openai",
    "Google": "google",
    "xAI": "xai",
    "Moonshot": "moonshot",
    "DeepSeek": "deepseek",
}

# --- 階級順・ベンダー順（表示・実行順のソートキーに使用） ---
TIER_ORDER: tuple[str, ...] = ("H", "M", "L")
VENDOR_ORDER: tuple[str, ...] = ("Anthropic", "OpenAI", "Google", "xAI", "Moonshot", "DeepSeek")

# サイクル4.2c: Anthropicのプロンプトキャッシュ書き込み（cache_creation）は
# 入力単価の1.25倍（公式料金表、2026-10-11確認）。estimate_cost()が
# cache_creation_input_tokensを受け取って課金する唯一の場所で、倍率は
# ここ1か所だけに持つ。
CACHE_WRITE_PRICE_MULTIPLIER: float = 1.25


@dataclass
class ModelInfo:
    """1モデルの定義情報"""
    model_id: str           # API呼出し用のモデルID
    provider: str           # プロバイダ名
    name: str               # 表示名
    adapter_type: str       # "anthropic" / "openai_compat" / "gemini"
    input_price: float      # 入力単価（$/1Mトークン）
    output_price: float     # 出力単価（$/1Mトークン）
    env_key: str            # 環境変数名（APIキー）
    base_url: str | None    # OpenAI互換のbase_url（Noneなら公式）
    timeout_seconds: int = 60  # APIコールのタイムアウト（秒）。reasoning系は長めに設定
    max_tokens: int | None = None       # per-model出力トークン上限（None → DEFAULT_MAX_TOKENS）
    phase2_max_tokens: int | None = None  # Phase 2だけの出力上限override（None → CLI/phase既定値）
    phase2_timeout_seconds: int | None = None  # Phase 2だけのtimeout override（None → 通常timeout）
    max_tokens_param: str = "max_tokens"  # OpenAI互換APIへ送るトークン上限のキー名。GPT-5系reasoningは "max_completion_tokens"
    supports_temperature: bool = True     # False なら temperature を一切送らない（reasoning系で拒否されるモデル用）
    temperature_override: float | None = None  # providerが要求する固定temperature（None → 呼出側の指定値）
    extra_params: dict | None = None    # API固有パラメータ（例: {"thinking": {"type": "disabled"}}）
    cached_input_price: float | None = None  # キャッシュヒット入力単価（$/1Mトークン）。None → input_price と同値
    reasoning_price: float | None = None     # thinkingトークン単価（$/1Mトークン）。None → output_price と同値
    tier: str = ""           # "H"(強)/"M"(中)/"L"(軽)。"" = 未分類（テスト用アドホック生成のデフォルト）
    hidden_thinking_reserve_tokens: int = 0
    billing: str = "api"     # "api"(通常API課金) / "subscription"(DevRelay経由・0円計上・席キャップ対象外)
    devrelay_ai: str = "claude"  # DevRelay raw-completionへ送る"ai"フィールド（"claude"/"codex"）。
    # adapter_type="devrelay_http" 以外では未使用（サイクル10.9）。
    # max_tokens の外側で課金される hidden thinking/reasoning の事前予約トークン数（worst_case_cost用）。
    # 0 = thinking が output/completion に内包される（Anthropic/OpenAI）か、
    #     thinking 無効化済み（Kimi/DeepSeek）→ 予約不要（計算は現行と完全同値）。
    # >0 の場合も provider が保証する上限ではない。実測最大値に対する経験的安全マージンであり、
    # 数学的worst-case完全保証ではない（scripts/model_smoke.py:worst_case_cost の docstring参照）。

    @property
    def vendor(self) -> str:
        """provider 表示名を emotion画像/資産のベンダーキーへ正規化する"""
        return _PROVIDER_TO_VENDOR.get(self.provider, "anthropic")


# --- Gemini OpenAI互換エンドポイント ---
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# --- Moonshot国際版エンドポイント ---
MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"

# --- ロスター仕様書v1.0の12モデル（疎通確認済みID） + L7（負荷試験用追加枠） ---
MODEL_REGISTRY: dict[str, ModelInfo] = {
    # Tier M（中量級）
    "M1": ModelInfo(
        model_id="claude-sonnet-5",
        provider="Anthropic", name="Claude Sonnet 5",
        adapter_type="anthropic",
        input_price=2.0, output_price=10.0,  # Anthropic料金表 2026-08-16
        # doc/llm_api_pricing.md(2026-08-16, 二次資料): $2.00/$10.00 一致。ただし
        # 「〜2026-08-31 導入価格」と明記（本日2026-08-27はまだ導入価格内）。
        # 2026-09-01以降は要再確認（Sonnet 4.6相当 $3/$15 になる可能性）。数値は変更しない。
        env_key="CLAUDE_API_KEY", base_url=None,
        supports_temperature=False,
        cached_input_price=0.20,  # サイクル4.2c: 未設定のままだとキャッシュ読みを
        # 入力単価と同値（過大）に計上してしまうため設定。Anthropic公式料金表
        # 2026-10-11確認（Sonnet系はキャッシュ読み=入力単価の1/10）。
        # サイクル4.2c仕上げD: 考える量を指定していなかったため、試走19回の
        # うち10回で考える分が出力上限(4000。借入1000)を使い切り本文が空に
        # なった（reasoning_tokens 3999〜4000）。出力52,015トークンのうち
        # 47,822が考える分、1コール中央値46秒・費用0.65ドル（試合全体の55%）。
        # 考える機能自体を止める（M5/M6と同じ形）。
        extra_params={"thinking": {"type": "disabled"}},
        tier="M",
    ),
    "M2": ModelInfo(
        model_id="gpt-4.1",
        provider="OpenAI", name="GPT-4.1",
        adapter_type="openai_compat",
        input_price=2.0, output_price=8.0,  # OpenAI料金表 2026-08-16
        env_key="OPENAI_API_KEY", base_url=None,
        cached_input_price=0.50,  # OpenAI料金表 2026-08-16
        tier="M",
    ),
    "M3": ModelInfo(
        model_id="gemini-3.5-flash",
        provider="Google", name="Gemini 3.5 Flash",
        adapter_type="gemini",
        input_price=1.50, output_price=9.00,  # Google公式 2026-08 ai.google.dev/gemini-api/docs/pricing
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        # TODO: Geminiキャッシュ単価はContext Caching API別体系（$/1M tokens/hour）。ここでは未設定=input同値
        tier="M",
        phase2_max_tokens=512,
        # 2026-08-18: Phase 1実測(max_tokens=64)でhidden thinking 185tok（completion外課金）を確認。
        # 512は実測最大343（L4実測）の約1.5倍の経験的安全マージン。provider保証の上限ではない。
        hidden_thinking_reserve_tokens=512,
    ),
    # 検証専用。正式ロスターのM3（Gemini 3.5 Flash）は絶対に置換しない。
    # Google Gemini API pricing / OpenAI compatibility docs を 2026-08-22 JST に確認:
    # input $0.75 / output（thinking含む）$3.75 per 1M tokens（2026-12-31までの導入価格）。
    # Gemini 3.7 Flash は sampling parameters (temperature/top_p/top_k) を受け付けないため、
    # supports_temperature=False とし、thinking は呼出し側の reasoning_effort="low" で指定する。
    "M3_G37_EVAL": ModelInfo(
        model_id="gemini-3.7-flash",
        provider="Google", name="Gemini 3.7 Flash (evaluation only)",
        adapter_type="gemini",
        input_price=0.75, output_price=3.75,
        # doc/llm_api_pricing.md(2026-08-16, 二次資料): $0.75/$3.75, cached $0.075 一致。
        # 「〜2026-12-31 導入価格。2027-01-01に$1.50/$7.50へ倍増」と明記。数値は変更しない。
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        max_tokens=2000,
        phase2_max_tokens=2000,
        supports_temperature=False,
        tier="",
        # 3.7はthinkingを常時有効化（low/medium/highのみ、無効化不可）。
        # 実測前は既存Geminiの保守的な予約を流用し、Phase 1で実測値に再評価する。
        hidden_thinking_reserve_tokens=512,
    ),
    "M4": ModelInfo(
        model_id="grok-4.5",
        provider="xAI", name="Grok 4.5",
        adapter_type="openai_compat",
        input_price=2.0, output_price=6.0,  # xAI料金表 2026-08-16
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        cached_input_price=0.30,  # xAI料金表 2026-08-16
        tier="M",
        # 2026-08-18: Phase 1実測(max_tokens=64)でhidden thinking 42tok（completion外課金）を確認。
        # Phase 2実測811tokenを覆う経験的安全マージン。provider保証の上限ではない。
        # Cycle 6.1 (2026-08-27): timeout=180化で初めてthinking medium実測が取得でき
        # reasoning_tokens=2035 を観測（旧1024の約2倍）。ceil(2035*1.2)=2442を512単位へ
        # 切り上げ2560とした。provider保証の上限ではない。
        hidden_thinking_reserve_tokens=2560,
        # Cycle 6.1: Cycle 6スモーク(thinking medium, max_output_tokens=3000)で61.1sタイムアウト
        # (H4の61.2s×2と同型の「60sの壁」)。H4が180sで123.5s成功した実績に合わせて180に引き上げ。
        timeout_seconds=180,
    ),
    "M5": ModelInfo(
        model_id="kimi-k2.6",
        provider="Moonshot", name="Kimi K2.6",
        adapter_type="openai_compat",
        input_price=0.95, output_price=4.0,  # Moonshot料金表 2026-08-16
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=90,
        extra_params={"thinking": {"type": "disabled"}},
        temperature_override=0.6,
        # cached_input_price: 記載なし→input同値
        tier="M",
    ),
    "M6": ModelInfo(
        model_id="deepseek-v4-flash",  # 実測: deepseek-chatはV4 Flashにリダイレクト確認 2026-08-16
        provider="DeepSeek", name="DeepSeek V4 Flash",
        adapter_type="openai_compat",
        input_price=0.14, output_price=0.28,  # DeepSeek料金表 2026-08-16（V4 Flash単価）
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        cached_input_price=0.0028,  # DeepSeek料金表 2026-08-16
        # 2026-08-17: thinking既定ON(effort=high)がmax_tokens=4000を食い潰しfinish_reason=length
        # →content空→reasoning_contentフォールバックでJSON崩壊（seed=701実測 P04 25.2%/P05 13.7%）。
        # 8コール診断実験でthinking無効化によりreasoning_tokens=0・JSON成功率100%・latency大幅改善を確認。
        extra_params={"thinking": {"type": "disabled"}},
        max_tokens=2000,
        tier="M",
    ),
    # Tier L（軽量級）
    "L1": ModelInfo(
        model_id="claude-haiku-4-5-20251001",
        provider="Anthropic", name="Claude Haiku 4.5",
        adapter_type="anthropic",
        input_price=1.0, output_price=5.0,  # Anthropic料金表 2026-08-16
        env_key="CLAUDE_API_KEY", base_url=None,
        cached_input_price=0.10,  # Anthropic料金表 2026-08-16
        tier="L",
        # anthropic 1.8.0 の Messages.create() は temperature/top_p を持たない
        # （M1/H1と同じ扱い。サイクル2.4疎通スモークでTypeError
        # "got an unexpected keyword argument 'temperature'"を実測確認）。
        supports_temperature=False,
    ),
    "L2": ModelInfo(
        model_id="gpt-4.1-mini",
        provider="OpenAI", name="GPT-4.1 Mini",
        adapter_type="openai_compat",
        input_price=0.40, output_price=1.60,  # OpenAI料金表 2026-08-16
        env_key="OPENAI_API_KEY", base_url=None,
        cached_input_price=0.10,  # OpenAI料金表 2026-08-16
        tier="L",
    ),
    "L3": ModelInfo(
        model_id="gemini-3.5-flash-lite",
        provider="Google", name="Gemini 3.5 Flash-Lite",
        adapter_type="gemini",
        input_price=0.30, output_price=2.50,  # Google公式 2026-08 ai.google.dev/gemini-api/docs/pricing
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        # TODO: Geminiキャッシュ単価はContext Caching API別体系。ここでは未設定=input同値
        tier="L",
        # 2026-08-18: Phase 1実測(max_tokens=64)ではこの試行のhidden thinking差分は0だったが、
        # M3/H3と同一Geminiエンドポイント・同一thinking課金構造のため安全側に予約を設定。
        # 512は他Gemini/xAIモデルの実測最大343（L4実測）の約1.5倍の経験的安全マージン。
        # provider保証の上限ではない。
        hidden_thinking_reserve_tokens=512,
    ),
    "L4": ModelInfo(
        model_id="grok-4.3",
        provider="xAI", name="Grok 4.3",
        adapter_type="openai_compat",
        input_price=1.25, output_price=2.50,  # xAI料金表 2026-08-16（旧: 0.60/3.00 は過小/過大）
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        cached_input_price=0.20,  # xAI料金表 2026-08-16
        tier="L",
        # 2026-08-18: Phase 1実測(max_tokens=64)でhidden thinking 343tok（全モデル中最大、completion外課金）を確認。
        # Phase 2実測526tokenを覆う経験的安全マージン。provider保証の上限ではない。
        hidden_thinking_reserve_tokens=768,
    ),
    "L5": ModelInfo(
        model_id="kimi-k2.6",
        provider="Moonshot", name="Kimi K2.6 (L)",
        adapter_type="openai_compat",
        input_price=0.95, output_price=4.0,  # Moonshot料金表 2026-08-16
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=90,
        extra_params={"thinking": {"type": "disabled"}},
        temperature_override=0.6,
        # cached_input_price: 記載なし→input同値
        tier="L",
    ),
    "L6": ModelInfo(
        model_id="deepseek-v4-flash",  # 実測: deepseek-chatはV4 Flashにリダイレクト確認 2026-08-16
        provider="DeepSeek", name="DeepSeek V4 Flash (L)",
        adapter_type="openai_compat",
        input_price=0.14, output_price=0.28,  # DeepSeek料金表 2026-08-16（V4 Flash単価）
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        cached_input_price=0.0028,  # DeepSeek料金表 2026-08-16
        # 2026-08-17: M6と同一model_idにつき同一の是正が必要（片方だけ直すと片肺暴走する）
        extra_params={"thinking": {"type": "disabled"}},
        max_tokens=2000,
        tier="L",
    ),
    # Tier L+（負荷試験用の追加安価枠。2026-08-16追加、ロスター仕様書v1.0の12モデルとは別枠）
    "L7": ModelInfo(
        model_id="gemini-2.5-flash-lite",
        provider="Google", name="Gemini 2.5 Flash-Lite",
        adapter_type="gemini",
        input_price=0.10, output_price=0.40,  # ユーザー指定単価 2026-08-16（18体負荷試験用）
        # doc/llm_api_pricing.md(2026-08-16, 二次資料): $0.10/$0.40 一致。
        # 「2026-10-16終了予定」と明記。18体ロスターの予備枠に使う場合は要注意。数値は変更しない。
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        # cached_input_price / reasoning_price: 未設定（L3/M3と同じ扱い、input/output単価を代用）
        tier="L",
    ),
    # Tier H（強量級・フラッグシップ）
    # model_idは scripts/discover_models.py（2026-08-17実行）の一覧APIで実在確認済み。
    # 単価は一覧APIからは取得不可のため、doc/uso8000000_model_roster_v1_0.md §3
    # （決勝候補、2026-08-09時点情報）を出典とする。実戦投入前に各社公式料金ページで
    # 必ず再確認すること（M2=GPT-5.6 Terra→実装gpt-4.1のような乖離が過去に発生済み）。
    "H1": ModelInfo(
        model_id="claude-opus-5",
        provider="Anthropic", name="Claude Opus 5",
        adapter_type="anthropic",
        input_price=5.0, output_price=25.0,  # roster_v1_0.md §3 (2026-08-09時点) 要再確認
        # doc/llm_api_pricing.md(2026-08-16, 二次資料): $5.00/$25.00, cached $0.50 一致
        # （input/output）。Cycle 6監査では数値を変更しない。
        env_key="CLAUDE_API_KEY", base_url=None,
        supports_temperature=False,
        cached_input_price=0.50,  # サイクル4.2d: Anthropic公式料金表2026-10-11確認
        # （入力5ドル・出力25ドル・キャッシュ読み0.50ドル）。未設定のままだと
        # キャッシュ読みを入力と同じ単価で数えてしまう（M1と同じ理由、§11.9）。
        # Structured Outputs + thinking disabled のPhase 2試験では visible JSON を優先する。
        phase2_max_tokens=400,
        # 初回Structured Outputs schema compilationが通常60秒を超える場合だけ待機する。
        phase2_timeout_seconds=300,
        # サイクル4.2d: DevRelay経由のOpus席（DR_OPUS）が交渉の手番の18%を
        # 時間切れで失ったため、APIで直接呼ぶ席に替えた（§11.3、§12.10）。
        # 考える機能を止める（M1と同じ理由。止めないと考える分が出力上限を
        # 使い切り、本文が空になることがある）。
        extra_params={"thinking": {"type": "disabled"}},
        tier="H",
    ),
    "H2": ModelInfo(
        model_id="gpt-5.6-sol",
        provider="OpenAI", name="GPT-5.6 Sol",
        adapter_type="openai_compat",
        input_price=5.0, output_price=30.0,  # roster_v1_0.md §3 (2026-08-09時点) 要再確認
        # doc/llm_api_pricing.md(2026-08-16, 二次資料): $5.00/$30.00, cached $0.50 一致
        # （input/output）。cached_input_price は未設定のまま。Cycle 6監査では数値を変更しない。
        env_key="OPENAI_API_KEY", base_url=None,
        tier="H",
        # 2026-08-18: Phase 1実測で400 "max_tokens is not supported" を確認。
        # reasoning系のため max_completion_tokens を使用し、temperatureは送らない。
        max_tokens_param="max_completion_tokens",
        supports_temperature=False,
    ),
    "H3": ModelInfo(
        model_id="gemini-3.1-pro-preview",
        provider="Google", name="Gemini 3.1 Pro",
        adapter_type="gemini",
        input_price=2.0, output_price=12.0,  # roster_v1_0.md §3 (2026-08-09時点) 要再確認。2M文脈
        # doc/llm_api_pricing.md(2026-08-16, 二次資料): 「≤200K」帯は $2.00/$12.00 一致。
        # 「>200K」帯は $4.00/$18.00（別料金）で registry は単一価格のため長文時に過小見積の
        # 可能性あり。Cycle 6監査では数値を変更しない。
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        tier="H",
        # Phase 2 retry_failed attempt 3 (2026-08-19): 487 hidden-thinking
        # tokens + 21 visible tokens exhausted the former 512-token cap.  Keep
        # the existing 400-token canonical JSON allowance plus the 25-token
        # margin to the 512-token hidden-thinking reserve: 487 + 400 + 25 = 912.
        # This is Phase-2-only; normal calls and other phases remain unchanged.
        phase2_max_tokens=912,
        # 2026-08-18: Phase 1実測(max_tokens=64)でhidden thinking 185tok（completion外課金）を確認。
        # 512は実測最大343（L4実測）の約1.5倍の経験的安全マージン。provider保証の上限ではない。
        hidden_thinking_reserve_tokens=512,
    ),
    "H4": ModelInfo(
        model_id="grok-4.6",
        provider="xAI", name="Grok 4.6",
        adapter_type="openai_compat",
        # roster_v1_0.md §3 にxAI強ティア記載なし（欠落）。grok-4.5(M4: $2/$6)からの
        # 推定値。実戦投入前に xAI 公式料金ページで確定させること（TODO）。
        # doc/llm_api_pricing.md(2026-08-16, 二次資料): 「<200K」帯は $2.00/$6.00、
        # 「≥200K」帯は $4.00/$12.00 で、registryの5.0/15.0とはどちらとも一致せず
        # 約2.5倍の食い違いがある。二次資料のため根拠にはできず、予算ガードを高い方に
        # 倒しておく安全のため、Cycle 6監査では数値を変更しない（xAI公式ページの確認が
        # 引き続き最優先の申し送り事項）。
        input_price=5.0, output_price=15.0,
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        # Cycle 6 (2026-08-27): 5.5実測で60秒(既定)のタイムアウトに2回連続で到達
        # （61.197s / 61.161s、差0.04s未満＝ネットワーク揺らぎではなく構造的な壁）。
        # grok-4.6は常時思考（無効化不可）でhidden_thinking_reserve_tokens=1536と
        # 出力上限6,000の組合せに60秒は不足。scripts/model_matrix.py Phase 3の
        # runtime override（_phase3_effective_model, timeout_seconds=180）が既に
        # 同条件で成功実績あり。それに合わせてレジストリの既定値を180へ引き上げる。
        timeout_seconds=180,
        tier="H",
        # Cycle 6.1 (2026-08-27): 旧1536はthinkingが浅い条件での観測値だったため、
        # thinking medium・max_output_tokens=3000 の条件では実測を大幅に下回っていた
        # （Cycle 6監査スモークで reasoning_tokens=6756、Cycle 6.1検証スモークで同7746と
        # 再現・さらに上振れを確認）。より大きい実測7746に、既存モデル群と同じ経験的安全率
        # (実測×1.1〜1.5)の1.2倍を適用しceil(7746*1.2)=9296を512単位へ切り上げ9728とした。
        # provider保証の上限ではなく経験的マージンのため、再度不足する可能性はある。
        hidden_thinking_reserve_tokens=9728,
    ),
    "H5": ModelInfo(
        model_id="kimi-k3",
        provider="Moonshot", name="Kimi K3",
        adapter_type="openai_compat",
        input_price=3.0, output_price=15.0,  # roster_v1_0.md §3 (2026-08-09時点) 要再確認
        # doc/llm_api_pricing.md(2026-08-16, 二次資料): $3.00/$15.00, cached $0.30 一致
        # （input/output）。cached_input_price は未設定のまま。Cycle 6監査では数値を変更しない。
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=90,
        extra_params={"thinking": {"type": "disabled"}},  # M5/L5と同じ理由でthinking無効化
        temperature_override=0.6,
        tier="H",
    ),
    "H6": ModelInfo(
        model_id="deepseek-v4-pro",
        provider="DeepSeek", name="DeepSeek V4 Pro",
        adapter_type="openai_compat",
        # roster_v1_0.md §2表(M6の当初案) 由来。DeepSeekは旗艦級でも低価格帯を維持する
        # 傾向があるため妥当性はあるが、v4-flash(M6/L6)と同じくthinking暴走リスクがあり
        # 実戦投入前に公式料金ページとthinking挙動を要再確認。
        input_price=0.435, output_price=0.87,
        # doc/llm_api_pricing.md(2026-08-16, 二次資料): $0.435/$0.87, cached $0.003625 一致
        # （input/output）。cached_input_price は未設定のまま（未設定時はinput同値でフォールバック）。
        # Cycle 6監査では数値を変更しない。
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        extra_params={"thinking": {"type": "disabled"}},  # M6/L6と同じ理由でthinking無効化
        max_tokens=2000,
        tier="H",
    ),
    # Cycle 5.5 (2026-08-26) 追加。上位・思考モデルreplay probe用の検証専用枠。
    # 正式ロスターの H1〜H6 とは別枠（tier=""）。model_id の実在は未確認
    # （H2=gpt-5.6-sol と同じ "gpt-5.6" 世代の別コードネームという設計文書上の
    # 記載のみが根拠）。scripts/replay_probe.py --model-override TERRA での
    # 1-callスモークテストで404なら使用せず、レジストリ監査へ申し送る。
    # 単価は roster_v1_0.md 由来の設計値（$2/$12）で未検証。
    # 【重要】test_registry_18.py::test_model_specific_temperature_and_phase2_overrides
    # が「実測したモデルだけ上書きする」ことをガードしているため、
    # max_tokens_param/supports_temperature等はH2(gpt-5.6-sol)と同世代でも
    # 未実測のまま憶測で上書きしない（Q2は model_id/単価/env_key/base_urlの
    # 4項目のみ追加と明記）。
    # 2026-08-26 Cycle 5.5スモーク実測: max_tokens指定で400
    # "Unsupported parameter: 'max_tokens' is not supported with this model.
    # Use 'max_completion_tokens' instead." を確認（＝model_id自体は実在し、
    # 404ではない）。H2(gpt-5.6-sol)と同じreasoning系制約が必要と判明した。
    # Cycle 6 (2026-08-27): レジストリ監査で対応。tests/test_registry_18.py の
    # 「実測したモデルだけ上書きする」許可リスト（test_model_specific_temperature_
    # and_phase2_overrides）に TERRA を追加した上で、H2と同型のパラメータ形へ修正。
    # doc/llm_api_pricing.md(2026-08-16, 二次資料): $2.00/$12.00, cached $0.20 一致
    # （input/output）。cached_input_price は未設定のまま。単価の数値は変更しない。
    "TERRA": ModelInfo(
        model_id="gpt-5.6-terra",
        provider="OpenAI", name="GPT-5.6 Terra (evaluation only)",
        adapter_type="openai_compat",
        input_price=2.0, output_price=12.0,  # roster doc記載値。実在確認済み・単価は未検証
        env_key="OPENAI_API_KEY", base_url=None,
        max_tokens_param="max_completion_tokens",
        supports_temperature=False,
        tier="",
    ),
    # Cycle 6 (2026-08-27) 追加。Moonshot L5候補 kimi-k2.5 の実在確認専用枠
    # （TERRA・M3_G37_EVALと同じ「評価専用枠」の前例に倣う）。
    # 単価はdoc/llm_api_pricing.md(2026-08-16, 二次資料)の値を見積にのみ使用（未検証）。
    # 実在すればM5/L5(kimi-k2.6重複)の代わりにL5へ採用可能だが、本Cycleでは
    # L5を変更しない（本走ロスターの挙動不変を優先。レジストリ監査へ申し送り）。
    "K25_EVAL": ModelInfo(
        model_id="kimi-k2.5",
        provider="Moonshot", name="Kimi K2.5 (evaluation only)",
        adapter_type="openai_compat",
        input_price=0.60, output_price=3.00,  # doc/llm_api_pricing.md(2026-08-16, 二次資料)。未検証
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=90,
        extra_params={"thinking": {"type": "disabled"}},  # M5/L5/H5と同じ理由でthinking無効化
        temperature_override=0.6,
        tier="",
    ),
    # サイクル10.7 (2026-09-20) 追加。DevRelayサーバー経由のサブスク実験席。
    # DevRelay(自宅マシンuso8m)がClaude Codeのサブスク認証で実行するため、
    # このプロジェクトのAPI課金は発生しない（billing="subscription"→estimate_cost()が
    # 常に0を返し、席キャップ/試合キャップの対象からも外れる）。
    # 正式ロスター(H/M/L 18モデル)には含めない。tier=""で get_models_by_tier() から除外される。
    # model_idは "devrelay/<DevRelay側のmodel名>" 形式。HttpAgentProviderがプレフィックスを
    # 剥がしてDevRelayへ送る。claude-opus-5等、既存レジストリのmodel_idと衝突させないための
    # 意図的な接頭辞（H1のmodel_id="claude-opus-5"と別物である必要がある）。
    # provider="Anthropic"は中身がClaudeのため（vendor画像・VENDOR_ORDER集合を変えない）。
    # サイクル10.13 (2026-09-22): timeout_seconds 120→180（DevRelay契約上限）。
    # l12r12_dr_1201本戦の504タイムアウト72件は全件elapsed≈108秒（120秒の9割）打ち切りで、
    # 原因はOpus 4.8/Sonnet 5のthinking出力量（doc/analysis/l12r12_dr_1201_timeout.md）。
    # httpx側のマージン(HTTP_TIMEOUT_MARGIN_S=30)は据え置きのため210秒に自動追従する。
    "DR_FABLE": ModelInfo(
        model_id="devrelay/claude-fable-5-1",
        provider="Anthropic", name="Claude Fable 5.1 (DevRelay experimental seat)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,  # DevRelay契約にtemperatureパラメータなし
        billing="subscription",
        tier="",
    ),
    "DR_OPUS": ModelInfo(
        model_id="devrelay/claude-opus-5",
        provider="Anthropic", name="Claude Opus 5 (DevRelay experimental seat)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="",
    ),
    "DR_OPUS48": ModelInfo(
        model_id="devrelay/claude-opus-4-8",
        provider="Anthropic", name="Claude Opus 4.8 (DevRelay experimental seat)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="",
    ),
    "DR_SONNET5": ModelInfo(
        model_id="devrelay/claude-sonnet-5",
        provider="Anthropic", name="Claude Sonnet 5 (DevRelay experimental seat)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="",
    ),
    # サイクル10.9 (2026-09-21) 追加。DevRelay raw-completionの ai="codex" 対応
    # （DevRelay Phase 2、commit 6f730dc）を受けたCodex実験席。
    # 実機確認済み(2026-09-21): ai="codex" + model="gpt-5.6-terra"/"gpt-5.6-sol" でHTTP 200、
    # レスポンスの "ai" が "codex" で返る。Codex側の既定指示（multi-agent前提、約11Kトークン）が
    # 毎回乗るが、DR_FABLE等と同じくsubscription課金のためこのプロジェクトの費用には影響しない。
    # provider="OpenAI"（中身がCodex=OpenAIのため。_PROVIDER_TO_VENDOR/VENDOR_ORDERに既存でvendor="openai"）。
    "DR_TERRA": ModelInfo(
        model_id="devrelay/gpt-5.6-terra",
        provider="OpenAI", name="GPT-5.6 Terra (DevRelay experimental seat)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="",
        devrelay_ai="codex",
    ),
    "DR_SOL": ModelInfo(
        model_id="devrelay/gpt-5.6-sol",
        provider="OpenAI", name="GPT-5.6 Sol (DevRelay experimental seat)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="",
        devrelay_ai="codex",
    ),
    # サイクル10.11 (2026-09-21) 追加。軽量DevRelay席。実機疎通確認済み（各200・約8秒）。
    # DR_HAIKUはdevrelay_ai既定"claude"のためClaude経路（provider="Anthropic"）。
    # model_id="devrelay/claude-haiku-4-5"はL1(model_id="claude-haiku-4-5-20251001")と
    # サフィックス違いのため衝突しない。
    "DR_HAIKU": ModelInfo(
        model_id="devrelay/claude-haiku-4-5",
        provider="Anthropic", name="Claude Haiku 4.5 (DevRelay experimental seat)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="",
    ),
    # DR_LUNAはDR_TERRA/DR_SOLと同じCodex経路（devrelay_ai="codex", provider="OpenAI"）。
    "DR_LUNA": ModelInfo(
        model_id="devrelay/gpt-5.6-luna",
        provider="OpenAI", name="GPT-5.6 Luna (DevRelay experimental seat)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="",
        devrelay_ai="codex",
    ),

    # ================= サイクル7.0: 第2戦（18体）ロスター =================
    # 6社×ハイ・ミドル・ローの18席。既存のH1〜L7・DR_*・TERRA・K25_EVAL・
    # M3_G37_EVALは1件も変更しない（第1戦の記録の再現性を保つため）。
    # 単価・考えさせない設定は2026-10-23にサイクル7.0で各社APIを実測して確定した
    # （doc/devlog/の本サイクルの記録を参照）。ロスター名"L18_S2"は
    # scripts/llm_trial.py::NAMED_ROSTERSで18キーに展開する。

    # --- Anthropic（API直結、4席）---
    # 実測: anthropic SDK 1.11.0のMessages.create()にtemperatureパラメータが
    # そもそも存在しない（inspect.signatureで確認。H1/M1/L1と同じSDK制約であり
    # モデル固有の制限ではない）。4席ともsupports_temperature=False。
    "S2_AN_H1": ModelInfo(
        model_id="claude-fable-5-1",
        provider="Anthropic", name="Claude Fable 5.1 (S2 High)",
        adapter_type="anthropic",
        input_price=10.0, output_price=50.0,  # Anthropic公式料金表 2026-10-23確認
        env_key="CLAUDE_API_KEY", base_url=None,
        supports_temperature=False,
        cached_input_price=0.25,  # 公式: cache hitは base inputの0.025倍
        # 実測2026-10-23: thinking.type="disabled"は400
        # （"thinking.type.disabled" is not supported for this model.
        #  Use "thinking.type.adaptive" and "output_config.effort"）。
        # Fable 5.1は常時思考（無効化不可）。effort="low"でも思考0トークンには
        # ならない場合がある（けいすけさん決定2: 完全に0でなくてよい）。
        extra_params={"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}},
        # 常時思考のモデルのため既定60秒より長めに取る（暫定値。Step3の実測で見直す）。
        timeout_seconds=120,
        tier="H",
    ),
    "S2_AN_H2": ModelInfo(
        model_id="claude-opus-5-5",
        provider="Anthropic", name="Claude Opus 5.5 (S2 High)",
        adapter_type="anthropic",
        input_price=4.0, output_price=20.0,  # Anthropic公式料金表 2026-10-23確認
        env_key="CLAUDE_API_KEY", base_url=None,
        supports_temperature=False,
        cached_input_price=0.20,  # 公式: cache hitは base inputの0.05倍
        # 実測2026-10-23: thinking.type="disabled"は400（Fable 5.1と同じ本文）。
        # effort="low"実測でも思考22トークン観測（完全に0にはならない。
        # けいすけさん決定2: それで良い）。effortの既定はmedium（Opus 5と違う）。
        extra_params={"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}},
        timeout_seconds=120,
        tier="H",
    ),
    "S2_AN_M": ModelInfo(
        model_id="claude-sonnet-5-5",
        provider="Anthropic", name="Claude Sonnet 5.5 (S2 Middle)",
        adapter_type="anthropic",
        input_price=2.0, output_price=10.0,  # Anthropic公式料金表 2026-10-23確認
        env_key="CLAUDE_API_KEY", base_url=None,
        supports_temperature=False,
        cached_input_price=0.10,
        # 実測2026-10-23: thinking.type="disabled"は400。このモデルは
        # "between_tools"で思考を止める（本文: To turn thinking off on this
        # model, send "thinking": {"type": "between_tools"}）。思考0トークン実測。
        extra_params={"thinking": {"type": "between_tools"}},
        tier="M",
    ),
    "S2_AN_L": ModelInfo(
        model_id="claude-haiku-5-5",
        provider="Anthropic", name="Claude Haiku 5.5 (S2 Low)",
        adapter_type="anthropic",
        input_price=0.10, output_price=0.50,  # Anthropic公式料金表 2026-10-23確認
        # プロンプト長が10万トークンを超えると単価5倍（0.50/2.50）になる段階
        # 料金だが、本プロジェクトの手番は15,000字上限（TURN_PROMPT_MAX_CHARS）
        # のため超えない前提。超えた場合は費用上限（game_cost_budget）で
        # 事前に止まる設計のため実害は無い。
        env_key="CLAUDE_API_KEY", base_url=None,
        supports_temperature=False,
        cached_input_price=0.01,
        extra_params={"thinking": {"type": "disabled"}},  # 実測2026-10-23: 通る
        tier="L",
    ),

    # --- OpenAI（DevRelay/Codex経路、3席）---
    # Codex CLIを0.147.0→0.163.0に更新（サイクル7.0、本サイクルで実施）後、
    # ~/.codex/models_cache.jsonにgpt-6-astra/gpt-6-sol/gpt-6-lunaが
    # visibility=list・supported_in_api=trueで掲載されていることを確認。
    # `codex exec -m <各モデル>`で実際に応答も確認済み（3モデルとも成功）。
    # DevRelay側はモデル名の許可リストを持たないため（devrelay_ai="codex"を
    # 明示送信するだけ）、Codexが知っているモデル名なら素通しで動く見込み。
    # billing="subscription"のため入力・出力単価は0（このプロジェクトの
    # API課金は発生しない。DR_TERRA/DR_SOL/DR_LUNAと同じ契約）。
    "S2_OA_H": ModelInfo(
        model_id="devrelay/gpt-6-astra",
        provider="OpenAI", name="GPT-6 Astra (S2 High, DevRelay/Codex)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="H",
        devrelay_ai="codex",
    ),
    "S2_OA_M": ModelInfo(
        model_id="devrelay/gpt-6-sol",
        provider="OpenAI", name="GPT-6 Sol (S2 Middle, DevRelay/Codex)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="M",
        devrelay_ai="codex",
    ),
    "S2_OA_L": ModelInfo(
        model_id="devrelay/gpt-6-luna",
        provider="OpenAI", name="GPT-6 Luna (S2 Low, DevRelay/Codex)",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=180,
        supports_temperature=False,
        billing="subscription",
        tier="L",
        devrelay_ai="codex",
    ),

    # --- Google（API直結・OpenAI互換エンドポイント、3席）---
    # 実測2026-10-23: reasoning_effort="none"で3モデルとも隠れた思考トークン
    # （total-input-output）が0になることを確認（既定だとgemini-3.8-flashは
    # 343トークン、effort="low"だとgemini-3.1-flash-liteで逆に126トークン
    # 発生したため、3モデルとも"none"に統一する）。
    "S2_GO_H": ModelInfo(
        model_id="gemini-4-argon",
        provider="Google", name="Gemini 4 Argon (S2 High)",
        adapter_type="gemini",
        input_price=2.0, output_price=10.0,
        # Google公式 2026-10-23確認。導入価格は2026-11-30まで
        # （以降 入力4.0/出力20.0/キャッシュ0.20へ倍増）。
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        cached_input_price=0.10,
        extra_params={"reasoning_effort": "none"},
        hidden_thinking_reserve_tokens=512,  # 実測0だが他Gemini系と同じ安全マージンを置く
        tier="H",
    ),
    "S2_GO_M": ModelInfo(
        model_id="gemini-3.8-flash",
        provider="Google", name="Gemini 3.8 Flash (S2 Middle)",
        adapter_type="gemini",
        input_price=0.75, output_price=3.75,
        # Google公式 2026-10-23確認。導入価格は2026-12-31まで
        # （以降 入力1.50/出力7.50/キャッシュ0.15へ倍増）。
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        cached_input_price=0.075,
        # 実測: 既定(effort未指定)だと隠れた思考343トークン発生 →
        # reasoning_effort="none"で0トークンに抑制。
        extra_params={"reasoning_effort": "none"},
        hidden_thinking_reserve_tokens=512,
        tier="M",
    ),
    "S2_GO_L": ModelInfo(
        model_id="gemini-3.1-flash-lite",
        provider="Google", name="Gemini 3.1 Flash-Lite (S2 Low)",
        adapter_type="gemini",
        input_price=0.30, output_price=2.50,  # Google公式 2026-10-23確認。値上げ予定なし
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        cached_input_price=0.03,
        # 実測: effort="low"だと逆に隠れた思考126トークン発生したため"none"を採用。
        extra_params={"reasoning_effort": "none"},
        hidden_thinking_reserve_tokens=512,
        tier="L",
    ),

    # --- xAI（API直結、3席）---
    # grok-4.1-fastは実測2026-10-23で404（"does not exist or your team does
    # not have access to it"）のため不採用（けいすけさん決定3でロー候補を
    # grok-4.20-0309-non-reasoningに変更）。
    "S2_XA_H": ModelInfo(
        model_id="grok-4.7",
        provider="xAI", name="Grok 4.7 (S2 High)",
        adapter_type="openai_compat",
        input_price=2.0, output_price=6.0,  # xAI一覧API実測価格 2026-10-23（$/1Mに変換済み）
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        cached_input_price=0.50,
        tier="H",
        # 常時思考・無効化不可（grok-4.5/4.6と同じ系列）。トリビアルな1コール
        # 実測ではreasoning_tokens=101だが、実際の交渉文面での実測はまだ無い。
        # H4(grok-4.6)が最終的に辿り着いた値(9728)を暫定で流用し、Step3の
        # 実測で見直す（provider保証の上限ではない）。
        hidden_thinking_reserve_tokens=9728,
        timeout_seconds=180,  # H4と同じ理由（常時思考モデルは60秒の壁に当たりやすい）
    ),
    "S2_XA_M": ModelInfo(
        model_id="grok-4.3",
        provider="xAI", name="Grok 4.3 (S2 Middle)",
        adapter_type="openai_compat",
        # 既存L4（model_id="grok-4.3"）と同一モデル・同一単価・同一設定で
        # S2のミドル席として再登録。L4は変更しない（get_model()のmodel_id
        # 逆引きはL4が先勝ちするが、設定が同一のため実害なし。CLAUDE.md
        # 落とし穴19と同種の確認観点：このS2登録自体はvalidate_terms型の
        # 検査ではないため該当しないが、念のため同一性を明記する）。
        input_price=1.25, output_price=2.50,
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        cached_input_price=0.20,
        hidden_thinking_reserve_tokens=768,  # L4と同じ実測ベースの値
        tier="M",
    ),
    "S2_XA_L": ModelInfo(
        model_id="grok-4.20-0309-non-reasoning",
        provider="xAI", name="Grok 4.20 (non-reasoning) (S2 Low)",
        adapter_type="openai_compat",
        input_price=1.25, output_price=2.50,  # xAI一覧API実測価格 2026-10-23
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        cached_input_price=0.20,
        # 非推論版のため思考トークンは実測0。hidden_thinking_reserve_tokens不要。
        tier="L",
    ),

    # --- Moonshot（API直結、3席）---
    # K2.5は公式API一覧から消えている（実測2026-10-23確認、想定どおり）。
    "S2_MO_H": ModelInfo(
        model_id="kimi-k3",
        provider="Moonshot", name="Kimi K3 (S2 High)",
        adapter_type="openai_compat",
        # 既存H5（model_id="kimi-k3"）と同一モデル・同一単価・同一設定で
        # S2のハイ席として再登録。H5は変更しない。
        input_price=3.0, output_price=15.0,
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=90,
        extra_params={"thinking": {"type": "disabled"}},  # 実測2026-10-23: 通る
        temperature_override=0.6,
        tier="H",
    ),
    "S2_MO_M": ModelInfo(
        model_id="kimi-k2.7-code",
        provider="Moonshot", name="Kimi K2.7 Code (S2 Middle)",
        adapter_type="openai_compat",
        input_price=0.95, output_price=4.0,
        # 公式料金ページ（platform.kimi.ai/docs/pricing/chat）は取得時点
        # （2026-10-23）で価格表が空（ブラウザ描画のみ）だったため、二次資料
        # （requesty.ai等、複数サイトが$0.95/$4.00で一致）を出典とする。
        # cached_input_priceの0.19も同じ二次資料のみで未確認。
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        cached_input_price=0.19,
        timeout_seconds=90,
        # 実測2026-10-23: thinkingを明示的に無効化しようとすると400
        # （"invalid thinking: only type=enabled is allowed for this model"）。
        # さらにthinkingキーを一切送らなくても温度0.7で400になる（常時思考が
        # 既定で有効なため）。よってextra_paramsでthinking.type="enabled"を
        # 明示し、adapters.py::_thinking_enabled_payload()にTrueと判定させ、
        # THINKING_TEMPERATURE_LOCKED_PROVIDERS経由でtemperature=1.0へ
        # 自動ロックさせる（けいすけさん決定4: 思考OFF不可のまま採用）。
        # reasoning_tokensはOpenAI系と同じでcompletion_tokens内に含まれる
        # ため二重計上は無い（hidden_thinking_reserve_tokens不要）。
        extra_params={"thinking": {"type": "enabled"}},
        tier="M",
    ),
    "S2_MO_L": ModelInfo(
        model_id="kimi-k2.6",
        provider="Moonshot", name="Kimi K2.6 (S2 Low)",
        adapter_type="openai_compat",
        # 既存M5/L5（model_id="kimi-k2.6"）と同一モデル・同一単価・同一設定で
        # S2のロー席として再登録。M5/L5は変更しない。
        input_price=0.95, output_price=4.0,
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=90,
        extra_params={"thinking": {"type": "disabled"}},
        temperature_override=0.6,
        tier="L",
    ),

    # --- DeepSeek（API直結、2席。ミドルは存在しないため2席のまま）---
    # 公式料金ページ（api-docs.deepseek.com/quick_start/pricing）実測確認
    # 2026-10-23。既存H6/M6/L6の単価（0.435/0.87・0.14/0.28）は古い値だが、
    # けいすけさん決定5により既存エントリは変更せず、新しい席だけ公式の
    # 新単価を使う。model_idはH6と重複する（"deepseek-v4-pro"）が、値が
    # 異なる（H6は旧単価のまま）。get_model()のmodel_id逆引きはH6が先勝ち
    # するため、model_idの生文字列からの逆引きが必要な場面（viewer/log_parser.py・
    # blog/seats.pyの表示用途のみで、金額はそこでは使っていないことを確認済み）
    # ではH6の旧ラベルが出る可能性があるが、実際の対局（llm_trial.py）は
    # --rosterで渡した登録キー（"S2_DS_H"等）を直接使うため、実際の課金・
    # 費用上限の計算には影響しない。
    #
    # オフピーク/ピークの2段料金（ピークはUTC 01:00-04:00・06:00-10:00の
    # 平日=JST 10-13時・15-19時で2倍）はModelInfoが単一価格のため未モデル化。
    # 既存レジストリの他モデルも長文帯等の複数価格を単一価格で簡略化している
    # のと同じ扱い（例: H3のコメント参照）。
    "S2_DS_H": ModelInfo(
        model_id="deepseek-v4-pro",
        provider="DeepSeek", name="DeepSeek V4 Pro (S2 High)",
        adapter_type="openai_compat",
        input_price=0.66, output_price=1.98,  # 公式オフピーク価格
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        cached_input_price=0.022,
        extra_params={"thinking": {"type": "disabled"}},  # 実測2026-10-23: 通る
        max_tokens=2000,
        tier="H",
    ),
    "S2_DS_L": ModelInfo(
        model_id="deepseek-flash",
        provider="DeepSeek", name="DeepSeek Flash (S2 Low)",
        adapter_type="openai_compat",
        # "deepseek-flash"は既存M6/L6の"deepseek-v4-flash"とは別の文字列
        # （DeepSeek公式一覧APIの実在モデル名はdeepseek-flashとdeepseek-v4-pro
        # の2つのみ。deepseek-v4-flash/deepseek-chatはそのエイリアス）。
        # model_idの重複は無い。
        input_price=0.15, output_price=0.60,  # 公式オフピーク価格
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        cached_input_price=0.003,
        extra_params={"thinking": {"type": "disabled"}},  # 実測2026-10-23: 通る
        max_tokens=2000,
        tier="L",
    ),
}

# サイクル7.0: 第2戦の18席ロスターを1つの名前で呼べるようにする。
# scripts/llm_trial.py::parse_roster()の手前でこの辞書を展開する
# （"--roster L18_S2" も "--roster S2_AN_H1,..." の直書きも両方通る）。
# 社ごとにH→M→Lの順、6社の並びはVENDOR_ORDERに合わせる。
NAMED_ROSTERS: dict[str, list[str]] = {
    "L18_S2": [
        "S2_AN_H1", "S2_AN_H2", "S2_AN_M", "S2_AN_L",
        "S2_OA_H", "S2_OA_M", "S2_OA_L",
        "S2_GO_H", "S2_GO_M", "S2_GO_L",
        "S2_XA_H", "S2_XA_M", "S2_XA_L",
        "S2_MO_H", "S2_MO_M", "S2_MO_L",
        "S2_DS_H", "S2_DS_L",
    ],
}


def get_model(key: str) -> ModelInfo:
    """モデルキー(H1〜H6, M1〜M6, L1〜L6, L7)またはmodel_idからModelInfoを取得

    model_idでの逆引きは「先勝ち」（MODEL_REGISTRY定義順で最初に一致した1件）。
    M5/L5, M6/L6 のようにmodel_idが重複するエントリでは、宣言順が早い方
    （辞書順=このファイルでの記述順）が常に返る。viewer/log_parser.py の
    ローカル逆引きも同じ先勝ちルールに統一されている（setdefault使用）。
    """
    if key in MODEL_REGISTRY:
        return MODEL_REGISTRY[key]
    for info in MODEL_REGISTRY.values():
        if info.model_id == key:
            return info
    raise ValueError(f"Unknown model: {key}")


def get_models_by_tier(tier: str) -> dict[str, ModelInfo]:
    """指定した階級("H"/"M"/"L")のエントリのみを宣言順で返す（L7のtierは"L"なので含まれる）"""
    return {k: v for k, v in MODEL_REGISTRY.items() if v.tier == tier}


def get_model_keys_by_id(model_id: str) -> list[str]:
    """指定model_idを持つ全レジストリキーを宣言順で返す（get_modelの先勝ちで潰れる重複を保持する正直版）"""
    return [k for k, v in MODEL_REGISTRY.items() if v.model_id == model_id]


def list_vendor_endpoints() -> list[tuple[str, str, str | None, str]]:
    """(provider, env_key, base_url, adapter_type) のベンダー別一覧をprovider重複を除いて返す

    scripts/discover_models.py がモデルID発見のためのAPIエンドポイント一覧として使用する。
    """
    seen: set[str] = set()
    result: list[tuple[str, str, str | None, str]] = []
    for info in MODEL_REGISTRY.values():
        if info.provider in seen:
            continue
        seen.add(info.provider)
        result.append((info.provider, info.env_key, info.base_url, info.adapter_type))
    return result


def estimate_cost(
    model: ModelInfo,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    total_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> float:
    """
    トークン数からコスト（USD）を概算する

    Stage 2: キャッシュ割引・thinking トークン内訳に対応。
    - cache_read_input_tokens: キャッシュヒット入力トークン数（割引単価適用）
    - total_tokens: 全トークン合計（0の場合 input+output で代用）
    - cache_creation_input_tokens: キャッシュ書き込み（新規作成）トークン数。
      入力単価の CACHE_WRITE_PRICE_MULTIPLIER 倍で課金する（サイクル4.2c新設。
      それまでusage/ログには記録していたが金額に一切入っていなかった
      計上漏れの修正）。input_tokens/cache_read_input_tokensとは独立した
      別建ての課金（二重計上ではない。Anthropicは書き込みと読み込みを
      別のトークン数として返す）。

    二重計上防止:
      separate_thinking = max(0, total_tokens - input_tokens - output_tokens)
      OpenAI系: reasoning は completion_tokens に含まれる → separate_thinking ≈ 0（自動的に二重計上なし）
      Gemini系: completion に thinking が含まれない → 差分 = thinking が加算される
      Anthropic: thinking は output_tokens に含まれる → total = input + output → separate_thinking = 0

    サイクル10.7: billing="subscription"（DevRelay経由のサブスク席）は常に0円。
    トークン数に関わらずAPI課金が発生しないため、席キャップ/試合キャップの対象からも外れる。
    """
    if model.billing == "subscription":
        return 0.0

    effective_cached_price = model.cached_input_price if model.cached_input_price is not None else model.input_price
    effective_reasoning_price = model.reasoning_price if model.reasoning_price is not None else model.output_price

    # 入力コスト: 未キャッシュ分はフル単価、キャッシュ分は割引単価
    uncached_input = input_tokens - cache_read_input_tokens
    input_cost = uncached_input * model.input_price + cache_read_input_tokens * effective_cached_price

    # キャッシュ書き込みコスト（入力単価の1.25倍、サイクル4.2c）
    cache_write_cost = cache_creation_input_tokens * model.input_price * CACHE_WRITE_PRICE_MULTIPLIER

    # 出力コスト
    output_cost = output_tokens * model.output_price

    # thinking コスト（二重計上防止: total - input - output の差分のみ）
    effective_total = total_tokens if total_tokens > 0 else (input_tokens + output_tokens)
    separate_thinking = max(0, effective_total - input_tokens - output_tokens)
    thinking_cost = separate_thinking * effective_reasoning_price

    return (input_cost + cache_write_cost + output_cost + thinking_cost) / 1_000_000
