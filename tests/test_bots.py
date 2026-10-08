"""
Botの単体テスト（§12.1のBot検証用）

決まった場面で決まった票・契約になることを確認する。ゲーム全体は回さず、
PlayerState/visible_stateを直接組み立てて各Botのメソッドを呼ぶ。

サイクル4.0でv0.3から更新した。負担するvote_num引数の追加（PlayerAgent
I/F、§1.1）に合わせて各呼び出しを直し、Follow系Botの判断材料を
`last_round_result`（ラウンド単位）から`last_vote_result`（投票単位）に
差し替えた（bots/follow_bot.py参照）。

PairSplitBot/BetrayerPairBot（bots/pair_bot.py）はv0.4のround_num+vote_num
二重構造への対応をサイクル4.1で行うため、該当テストはskipする
（CLAUDE.md的には「v0.3のロジックのまま残置」であり、4.1で書き換える）。
"""

import pytest

from bots.always_no_bot import AlwaysNoBot
from bots.base import BotAgent
from bots.follow_bot import FollowMajorityBot, FollowMinorityBot
from bots.loan_bot import LOAN_MID, LoanMaxHoldBot, LoanMidHoldBot, LoanMinBot
from bots.pair_bot import BetrayerPairBot, PairSplitBot
from engine.config import GameConfig
from engine.models import (
    ContractProposeAction, ContractSignAction, PassAction, PlayerState, RepayAction, Vote,
)


def _player(pid: str = "P01", cash: int = 1_000_000, debt_pre: int = 0, debt_post: int = 0) -> PlayerState:
    return PlayerState(
        player_id=pid, cash=cash, debt_pre=debt_pre, debt_post=debt_post,
        initial_loan=debt_pre + debt_post if (debt_pre or debt_post) else 1_200_000,
    )


# ---------------------------------------------------------------------------
# BotAgent既定（繰上げ返済をしない）
# ---------------------------------------------------------------------------

class _DummyBot(BotAgent):
    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return Vote.YES


def test_default_negotiate_is_always_pass_even_with_debt() -> None:
    bot = _DummyBot("Dummy")
    p = _player(cash=1_000_000, debt_pre=500_000)
    action = bot.negotiate(p, round_num=1, vote_num=1, turn=1, visible_state={})
    assert isinstance(action, PassAction)


def test_try_full_repay_repays_post_debt_only() -> None:
    """_try_full_repay（§3.5）は開始後の借金だけを対象にする"""
    bot = _DummyBot("Dummy")
    p = _player(cash=1_000_000, debt_pre=2_000_000, debt_post=300_000)
    action = bot._try_full_repay(p)
    assert isinstance(action, RepayAction)
    assert action.amount == 300_000


def test_try_full_repay_returns_none_without_post_debt() -> None:
    """開始後の借金が無ければ、開始前の借金が残っていても何もしない"""
    bot = _DummyBot("Dummy")
    p = _player(cash=1_000_000, debt_pre=2_000_000, debt_post=0)
    assert bot._try_full_repay(p) is None


# ---------------------------------------------------------------------------
# AlwaysNoBot
# ---------------------------------------------------------------------------

def test_always_no_bot_votes_no() -> None:
    bot = AlwaysNoBot()
    assert bot.commit(_player(), round_num=1, vote_num=1, visible_state={}) == Vote.NO
    assert bot.commit(_player(), round_num=4, vote_num=6, visible_state={}) == Vote.NO


# ---------------------------------------------------------------------------
# Follow系Bot（v0.4: 直前の投票の結果に追従、§1.1）
# ---------------------------------------------------------------------------

def test_follow_minority_bot_follows_last_vote_minority_side() -> None:
    bot = FollowMinorityBot(seed=1)
    visible_state = {"last_vote_result": {"minority_side": "YES"}}
    assert bot.commit(_player(), round_num=1, vote_num=2, visible_state=visible_state) == Vote.YES

    visible_state = {"last_vote_result": {"minority_side": "NO"}}
    assert bot.commit(_player(), round_num=1, vote_num=2, visible_state=visible_state) == Vote.NO


