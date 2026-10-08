"""
ラウンド（山の額と支払い）モジュール（§4.1/§4.4/§4.5、v0.4新設）

完全新規実装（C分類。v0.3には対応物がない。v0.3は投票=ラウンドで配当も
投票単位だったが、v0.4は複数の投票をまとめた1回の勝負としてラウンドを扱い、
山の支払いはラウンドの終わりにまとめて行う、§7.4）。

本モジュールが扱うのは「山の額の計算」「山の支払いの割り切れ処理」
「打ち切り時の持ち越し・没収」だけである。実際の現金の授受
（player_ops.receive/pay_or_borrow）と判定（engine/vote.py）は呼び出し側
（engine/game.py）が行う。
"""

from engine.config import GameConfig


def initial_pot(config: GameConfig, carryover_in: int) -> int:
    """
    ラウンド開始時点の山の額を計算する（§4.1）

    山 = 参加費12人分 ＋ 前のラウンドからの持ち越し（あれば）。
    参加費は上限の例外（§3.4）で必ず全額徴収されるため、山への繰入れは
    常に config.num_players * config.entry_fee の満額になる。

    Args:
        config: ゲーム設定
        carryover_in: このラウンド開始時点の持ち越し額

    Returns:
        山の額
    """
    return config.num_players * config.entry_fee + carryover_in


def compute_payout(pot_final: int, winner_ids: list[str]) -> tuple[int, int]:
    """
    山の支払い額を計算する（§4.5）

    勝ち残りが1人なら全額、2人なら半分ずつ。割り切れなければ1円未満を
    切り捨て、余りは没収する。

    Args:
        pot_final: このラウンドの山の最終額
        winner_ids: 勝ち残りのプレイヤーID（1人か2人。打ち切りなら空）

    Returns:
        (1人あたりの配当, 没収される端数)
    """
    if not winner_ids:
        return 0, 0
    payout_per_winner = pot_final // len(winner_ids)
    forfeited_remainder = pot_final % len(winner_ids)
    return payout_per_winner, forfeited_remainder


def resolve_pot_carryover(
    pot_final: int, *, aborted: bool, is_final_round: bool,
) -> tuple[int, int]:
    """
    打ち切り時の山の持ち越し・没収を計算する（§4.4）

    決着（打ち切りでない）なら持ち越しも没収もない（全額が勝ち残りへ）。
    打ち切りなら、次ラウンドへ持ち越す。ただし最終ラウンド（R4）での
    打ち切りは没収（消滅）する。

    Args:
        pot_final: このラウンドの山の最終額
        aborted: このラウンドが打ち切りで終わったか
        is_final_round: 最終ラウンド（R4）かどうか

    Returns:
        (次ラウンドへの持ち越し額, 没収された額)
    """
    if not aborted:
        return 0, 0
    if is_final_round:
        return 0, pot_final
    return pot_final, 0
