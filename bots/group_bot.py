"""
組んで票を割るBot群（§12.1のBot検証用v0.4対応、サイクル4.1新規実装）

v0.3の「ペア割り」（bots/pair_bot.py、ラウンド丸ごと2人で型B）はv0.4の
ラウンド/投票の二重構造に合わなくなったため削除し、本モジュールで
作り直した（CLAUDE.md過去の落とし穴⑨「意味が変わる改修は作り直しで扱う」）。

v0.4では1ラウンドに複数の投票（V1〜V6）が入るため、「組で票を割る」決まりは
投票ごとに公開情報（remaining_ids・round_num・vote_num）だけから決まる
純粋関数 `_rotated_sides()` にした。組員は相談なしに同じ答えに至る
（計画のV2〜V7で前提にしている設計）。

- GroupSplitBot: 残っている組員をID昇順roundで回転させ、前半をYES・
  残り半分をNOに割る。賞金を山分けする契約（wins_round + share_percent、
  相手は義務者自身）を毎ラウンドV1で提案・署名する。
- TypeBPactGroupBot: GroupSplitBotに加えて、組員を輪番でつないだ型B義務
  （R*V1対象、相手方1人だけ）を結ぶ。breaks=Trueなら毎ラウンドのV1だけ
  指定と逆に投票する（V8の「破る」役）。
- MultiGroupHubBot: 複数の組（本人を除いた形で渡す）それぞれの指示票を
  _rotated_sides() で計算し、少ない側に投票する（V7の掛け持ち本人）。
  山分けの契約には一切署名しない（既定のpass）。
- SitInBot: 通常は無作為。残り4人が「組の2人＋外の2人」になったときだけ、
  外の2人がID昇順で割れて投票し、同数（打ち切り）を作る（V6の「外」側）。
  組の顔ぶれはコンストラクタで直接渡す。実際のゲームでは契約の当事者名は
  非公開だが、本Botは§11.4の「残り4人での居座り」を数字にする検査用であり、
  組の顔ぶれを知っている前提で作る（報告書に明記する）。
"""

from bots.base import BotAgent
from engine.models import (
    Action, ContractProposeAction, ContractSignAction, PassAction, PlayerState, Vote,
)