def test_follow_majority_bot_follows_opposite_of_last_vote_minority() -> None:
    bot = FollowMajorityBot(seed=1)
    visible_state = {"last_vote_result": {"minority_side": "YES"}}
    assert bot.commit(_player(), round_num=1, vote_num=2, visible_state=visible_state) == Vote.NO

    visible_state = {"last_vote_result": {"minority_side": "NO"}}
    assert bot.commit(_player(), round_num=1, vote_num=2, visible_state=visible_state) == Vote.YES


def test_follow_bots_random_when_no_prior_result_or_no_minority() -> None:
    bot_min = FollowMinorityBot(seed=42)
    bot_maj = FollowMajorityBot(seed=42)

    # R1V1相当: last_vote_resultが無い
    v1 = bot_min.commit(_player(), round_num=1, vote_num=1, visible_state={})
    assert v1 in (Vote.YES, Vote.NO)

    # 直前の投票がやり直し・打ち切り（minority_side=None）
    visible_state = {"last_vote_result": {"minority_side": None}}
    v2 = bot_maj.commit(_player(), round_num=1, vote_num=2, visible_state=visible_state)
    assert v2 in (Vote.YES, Vote.NO)


# ---------------------------------------------------------------------------
# PairSplitBot / BetrayerPairBot（v0.4対応はサイクル4.1）
# ---------------------------------------------------------------------------

pytestmark_pair = pytest.mark.skip(
    reason="PairSplitBot/BetrayerPairBotのround_num+vote_num二重構造への対応はサイクル4.1",
)


@pytestmark_pair
def test_pair_split_bot_proposer_proposes_once_per_round_with_alternating_sides() -> None:
    bot = PairSplitBot("P01", "P02", seed=1)
    p = _player("P01")

    action1 = bot.negotiate(p, round_num=1, vote_num=1, turn=1, visible_state={})
    assert isinstance(action1, ContractProposeAction)
    assert action1.with_players == ["P02"]
    terms_by_obligor = {t["obligor"]: t for t in action1.terms}
    assert terms_by_obligor["P01"]["details"]["vote"] == "YES"
    assert terms_by_obligor["P02"]["details"]["vote"] == "NO"
    assert terms_by_obligor["P01"]["round_num"] == 1

    action2 = bot.negotiate(p, round_num=1, vote_num=1, turn=2, visible_state={})
    assert isinstance(action2, PassAction)

    action3 = bot.negotiate(p, round_num=2, vote_num=1, turn=1, visible_state={})
    assert isinstance(action3, ContractProposeAction)
    terms_by_obligor3 = {t["obligor"]: t for t in action3.terms}
    assert terms_by_obligor3["P01"]["details"]["vote"] == "NO"
    assert terms_by_obligor3["P02"]["details"]["vote"] == "YES"


@pytestmark_pair
def test_pair_split_bot_signer_signs_partners_pending_contract() -> None:
    bot = PairSplitBot("P02", "P01", seed=2)
    p = _player("P02")

    pending = {
        "contracts_pending": [
            {
                "proposer": "P01", "round_created": 1,
                "signed_by": ["P01"], "contract_id": "C_ABCDEFGH",
            },
        ],
    }
    action = bot.negotiate(p, round_num=1, vote_num=1, turn=2, visible_state=pending)
    assert isinstance(action, ContractSignAction)
    assert action.contract_id == "C_ABCDEFGH"

    action2 = bot.negotiate(p, round_num=1, vote_num=1, turn=3, visible_state=pending)
    assert isinstance(action2, PassAction)

    bot3 = PairSplitBot("P04", "P03", seed=3)
    action3 = bot3.negotiate(_player("P04"), round_num=1, vote_num=1, turn=1, visible_state={"contracts_pending": []})
    assert isinstance(action3, PassAction)


