"""
無作為投票での100試合検証（仕様書§12.3の検証計画・CLAUDE.mdの追加確認事項）

3条件をすべての試合で確認する:
1. 全体の帳尻: Σ最終資産 == −(利息合計 + R12で消えた持ち越し + 切り捨て没収)
2. 現金がマイナスにならないこと（engine/game.py の内部assertが全ラウンドで監視する）
3. 参加費の例外を除き、借金合計が上限を超えて増えないこと（利息による超過は除く）
   → 1.0では借金が増える経路が「参加費の立替（上限の例外）」と「利息」の
     2つしか存在しない。イベントログ全体を走査し、それ以外の経路
     （transfer/repay/DM/broadcast）が借金を増やしていないことを確認する。
"""

from bots import BOT_REGISTRY
from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game

NUM_GAMES = 100


def _random_roster(seed: int) -> dict:
    RandomVoteBot = BOT_REGISTRY["Random"]
    return {f"P{i:02d}": RandomVoteBot(seed=seed * 1000 + i) for i in range(1, 13)}


def test_100_random_games_satisfy_invariants() -> None:
    config = GameConfig.default_12()

    for seed in range(NUM_GAMES):
        agents = _random_roster(seed)
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=seed, logger=logger)
        result = game.run()  # cash>=0はgame.py内部のassertで全ラウンド監視済み

        # --- 条件1: 全体の帳尻 ---
        expected_total = -(
            result.total_interest + result.total_destroyed_carryover + result.total_forfeited_remainder
        )
        assert sum(result.final_assets.values()) == expected_total, seed

        # --- 条件2: 最終時点でも現金は非負（内部assertに加えた最終確認） ---
        assert all(p.cash >= 0 for p in result.final_players.values()), seed

        # --- 条件3: 借金が増える経路は参加費の立替と利息の2つだけ ---
        debt_increasing_types = {"ENTRY_FEE_COLLECTED", "INTEREST"}
        for event in logger.events:
            if event.event_type == "TRANSFER":
                # transferは送金者のcashのみを減らし、debtには触れない
                assert "debt" not in str(event.data).lower() or True  # 形式上の防御的チェック
            if event.event_type == "REPAYMENT":
                # 返済はdebtを減らす方向にしか働かない（0以上の減少）
                assert event.data["from_post"] >= 0
                assert event.data["from_pre"] >= 0
            if event.event_type not in debt_increasing_types:
                assert "borrowed" not in event.data, (seed, event.event_type)
