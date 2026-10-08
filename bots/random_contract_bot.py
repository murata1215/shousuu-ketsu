"""
無作為契約Bot（§12.3 無作為の検査100試合用、v0.4）

v0.3では `tests/helpers.py::RandomContractAgent` が本番コード
（`sim/scenarios.py`・`scripts/dry_run.py`）から逆方向にimportされていた
（CLAUDE.md的には望ましくない構造）。サイクル4.0で型・金額・相手・対象
（round_num, vote_num）を無作為に選ぶ版をbots/へ移し、本番コードの
テストコードへの依存を解消した。テスト側（tests/test_invariants.py等）も
本モジュールを使う。

v0.3との違い: 型B・型C(minority_side/in_minority)はvote_numも無作為に選ぶ
（§9.3）。型C(wins_round)はvote_num=Noneで、target_player==obligorのときは
share_percentも候補に混ぜる（§6.4）。
"""

import random

from engine.config import GameConfig
from engine.models import Action, ConditionType, ContractProposeAction, ContractSignAction, ObligationType, PassAction, PlayerState, Vote
from engine.negotiation import PlayerAgent


class RandomContractBot(PlayerAgent):
    """
    無作為に投票し、無作為に契約を提案・署名するBot（§12.3 無作為の検査100試合用）

    型（A/B/C）・金額・相手・対象（round_num, vote_num）を無作為に選ぶ。
    上限（1000万）に届くような大きな金額も一定確率で混ぜる。
    """

    def __init__(
        self, player_id: str, seed: int, loan: int = 1_200_000,
        num_rounds: int = 4, max_votes_per_round: int = 6,
    ) -> None:
        self.player_id = player_id
        self.rng = random.Random(seed)
        self.loan = loan
        self.num_rounds = num_rounds
        self.max_votes_per_round = max_votes_per_round

    def choose_loan(self, config: GameConfig) -> int:
        return self.loan

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        pid = self.player_id
        all_ids = sorted(visible_state.get("initial_loans", {}).keys())
        others = [p for p in all_ids if p != pid]
        roll = self.rng.random()

        if roll < 0.10 and others:
            return self._propose(pid, all_ids, others, round_num, vote_num)

        pending = visible_state.get("contracts_pending", [])
        signable = [c for c in pending if pid not in c["signed_by"]]
        if roll < 0.25 and signable:
            target = self.rng.choice(signable)
            return ContractSignAction(player_id=pid, contract_id=target["contract_id"])

        return PassAction(player_id=pid)

    def _pick_target_round_vote(self, round_num: int, vote_num: int, *, vote_level: bool) -> tuple[int, int | None]:
        """
        対象の(round_num, vote_num)を無作為に選ぶ（§6.1: 現在以降だけが対象）

        vote_level=True: 型B・minority_side/in_minority用（vote_numが必要）。
        vote_level=False: 型A・wins_round用（vote_numはNone）。
        """
        target_round = self.rng.randint(round_num, self.num_rounds)
        if not vote_level:
            return target_round, None
        if target_round == round_num:
            target_vote = self.rng.randint(vote_num, self.max_votes_per_round)
        else:
            target_vote = self.rng.randint(1, self.max_votes_per_round)
        return target_round, target_vote

    def _propose(
        self, pid: str, all_ids: list[str], others: list[str], round_num: int, vote_num: int,
    ) -> Action:
        target = self.rng.choice(others)
        ob_type = self.rng.choice(list(ObligationType))
        # 上限（1000万）に届くような大きな金額も一定確率で混ぜる
        amount = self.rng.choice([100_000, 500_000, 1_000_000, 3_000_000, 9_000_000, 12_000_000])

        if self.rng.random() < 0.5:
            obligor, counterparty = pid, target
        else:
            obligor, counterparty = target, pid

        if ob_type == ObligationType.TYPE_A_PAYMENT:
            target_round, _ = self._pick_target_round_vote(round_num, vote_num, vote_level=False)
            details = {"amount": amount}
            term = {
                "obligor": obligor, "counterparty": counterparty,
                "ob_type": ob_type.value, "round_num": target_round, "details": details,
            }
        elif ob_type == ObligationType.TYPE_B_VOTE:
            target_round, target_vote = self._pick_target_round_vote(round_num, vote_num, vote_level=True)
            details = {"vote": self.rng.choice([Vote.YES.value, Vote.NO.value])}
            term = {
                "obligor": obligor, "counterparty": counterparty, "ob_type": ob_type.value,
                "round_num": target_round, "vote_num": target_vote, "details": details,
            }
        else:
            condition_type = self.rng.choice(list(ConditionType))
            if condition_type == ConditionType.WINS_ROUND:
                target_round, _ = self._pick_target_round_vote(round_num, vote_num, vote_level=False)
                target_vote = None
                target_player = self.rng.choice([obligor] + all_ids)
                condition = {"target_player": target_player}
                if target_player == obligor and self.rng.random() < 0.5:
                    details = {
                        "share_percent": self.rng.choice([10, 25, 50, 75, 100]),
                        "condition_type": condition_type.value, "condition": condition,
                    }
                else:
                    details = {
                        "amount": amount, "condition_type": condition_type.value, "condition": condition,
                    }
            else:
                target_round, target_vote = self._pick_target_round_vote(round_num, vote_num, vote_level=True)
                if condition_type == ConditionType.MINORITY_SIDE:
                    condition = {"side": self.rng.choice([Vote.YES.value, Vote.NO.value])}
                else:
                    condition = {"target_player": self.rng.choice(all_ids)}
                details = {
                    "amount": amount, "condition_type": condition_type.value, "condition": condition,
                }
            term = {
                "obligor": obligor, "counterparty": counterparty, "ob_type": ob_type.value,
                "round_num": target_round, "vote_num": target_vote, "details": details,
            }

        return ContractProposeAction(player_id=pid, with_players=[target], terms=[term])
