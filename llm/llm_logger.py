"""
LLMコールロガー

全プロンプト・全生レスポンスをJSONLで保存する。
トークン数・所要時間・コスト概算・キャッシュ情報も記録。

P0修正: 逐次書き込み化。各コール完了時点でJSONLに追記し、
クラッシュしても直前までのログが必ず残る。
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LLMLogger:
    """
    LLMコールのJSONLロガー（逐次書き込み方式）

    各log_call()でファイルに即時追記+flushする。
    試合終了前にクラッシュしてもログが残る。
    """

    def __init__(self, output_dir: str | Path, game_id: str = "game") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.game_id = game_id
        self._entries: list[dict[str, Any]] = []
        self._total_cost: float = 0.0
        # 逐次書き込み用ファイルハンドル
        self._file_path = self.output_dir / f"{self.game_id}_llm_calls.jsonl"
        self._file = open(self._file_path, "a", encoding="utf-8")
        # サイクル2.6: engine/game.pyが複数プレイヤーのLLM呼び出しを並列化
        # できるようになったため、_entries.append()とファイルへの逐次書き込みが
        # 複数スレッドから同時に起きうる。ロックが無いとJSONL行が交差して
        # 壊れたり（1行に2エントリ分が混在する等）、_entriesへの追記が
        # 競合で欠落したりする実害があるため、書き込み経路を全てロックで囲む。
        self._lock = threading.Lock()

    def log_call(
        self,
        player_id: str,
        model_id: str,
        phase: str,
        round_num: int,
        turn: int | None,
        system_prompt: str,
        user_prompt: str,
        response_text: str,
        usage: dict[str, int],
        cost: float,
        elapsed_ms: float,
        retry_count: int = 0,
        error: str | None = None,
        error_type: str | None = None,
        emotion: str | None = None,
        reasoning: str | None = None,
        finish_reason: str | None = None,
        unit_price_input: float = 0.0,
        unit_price_output: float = 0.0,
    ) -> None:
        """
        1回のLLMコールを記録し、即時にファイルへ追記する
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "game_id": self.game_id,
            "player_id": player_id,
            "model_id": model_id,
            "phase": phase,
            "round_num": round_num,
            "turn": turn,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_text": response_text,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cost_usd": cost,
            "api_called": True,
            "budget_blocked": False,
            "elapsed_ms": elapsed_ms,
            "retry_count": retry_count,
            "error": error,
            "error_type": error_type,
            "emotion": emotion,
            "reason_category": None,  # 未使用（dangou-cardの_update_last_log_emotion相当は本サイクル未移植）
            "reasoning": reasoning,  # CoT: response_parser.extract_reasoning_and_emotion()で取得（god専用）
            "finish_reason": finish_reason,
            "reasoning_tokens": usage.get("reasoning_tokens", 0),
            "usage_raw": usage.get("usage_raw"),  # 生usageダンプ（Gemini等の未知フィールド炙り出し用）
            "response_model": usage.get("response_model"),  # API実返却モデル名（取得不能時はNone）
            "unit_price_input": unit_price_input,   # 単価スナップショット（$/1Mトークン）
            "unit_price_output": unit_price_output,  # 単価スナップショット（$/1Mトークン）
        }
        with self._lock:
            self._entries.append(entry)
            self._total_cost += cost

            # 逐次書き込み: 即時にファイルへ追記+flush
            try:
                self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._file.flush()
            except Exception:
                pass  # ファイル書き込みエラーでも試合は続行

    @property
    def total_cost(self) -> float:
        with self._lock:
            return self._total_cost

    @property
    def total_calls(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def total_input_tokens(self) -> int:
        with self._lock:
            return sum(e.get("input_tokens", 0) for e in self._entries)

    @property
    def total_output_tokens(self) -> int:
        with self._lock:
            return sum(e.get("output_tokens", 0) for e in self._entries)

    @property
    def total_cache_read_tokens(self) -> int:
        with self._lock:
            return sum(e.get("cache_read_input_tokens", 0) for e in self._entries)

    @property
    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries)

    def save(self) -> Path:
        """
        in-memoryエントリでファイルを全書き直しする。

        emotion/reasoningはlog_call()呼び出し時点で確定値を渡すため、本来は
        逐次書き込みのままで整合するが、試合完了後に一度全書き直しすることで
        逐次書き込み中の書きかけ行（クラッシュ時の不完全な最終行等）が
        残っていても最終ファイルには影響しないようにする。
        """
        with self._lock:
            try:
                self._file.close()
                with open(self._file_path, "w", encoding="utf-8") as f:
                    for entry in self._entries:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                # 再オープン（以降の追記に備える）
                self._file = open(self._file_path, "a", encoding="utf-8")
            except Exception:
                pass
        return self._file_path

    def log_budget_block(
        self, *, player_id: str, model_id: str, phase: str, round_num: int,
        turn: int | None, system_prompt: str, user_prompt: str,
        reason: str, estimated_cost_usd: float, player_spent_usd: float,
        game_spent_usd: float, per_player_cap_usd: float, game_cap_usd: float,
    ) -> None:
        """API未実行の予算ブロックを通常callと区別して記録する。"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(), "game_id": self.game_id,
            "player_id": player_id, "model_id": model_id, "phase": phase,
            "round_num": round_num, "turn": turn, "system_prompt": system_prompt,
            "user_prompt": user_prompt, "response_text": "", "input_tokens": 0,
            "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
            "api_called": False, "budget_blocked": True, "budget_reason": reason,
            "budget_estimated_next_cost_usd": estimated_cost_usd,
            "player_actual_spent_usd": player_spent_usd,
            "game_actual_spent_usd": game_spent_usd,
            "per_player_game_cost_cap_usd": per_player_cap_usd,
            "game_cost_cap_usd": game_cap_usd,
        }
        with self._lock:
            self._entries.append(entry)
            try:
                self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._file.flush()
            except Exception:
                pass

    def close(self) -> None:
        """ファイルハンドルを閉じる"""
        with self._lock:
            try:
                self._file.close()
            except Exception:
                pass
