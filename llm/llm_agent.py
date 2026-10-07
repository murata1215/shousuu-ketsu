"""
LLMAgent — PlayerAgentの実装（§9・§13、AI席）

gentei-janken `llm/llm_agent.py`（B分類）の足場（予算予約・リトライ・ログ連携・
`_call()`のtry/finally後始末）をそのまま移植し、呼び出すプロンプト・パーサを
少数決専用の `llm/prompt_builder.py` / `llm/response_parser.py` に差し替えた。

直した点:
- `act()` を `negotiate()`（§7.1 Negotiation）と `commit()`（§4.1 Commit）の
  2メソッドへ分割。`commit()` は内部で再試行しない——無効な応答はNoneを返し、
  `engine/game.py::_phase_commit` の既存2回ループ（§4.4: 1回だけ再試行→
  自動代行）に委ねる（二重に再試行しないための判断。計画の方針）。
- `choose_loan()` は `build_loan_prompt` ＋ `parse_loan_amount`（§13.2）。
- `reflect()` は談合カード/gentei-jankenの引き継ぎメモリ（§9.4）をそのまま。
- DevRelay席（`model_info.adapter_type == "devrelay_http"`）では
  `build_system_prompt(..., include_anonymization=False)` を使う。
  provider側（`llm/providers/devrelay_http.py`）が送信直前に同じ
  `ANONYMIZATION_LINE` を付与するため、ここで付けると二重になる
  （サイクル1.4ユーザー指示）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import ValidationError

from engine.config import GameConfig
from engine.models import Action, PassAction, PlayerState, Vote, VoteCommitAction
from engine.negotiation import PlayerAgent
from llm.adapters import AdapterError
from llm.constants import DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, MAX_RETRIES
from llm.costing import usage_cost, worst_case_cost
from llm.game_cost_budget import BudgetBlockedError, GameCostBudget
from llm.llm_logger import LLMLogger
from llm.models import ModelInfo
from llm.prompt_builder import (
    build_commit_prompt, build_loan_prompt, build_negotiation_prompt,
    build_post_game_reflection_prompt, build_reflection_prompt, build_system_prompt,
)
from llm.response_parser import (
    ParseError, extract_json, extract_memory, make_correction_message, parse_loan_amount,
    parse_post_game_reflection, parse_response,
)

logger = logging.getLogger(__name__)

# response_format={"type":"json_object"}はOpenAI互換のchat.completions系だけが
# 受け付ける（Anthropicのmessages.createには無い。devrelay_httpはrequest_options
# 自体を無視する契約）。対応確認済みのadapter_typeだけに絞る
# （gentei-janken由来。CLAUDE.md過去の落とし穴④: 同型関数は全数検査）。
_JSON_OBJECT_RESPONSE_FORMAT_ADAPTER_TYPES = {"openai_compat", "gemini"}


def build_json_object_request_options(
    model_info: ModelInfo, system: str, user_message: str, *, player_id: str = "?", phase: str = "?",
) -> dict[str, Any] | None:
    """
    response_format={"type":"json_object"}を送るべきかどうかを判定する

    openai_compat/gemini系adapterは本文（system+user）に"json"の語が無いと
    response_format=json_objectをAPI側が400で拒否する。少数決の全プロンプトは
    §13.1のアクション形式で必ず"JSON"の語を含むため、通常は常にTrueになる
    （万一含まない呼び出しがあっても安全側でスキップするだけで試合は止まらない）。
    """
    if model_info.adapter_type not in _JSON_OBJECT_RESPONSE_FORMAT_ADAPTER_TYPES:
        return None
    if "json" in (system + "\n" + user_message).lower():
        return {"response_format": {"type": "json_object"}}
    logger.warning(
        "response_format=json_objectをスキップ（%s phase=%s: プロンプトに'json'の語が無い）",
        player_id, phase,
    )
    return None


class LLMAgent(PlayerAgent):
    """LLM APIを使用するプレイヤーエージェント（§9・§13）"""

    def __init__(
        self,
        player_id: str,
        model_info: ModelInfo,
        adapter: Any,
        llm_logger: LLMLogger,
        config: GameConfig,
        game_cost_budget: GameCostBudget | None = None,
    ) -> None:
        self.player_id = player_id
        self.model_info = model_info
        self.adapter = adapter
        bind_seat = getattr(adapter, "bind_seat", None)
        if callable(bind_seat):
            bind_seat(player_id)
        self.llm_logger = llm_logger
        self.config = config
        self.game_cost_budget = game_cost_budget

        # DevRelay席はprovider側がANONYMIZATION_LINEを付与するため二重を避ける。
        include_anonymization = model_info.adapter_type != "devrelay_http"
        self._system_prompt = build_system_prompt(
            player_id, config, include_anonymization=include_anonymization,
        )

        self._memory: str = ""
        """次ラウンドへ引き継ぐ自分自身へのメモ（§9.4）。エンジンの状態には一切触れず、
        このエージェントのプロンプトにのみ再注入する"""

        self.total_calls = 0
        self.valid_json_count = 0
        self.action_correction_count = 0
        self.last_emotion: str | None = None
        self._last_call_was_budget_blocked = False

    # ------------------------------------------------------------------
    # PlayerAgent実装
    # ------------------------------------------------------------------

    def choose_loan(self, config: GameConfig) -> int:
        """借入額を選択する（§3.1・§13.2）。失敗時は安全側（loan_min）にフォールバック"""
        prompt = build_loan_prompt(config)
        text, _usage = self._call(self._system_prompt, prompt, max_tokens=1000, phase="loan", round_num=0)
        if text is None:
            return config.loan_min
        try:
            amount = parse_loan_amount(text)
        except ParseError:
            return config.loan_min
        return max(config.loan_min, min(config.loan_max, amount))

    def negotiate(
        self, player_state: PlayerState, round_num: int, turn: int, visible_state: dict,
    ) -> Action:
        """Negotiationフェイズで1アクションを選択する（§7.1）。解析失敗は1回だけ是正を試みる"""
        base_prompt = build_negotiation_prompt(
            player_state, round_num, turn, visible_state, self.config, memory=self._memory or None,
        )
        prompt = base_prompt

        for _attempt in range(MAX_RETRIES + 1):
            text, _usage = self._call(
                self._system_prompt, prompt, max_tokens=DEFAULT_MAX_TOKENS,
                phase="negotiation", round_num=round_num, turn=turn,
            )
            self.total_calls += 1
            if text is None:
                self.last_emotion = None
                return PassAction(player_id=player_state.player_id)

            new_memory = extract_memory(text)
            if new_memory is not None:
                self._memory = new_memory

            try:
                strategy, action = parse_response(text, player_state.player_id, "negotiation")
                self.valid_json_count += 1
                self.last_emotion = (strategy or {}).get("emotion") if isinstance(strategy, dict) else None
                return action
            except (ParseError, ValueError, TypeError, ValidationError) as e:
                parse_error = e if isinstance(e, ParseError) else ParseError(
                    str(e), "JSON形式とフィールドの型・必須項目を仕様に合わせて修正してください。",
                )
                self.action_correction_count += 1
                logger.warning(
                    "parse error for %s negotiation turn=%d: %s", player_state.player_id, turn, parse_error,
                )
                prompt = base_prompt + "\n\n" + make_correction_message(parse_error)
                continue

        return PassAction(player_id=player_state.player_id)

    def commit(
        self, player_state: PlayerState, round_num: int, visible_state: dict,
    ) -> Vote:
        """
        Commitフェイズで投票先を選択する（§4.1）。

        内部で再試行しない——無効な応答・解析失敗は None 相当として扱われる
        （戻り値の型はVote宣言だが、実際にはVoteでない値を返すことでこれを
        表す。engine/game.py::_phase_commit は `isinstance(candidate, Vote)`
        でしか判定しないため、Vote以外を返せば自動的に1回だけ再試行→
        自動代行のフローに乗る。§4.4の「1回だけ再試行」を二重にしないための
        設計判断）。
        """
        prompt = build_commit_prompt(
            player_state, round_num, visible_state, self.config, memory=self._memory or None,
        )
        text, _usage = self._call(
            self._system_prompt, prompt, max_tokens=DEFAULT_MAX_TOKENS,
            phase="commit", round_num=round_num,
        )
        self.total_calls += 1
        if text is None:
            return None  # type: ignore[return-value]

        new_memory = extract_memory(text)
        if new_memory is not None:
            self._memory = new_memory

        try:
            strategy, action = parse_response(text, player_state.player_id, "commit")
        except (ParseError, ValueError, TypeError, ValidationError) as e:
            self.action_correction_count += 1
            logger.warning("parse error for %s commit round=%d: %s", player_state.player_id, round_num, e)
            return None  # type: ignore[return-value]

        self.last_emotion = (strategy or {}).get("emotion") if isinstance(strategy, dict) else None
        if isinstance(action, VoteCommitAction):
            self.valid_json_count += 1
            return action.vote
        return None  # type: ignore[return-value]

    def reflect(self, player_state: PlayerState, round_num: int, visible_state: dict) -> None:
        """ラウンド終了後の振り返り（引き継ぎメモリ、§9.4）。失敗時はメモを更新しない"""
        prompt = build_reflection_prompt(
            player_state, round_num, visible_state, self.config, memory=self._memory or None,
        )
        text, _usage = self._call(
            self._system_prompt, prompt, max_tokens=1200, phase="reflect", round_num=round_num,
        )
        if text is None:
            return
        new_memory = extract_memory(text)
        if new_memory is not None:
            self._memory = new_memory

    def post_game_reflect(self, post_game_context: dict[str, Any]) -> dict[str, Any] | None:
        """
        試合後の振り返り（§9.4）。ゲーム完全終了後に1回だけ呼ぶことを想定
        （Game本体からの配線は次サイクル。ここではメソッドとパーサが孤児に
        ならないよう用意だけしておく）。
        """
        prompt = build_post_game_reflection_prompt(
            self.config, post_game_context, memory=self._memory or None,
        )
        text, _usage = self._call(
            self._system_prompt, prompt, max_tokens=1200, phase="post_game", round_num=self.config.num_rounds,
        )
        return parse_post_game_reflection(text, max_chars=400)

    # ------------------------------------------------------------------
    # 内部: API呼び出し
    # ------------------------------------------------------------------

    def _call(
        self, system: str, user_message: str, max_tokens: int, phase: str = "act",
        round_num: int = 0, turn: int | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """
        APIを1回呼び出す。予算ブロック・アダプタエラー・想定外の例外時は
        (None, None) を返す（試合を止めない）。

        呼び出しはllm_loggerへ記録する。game_cost_budgetが注入されている場合は
        呼び出し前にworst_case_cost()で予約し、成功時に実コストで精算する。
        """
        self._last_call_was_budget_blocked = False
        reservation = None
        if self.game_cost_budget is not None:
            reserve_amount = worst_case_cost(self.model_info, system, user_message, max_tokens)
            try:
                reservation = self.game_cost_budget.reserve(
                    self.player_id, amount_usd=reserve_amount, round_num=round_num, phase=phase, turn=turn,
                )
            except BudgetBlockedError:
                self._last_call_was_budget_blocked = True
                return None, None

        request_options = build_json_object_request_options(
            self.model_info, system, user_message, player_id=self.player_id, phase=phase,
        )

        started = time.monotonic()
        settled = False
        try:
            text, usage = self.adapter.complete(
                system=system,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=max_tokens,
                temperature=DEFAULT_TEMPERATURE,
                request_options=request_options,
            )
            elapsed_ms = (time.monotonic() - started) * 1000

            cost = usage_cost(self.model_info, usage)
            if reservation is not None and self.game_cost_budget is not None:
                self.game_cost_budget.settle(reservation, cost)
                settled = True

            emotion = None
            data = extract_json(text) if text else None
            if isinstance(data, dict):
                strategy = data.get("strategy")
                if isinstance(strategy, dict):
                    emotion = strategy.get("emotion")
                elif isinstance(data.get("emotion"), str):
                    emotion = data.get("emotion")
            self.last_emotion = emotion

            self.llm_logger.log_call(
                player_id=self.player_id, model_id=self.model_info.model_id,
                phase=phase, round_num=round_num, turn=turn,
                system_prompt=system, user_prompt=user_message, response_text=text,
                usage=usage, cost=cost, elapsed_ms=elapsed_ms, emotion=emotion,
            )
            return text, usage
        except AdapterError as e:
            logger.warning("adapter error for %s: %s", self.player_id, e)
            return None, None
        except Exception as e:  # noqa: BLE001 — 想定外の例外も安全側へ丸める
            logger.warning("unexpected error in _call for %s phase=%s: %s", self.player_id, phase, e)
            return None, None
        finally:
            if reservation is not None and self.game_cost_budget is not None and not settled:
                self.game_cost_budget.release(reservation)
