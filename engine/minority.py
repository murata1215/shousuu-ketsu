"""
少数決の判定・配当・持ち越しモジュール（§4）

完全新規実装（C分類。dangou-card/gentei-jankenのどちらにも対応物がない、
doc/analysis/reuse_investigation.md §3「どちらも使えない」）。

金額はすべて整数の円。配当は切り捨て除算（`//`）を使い、割り切れない
端数は没収する（§4.3）。12人・参加費10万の設定では持ち越しがあっても
必ず割り切れるため、実運用で forfeited_remainder が0でなくなることはない
（仕様書§4.3/§11.4の回答#2）。
"""

from engine.config import GameConfig
from engine.models import MinorityOutcome, PlayerState, Vote
from engine import player as player_ops


def resolve_minority(
    votes: dict[str, Vote],
    config: GameConfig,
    carryover_before: int,
    round_num: int,
    *,
    is_final_round: bool,
) -> MinorityOutcome:
    """
    1ラウンドの投票結果から少数決を判定する（§4.2/§4.3）

    - 人数の少ない側が少数派。多数派の参加費合計（＋持ち越し）を
      少数派で均等に分ける。
    - 6対6、または全員が同じ側（12対0）なら「少数派なし」とし、
      全員の参加費を没収して次ラウンドへ持ち越す。
    - R12で少数派なしの場合、持ち越しは消滅する（§4.3）。

    Args:
        votes: player_id -> Vote（全員分、§4.1: 棄権はない）
        config: ゲーム設定
        carryover_before: このラウンド開始時点の持ち越し額
        round_num: ラウンド番号
        is_final_round: 最終ラウンド（R12）かどうか

    Returns:
        MinorityOutcome
    """
    yes_ids = sorted(pid for pid, v in votes.items() if v == Vote.YES)
    no_ids = sorted(pid for pid, v in votes.items() if v == Vote.NO)
    n_yes, n_no = len(yes_ids), len(no_ids)

    if n_yes == n_no or n_yes == 0 or n_no == 0:
        # 少数派なし（6対6、または12対0）
        all_ids = sorted(votes.keys())
        collected = len(all_ids) * config.entry_fee + carryover_before
        if is_final_round:
            destroyed_carryover = collected
            carryover_after = 0
        else:
            destroyed_carryover = 0
            carryover_after = collected
        return MinorityOutcome(
            round_num=round_num,
            yes_ids=yes_ids, no_ids=no_ids,
            minority_side=None, minority_ids=[], majority_ids=[],
            carryover_before=carryover_before,
            pool=collected,
            payout_per_minority=0,
            forfeited_remainder=0,
            carryover_after=carryover_after,
            destroyed_carryover=destroyed_carryover,
        )

    if n_yes < n_no:
        minority_side, minority_ids, majority_ids = Vote.YES, yes_ids, no_ids
    else:
        minority_side, minority_ids, majority_ids = Vote.NO, no_ids, yes_ids

    pool = len(majority_ids) * config.entry_fee + carryover_before
    payout_per_minority = pool // len(minority_ids)
    forfeited_remainder = pool % len(minority_ids)

    return MinorityOutcome(
        round_num=round_num,
        yes_ids=yes_ids, no_ids=no_ids,
        minority_side=minority_side, minority_ids=minority_ids, majority_ids=majority_ids,
        carryover_before=carryover_before,
        pool=pool,
        payout_per_minority=payout_per_minority,
        forfeited_remainder=forfeited_remainder,
        carryover_after=0,
        destroyed_carryover=0,
    )


def apply_payouts(
    players: dict[str, PlayerState], outcome: MinorityOutcome, config: GameConfig,
) -> dict[str, PlayerState]:
    """
    配当を反映する（§4.2: 少数派は参加費が戻り、さらに配当を受け取る）

    少数派なし（minority_ids が空）の場合は何も起きない（全員の参加費は
    既にCommitで徴収済みで、そのまま没収＝持ち越しに積まれる）。
    多数派は参加費を失ったまま（Commit時点で徴収済み）で、ここでは何もしない。

    Args:
        players: 全プレイヤーの状態辞書（更新前）
        outcome: resolve_minority() の結果
        config: ゲーム設定

    Returns:
        更新後のplayers辞書（新しい辞書。入力は変更しない）
    """
    updated = dict(players)
    payout_total = config.entry_fee + outcome.payout_per_minority
    for pid in outcome.minority_ids:
        updated[pid] = player_ops.receive(updated[pid], payout_total)
    return updated
