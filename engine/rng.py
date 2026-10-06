"""
乱数管理モジュール

全ゲームの乱数を単一seedから導出し、同一seedで完全再現可能にする。
用途別にサブRNGを生成して独立性を保つ。

gentei-janken `engine/rng.py`（B分類）から流用。`shuffle_action_order` を
`shuffle_turn_order` に改名し（§7.1 Negotiationの手番順の意味を明確化）、
自動代行（§4.4）の無作為選択用に `random_vote()` を追加した。
"""

import random

from engine.models import Vote


class GameRng:
    """
    ゲーム用乱数ジェネレータ

    単一のseedから全ゲーム内乱数を再現可能に生成する。
    用途ごとに独立したRNGインスタンスを返す。

    Args:
        seed: ゲームのマスターシード
    """

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._master = random.Random(seed)
        # 用途別にサブシードを生成して独立性を確保
        self._negotiation_rng = random.Random(self._master.randint(0, 2**63))
        self._autocommit_rng = random.Random(self._master.randint(0, 2**63))
        self._general_rng = random.Random(self._master.randint(0, 2**63))

    def shuffle_turn_order(self, player_ids: list[str]) -> list[str]:
        """
        Negotiationの手番順をランダムシャッフルする（§7 Negotiation: 毎巡ランダム手番）

        同一seedなら同じ順序が再現される。

        Args:
            player_ids: シャッフル対象のプレイヤーIDリスト

        Returns:
            シャッフルされたプレイヤーIDリスト（元リストは変更しない）
        """
        shuffled = list(player_ids)
        self._negotiation_rng.shuffle(shuffled)
        return shuffled

    def random_vote(self) -> Vote:
        """
        自動代行（§4.4）で、型Bの指定がない／矛盾する場合に使う無作為な投票先

        同一seedなら同じ結果が再現される。
        """
        return self._autocommit_rng.choice([Vote.YES, Vote.NO])
