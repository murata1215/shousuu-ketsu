"""
型Bの「守れば退場・破れば残れたか」計算モジュール（投票単位、サイクル4.1で
v0.4対応として作り直した）

v0.3〜サイクル4.0は「破っていたら受け取れた額」をラウンド単位（配当まで
含む）で計算していたが、v0.4では「少数決の判定」（engine/vote.py
::resolve_vote）と「配当」（engine/round.py）が投票単位・ラウンド単位に
分かれたため（CLAUDE.md落とし穴⑨）、本モジュールは「その投票を生き残れる
か」だけを判定する純粋な投票単位の計算にした。「受け取れた額」までの
金額換算はsim/metrics.py::paired_final_asset_diff（試合全体の最終資産を
同じシードで比較する指標）が担う。

試合の回し直しは行わない。実際に記録された投票の票（yes_ids/no_ids）の
うち、義務者本人の票だけを「守った場合」「破った場合」で切り替え、
engine.vote.resolve_vote（ルールエンジンそのもの、無改変で再利用）に通して
判定する。
"""

from engine.config import GameConfig
from engine.models import Vote
from engine.vote import resolve_vote


def _survives(votes: dict[str, Vote], config: GameConfig, round_num: int, vote_num: int, obligor: str) -> bool:
    """
    この票構成でresolve_voteを実行した場合、obligorが退場しないか

    決着の少数派に入れば残る。やり直し・打ち切りは誰も退場しないので
    常に残る。
    """
    outcome = resolve_vote(votes, config, round_num, vote_num, consecutive_ties_before=0)
    return obligor not in outcome.eliminated_ids


def type_b_vote_survival(
    yes_ids: list[str], no_ids: list[str], obligor: str, required_vote: Vote,
    config: GameConfig, round_num: int, vote_num: int,
) -> dict[str, bool]:
    """
    型Bの義務1本について、守った場合／破った場合でこの投票を生き残れるかを計算する

    Args:
        yes_ids: 実際にYESへ投票した全員（義務者本人を含む）
        no_ids: 実際にNOへ投票した全員（義務者本人を含む）
        obligor: 義務者のプレイヤーID
        required_vote: 義務が指定する側
        config: ゲーム設定
        round_num: ラウンド番号
        vote_num: 投票番号

    Returns:
        {"survives_if_obey": bool, "survives_if_break": bool}
    """
    votes: dict[str, Vote] = {}
    for pid in yes_ids:
        votes[pid] = Vote.YES
    for pid in no_ids:
        votes[pid] = Vote.NO

    broken = Vote.NO if required_vote == Vote.YES else Vote.YES

    votes_obey = dict(votes)
    votes_obey[obligor] = required_vote
    votes_break = dict(votes)
    votes_break[obligor] = broken

    return {
        "survives_if_obey": _survives(votes_obey, config, round_num, vote_num, obligor),
        "survives_if_break": _survives(votes_break, config, round_num, vote_num, obligor),
    }
