"""
手番の文面の上限（#64、§9.4）のBot確認

契約をでたらめに結び（`bots/random_contract_bot.py::RandomContractBot`を
下敷きにする）、毎巡約200字で発言するBotで30試合（R1〜R4・既定の巡
10/6/3のまま）を回し、次を確かめる（人間の指示どおり、巡を縮めない通し。
CLAUDE.md落とし穴⑬参照）:

(a) 会話以外が15,000字以内の文面は、すべて15,000字以内に収まっている
    （会話を省いた結果、上限を超えるのは会話以外だけで既に上限を超えて
    いる場合に限られる）
(b) 残った発言はどれも省かれた発言より新しい
(c) 会話以外だけで15,000字を超えた文面の件数（0件の見込み。0でなければ
    報告する）

参考値として、ラウンド別の「会話以外」の文字数（中央値・最大）も記録する
（人間への完了報告用。`visible_state`の`messages`を空にした同じ文面を
もう一度組み立てて測る。探索ロジックや本番の`visible_state`自体には
一切触れない）。

`visible_state`はエンジンが毎手番新規に作るため（`VisibleStateRecordingAgent`
のようなdeepcopyは不要）、文面をその場で組み立てて集計するだけの軽量な
プローブエージェントを使う。
"""

from __future__ import annotations

import random
import re
import statistics
from typing import Any

from bots.random_contract_bot import RandomContractBot
from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import Action, BroadcastAction, DmAction, PassAction, PlayerState, Vote
from engine.negotiation import PlayerAgent
from llm.constants import TURN_PROMPT_MAX_CHARS
from llm.prompt_builder import (
    TurnPrompt, build_commit_prompt, build_negotiation_prompt, build_reflection_prompt,
)

_NUM_GAMES = 30
_CHAT_CHARS = 200


class _ChattyContractBot(PlayerAgent):
    """
    RandomContractBotの契約行動はそのまま使い、何もしない（pass）はずの巡には
    代わりに約200字の発言（全体発言とDMを半々）を送る（テスト専用）。
    """

    def __init__(self, player_id: str, seed: int, loan: int = 1_200_000) -> None:
        self._inner = RandomContractBot(player_id, seed=seed, loan=loan)
        self.player_id = player_id
        self._rng = random.Random(seed * 7919 + 1)

    def choose_loan(self, config: GameConfig) -> int:
        return self._inner.choose_loan(config)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self._inner.commit(player_state, round_num, vote_num, visible_state)

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        action = self._inner.negotiate(player_state, round_num, vote_num, turn, visible_state)
        if not isinstance(action, PassAction):
            return action
        message = "".join(self._rng.choices("あいうえおかきくけこさしすせそ", k=_CHAT_CHARS))
        all_ids = sorted(visible_state.get("initial_loans", {}).keys())
        others = [p for p in all_ids if p != self.player_id]
        if others and self._rng.random() < 0.5:
            return DmAction(player_id=self.player_id, to=self._rng.choice(others), message=message)
        return BroadcastAction(player_id=self.player_id, message=message)


class _TurnPromptStats:
    """文面の長さ・省略件数の集計（テスト専用）"""

    def __init__(self) -> None:
        self.total = 0
        self.over_limit_without_messages = 0
        self.non_message_chars_by_round: dict[int, list[int]] = {}

    def record_turn(
        self, result: TurnPrompt, visible_state: dict[str, Any], no_message_text_len: int,
    ) -> None:
        self.total += 1
        messages = visible_state.get("messages") or []

        if result.omitted_messages < len(messages):
            # 会話がまだ残っている=この時点で上限に収まっている
            # （_assemble_turn_promptの契約: 収まった時点で即returnする）
            assert len(result.text) <= TURN_PROMPT_MAX_CHARS, (
                f"会話が残っているのに上限を超えている（len={len(result.text)}）"
            )
        elif len(result.text) > TURN_PROMPT_MAX_CHARS:
            # 会話を全部省いても、会話以外だけで上限を超えている
            self.over_limit_without_messages += 1

        # 残った発言は、どれも省かれた発言より新しい（タグの並びで確認）
        kept = messages[result.omitted_messages:]
        if kept:
            tags = re.findall(r"\[R\d+V(\d+) (\d+)巡目\]", result.text)
            assert tags, "会話が残っているのに会話のタグが文面に無い"
            first_kept_tag = (kept[0]["vote_num"], kept[0]["turn"])
            assert (int(tags[0][0]), int(tags[0][1])) == first_kept_tag, (
                "残った発言の先頭が、省かれなかった最初の発言と一致しない"
            )

        round_num = visible_state["round_num"]
        self.non_message_chars_by_round.setdefault(round_num, []).append(no_message_text_len)


