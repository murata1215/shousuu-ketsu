"""
LLM関連の定数

トークン予算・コスト上限・temperature等の調整可能パラメータ。
"""

# --- API呼出し設定 ---
DEFAULT_TEMPERATURE = 0.7  # 全モデル統一（ロスター仕様§5）
DEFAULT_MAX_TOKENS = 4000  # 1コールあたりの最大出力トークン（reasoning系モデル対応: thinking/reasoningトークンがmax_tokensに含まれるため余裕を確保）
MAX_RETRIES = 2            # JSON不正時の最大リトライ回数

# --- 修正7: API堅牢化 ---
API_TIMEOUT_SECONDS = 60   # 1コールのタイムアウト（秒）
API_MAX_RETRIES = 2        # 429/5xx/タイムアウト時の最大リトライ回数

# --- 修正4: 交渉空回り削減 ---
AUTO_PASS_ON_NO_NEWS = True  # 新規メッセージがない場合に自動pass（LLMコールしない）

# --- サイクル1.8: LLMの記憶（memory）配線 ---
# サイクル4.2cで500→1000に拡大（§9.4、v0.4.3 §11.8 #15）。会話の引き継ぎを
# ラウンド単位から投票単位に戻した安全網として、メモの上限を上げた。
# サイクル4.2dで「求める字数」と「切り捨てる字数」に分割（§9.4、v0.4.4
# §11.9 #1）。AIは字数を正確に数えられないため、2回目の12席試走では
# 1,000字で切り捨てていたところメモ72件のうち18件が末尾を切られた
# （CLAUDE.md過去の落とし穴④: 同じ役目の値を1つの定数に詰め込まない）。
MEMORY_REQUEST_MAX_LENGTH = 1000  # 文面でAIに求める字数（次の投票・ラウンドへ引き継ぐメモ）
MEMORY_TRUNCATE_LENGTH = 1500     # 実際に切り捨てる字数（response_parser.py::extract_memory）

# --- サイクル4.2c: 手番の文面の上限（§9.4） ---
TURN_PROMPT_MAX_CHARS = 15_000  # 1回の手番の文面（user_prompt、ルール全文を除く）の上限文字数。
# 超える場合は、その投票の会話を古い発言から1件ずつ省く（§9.4・§11.8 #9）。

# --- サイクル4.2c仕上げC: 出力が上限で切れたことの判定 ---
# finish_reason（各社の言い方のまま記録する値）のうち「出力の上限で切れた」を
# 表す値の一覧。各社の言い方が違う（Anthropic: "max_tokens"、OpenAI互換:
# "length"、DevRelay: サーバーの言い方次第）ため、ここ1か所にまとめる
# （CLAUDE.md過去の落とし穴④: 同型の判定を複数箇所に分散させない）。
TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})


def is_truncated_finish_reason(value: str | None) -> bool:
    """finish_reasonが「出力の上限で切れた」ことを表す値かどうかを判定する"""
    if not isinstance(value, str):
        return False
    return value.strip().lower() in TRUNCATED_FINISH_REASONS
