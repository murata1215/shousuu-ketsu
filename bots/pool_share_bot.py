"""
山分け（pool_share）を結ぶBot（§12.1のBot検証用v0.5対応、サイクル5.0新設）

`bots/group_bot.py::GroupSplitBot` の賞金山分け（wins_round + share_percentを
組員の全組み合わせぶん、N×(N-1)本・毎ラウンド結び直す）を、§6.4の山分け
（pool_share、1本で成立したラウンドから最終ラウンドまで効く）に置き換えた
もの。山分けは抜けられず、取り消しもできないため（§6.4）、R1V1の1回だけ
提案する。

本番の試合 t12_r4_4401b（§12.11）で、型Cの義務26本のうち条件が成立した
のは1本だけだったことを踏まえ（§11.10）、組で票を割り、誰が勝っても
山を分ける原作の必勝法を契約で書けることを確かめるためのBot。
"""

from bots.group_bot import GroupSplitBot
from engine.models import Action, ContractProposeAction, ContractSignAction, PassAction, PlayerState


class PoolShareGroupBot(GroupSplitBot):
    """
    決まった組で票を半々に割り、山分け（pool_share）を結ぶBot（必ず守る）

    投票の割り方（_rotated_sides）はGroupSplitBotをそのまま使う。契約の
    提案だけを差し替え、R1V1の1回だけ山分けを提案する（山分けは1つの契約に
    1本までで、成立すれば残りのラウンドすべてに効くため、GroupSplitBotの
    ような「毎ラウンド結び直す」必要がない、§6.4）。

    Args:
        player_id: このインスタンスのプレイヤーID
        members: 組の全員（自分を含む、2人以上）
        seed: 乱数シード（投票の割り方の回転に使う。CLAUDE.mdの流儀どおり
            seed*100+プレイヤー番号をsim/scenarios.py側で渡す）
        share_percent: 山分けの割合X（1〜100の整数、既定60）
    """

    def __init__(
        self, player_id: str, members: list[str], seed: int = 0, share_percent: int = 60,
    ) -> None:
        super().__init__(player_id, members, seed=seed, propose_profit_share=True)
        self.bot_type = "PoolShareGroup"
        self.share_percent = share_percent

    def _contract_terms(self, round_num: int) -> list[dict]:
        """山分けのterms（obligor/counterparty/round_numを持たない、§6.4/§9.3）"""
        return [{
            "ob_type": "type_c_conditional",
            "details": {"share_percent": self.share_percent, "condition_type": "pool_share"},
        }]

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        proposer_id = self.members[0]
        if self.player_id == proposer_id:
            if round_num == 1 and vote_num == 1 and 1 not in self._proposed_rounds:
                self._proposed_rounds.add(1)
                others = [m for m in self.members if m != self.player_id]
                return ContractProposeAction(
                    player_id=self.player_id, with_players=others,
                    terms=self._contract_terms(round_num),
                )
            return PassAction(player_id=self.player_id)

        # 署名役: R1V1に提案された山分けだけを待つ（1本だけなので、
        # 以降のラウンドで新たな提案を探す必要がない）
        for c in visible_state.get("contracts_pending", []):
            if (
                c["proposer"] == proposer_id and c["round_created"] == 1
                and self.player_id in c["parties"] and self.player_id not in c["signed_by"]
            ):
                return ContractSignAction(player_id=self.player_id, contract_id=c["contract_id"])
        return PassAction(player_id=self.player_id)