def _player_state(pid: str) -> PlayerState:
    return PlayerState(player_id=pid, cash=0, initial_loan=0)


class _Probe(PlayerAgent):
    """negotiate/commit/reflectの手番ごとに文面を組み立てて集計するだけのラッパー
    （テスト専用。行動の判断はinnerにそのまま委譲する）"""

    def __init__(self, inner: PlayerAgent, config: GameConfig, stats: _TurnPromptStats) -> None:
        self._inner = inner
        self.player_id = inner.player_id
        self._config = config
        self._stats = stats

    def choose_loan(self, config: GameConfig) -> int:
        return self._inner.choose_loan(config)

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        result = build_negotiation_prompt(
            player_state, round_num, vote_num, turn, visible_state, self._config,
        )
        no_msg = build_negotiation_prompt(
            player_state, round_num, vote_num, turn,
            {**visible_state, "messages": []}, self._config,
        )
        self._stats.record_turn(result, visible_state, len(no_msg.text))
        return self._inner.negotiate(player_state, round_num, vote_num, turn, visible_state)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        result = build_commit_prompt(player_state, round_num, vote_num, visible_state, self._config)
        no_msg = build_commit_prompt(
            player_state, round_num, vote_num, {**visible_state, "messages": []}, self._config,
        )
        self._stats.record_turn(result, visible_state, len(no_msg.text))
        return self._inner.commit(player_state, round_num, vote_num, visible_state)

    def reflect(
        self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict,
        *, is_round_end: bool,
    ) -> None:
        result = build_reflection_prompt(
            player_state, round_num, vote_num, visible_state, self._config, is_round_end=is_round_end,
        )
        no_msg = build_reflection_prompt(
            player_state, round_num, vote_num, {**visible_state, "messages": []}, self._config,
            is_round_end=is_round_end,
        )
        self._stats.record_turn(result, visible_state, len(no_msg.text))
        return self._inner.reflect(
            player_state, round_num, vote_num, visible_state, is_round_end=is_round_end,
        )


def _run_one_game(seed: int, stats: _TurnPromptStats) -> None:
    config = GameConfig.default_12()
    agents: dict[str, PlayerAgent] = {
        f"P{i:02d}": _Probe(_ChattyContractBot(f"P{i:02d}", seed=seed * 100 + i), config, stats)
        for i in range(1, 13)
    }
    game = Game(config=config, agents=agents, seed=seed, logger=EventLogger())
    game.run()


def test_turn_prompt_respects_15000_char_limit_under_heavy_chat_load() -> None:
    stats = _TurnPromptStats()
    for seed in range(1, _NUM_GAMES + 1):
        _run_one_game(seed, stats)

    assert stats.total > 0, "文面が1件も記録されていない"

    # (c) 会話以外だけで15,000字を超えた件数（0件の見込み。0でなければ報告する）
    if stats.over_limit_without_messages:
        print(
            f"[報告] 会話以外だけで15,000字を超えた件数: "
            f"{stats.over_limit_without_messages}/{stats.total}",
        )
    # 大多数は上限内に収まる設計（会話を省くことで吸収できる想定）。
    # 「会話以外」単体で上限を超えるのは異常なラウンド構成であるべきなので、
    # 全体の1割を超えたら明確な劣化として失敗させる。
    assert stats.over_limit_without_messages <= stats.total * 0.10

    # 参考値（人間への完了報告用）: ラウンド別の「会話以外」の文字数
    for round_num in sorted(stats.non_message_chars_by_round):
        lengths = stats.non_message_chars_by_round[round_num]
        print(
            f"R{round_num}: 会話以外の文字数 中央値={statistics.median(lengths):.0f} "
            f"最大={max(lengths)} (n={len(lengths)})",
        )
