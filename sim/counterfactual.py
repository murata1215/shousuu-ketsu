"""
型Bの「破っていたら得だったか」計算モジュール（サイクル1.2、新規実装）

試合の回し直しは行わない。実際に記録されたそのラウンドの票
（RoundSummary.votes）のうち、他のプレイヤー全員の票は固定したまま、
義務者本人の票だけを「守った場合」「破った場合」で切り替え、
engine.minority.resolve_minority()（ルールエンジンそのもの、無改変で
再利用）に通して受取額の差を取る。

判断した点（計画§6判断4）:
- 比較対象は当該ラウンドの受取（参加費の戻り＋配当。多数派・少数派なしは0）
  だけで、将来の持ち越しへの影響は含めない。
- 1ラウンドに複数本の型B義務があっても、1本ずつ独立に反転させて計算する
  （他の義務者の票は「実際の記録どおり」のまま。複数本を同時に反転させる
  計算はしない）。
- gain = 破った場合の受取 − 守った場合の受取。gain > 違約金 を「得」とし、
  同額は「得でない」とする。
"""

from engine.config import GameConfig
from engine.minority import resolve_minority
from engine.models import Obligation, RoundSummary, Vote


def _receive_if(
    votes: dict[str, Vote],
    config: GameConfig,
    carryover_before: int,
    round_num: int,
    is_final_round: bool,
    player_id: str,
) -> int:
    """指定した票パターンで少数決を判定した場合の、player_idの当該ラウンド受取額"""
    outcome = resolve_minority(votes, config, carryover_before, round_num, is_final_round=is_final_round)
    if player_id in outcome.minority_ids:
        return config.entry_fee + outcome.payout_per_minority
    return 0


def type_b_obligation_gain(
    obligation: Obligation,
    round_summary: RoundSummary,
    config: GameConfig,
    *,
    is_final_round: bool,
) -> int:
    """
    型Bの義務1本について、違約金を引く前の「破った方が得だった額」を計算する

    Args:
        obligation: 対象の型B義務（ob_type=type_b_vote、round_num=当該ラウンド）
        round_summary: 当該ラウンドのRoundSummary（実際に記録された票を含む）
        config: ゲーム設定
        is_final_round: 当該ラウンドが最終ラウンド（R12）かどうか

    Returns:
        receive_if_break - receive_if_obey（正なら破った方が得。0以下なら得でない）
    """
    required = Vote(obligation.details["vote"])
    broken = Vote.NO if required == Vote.YES else Vote.YES

    votes_obey = dict(round_summary.votes)
    votes_obey[obligation.obligor] = required
    votes_break = dict(round_summary.votes)
    votes_break[obligation.obligor] = broken

    carryover_before = round_summary.minority_outcome.carryover_before
    receive_obey = _receive_if(
        votes_obey, config, carryover_before, obligation.round_num, is_final_round, obligation.obligor,
    )
    receive_break = _receive_if(
        votes_break, config, carryover_before, obligation.round_num, is_final_round, obligation.obligor,
    )
    return receive_break - receive_obey
