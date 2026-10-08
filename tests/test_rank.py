"""
順位のテスト（§7.6、仕様書v0.4 §12.3 #49を含む）

サイクル4.0でv0.3から全面更新した。v0.4は全員の順位の公開がR3・R6・R9の
3回からR2の1回に変わった（§7.6/§11.2暫定8）。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import Vote
from tests.helpers import make_roster


def _build_game(num_rounds: int = 2, seed: int = 1) -> Game:
    config = GameConfig.dev_small(num_players=12, num_rounds=num_rounds)
    # 毎ラウンドV1で10対2（NO側2人が勝ち残り）にし、同グループ内で資産が
    # 同額になるようにする
    split = {f"P{i:02d}": Vote.YES for i in range(1, 11)}
    split.update({"P11": Vote.NO, "P12": Vote.NO})
    votes_by_vote = {(r, 1): split for r in range(1, num_rounds + 1)}
    agents = make_roster(votes_by_vote, num_players=12)
    return Game(config=config, agents=agents, seed=seed, logger=EventLogger())


def test_acceptance_49_r2_public_ranks_names_only_same_amount_same_rank() -> None:
    """#49: R2のFinance終了後 → 全員の順位を名前だけで公開。資産額は出ない。同額は同順位"""
    game = _build_game(num_rounds=2)
    result = game.run()

    r2 = next(s for s in result.round_summaries if s.round_num == 2)
    assert r2.public_ranks is not None
    assert set(r2.public_ranks.keys()) == set(f"P{i:02d}" for i in range(1, 13))

    # 資産額そのものは public_ranks に含まれない（順位のみ）
    for v in r2.public_ranks.values():
        assert isinstance(v, int)

    # 多数派10人（同じ軌跡、毎回負け続ける）は同順位、少数派2人（毎回勝ち残る）は同順位
    majority_ranks = {r2.public_ranks[f"P{i:02d}"] for i in range(1, 11)}
    minority_ranks = {r2.public_ranks[f"P{i:02d}"] for i in range(11, 13)}
    assert len(majority_ranks) == 1
    assert len(minority_ranks) == 1
    assert majority_ranks != minority_ranks


def test_non_r2_round_has_no_public_ranks() -> None:
    """R2以外は全員の順位を公開しない（§7.6）"""
    game = _build_game(num_rounds=1)
    result = game.run()
    for s in result.round_summaries:
        assert s.public_ranks is None


def test_self_rank_only_visible_in_private_state() -> None:
    """各投票のOpenで、自分の順位は本人にだけ通知される（§7.6/§8）"""
    game = _build_game(num_rounds=1)
    game._setup()
    game._remaining_ids = set(game.players.keys())
    game._eliminated_ids = set()
    game._consecutive_ties = 0
    game._pot = 0
    game._phase_open(1, 1)
    state_for_me = game._build_visible_state(1, 1, for_player_id="P01")
    state_public = game._build_visible_state(1, 1, for_player_id=None)

    assert "my_rank" in state_for_me
    assert "my_rank" not in state_public
    # 他人の順位・資産額は本人向けの辞書にも含まれない
    assert "cash" not in state_for_me
    assert "all_ranks" not in state_for_me


def test_final_round_reports_ranks_and_assets() -> None:
    """最終ラウンド終了後は最終順位と最終資産額を公開する（§7.6）"""
    game = _build_game(num_rounds=2)
    result = game.run()
    assert set(result.final_ranks.keys()) == set(f"P{i:02d}" for i in range(1, 13))
    assert set(result.final_assets.keys()) == set(f"P{i:02d}" for i in range(1, 13))