def _rotated_sides(
    members: list[str], remaining_ids: set[str], round_num: int, vote_num: int,
) -> dict[str, Vote]:
    """
    公開情報だけから決まる純粋関数（組員は相談なしに同じ答えに至る）

    残っている組員をID昇順に並べ、(round_num-1 + vote_num-1) だけ回転させ、
    前半ceil(n/2)人をYES、残りをNOにする。同じ人がいつも同じ側になって
    得をし続けるのを避けるための回転。
    """
    present = sorted(m for m in members if m in remaining_ids)
    n = len(present)
    if n == 0:
        return {}
    shift = (round_num - 1 + vote_num - 1) % n
    rotated = present[shift:] + present[:shift]
    half = -(-n // 2)  # ceil(n/2)
    return {pid: (Vote.YES if i < half else Vote.NO) for i, pid in enumerate(rotated)}


def _profit_share_terms(members: list[str], round_num: int) -> list[dict]:
    """
    賞金の山分け契約のterms（§6.4: wins_round + share_percentは対象者が
    義務者自身のときだけ使える）

    組員 m が勝ち残ったら、m は他の組員それぞれへ floor(100/人数)% を払う
    （全組み合わせ分）。端数は勝った本人に残る（§6.4/§11.2暫定5）。
    """
    n = len(members)
    pct = 100 // n
    terms = []
    for obligor in members:
        for counterparty in members:
            if obligor == counterparty:
                continue
            terms.append({
                "obligor": obligor, "counterparty": counterparty,
                "ob_type": "type_c_conditional", "round_num": round_num,
                "details": {
                    "condition_type": "wins_round",
                    "condition": {"target_player": obligor},
                    "share_percent": pct,
                },
            })
    return terms


def _type_b_ring_terms(members: list[str], round_num: int) -> list[dict]:
    """
    型Bで票を縛り合うterms（V8）。相手方は輪番で1人だけ
    （義務1本=違約金1回分、§6.3）。対象はそのラウンドのV1のみ。
    """
    sides = _rotated_sides(members, set(members), round_num, 1)
    n = len(members)
    terms = []
    for i, obligor in enumerate(members):
        counterparty = members[(i + 1) % n]
        terms.append({
            "obligor": obligor, "counterparty": counterparty, "ob_type": "type_b_vote",
            "round_num": round_num, "vote_num": 1,
            "details": {"vote": sides[obligor].value},
        })
    return terms


class GroupSplitBot(BotAgent):
    """
    決まった組で票を半々に割り、賞金を山分けする契約を結ぶBot（必ず守る）

    Args:
        player_id: このインスタンスのプレイヤーID
        members: 組の全員（自分を含む、2人以上）
        propose_profit_share: Falseなら契約の提案・署名を一切しない
            （投票の割り方だけ使うとき用。既定True）
    """

    def __init__(
        self, player_id: str, members: list[str], seed: int = 0,
        propose_profit_share: bool = True,
    ) -> None:
        super().__init__("GroupSplit", seed=seed)
        self.player_id = player_id
        self.members = sorted(members)
        self.propose_profit_share = propose_profit_share
        self._proposed_rounds: set[int] = set()

    def _group_vote(self, round_num: int, vote_num: int, remaining_ids: set[str]) -> Vote | None:
        sides = _rotated_sides(self.members, remaining_ids, round_num, vote_num)
        return sides.get(self.player_id)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        remaining_ids = set(visible_state.get("remaining_ids", []))
        side = self._group_vote(round_num, vote_num, remaining_ids)
        return side if side is not None else self.rng.choice([Vote.YES, Vote.NO])

    def _contract_terms(self, round_num: int) -> list[dict]:
        """このラウンドで提案する義務のterms（既定は山分けのみ。サブクラスで追加可能）"""
        return _profit_share_terms(self.members, round_num)

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        if not self.propose_profit_share:
            return PassAction(player_id=self.player_id)

        proposer_id = self.members[0]
        if self.player_id == proposer_id:
            if vote_num == 1 and round_num not in self._proposed_rounds:
                self._proposed_rounds.add(round_num)
                others = [m for m in self.members if m != self.player_id]
                return ContractProposeAction(
                    player_id=self.player_id, with_players=others,
                    terms=self._contract_terms(round_num),
                )
            return PassAction(player_id=self.player_id)

        # 署名役: 提案者からの当該ラウンドの提案がまだ未署名なら署名する
        for c in visible_state.get("contracts_pending", []):
            if (
                c["proposer"] == proposer_id and c["round_created"] == round_num
                and self.player_id in c["parties"] and self.player_id not in c["signed_by"]
            ):
                return ContractSignAction(player_id=self.player_id, contract_id=c["contract_id"])
        return PassAction(player_id=self.player_id)


class TypeBPactGroupBot(GroupSplitBot):
    """
    GroupSplitBotに加え、組員を輪番でつないだ型B義務（R*V1対象）を結ぶBot

    breaks=Trueの場合、毎ラウンドのV1だけ指定と逆の側に投票する
    （違約金を払って約束を破る。V2以降はGroupSplitBotと同じ規則）。
    """

    def __init__(
        self, player_id: str, members: list[str], seed: int = 0, breaks: bool = False,
    ) -> None:
        super().__init__(player_id, members, seed=seed, propose_profit_share=True)
        self.bot_type = "TypeBBreaker" if breaks else "TypeBPact"
        self.breaks = breaks

    def _contract_terms(self, round_num: int) -> list[dict]:
        return super()._contract_terms(round_num) + _type_b_ring_terms(self.members, round_num)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        remaining_ids = set(visible_state.get("remaining_ids", []))
        side = self._group_vote(round_num, vote_num, remaining_ids)
        if side is None:
            return self.rng.choice([Vote.YES, Vote.NO])
        if self.breaks and vote_num == 1:
            return Vote.NO if side == Vote.YES else Vote.YES
        return side


class MultiGroupHubBot(BotAgent):
    """
    掛け持ちBot（V7）: 複数の4人組それぞれに入り、各組の指示どおりに票を
    割らせる本人。各組の指示票は _rotated_sides() で（本人を除いた組員
    だけで）計算し、少ない側に自分の票を入れる（同数なら自分の乱数）。
    山分けの契約には一切署名しない（既定のpass）。

    Args:
        player_id: 本人のプレイヤーID
        groups: 各組の「本人を除いた組員」のリストのリスト
            （例: [["P02","P03","P04"], ["P05","P06","P07"], ["P08","P09","P10"]]）
    """

    def __init__(self, player_id: str, groups: list[list[str]], seed: int = 0) -> None:
        super().__init__("MultiGroupHub", seed=seed)
        self.player_id = player_id
        self.groups = [sorted(g) for g in groups]

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        remaining_ids = set(visible_state.get("remaining_ids", []))
        yes_count = 0
        no_count = 0
        for group in self.groups:
            sides = _rotated_sides(group, remaining_ids, round_num, vote_num)
            for vote in sides.values():
                if vote == Vote.YES:
                    yes_count += 1
                else:
                    no_count += 1
        if yes_count < no_count:
            return Vote.YES
        if no_count < yes_count:
            return Vote.NO
        return self.rng.choice([Vote.YES, Vote.NO])


class SitInBot(BotAgent):
    """
    居座りBot（V6）: 通常は無作為に投票する。残り4人が「組の2人＋外の2人」
    になったときだけ、外の2人がID昇順で小さい方がYES・大きい方がNOに入れ、
    必ず2対2を作って打ち切りに持ち込む。

    Args:
        player_id: このインスタンスのプレイヤーID（組の外）
        group_members: 組の全員のプレイヤーID
    """

    def __init__(self, player_id: str, group_members: list[str], seed: int = 0) -> None:
        super().__init__("SitIn", seed=seed)
        self.player_id = player_id
        self.group_members = set(group_members)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        remaining_ids = set(visible_state.get("remaining_ids", []))
        if len(remaining_ids) == 4:
            in_group = remaining_ids & self.group_members
            outsiders = sorted(remaining_ids - self.group_members)
            if len(in_group) == 2 and len(outsiders) == 2 and self.player_id in outsiders:
                return Vote.YES if self.player_id == outsiders[0] else Vote.NO
        return self.rng.choice([Vote.YES, Vote.NO])
