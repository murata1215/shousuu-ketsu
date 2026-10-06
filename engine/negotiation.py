"""
抽象プレイヤーインターフェース・スタブモジュール（§7.1 Negotiation/Commit）

dangou-card `engine/negotiation.py`（B分類）の `PlayerAgent`/`StubAgent` から
流用。`commit()` の戻り値を市場+カードの `MarketCommitAction` から単純な
`Vote`（YES/NO）に差し替えた。
"""

from abc import ABC, abstractmethod

from engine.config import GameConfig
from engine.models import Action, PassAction, PlayerState, Vote


class PlayerAgent(ABC):
    """
    プレイヤーエージェントの抽象基底クラス

    各AIエージェント（LLM, Bot, スタブ）はこのクラスを継承し、
    借入選択・交渉・投票提出の3メソッドを実装する。
    """

    @abstractmethod
    def choose_loan(self, config: GameConfig) -> int:
        """
        ゲーム開始前の借入額を選択する（§3.1）

        Args:
            config: ゲーム設定（loan_min〜loan_maxの範囲内で選択）

        Returns:
            借入額（整数）
        """
        ...

    @abstractmethod
    def negotiate(
        self,
        player_state: PlayerState,
        round_num: int,
        turn: int,
        visible_state: dict,
    ) -> Action:
        """
        Negotiationフェイズで1アクションを選択する（§7.1 Negotiation）

        Args:
            player_state: 自分のプレイヤー状態
            round_num: ラウンド番号
            turn: 現在の巡数（1〜10）
            visible_state: 公開情報の辞書

        Returns:
            選択したAction
        """
        ...

    @abstractmethod
    def commit(
        self,
        player_state: PlayerState,
        round_num: int,
        visible_state: dict,
    ) -> Vote:
        """
        Commitフェイズで投票先を選択する（§4.1）

        Args:
            player_state: 自分のプレイヤー状態
            round_num: ラウンド番号
            visible_state: 公開情報の辞書

        Returns:
            選択した投票先（YES/NO）
        """
        ...

    def reflect(
        self,
        player_state: PlayerState,
        round_num: int,
        visible_state: dict,
    ) -> None:
        """
        ラウンド終了後の振り返り機会（既定は何もしない）

        Bot/StubAgentはオーバーライド不要。LLMAgent導入時（サイクル1.3）に
        使う想定のフック。

        Args:
            player_state: 自分のプレイヤー状態
            round_num: 終了したラウンドの番号
            visible_state: 公開情報の辞書（当ラウンドの結果を含む）
        """
        return None


class StubAgent(PlayerAgent):
    """
    固定行動スタブエージェント（ドライラン用）

    - 借入: 最低額（120万）
    - 交渉: 常にpass
    - 投票: 常にYES
    """

    def choose_loan(self, config: GameConfig) -> int:
        """最低借入額を選択"""
        return config.loan_min

    def negotiate(
        self,
        player_state: PlayerState,
        round_num: int,
        turn: int,
        visible_state: dict,
    ) -> Action:
        """常にpassを返す"""
        return PassAction(player_id=player_state.player_id)

    def commit(
        self,
        player_state: PlayerState,
        round_num: int,
        visible_state: dict,
    ) -> Vote:
        """常にYESを選択"""
        return Vote.YES
