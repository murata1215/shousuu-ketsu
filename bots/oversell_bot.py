"""
割合の重ね売りBot（V10、§12.1のBot検証用、サイクル4.1新規実装）

OversellBot（本人）は、R1V1の1〜3巡目に、別々の相手（signers）へ1本ずつ
「そのラウンドで勝ち残ったら賞金の50%を払う」という契約を提案する
（対象ラウンドはR1〜R4の4義務、1契約あたり）。3本を別契約にするのは
contract_seqを分け、§7.3手順6の成立順の優先と3本目の取りはぐれを見るため。
SignerBotは提案者からの未署名契約に署名するだけで、投票は無作為。
"""

from bots.base import BotAgent
from engine.models import Action, ContractProposeAction, ContractSignAction, PassAction, PlayerState, Vote


class OversellBot(BotAgent):
    """
    R1V1の1〜3巡目に、別々の相手へ「勝ち残ったら賞金のshare_percent%を
    払う」契約を1本ずつ提案する。投票は無作為。

    Args:
        player_id: 本人のプレイヤーID
        signers: 契約を結ぶ相手（3人、提案順。1人目→turn1, 2人目→turn2, ...）
        share_percent: 各契約の約束の割合（既定50。3本で合計150%になる）
        num_rounds: 対象にするラウンド数（§10既定4。義務はR1〜num_rounds全部）
    """

    def __init__(
        self, player_id: str, signers: list[str], seed: int = 0,
        share_percent: int = 50, num_rounds: int = 4,
    ) -> None:
        super().__init__("Oversell", seed=seed)
        self.player_id = player_id
        self.signers = list(signers)
        self.share_percent = share_percent
        self.num_rounds = num_rounds
        self._proposed_turns: set[int] = set()

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        if round_num == 1 and vote_num == 1 and 1 <= turn <= len(self.signers) and turn not in self._proposed_turns:
            self._proposed_turns.add(turn)
            signer = self.signers[turn - 1]
            terms = [
                {
                    "obligor": self.player_id, "counterparty": signer,
                    "ob_type": "type_c_conditional", "round_num": r,
                    "details": {
                        "condition_type": "wins_round",
                        "condition": {"target_player": self.player_id},
                        "share_percent": self.share_percent,
                    },
                }
                for r in range(1, self.num_rounds + 1)
            ]
            return ContractProposeAction(player_id=self.player_id, with_players=[signer], terms=terms)
        return PassAction(player_id=self.player_id)


class SignerBot(BotAgent):
    """
    提案者からの未署名契約に署名するだけのBot（V10用）。投票は無作為。
    """

    def __init__(self, player_id: str, proposer_id: str, seed: int = 0) -> None:
        super().__init__("Signer", seed=seed)
        self.player_id = player_id
        self.proposer_id = proposer_id

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self.rng.choice([Vote.YES, Vote.NO])

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        for c in visible_state.get("contracts_pending", []):
            if c["proposer"] == self.proposer_id and self.player_id not in c["signed_by"]:
                return ContractSignAction(player_id=self.player_id, contract_id=c["contract_id"])
        return PassAction(player_id=self.player_id)
