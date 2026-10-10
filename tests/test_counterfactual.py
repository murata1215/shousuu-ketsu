"""
sim.counterfactual.type_b_vote_survival() のテスト（投票単位、サイクル4.1）

手計算した例で、「ほかの人の票が同じだったとして、型Bの義務を守った場合／
破った場合にその投票を生き残れるか」の判定が正しいことを確認する。試合は
回さず、engine.vote.resolve_vote()（ルールエンジンそのもの、無改変）を
直接呼ぶ。v0.3〜サイクル4.0は「破っていたら受け取れた額」を計算していたが、
v0.4は少数決の判定（投票単位）と配当（ラウンド単位）が分かれたため、
本モジュールは「生き残れるか」だけを判定する（金額換算はsim/metrics.py
::paired_final_asset_diffが別途担う）。
"""

from engine.config import GameConfig
from engine.models import Vote
from sim.counterfactual import type_b_vote_survival


def _ids(n_yes: int, n_no: int, start: int = 1) -> tuple[list[str], list[str]]:
    i = start
    yes_ids = []
    for _ in range(n_yes):
        yes_ids.append(f"P{i:02d}")
        i += 1
    no_ids = []
    for _ in range(n_no):
        no_ids.append(f"P{i:02d}")
        i += 1
    return yes_ids, no_ids


def test_breaking_from_majority_into_minority_survives() -> None:
    """8YES-4NO、義務者は多数派のYESを指定。守れば退場、破ってNOに回ると
    7対5になり少数派で生き残る"""
    config = GameConfig.default_12()
    yes_ids, no_ids = _ids(8, 4)
    obligor = yes_ids[0]  # P01、多数派(YES)の一員

    result = type_b_vote_survival(yes_ids, no_ids, obligor, Vote.YES, config, round_num=1, vote_num=1)

    assert result["survives_if_obey"] is False  # YES(8人、多数派)は退場
    assert result["survives_if_break"] is True  # 7YES-5NOになり、NO(5人)が少数派で残る


def test_breaking_out_of_minority_gets_eliminated() -> None:
    """8YES-4NO、義務者は少数派のNOを指定。守れば残り、破ってYESに回ると
    9対3になり多数派で退場する"""
    config = GameConfig.default_12()
    yes_ids, no_ids = _ids(8, 4)
    obligor = no_ids[0]  # P09、少数派(NO)の一員

    result = type_b_vote_survival(yes_ids, no_ids, obligor, Vote.NO, config, round_num=1, vote_num=1)

    assert result["survives_if_obey"] is True  # NO(4人、少数派)は残る
    assert result["survives_if_break"] is False  # 9YES-3NOになり、YES(9人、多数派)で退場


def test_breaking_creates_a_tie_still_survives_because_nobody_is_eliminated() -> None:
    """7YES-5NO、義務者は多数派のYESを指定。守れば退場、破ってNOに回ると
    6対6の同数（やり直し）になり、誰も退場しないので「残る」扱いになる"""
    config = GameConfig.default_12()
    yes_ids, no_ids = _ids(7, 5)
    obligor = yes_ids[0]  # P01、多数派(YES)の一員

    result = type_b_vote_survival(yes_ids, no_ids, obligor, Vote.YES, config, round_num=1, vote_num=1)

    assert result["survives_if_obey"] is False  # YES(7人、多数派)は退場
    assert result["survives_if_break"] is True  # 6対6はやり直しで誰も退場しない


def test_both_obey_and_break_survive_when_starting_from_unanimous() -> None:
    """12対0（全員一致、やり直し）。義務者はYESを指定（実際の票と一致）。
    守れば（12対0のまま）やり直しで誰も退場しない。破って1人だけNOに回ると
    11対1の決着になるが、NO側1人（自分だけ）が少数派として残る。
    どちらでも退場しない（＝破る動機そのものがない場面）"""
    config = GameConfig.default_12()
    yes_ids, no_ids = _ids(12, 0)
    obligor = yes_ids[0]

    result = type_b_vote_survival(yes_ids, no_ids, obligor, Vote.YES, config, round_num=1, vote_num=1)

    assert result["survives_if_obey"] is True  # 12対0はやり直しで誰も退場しない
    assert result["survives_if_break"] is True  # 11対1になり、自分(NO側1人)が少数派で残る
