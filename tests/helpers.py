"""
テスト用の固定エージェント（v0.4: L12R4V6）

ラウンドごとの投票・交渉行動を事前に指定できる ScriptedAgent と、常に
無効な応答を返す FailingCommitAgent（自動代行のテスト用）を提供する。

サイクル4.0でv0.3から全面更新した。v0.4は1ラウンドに複数の投票が入る
二重構造（§1.1）のため、ScriptedAgentのキーを(round_num, vote_num)の
組に変更した。無作為に投票・契約を行うBotは本番コード
（scripts/dry_run.py・sim/scenarios.py）からの逆依存を避けるため
`bots/random_contract_bot.py::RandomContractBot` に移した
（tests.helpers.RandomContractAgentは廃止）。
"""

from engine.config import GameConfig
from engine.models import (
    Action, ContractSignAction, PassAction, PlayerState, Vote,
)
from engine.negotiation import PlayerAgent


class ScriptedAgent(PlayerAgent):
    """
    (ラウンド, 投票)ごとの投票・交渉行動をあらかじめ指定できるエージェント

    Args:
        player_id: プレイヤーID
        votes: (round_num, vote_num) -> Vote。指定の無い投票は既定でYES
        loan: choose_loan() が返す借入額
        negotiate_actions: (round_num, vote_num, turn) -> Action。
            指定の無い巡はpass
        sign_proposer_at: (round_num, vote_num, turn) -> 署名したい契約の
            提案者 player_id。該当契約がまだ存在しない/署名済みなら何もしない
            （negotiate_actionsより優先。提案順・成立順を厳密に制御する
            テスト用）。
    """

    def __init__(
        self,
        player_id: str,
        votes: dict[tuple[int, int], Vote] | None = None,
        loan: int = 1_200_000,
        negotiate_actions: dict[tuple[int, int, int], Action] | None = None,
        sign_proposer_at: dict[tuple[int, int, int], str] | None = None,
    ) -> None:
        self.player_id = player_id
        self.votes = votes or {}
        self.loan = loan
        self.negotiate_actions = negotiate_actions or {}
        self.sign_proposer_at = sign_proposer_at or {}

    def choose_loan(self, config: GameConfig) -> int:
        return self.loan

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        target_proposer = self.sign_proposer_at.get((round_num, vote_num, turn))
        if target_proposer is not None:
            for c in visible_state.get("contracts_pending", []):
                if c["proposer"] == target_proposer and self.player_id not in c["signed_by"]:
                    return ContractSignAction(player_id=self.player_id, contract_id=c["contract_id"])
        return self.negotiate_actions.get(
            (round_num, vote_num, turn), PassAction(player_id=self.player_id),
        )

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.votes.get((round_num, vote_num), Vote.YES)


class FailingCommitAgent(ScriptedAgent):
    """
    commit() が常に無効な応答（例外）を返すエージェント（§4.6 自動代行のテスト用）

    negotiate/choose_loan はScriptedAgentと同じ。
    """

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        raise RuntimeError("invalid output (simulated)")


class FakeAdapter:
    """
    .complete() が、呼び出し回数に応じて事前に積んだ応答テキストを順番に返す
    偽アダプタ（llm/llm_agent.py のテスト用。AIを呼ばない）。

    llm/adapters.py の各アダプタと同じダックタイピングインターフェース
    （complete(system, messages, max_tokens, temperature, request_options)
    -> tuple[str, dict]）に準拠する。texts の要素がExceptionインスタンスなら
    そのまま raise する（AdapterError等の模擬）。texts を使い切った後は
    default_text を繰り返す。
    """

    DEFAULT_PASS_TEXT = (
        '{"strategy": {"reason": "pass", "emotion": "楽"}, "action": {"type": "pass"}}'
    )

    def __init__(
        self, texts: list[str | Exception] | None = None, default_text: str | None = None,
    ) -> None:
        self.texts: list[str | Exception] = list(texts or [])
        self.default_text = self.DEFAULT_PASS_TEXT if default_text is None else default_text
        self.calls: list[dict] = []
        self.seat_key: str | None = None

    def bind_seat(self, seat_key: str) -> None:
        self.seat_key = seat_key

    def complete(
        self, system, messages, max_tokens=1000, temperature=0.7, request_options=None,
    ) -> tuple[str, dict]:
        self.calls.append({
            "system": system, "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature,
        })
        item = self.texts.pop(0) if self.texts else self.default_text
        if isinstance(item, Exception):
            raise item
        usage = {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}
        return item, usage


def make_roster(
    votes_by_vote: dict[tuple[int, int], dict[str, Vote]], num_players: int = 12, loan: int = 1_200_000,
) -> dict[str, ScriptedAgent]:
    """
    (round_num, vote_num) -> {player_id: Vote} の表から、全プレイヤー分の
    ScriptedAgentを作る

    指定の無い (round_num, vote_num, player_id) はScriptedAgent既定のYESになる。
    """
    player_ids = [f"P{i:02d}" for i in range(1, num_players + 1)]
    per_player: dict[str, dict[tuple[int, int], Vote]] = {pid: {} for pid in player_ids}
    for key, votes in votes_by_vote.items():
        for pid, vote in votes.items():
            per_player[pid][key] = vote
    return {
        pid: ScriptedAgent(pid, votes=per_player[pid], loan=loan)
        for pid in player_ids
    }
