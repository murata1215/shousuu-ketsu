"""
抽象プレイヤーインターフェース・スタブモジュール（§7.2 Negotiation/Commit、v0.4）

dangou-card `engine/negotiation.py`（B分類）の `PlayerAgent`/`StubAgent` から
流用。`commit()` の戻り値を市場+カードの `MarketCommitAction` から単純な
`Vote`（YES/NO）に差し替えた。サイクル4.0で `negotiate()`/`commit()` に
`vote_num`（投票番号、§1.1）を追加した。
"""

from abc import ABC, abstractmethod
from typing import Any

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
        vote_num: int,
        turn: int,
        visible_state: dict,
    ) -> Action:
        """
        Negotiationフェイズで1アクションを選択する（§7.2 Negotiation）

        Args:
            player_state: 自分のプレイヤー状態
            round_num: ラウンド番号
            vote_num: 投票番号（v0.4新設、§1.1）
            turn: 現在の巡数
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
        vote_num: int,
        visible_state: dict,
    ) -> Vote:
        """
        Commitフェイズで投票先を選択する（§4.2）

        Args:
            player_state: 自分のプレイヤー状態
            round_num: ラウンド番号
            vote_num: 投票番号（v0.4新設、§1.1）
            visible_state: 公開情報の辞書

        Returns:
            選択した投票先（YES/NO）
        """
        ...

    def reflect(
        self,
        player_state: PlayerState,
        round_num: int,
        vote_num: int,
        visible_state: dict,
        *,
        is_round_end: bool,
    ) -> None:
        """
        投票終了後の振り返り機会（既定は何もしない、§9.4: v0.4.3で
        「ラウンドの終わりと試合後」から「投票が終わるたびと試合後」に変わった）

        Bot/StubAgentはオーバーライド不要。LLMAgent導入時に使う想定のフック。

        Args:
            player_state: 自分のプレイヤー状態
            round_num: ラウンド番号
            vote_num: 終了した投票の番号（v0.4.3新設）
            visible_state: 公開情報の辞書（当該投票までの結果を含む）
            is_round_end: ラウンドの最後の投票（ラウンドの精算＋利息の計上の後）かどうか。
                engine側（_run_round のround_overの判定）だけが決め、文面層で
                再判定しない（negotiation_max_turnsと同じ役割分担、CLAUDE.md落とし穴④）
        """
        return None

    def post_game_reflect(self, post_game_context: dict[str, Any]) -> dict[str, Any] | None:
        """
        試合完全終了後の振り返り機会（既定は何もしない、§9.4）

        Bot/StubAgentはオーバーライド不要。LLMAgentが既に実装を持つ
        （llm/llm_agent.py::LLMAgent.post_game_reflect()）。サイクル2.0で
        Game.run()からの配線を追加するにあたり、抽象基底にも既定no-opとして
        追加した（Botへの機能追加なしでGame側から全エージェントに安全に
        呼べるようにするため）。

        Args:
            post_game_context: 最終順位・最終資産等（engine/game.py側で構築）

        Returns:
            既定はNone（コメントなし）
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
        vote_num: int,
        turn: int,
        visible_state: dict,
    ) -> Action:
        """常にpassを返す"""
        return PassAction(player_id=player_state.player_id)

    def commit(
        self,
        player_state: PlayerState,
        round_num: int,
        vote_num: int,
        visible_state: dict,
    ) -> Vote:
        """常にYESを選択"""
        return Vote.YES
