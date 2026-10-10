"""
12席・4ラウンドの通し受け入れテスト（§9・§13、AIを呼ばない。台本どおりに動く
偽AIを使う。サイクル4.2bのB10「決まった応答を返す偽のAIで、12席・4ラウンドの
試合を最後まで回す通しテストを1本入れる」）

通す場面:
- 契約の提案と署名（P01→P02、R1に型A義務）
- 退場者の交渉（R1V1で退場した7人がR1V2にも参加し、交渉プロンプトに
  退場中の事実が出る）
- 不成立の理由の通知（P07がR1V1の1巡目に手持ちを超える送金を試み、
  次の手番に日本語の理由が本人にだけ渡る）
- やり直し（R2V1を6対6の同数にする）
- ラウンドの終わりの振り返り（全ラウンド後に呼ばれる）
- 試合後の振り返り（ゲーム完全終了後に1回だけ呼ばれる）

LLMAgent→llm/response_parser.py→engine/game.pyの実際の経路を全て通す
（ScriptedAgentではなく、プロンプトを読んでJSON応答を返す偽AIを使う）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from llm.models import get_model

_USAGE = {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}

# (round_num, vote_num) -> {player_id: "YES"/"NO"}。未指定のplayer_idはYES
# （decisiveのときYES側が多数なら多数派、NOが少ないと少数派になる等、
# 下のVOTES定義で明示する）。
VOTES: dict[tuple[int, int], dict[str, str]] = {
    # R1V1: YES(5人、残る)=P01-P05 / NO(7人、退場)=P06-P12
    (1, 1): {**{f"P{i:02d}": "YES" for i in range(1, 6)}, **{f"P{i:02d}": "NO" for i in range(6, 13)}},
    # R1V2（残りP01-P05）: YES(2人、残る・勝ち残り)=P01,P02 / NO(3人、退場)=P03-P05
    (1, 2): {"P01": "YES", "P02": "YES", "P03": "NO", "P04": "NO", "P05": "NO"},
    # R2V1: 6対6の同数（やり直し）
    (2, 1): {**{f"P{i:02d}": "YES" for i in range(1, 7)}, **{f"P{i:02d}": "NO" for i in range(7, 13)}},
    # R2V2（やり直し後、12人のまま）: YES(2人、残る)=P01,P02 / NO(10人、退場)
    (2, 2): {"P01": "YES", "P02": "YES"},
    # R3V1: YES(2人、残る)=P01,P02 / NO(10人、退場)
    (3, 1): {"P01": "YES", "P02": "YES"},
    # R4V1（最終ラウンド）: YES(2人、残る)=P01,P02 / NO(10人、退場)
    (4, 1): {"P01": "YES", "P02": "YES"},
}


def _vote_for(round_num: int, vote_num: int, pid: str) -> str:
    return VOTES.get((round_num, vote_num), {}).get(pid, "NO")


_CONTRACT_SIGN_RE = re.compile(r"契約(C_[A-Z0-9]{8})（R\d+V\d+提案、提案者: (P\d+)")


class ScriptedLLMResponder:
    """
    台本どおりに動く偽AI（llm/adapters.pyの各アダプタと同じduck-typing:
    .bind_seat(seat_key)・.complete(system, messages, ...) -> (text, usage)）。

    既定は毎回pass/YES投票/空メモで応答する。negotiate_overridesで特定の
    (player_id, round_num, vote_num, turn)だけ上書きできる。sign_for
    （{player_id: 待っている提案者のplayer_id}）に登録されていれば、
    その提案者からの署名待ち契約を見つけたら自動で署名する。
    """

    def __init__(
        self,
        negotiate_overrides: dict[tuple[str, int, int, int], dict[str, Any]] | None = None,
        sign_for: dict[str, str] | None = None,
    ) -> None:
        self.negotiate_overrides = negotiate_overrides or {}
        self.sign_for = sign_for or {}
        self.seat_key: str | None = None
        self._signed: set[str] = set()
        self.calls: list[dict[str, Any]] = []

    def bind_seat(self, seat_key: str) -> None:
        self.seat_key = seat_key

    def complete(
        self, system: str, messages: list[dict[str, str]], max_tokens: int = 1000,
        temperature: float = 0.7, request_options: dict | None = None,
    ) -> tuple[str, dict[str, int]]:
        user = messages[0]["content"]
        self.calls.append({"system": system, "messages": messages})
        pid = self.seat_key

        if "借入額を" in user:
            return json.dumps({"loan_amount": 1_200_000, "reason": "safe"}), _USAGE
        if "振り返り（引き継ぎメモ）" in user:
            return json.dumps({"memory": ""}), _USAGE
        if "=== ゲーム終了" in user:
            return json.dumps({"emotion": "楽", "comment": "良い試合だった"}), _USAGE

        rv_match = re.search(r"=== R(\d+)V(\d+)", user)
        assert rv_match is not None, f"R{{r}}V{{v}}見出しが見つからない: {user[:80]!r}"
        round_num, vote_num = int(rv_match.group(1)), int(rv_match.group(2))

        if "投票（Commit）" in user:
            vote = _vote_for(round_num, vote_num, pid)
            return (
                json.dumps({"strategy": {"emotion": "楽"}, "action": {"type": "vote_commit", "vote": vote}}),
                _USAGE,
            )

        # 交渉フェイズ
        turn_match = re.search(r"（(\d+)巡目", user)
        turn = int(turn_match.group(1)) if turn_match else 1
        override = self.negotiate_overrides.get((pid, round_num, vote_num, turn))
        if override is not None:
            return json.dumps(override), _USAGE

        proposer = self.sign_for.get(pid)
        if proposer is not None and pid not in self._signed:
            for contract_id, found_proposer in _CONTRACT_SIGN_RE.findall(user):
                if found_proposer == proposer:
                    self._signed.add(pid)
                    return (
                        json.dumps({
                            "strategy": {"emotion": "楽"},
                            "action": {"type": "contract_sign", "contract_id": contract_id},
                        }),
                        _USAGE,
                    )

        return json.dumps({"strategy": {"emotion": "楽"}, "action": {"type": "pass"}}), _USAGE


def test_l12r4v6_full_game_with_scripted_llm_agents_completes() -> None:
    config = GameConfig.default_12()

    negotiate_overrides: dict[tuple[str, int, int, int], dict[str, Any]] = {
        (
            "P01", 1, 1, 1,
        ): {
            "strategy": {"emotion": "奸"},
            "action": {
                "type": "contract_propose", "with": ["P02"],
                "terms": [{"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
                           "round_num": 1, "details": {"amount": 200_000}}],
            },
        },
        # P07はR1V1の1巡目に手持ち（120万）を超える送金を試みて不成立になる
        ("P07", 1, 1, 1): {
            "strategy": {"emotion": "焦"},
            "action": {"type": "transfer", "to": "P08", "amount": 50_000_000},
        },
    }
    sign_for = {"P02": "P01"}

    llm_logger = LLMLogger(output_dir="/tmp/shousuu_ketsu_e2e_logs", game_id="e2e")
    agents: dict[str, LLMAgent] = {}
    responders: dict[str, ScriptedLLMResponder] = {}
    for i in range(1, 13):
        pid = f"P{i:02d}"
        responder = ScriptedLLMResponder(negotiate_overrides=negotiate_overrides, sign_for=sign_for)
        responders[pid] = responder
        agents[pid] = LLMAgent(pid, get_model("H1"), responder, llm_logger, config)

    event_logger = EventLogger()
    game = Game(config=config, agents=agents, seed=1, logger=event_logger)
    result = game.run()

    # --- 試合が最後まで回ること ---
    assert len(result.round_summaries) == config.num_rounds

    # --- 契約の提案と署名 ---
    established = [e for e in event_logger.events if e.event_type == "CONTRACT_ESTABLISHED"]
    assert len(established) == 1
    payments = [
        e for e in event_logger.events
        if e.event_type == "CONTRACT_PAYMENT" and e.data.get("obligor") == "P01"
    ]
    assert payments
    assert payments[0].data["paid"] == 200_000

    # --- 退場者の交渉: R1V1で退場した人がR1V2でも交渉プロンプトを受け取る ---
    r1v1_eliminated = {pid for pid, v in VOTES[(1, 1)].items() if v == "NO"}
    assert r1v1_eliminated  # P06..P12
    r1v2_negotiation_calls = [
        c for pid in r1v1_eliminated for c in responders[pid].calls
        if "=== R1V2 / 交渉" in c["messages"][0]["content"]
    ]
    assert r1v2_negotiation_calls, "退場者がR1V2の交渉に参加していない"
    assert any(
        "退場しています" in c["messages"][0]["content"] for c in r1v2_negotiation_calls
    )

    # --- 不成立の理由の通知: P07だけに日本語で、次の手番（同じ投票の2巡目）にだけ出る ---
    # 「理由は次の手番に本人にだけ通知される」（§7.5）。passも行動なので3巡目には
    # 残らない（engine/game.py::_execute_negotiation_action の既定方針）。
    p07_turn2 = [
        c for c in responders["P07"].calls
        if "=== R1V1 / 交渉（2巡目" in c["messages"][0]["content"]
    ]
    assert p07_turn2
    assert any("不成立:" in c["messages"][0]["content"] for c in p07_turn2)
    assert all("Insufficient cash" not in c["messages"][0]["content"] for c in responders["P07"].calls)
    for pid in ("P01", "P02", "P08"):
        assert all("不成立:" not in c["messages"][0]["content"] for c in responders[pid].calls)

    # --- やり直し: R2V1が同数で発生していること ---
    retry_events = [
        e for e in event_logger.events
        if e.event_type == "VOTE_RESOLVED" and e.round_num == 2 and e.vote_num == 1
        and e.data.get("result") == "retry"
    ]
    assert len(retry_events) == 1

    # --- ラウンドの終わりの振り返りが全ラウンドで全員に呼ばれていること ---
    # reflect()は戻り値もイベントログも持たないため（engine/game.py::_phase_reflect
    # のdocstring）、偽AI側の呼び出しログで確認する。
    reflect_calls_per_player = [
        sum(1 for c in r.calls if "振り返り（引き継ぎメモ）" in c["messages"][0]["content"])
        for r in responders.values()
    ]
    assert all(n == config.num_rounds for n in reflect_calls_per_player)

    # --- 試合後の振り返り: ゲーム完全終了後に全員へ1回だけ（イベントログにも残る） ---
    post_game_events = [e for e in event_logger.events if e.event_type == "POST_GAME_REFLECTION"]
    assert len(post_game_events) == config.num_players
    assert len(result.post_game_reflections) == config.num_players
    for comment in result.post_game_reflections.values():
        assert comment["comment"] == "良い試合だった"

    # --- 帳尻・現金非負 ---
    expected_total = -(
        result.total_interest + result.total_destroyed_pot + result.total_forfeited_remainder
    )
    assert sum(result.final_assets.values()) == expected_total
    assert all(p.cash >= 0 for p in result.final_players.values())
