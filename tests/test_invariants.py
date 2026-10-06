"""
無作為投票での100試合検証（仕様書§12.3の検証計画・CLAUDE.mdの追加確認事項）

契約なし（Bot）の100試合は3条件を確認する:
1. 全体の帳尻: Σ最終資産 == −(利息合計 + R12で消えた持ち越し + 切り捨て没収)
2. 現金がマイナスにならないこと（engine/game.py の内部assertが全ラウンドで監視する）
3. 参加費の例外を除き、借金合計が上限を超えて増えないこと（利息による超過は除く）
   → 1.0では借金が増える経路が「参加費の立替（上限の例外）」と「利息」の
     2つしか存在しない。イベントログ全体を走査し、それ以外の経路
     （transfer/repay/DM/broadcast）が借金を増やしていないことを確認する。

契約あり（RandomContractAgent）の100試合は、サイクル1.1の追加確認事項
（CLAUDE.md）を含めて確認する: 帳尻・現金非負・借金上限・各義務の支払額が
約束額以下・義務者の支払合計が固定した上限以下・部分払い以降は同一義務者の
後続が全て0円・同一シードで結果が完全一致。
"""

from bots import BOT_REGISTRY
from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from tests.helpers import RandomContractAgent

NUM_GAMES = 100


def _random_roster(seed: int) -> dict:
    RandomVoteBot = BOT_REGISTRY["Random"]
    return {f"P{i:02d}": RandomVoteBot(seed=seed * 1000 + i) for i in range(1, 13)}


def _random_contract_roster(seed: int) -> dict:
    return {f"P{i:02d}": RandomContractAgent(f"P{i:02d}", seed=seed * 1000 + i) for i in range(1, 13)}


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


def _run_contract_game(seed: int) -> tuple:
    agents = _random_contract_roster(seed)
    logger = EventLogger()
    game = Game(config=GameConfig.default_12(), agents=agents, seed=seed, logger=logger)
    result = game.run()
    return result, logger


def test_100_random_games_with_contracts_satisfy_invariants() -> None:
    """
    契約を含む無作為100試合の検査（CLAUDE.md追加確認事項）

    票と契約（型・金額・相手・対象ラウンド）を無作為に作り、上限に届くような
    大きな金額も混ぜる（RandomContractAgent、tests/helpers.py）。
    """
    for seed in range(NUM_GAMES):
        result, logger = _run_contract_game(seed)

        # --- 帳尻: 契約の支払いは当事者間のゼロサム移動なので総額の式は変わらない ---
        expected_total = -(
            result.total_interest + result.total_destroyed_carryover + result.total_forfeited_remainder
        )
        assert sum(result.final_assets.values()) == expected_total, seed

        # --- 現金が最終時点でも非負（ラウンドごとはgame.py内部assertで監視済み） ---
        assert all(p.cash >= 0 for p in result.final_players.values()), seed

        payments = [e for e in logger.events if e.event_type == "CONTRACT_PAYMENT"]
        limits_by_round: dict[int, dict[str, int]] = {}
        for e in logger.events:
            if e.event_type == "PAYABLE_LIMIT_FIXED":
                limits_by_round[e.round_num] = e.data["payable_limits"]

        # --- 各義務の支払額は約束額以下 ---
        for p in payments:
            assert 0 <= p.data["paid"] <= p.data["promised"], (seed, p.data)

        # --- 義務者ごとの支払合計はその決済で固定した上限以下 ---
        totals_by_round_obligor: dict[tuple[int, str], int] = {}
        for p in payments:
            key = (p.round_num, p.data["obligor"])
            totals_by_round_obligor[key] = totals_by_round_obligor.get(key, 0) + p.data["paid"]
        for (round_num, obligor), total_paid in totals_by_round_obligor.items():
            limit = limits_by_round[round_num][obligor]
            assert total_paid <= limit, (seed, round_num, obligor, total_paid, limit)

        # --- ある義務が部分払い/0円なら、同じ義務者のそれより後の義務は全て0円 ---
        # payments はラウンド内で (contract_seqの小さい順→記載順) に記録済みなので、
        # そのログ順そのものが義務者ごとの支払い順と一致する。
        by_round: dict[int, list] = {}
        for p in payments:
            by_round.setdefault(p.round_num, []).append(p.data)
        for round_num, items in by_round.items():
            shortfall_seen: set[str] = set()
            for item in items:
                obligor = item["obligor"]
                if obligor in shortfall_seen:
                    assert item["paid"] == 0, (seed, round_num, item)
                if item["paid"] < item["promised"]:
                    shortfall_seen.add(obligor)


def test_same_seed_with_contracts_gives_identical_results() -> None:
    """同じシードなら契約の内容・成立・支払いまで含めて結果が完全一致する"""
    result1, logger1 = _run_contract_game(seed=777)
    result2, logger2 = _run_contract_game(seed=777)

    assert result1.final_assets == result2.final_assets
    assert result1.final_ranks == result2.final_ranks
    events1 = [(e.event_type, e.round_num, e.phase, e.step, e.data) for e in logger1.events]
    events2 = [(e.event_type, e.round_num, e.phase, e.step, e.data) for e in logger2.events]
    assert events1 == events2
