"""
ペア割りBot・裏切りBot（§12.1のBot検証用、新規実装）

決まった相手と毎ラウンド型Bの契約を結び、片方がYES・片方がNOに入れる
（どちらがYESかはラウンドごとに交代、計画§6 判断2）。
相手はコンストラクタで固定（BOT_REGISTRYには登録せず、シナリオ側
（sim/scenarios.py）がペアごとにインスタンス化する）。

段取り（計画§6 判断2）:
- プレイヤーIDの小さい方が提案者、大きい方が署名者（役割は固定）。
- 提案者はそのラウンドの最初の自分の巡で、当該ラウンド対象の型B義務
  2本（自分→相手・相手→自分）を1契約にまとめて提案する。
- 署名者は、相手からの当該ラウンドの提案が見えた次の巡で署名する。
- 奇数ラウンドはID小さい方がYES・大きい方がNO、偶数ラウンドは入れ替え。
- 契約が（巡が尽きて）不成立でも、PairSplitBotは約束どおりに投票する
  （「必ず守る」）。BetrayerPairBotは契約の提案・署名は必ず行うが、
  Commitで自分のRNGにより確率20%（ラウンドごとに1回判定、計画§6 判断3）
  で約束と逆に投票する。
"""

from bots.base import BotAgent
from engine.models import ContractProposeAction, ContractSignAction, PassAction, PlayerState, Vote


class PairSplitBot(BotAgent):
    """決まった相手と毎ラウンド型Bの契約を結び、票を割るBot（必ず守る）"""

    def __init__(self, player_id: str, partner_id: str, seed: int = 0) -> None:
        super().__init__("PairSplit", seed=seed)
        self.player_id = player_id
        self.partner_id = partner_id
        self._proposed_round: int | None = None
        self._signed_round: int | None = None

    def _roles(self) -> tuple[str, str]:
        """(小さいID, 大きいID) を返す"""
        return tuple(sorted([self.player_id, self.partner_id]))  # type: ignore[return-value]

    def _sides(self, round_num: int) -> dict[str, Vote]:
        """このラウンドで各自が入れるべき側（§6判断2: 奇数Rは小さい方がYES）"""
        small, large = self._roles()
        if round_num % 2 == 1:
            return {small: Vote.YES, large: Vote.NO}
        return {small: Vote.NO, large: Vote.YES}

    def negotiate(
        self, player_state: PlayerState, round_num: int, turn: int, visible_state: dict,
    ) -> "ContractProposeAction | ContractSignAction | PassAction":
        small, large = self._roles()

        if self.player_id == small:
            # 提案者役: このラウンドでまだ提案していなければ提案する
            if self._proposed_round != round_num:
                self._proposed_round = round_num
                sides = self._sides(round_num)
                terms = [
                    {
                        "obligor": small, "counterparty": large, "ob_type": "type_b_vote",
                        "round_num": round_num, "details": {"vote": sides[small].value},
                    },
                    {
                        "obligor": large, "counterparty": small, "ob_type": "type_b_vote",
                        "round_num": round_num, "details": {"vote": sides[large].value},
                    },
                ]
                return ContractProposeAction(
                    player_id=self.player_id, with_players=[self.partner_id], terms=terms,
                )
            return PassAction(player_id=self.player_id)

        # 署名者役: 相手からの当該ラウンドの提案が見えたら署名する
        if self._signed_round != round_num:
            for c in visible_state.get("contracts_pending", []):
                if (
                    c["proposer"] == self.partner_id
                    and c["round_created"] == round_num
                    and self.player_id not in c["signed_by"]
                ):
                    self._signed_round = round_num
                    return ContractSignAction(player_id=self.player_id, contract_id=c["contract_id"])
        return PassAction(player_id=self.player_id)

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        """約束どおりに投票する（必ず守る）"""
        return self._sides(round_num)[self.player_id]


class BetrayerPairBot(PairSplitBot):
    """
    PairSplitBotと同じ契約を結ぶが、Commitでラウンドごとに確率20%で約束を破るBot
    """

    def __init__(
        self, player_id: str, partner_id: str, seed: int = 0, betray_prob: float = 0.2,
    ) -> None:
        super().__init__(player_id, partner_id, seed=seed)
        self.bot_type = "BetrayerPair"
        self.betray_prob = betray_prob

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        obligated = self._sides(round_num)[self.player_id]
        if self.rng.random() < self.betray_prob:
            return Vote.NO if obligated == Vote.YES else Vote.YES
        return obligated
