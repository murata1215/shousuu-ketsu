"""
テスト用の固定エージェント

本サイクルはAI呼び出しを行わないため、ラウンドごとの投票・交渉行動を
事前に指定できる ScriptedAgent と、常に無効な応答を返す
FailingCommitAgent（自動代行のテスト用）を提供する。
サイクル1.1で、契約の提案・署名を無作為に行う RandomContractAgent
（§12.3 無作為の検査100試合用）を追加した。
"""

import random

from engine.config import GameConfig
from engine.models import (
    Action, ConditionType, ContractProposeAction, ContractSignAction, ObligationType,
    PassAction, PlayerState, Vote,
)
from engine.negotiation import PlayerAgent


class ScriptedAgent(PlayerAgent):
    """
    ラウンドごとの投票・交渉行動をあらかじめ指定できるエージェント

    Args:
        player_id: プレイヤーID
        votes: round_num -> Vote。指定の無いラウンドは既定でYES
        loan: choose_loan() が返す借入額
        negotiate_actions: (round_num, turn) -> Action。指定の無い巡はpass
        sign_proposer_at: (round_num, turn) -> 署名したい契約の提案者
            player_id。該当契約がまだ存在しない/署名済みなら何もしない
            （negotiate_actionsより優先。提案順・成立順を厳密に制御する
            テスト用、§12.3 #20）。
    """

    def __init__(
        self,
        player_id: str,
        votes: dict[int, Vote] | None = None,
        loan: int = 1_200_000,
        negotiate_actions: dict[tuple[int, int], Action] | None = None,
        sign_proposer_at: dict[tuple[int, int], str] | None = None,
    ) -> None:
        self.player_id = player_id
        self.votes = votes or {}
        self.loan = loan
        self.negotiate_actions = negotiate_actions or {}
        self.sign_proposer_at = sign_proposer_at or {}

    def choose_loan(self, config: GameConfig) -> int:
        return self.loan

    def negotiate(
        self, player_state: PlayerState, round_num: int, turn: int, visible_state: dict,
    ) -> Action:
        target_proposer = self.sign_proposer_at.get((round_num, turn))
        if target_proposer is not None:
            for c in visible_state.get("contracts_pending", []):
                if c["proposer"] == target_proposer and self.player_id not in c["signed_by"]:
                    return ContractSignAction(player_id=self.player_id, contract_id=c["contract_id"])
        return self.negotiate_actions.get((round_num, turn), PassAction(player_id=self.player_id))

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        return self.votes.get(round_num, Vote.YES)


class FailingCommitAgent(ScriptedAgent):
    """
    commit() が常に無効な応答（例外）を返すエージェント（§4.4 自動代行のテスト用）

    negotiate/choose_loan はScriptedAgentと同じ。
    """

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        raise RuntimeError("invalid output (simulated)")


class RandomContractAgent(PlayerAgent):
    """
    無作為に投票し、無作為に契約を提案・署名するエージェント
    （§12.3 無作為の検査100試合用）

    型（A/B/C）・金額・相手・対象ラウンドを無作為に選ぶ。上限（1000万）に
    届くような大きな金額も一定確率で混ぜる。
    """

    def __init__(self, player_id: str, seed: int, loan: int = 1_200_000, num_rounds: int = 12) -> None:
        self.player_id = player_id
        self.rng = random.Random(seed)
        self.loan = loan
        self.num_rounds = num_rounds

    def choose_loan(self, config: GameConfig) -> int:
        return self.loan

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])

    def negotiate(
        self, player_state: PlayerState, round_num: int, turn: int, visible_state: dict,
    ) -> Action:
        pid = self.player_id
        all_ids = sorted(visible_state.get("initial_loans", {}).keys())
        others = [p for p in all_ids if p != pid]
        roll = self.rng.random()

        if roll < 0.10 and others:
            return self._propose(pid, all_ids, others, round_num)

        pending = visible_state.get("contracts_pending", [])
        signable = [c for c in pending if pid not in c["signed_by"]]
        if roll < 0.25 and signable:
            target = self.rng.choice(signable)
            return ContractSignAction(player_id=pid, contract_id=target["contract_id"])

        return PassAction(player_id=pid)

    def _propose(
        self, pid: str, all_ids: list[str], others: list[str], round_num: int,
    ) -> Action:
        target = self.rng.choice(others)
        target_round = self.rng.randint(round_num, self.num_rounds)
        ob_type = self.rng.choice(list(ObligationType))
        # 上限（1000万）に届くような大きな金額も一定確率で混ぜる
        amount = self.rng.choice([100_000, 500_000, 1_000_000, 3_000_000, 9_000_000, 12_000_000])

        if ob_type == ObligationType.TYPE_A_PAYMENT:
            details = {"amount": amount}
        elif ob_type == ObligationType.TYPE_B_VOTE:
            details = {"vote": self.rng.choice([Vote.YES.value, Vote.NO.value])}
        else:
            condition_type = self.rng.choice(list(ConditionType))
            if condition_type == ConditionType.MINORITY_SIDE:
                condition = {"side": self.rng.choice([Vote.YES.value, Vote.NO.value])}
            else:
                condition = {"target_player": self.rng.choice(all_ids)}
            details = {
                "amount": amount, "condition_type": condition_type.value, "condition": condition,
            }

        if self.rng.random() < 0.5:
            obligor, counterparty = pid, target
        else:
            obligor, counterparty = target, pid

        terms = [{
            "obligor": obligor, "counterparty": counterparty,
            "ob_type": ob_type.value, "round_num": target_round, "details": details,
        }]
        return ContractProposeAction(player_id=pid, with_players=[target], terms=terms)


def make_roster(
    votes_by_round: dict[int, dict[str, Vote]], num_players: int = 12, loan: int = 1_200_000,
) -> dict[str, ScriptedAgent]:
    """
    round_num -> {player_id: Vote} の表から、全プレイヤー分のScriptedAgentを作る

    指定の無い (round_num, player_id) はScriptedAgent既定のYESになる。
    """
    player_ids = [f"P{i:02d}" for i in range(1, num_players + 1)]
    per_player: dict[str, dict[int, Vote]] = {pid: {} for pid in player_ids}
    for round_num, votes in votes_by_round.items():
        for pid, vote in votes.items():
            per_player[pid][round_num] = vote
    return {
        pid: ScriptedAgent(pid, votes=per_player[pid], loan=loan)
        for pid in player_ids
    }
