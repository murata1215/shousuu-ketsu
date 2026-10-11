"""
Financeフェイズモジュール（§7 ラウンド進行: Finance / §3.3 利息）

完全新規実装（C分類）。毎ラウンド、2種の借金残高にそれぞれの利率で
複利を計上する。そのラウンドに新規発生した借金（参加費の立替など）にも
同じラウンドのFinanceから利息がつく（§3.3）。dangou-card の生還判定・
自動返済・強制返済（S2拡張）は本作に存在しないため移植しない。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.models import PlayerState
from engine import player as player_ops


def execute_finance(
    players: dict[str, PlayerState],
    round_num: int,
    config: GameConfig,
    logger: EventLogger,
) -> tuple[dict[str, PlayerState], int, dict[str, tuple[int, int]]]:
    """
    Financeフェイズを実行する（§7 手順5: 利息の計上）

    Args:
        players: 全プレイヤーの状態辞書
        round_num: ラウンド番号（1-12）
        config: ゲーム設定
        logger: イベントロガー

    Returns:
        (更新されたplayers, このラウンドの利息合計,
         player_id -> (interest_pre, interest_post) の辞書)

        3つめの戻り値はサイクル4.2c新設（§7.5「お金の出入りの記録」用。
        INTERESTイベントが既に持つ値を、呼び出し側がイベントログを読み返さずに
        受け取れるようにする。CLAUDE.md落とし穴⑦・⑩: ログに残る値を呼び出し側が
        別経路で再構築する設計は避け、戻り値から直接渡す）。
    """
    updated = dict(players)
    interest_total = 0
    interest_by_player: dict[str, tuple[int, int]] = {}
    for pid in sorted(updated):
        p = updated[pid]
        new_p, interest_pre, interest_post = player_ops.apply_interest(p, config)
        updated[pid] = new_p
        interest_total += interest_pre + interest_post
        interest_by_player[pid] = (interest_pre, interest_post)
        logger.log("INTEREST", round_num, "finance", visibility="self", data={
            "player_id": pid,
            "interest_pre": interest_pre,
            "interest_post": interest_post,
            "new_debt_pre": new_p.debt_pre,
            "new_debt_post": new_p.debt_post,
        })
    return updated, interest_total, interest_by_player