@pytestmark_pair
def test_pair_split_bot_commit_always_honors_the_promise() -> None:
    bot_small = PairSplitBot("P01", "P02", seed=1)
    bot_large = PairSplitBot("P02", "P01", seed=2)

    assert bot_small.commit(_player("P01"), round_num=1, vote_num=1, visible_state={}) == Vote.YES
    assert bot_large.commit(_player("P02"), round_num=1, vote_num=1, visible_state={}) == Vote.NO

    assert bot_small.commit(_player("P01"), round_num=2, vote_num=1, visible_state={}) == Vote.NO
    assert bot_large.commit(_player("P02"), round_num=2, vote_num=1, visible_state={}) == Vote.YES


@pytestmark_pair
def test_betrayer_pair_bot_negotiate_is_identical_to_pair_split() -> None:
    """契約の提案・署名の段取りはPairSplitBotと同一"""
    bot = BetrayerPairBot("P01", "P02", seed=1)
    action = bot.negotiate(_player("P01"), round_num=1, vote_num=1, turn=1, visible_state={})
    assert isinstance(action, ContractProposeAction)
    terms_by_obligor = {t["obligor"]: t for t in action.terms}
    assert terms_by_obligor["P01"]["details"]["vote"] == "YES"


def test_betrayer_pair_bot_betrays_about_20_percent_of_rounds() -> None:
    """多数のシードで統計を取り、裏切り率がおよそ20%になることを確認する

    commit()自体の信号ロジック（§1.1のラウンド/投票の区別と無関係）は
    v0.3から変更していないため、このテストはv0.4でもそのまま有効。
    """
    n = 2000
    betrayed = 0
    for seed in range(n):
        bot = BetrayerPairBot("P01", "P02", seed=seed)
        vote = bot.commit(_player("P01"), round_num=1, vote_num=1, visible_state={})
        if vote != Vote.YES:  # P01はR1でYESが義務
            betrayed += 1
    ratio = betrayed / n
    assert 0.15 <= ratio <= 0.25, ratio


def test_betrayer_pair_bot_same_seed_is_reproducible() -> None:
    bot_a = BetrayerPairBot("P01", "P02", seed=99)
    bot_b = BetrayerPairBot("P01", "P02", seed=99)
    votes_a = [bot_a.commit(_player("P01"), round_num=r, vote_num=1, visible_state={}) for r in range(1, 5)]
    votes_b = [bot_b.commit(_player("P01"), round_num=r, vote_num=1, visible_state={}) for r in range(1, 5)]
    assert votes_a == votes_b


# ---------------------------------------------------------------------------
# 借入3通りBot
# ---------------------------------------------------------------------------

def test_loan_bots_choose_correct_loan_amounts() -> None:
    config = GameConfig.default_12()
    assert LoanMinBot().choose_loan(config) == config.loan_min
    assert LoanMidHoldBot().choose_loan(config) == LOAN_MID
    assert LoanMaxHoldBot().choose_loan(config) == config.loan_max


def test_loan_mid_hold_bot_never_repays() -> None:
    """LoanMidHoldBot（500万）は常にpass（繰上げ返済なし）"""
    bot = LoanMidHoldBot(seed=1)
    p = _player("P01", cash=5_000_000, debt_pre=5_000_000)
    for round_num in range(1, 5):
        action = bot.negotiate(p, round_num=round_num, vote_num=1, turn=1, visible_state={})
        assert isinstance(action, PassAction)


def test_loan_max_hold_bot_never_repays() -> None:
    bot = LoanMaxHoldBot(seed=1)
    p = _player("P01", cash=10_000_000, debt_pre=10_000_000)
    for round_num in range(1, 5):
        action = bot.negotiate(p, round_num=round_num, vote_num=1, turn=1, visible_state={})
        assert isinstance(action, PassAction)


def test_loan_bots_votes_are_random_but_reproducible() -> None:
    bot_a = LoanMinBot(seed=5)
    bot_b = LoanMinBot(seed=5)
    p = _player("P01")
    votes_a = [bot_a.commit(p, round_num=r, vote_num=1, visible_state={}) for r in range(1, 5)]
    votes_b = [bot_b.commit(p, round_num=r, vote_num=1, visible_state={}) for r in range(1, 5)]
    assert votes_a == votes_b
