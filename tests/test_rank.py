"""
順位のテスト（§7.3、仕様書§12.3 #23を含む）
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import Vote
from tests.helpers import make_roster


def _build_game(num_rounds: int = 3, seed: int = 1) -> Game:
    config = GameConfig.dev_small(num_players=12, num_rounds=num_rounds)
    # 毎ラウンド同じ8対4の割れ方にして、同グループ内で資産が同額になるようにする
    split = {f"P{i:02d}": Vote.YES for i in range(1, 9)}
    split.update({f"P{i:02d}": Vote.NO for i in range(9, 13)})
    votes_by_round = {r: split for r in range(1, num_rounds + 1)}
    agents = make_roster(votes_by_round, num_players=12)
    return Game(config=config, agents=agents, seed=seed, logger=EventLogger())


def test_acceptance_23_r3_public_ranks_names_only_same_amount_same_rank() -> None:
    """#23: R3のFinance終了後 → 全員の順位を名前だけで公開。資産額は出ない。同額は同順位"""
    game = _build_game(num_rounds=3)
    result = game.run()

    r3 = next(s for s in result.round_summaries if s.round_num == 3)
    assert r3.public_ranks is not None
    assert set(r3.public_ranks.keys()) == set(f"P{i:02d}" for i in range(1, 13))

    # 資産額そのものは public_ranks に含まれない（順位のみ）
    for v in r3.public_ranks.values():
        assert isinstance(v, int)

    # 多数派8人（同じ軌跡）は同順位、少数派4人（同じ軌跡）は同順位
    majority_ranks = {r3.public_ranks[f"P{i:02d}"] for i in range(1, 9)}
    minority_ranks = {r3.public_ranks[f"P{i:02d}"] for i in range(9, 13)}
    assert len(majority_ranks) == 1
    assert len(minority_ranks) == 1
    assert majority_ranks != minority_ranks


def test_non_public_round_has_no_public_ranks() -> None:
    """R3・R6・R9以外は全員の順位を公開しない（§7.3）"""
    game = _build_game(num_rounds=2)
    result = game.run()
    for s in result.round_summaries:
        assert s.public_ranks is None


def test_self_rank_only_visible_in_private_state() -> None:
    """毎ラウンドの自分の順位は本人にだけ通知される（§7.3/§8）"""
    game = _build_game(num_rounds=1)
    game._setup()
    game._phase_open(1)
    state_for_me = game._build_visible_state(1, for_player_id="P01")
    state_public = game._build_visible_state(1, for_player_id=None)

    assert "my_rank" in state_for_me
    assert "my_rank" not in state_public
    # 他人の順位・資産額は本人向けの辞書にも含まれない
    assert "cash" not in state_for_me
    assert "all_ranks" not in state_for_me


def test_final_round_reports_ranks_and_assets() -> None:
    """R12終了後は最終順位と最終資産額を公開する（§7.3）"""
    game = _build_game(num_rounds=3)
    result = game.run()
    assert set(result.final_ranks.keys()) == set(f"P{i:02d}" for i in range(1, 13))
    assert set(result.final_assets.keys()) == set(f"P{i:02d}" for i in range(1, 13))
