"""
少数決の投票判定モジュール（§4.2〜§4.4、v0.4）

v0.3の `engine/minority.py` を改名・再設計した（サイクル4.0: v0.3では
「ラウンド」=投票1回で多数派が参加費を失い少数派が山分けしていたが、v0.4では
多数派が退場するだけで、お金は動かない。配当（山の支払い）はラウンド単位に
移った、§7.4/engine/round.py）。

本モジュールが判定するのは「このラウンドのこの1回の投票で誰が退場するか」
「やり直しになるか」「やり直しが連続max_consecutive_ties回に達して打ち切りに
なるか」だけである（§4.3/§4.4）。お金の計算（延長料の徴収・山の支払い）は
呼び出し側（engine/settlement.py・engine/round.py）が行う。
"""

from engine.config import GameConfig
from engine.models import VoteOutcome, Vote


def resolve_vote(
    votes: dict[str, Vote],
    config: GameConfig,
    round_num: int,
    vote_num: int,
    consecutive_ties_before: int,
) -> VoteOutcome:
    """
    1回の投票の結果を判定する（§4.3/§4.4）

    - 両側に1人以上いて人数が違えば決着。人数の多い側（多数派）が退場する。
    - 同数、または全員が同じ側（6対6・12対0等）ならやり直し。残っている全員
      から延長料を徴収する対象になる（徴収自体は呼び出し側）。
    - やり直しの連続回数が config.max_consecutive_ties に達したら打ち切り。

    Args:
        votes: player_id -> Vote（残っている人全員分、§4.2: 棄権はない）
        config: ゲーム設定
        round_num: ラウンド番号
        vote_num: 投票番号（1〜6）
        consecutive_ties_before: この投票の前までのやり直し連続回数

    Returns:
        VoteOutcome
    """
    yes_ids = sorted(pid for pid, v in votes.items() if v == Vote.YES)
    no_ids = sorted(pid for pid, v in votes.items() if v == Vote.NO)
    n_yes, n_no = len(yes_ids), len(no_ids)
    all_ids = sorted(votes.keys())

    if n_yes == n_no or n_yes == 0 or n_no == 0:
        # やり直し（同数、または全員が同じ側）
        consecutive_ties_after = consecutive_ties_before + 1
        extension_fee_collected = len(all_ids) * config.extension_fee
        aborted = consecutive_ties_after >= config.max_consecutive_ties
        return VoteOutcome(
            round_num=round_num,
            vote_num=vote_num,
            yes_ids=yes_ids,
            no_ids=no_ids,
            result="abort" if aborted else "retry",
            eliminated_ids=[],
            remaining_ids=all_ids,
            consecutive_ties_before=consecutive_ties_before,
            consecutive_ties_after=consecutive_ties_after,
            extension_fee_collected=extension_fee_collected,
            round_over=aborted,
        )

    if n_yes < n_no:
        minority_side, minority_ids, majority_ids = Vote.YES, yes_ids, no_ids
    else:
        minority_side, minority_ids, majority_ids = Vote.NO, no_ids, yes_ids

    round_over = len(minority_ids) <= config.survivors_max

    return VoteOutcome(
        round_num=round_num,
        vote_num=vote_num,
        yes_ids=yes_ids,
        no_ids=no_ids,
        result="decisive",
        eliminated_ids=majority_ids,
        remaining_ids=minority_ids,
        minority_side=minority_side,
        consecutive_ties_before=consecutive_ties_before,
        consecutive_ties_after=0,
        extension_fee_collected=0,
        round_over=round_over,
    )
