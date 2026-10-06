"""
記録用エージェントラッパー（新規実装）

PlayerAgentをそのまま包み、choose_loan/negotiate/commitは中身にそのまま
委譲する。reflect()（ラウンド終了後の振り返りフック、§9.4）だけを使って
「そのラウンド終了時点の自分のPlayerState」をround_num別に記録する。
エージェントの行動には一切介入しないため、ゲームの結果（投票・契約・
配当・借金）はラッパーの有無で変わらない（ルールエンジンを変えない）。

借金の上限到達（「借金の上限に達した人数」）や王様作り（「R12開始時点
＝R11終了時点の残り借入枠」）の判定に、ラウンドごとの自分の現金・
借金残高が必要なため、Game本体の外からこの記録を使う。
"""

from engine.config import GameConfig
from engine.models import Action, PlayerState, Vote
from engine.negotiation import PlayerAgent


class RecordingAgent(PlayerAgent):
    """
    内側のPlayerAgentに全メソッドを委譲し、reflect()だけで自分の状態を記録する

    Args:
        inner: 実際に判断を行うエージェント（Bot/StubAgent等）
        label: 集計用のBot種別ラベル（例: "Random", "PairSplit"）
    """

    def __init__(self, inner: PlayerAgent, label: str) -> None:
        self.inner = inner
        self.label = label
        self.history: dict[int, PlayerState] = {}
        """round_num -> そのラウンドのFinance終了時点のPlayerState"""

    def choose_loan(self, config: GameConfig) -> int:
        return self.inner.choose_loan(config)

    def negotiate(
        self, player_state: PlayerState, round_num: int, turn: int, visible_state: dict,
    ) -> Action:
        return self.inner.negotiate(player_state, round_num, turn, visible_state)

    def commit(self, player_state: PlayerState, round_num: int, visible_state: dict) -> Vote:
        return self.inner.commit(player_state, round_num, visible_state)

    def reflect(self, player_state: PlayerState, round_num: int, visible_state: dict) -> None:
        self.history[round_num] = player_state
        self.inner.reflect(player_state, round_num, visible_state)
