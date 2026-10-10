"""
Botの単体テスト（§12.1のBot検証用）

決まった場面で決まった票・契約になることを確認する。ゲーム全体は回さず、
PlayerState/visible_stateを直接組み立てて各Botのメソッドを呼ぶ。

サイクル4.0でv0.3から更新した。負担するvote_num引数の追加（PlayerAgent
I/F、§1.1）に合わせて各呼び出しを直し、Follow系Botの判断材料を
`last_round_result`（ラウンド単位）から`last_vote_result`（投票単位）に
差し替えた（bots/follow_bot.py参照）。

サイクル4.1でv0.3の「ペア割り」（bots/pair_bot.py）を削除し、
bots/group_bot.py（GroupSplitBot/TypeBPactGroupBot/MultiGroupHubBot/
SitInBot）とbots/oversell_bot.py（OversellBot/SignerBot）に作り直した
（CLAUDE.md落とし穴⑨）。借入3通りBotはLoanFixedBot(loan=...)に一本化した。
"""

from bots.always_no_bot import AlwaysNoBot
from bots.base import BotAgent
from bots.follow_bot import FollowMajorityBot, FollowMinorityBot
from bots.group_bot import GroupSplitBot, MultiGroupHubBot, SitInBot, TypeBPactGroupBot
from bots.loan_bot import LoanFixedBot
from bots.oversell_bot import OversellBot, SignerBot
from engine.config import GameConfig
from engine.models import (
    ContractProposeAction, ContractSignAction, PassAction, PlayerState, RepayAction, Vote,
)


def _player(pid: str = "P01", cash: int = 1_000_000, debt_pre: int = 0, debt_post: int = 0) -> PlayerState:
    return PlayerState(
        player_id=pid, cash=cash, debt_pre=debt_pre, debt_post=debt_post,
        initial_loan=debt_pre + debt_post if (debt_pre or debt_post) else 1_200_000,
    )


def _vs(remaining_ids: list[str] | None = None, contracts_pending: list[dict] | None = None) -> dict:
    return {
        "remaining_ids": remaining_ids if remaining_ids is not None else [],
        "contracts_pending": contracts_pending or [],
    }


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
# GroupSplitBot（V2〜V7の土台、サイクル4.1新規）
# ---------------------------------------------------------------------------

def test_group_split_bot_commit_splits_group_in_half_by_rotation() -> None:
    """4人組は(round_num-1+vote_num-1)で回転し、前半2人がYES・残り2人がNO"""
    members = ["P01", "P02", "P03", "P04"]
    bots = {pid: GroupSplitBot(pid, members, seed=i) for i, pid in enumerate(members)}
    votes = {
        pid: bots[pid].commit(_player(pid), round_num=1, vote_num=1, visible_state=_vs(members))
        for pid in members
    }
    assert sum(1 for v in votes.values() if v == Vote.YES) == 2
    assert sum(1 for v in votes.values() if v == Vote.NO) == 2
    # round_num=1, vote_num=1 → shift=0 → 先頭2人(P01,P02)がYES
    assert votes["P01"] == Vote.YES and votes["P02"] == Vote.YES
    assert votes["P03"] == Vote.NO and votes["P04"] == Vote.NO


def test_group_split_bot_rotation_changes_with_round_and_vote() -> None:
    """投票番号が進むと回転して割り方が変わる（毎回同じ人が得をしない）"""
    members = ["P01", "P02", "P03", "P04"]
    bot = GroupSplitBot("P01", members, seed=1)
    v1 = bot.commit(_player("P01"), round_num=1, vote_num=1, visible_state=_vs(members))
    v2 = bot.commit(_player("P01"), round_num=1, vote_num=2, visible_state=_vs(members))
    assert v1 != v2  # shift 0→1で先頭がP02に変わり、P01はYESから外れる


def test_group_split_bot_excludes_eliminated_members_from_rotation() -> None:
    """退場した組員はremaining_idsに含まれないため、回転の対象から外れる"""
    members = ["P01", "P02", "P03", "P04"]
    bot = GroupSplitBot("P03", members, seed=1)
    remaining = ["P02", "P03", "P04"]  # P01は退場済み
    vote = bot.commit(_player("P03"), round_num=1, vote_num=1, visible_state=_vs(remaining))
    assert vote in (Vote.YES, Vote.NO)


def test_group_split_bot_proposer_proposes_profit_share_once_per_round() -> None:
    members = ["P01", "P02", "P03", "P04"]
    bot = GroupSplitBot("P01", members, seed=1)  # ID最小=提案者

    action1 = bot.negotiate(_player("P01"), round_num=1, vote_num=1, turn=1, visible_state=_vs(members))
    assert isinstance(action1, ContractProposeAction)
    assert sorted(action1.with_players) == ["P02", "P03", "P04"]
    # 4人組なので義務はobligor x counterparty（自分以外）の組み合わせ = 4*3=12本
    assert len(action1.terms) == 12
    for term in action1.terms:
        assert term["ob_type"] == "type_c_conditional"
        assert term["details"]["condition_type"] == "wins_round"
        assert term["details"]["condition"]["target_player"] == term["obligor"]
        assert term["details"]["share_percent"] == 25  # floor(100/4)

    # 同じラウンドのV1では2回提案しない
    action2 = bot.negotiate(_player("P01"), round_num=1, vote_num=1, turn=2, visible_state=_vs(members))
    assert isinstance(action2, PassAction)

    # 次のラウンドでは再び提案する
    action3 = bot.negotiate(_player("P01"), round_num=2, vote_num=1, turn=1, visible_state=_vs(members))
    assert isinstance(action3, ContractProposeAction)


def test_group_split_bot_signer_signs_proposers_pending_contract() -> None:
    members = ["P01", "P02", "P03", "P04"]
    bot = GroupSplitBot("P02", members, seed=2)
    pending = _vs(members, contracts_pending=[
        {
            "contract_id": "C_ABCDEFGH", "proposer": "P01", "round_created": 1,
            "parties": members, "signed_by": ["P01"],
        },
    ])
    action = bot.negotiate(_player("P02"), round_num=1, vote_num=1, turn=2, visible_state=pending)
    assert isinstance(action, ContractSignAction)
    assert action.contract_id == "C_ABCDEFGH"

    # 署名済みなら次はpass
    pending_signed = _vs(members, contracts_pending=[
        {
            "contract_id": "C_ABCDEFGH", "proposer": "P01", "round_created": 1,
            "parties": members, "signed_by": ["P01", "P02"],
        },
    ])
    action2 = bot.negotiate(_player("P02"), round_num=1, vote_num=1, turn=3, visible_state=pending_signed)
    assert isinstance(action2, PassAction)


def test_group_split_bot_without_profit_share_never_proposes() -> None:
    """propose_profit_share=Falseなら契約の提案・署名を一切しない"""
    members = ["P01", "P02", "P03", "P04"]
    bot = GroupSplitBot("P01", members, seed=1, propose_profit_share=False)
    action = bot.negotiate(_player("P01"), round_num=1, vote_num=1, turn=1, visible_state=_vs(members))
    assert isinstance(action, PassAction)


def test_group_split_bot_two_member_group_splits_one_each() -> None:
    """V3（2人組）: 1人YES・1人NO、端数なしのfloor(100/2)=50%"""
    members = ["P05", "P06"]
    bot = GroupSplitBot("P05", members, seed=1)
    action = bot.negotiate(_player("P05"), round_num=1, vote_num=1, turn=1, visible_state=_vs(members))
    assert isinstance(action, ContractProposeAction)
    assert len(action.terms) == 2  # P05→P06, P06→P05
    assert all(t["details"]["share_percent"] == 50 for t in action.terms)


# ---------------------------------------------------------------------------
# TypeBPactGroupBot（V8、サイクル4.1新規）
# ---------------------------------------------------------------------------

def test_type_b_pact_group_bot_contract_terms_include_profit_share_and_type_b_ring() -> None:
    members = ["P01", "P02", "P03", "P04"]
    bot = TypeBPactGroupBot("P01", members, seed=1, breaks=False)
    terms = bot._contract_terms(round_num=1)
    type_c_terms = [t for t in terms if t["ob_type"] == "type_c_conditional"]
    type_b_terms = [t for t in terms if t["ob_type"] == "type_b_vote"]
    assert len(type_c_terms) == 12  # 山分け（4人組）
    assert len(type_b_terms) == 4  # 輪番で1人だけへの型B（組員数と同じ本数）
    for t in type_b_terms:
        assert t["round_num"] == 1
        assert t["vote_num"] == 1
        # 相手は輪番で1人だけ（自分以外）
        assert t["counterparty"] != t["obligor"]


def test_type_b_pact_group_bot_keeps_the_promise_when_not_breaking() -> None:
    members = ["P01", "P02", "P03", "P04"]
    bot = TypeBPactGroupBot("P01", members, seed=1, breaks=False)
    vote = bot.commit(_player("P01"), round_num=1, vote_num=1, visible_state=_vs(members))
    assert vote == Vote.YES  # shift=0なので先頭のP01はYES


def test_type_b_pact_group_bot_breaker_inverts_only_on_vote_1() -> None:
    members = ["P01", "P02", "P03", "P04"]
    bot = TypeBPactGroupBot("P01", members, seed=1, breaks=True)
    # V1: 本来YESの指示を破ってNOに入れる
    v1 = bot.commit(_player("P01"), round_num=1, vote_num=1, visible_state=_vs(members))
    assert v1 == Vote.NO
    # V2以降はGroupSplitBotと同じ規則（破らない）
    honest = GroupSplitBot("P01", members, seed=1)
    v2_bot = bot.commit(_player("P01"), round_num=1, vote_num=2, visible_state=_vs(members))
    v2_honest = honest.commit(_player("P01"), round_num=1, vote_num=2, visible_state=_vs(members))
    assert v2_bot == v2_honest


# ---------------------------------------------------------------------------
# MultiGroupHubBot（V7、サイクル4.1新規）
# ---------------------------------------------------------------------------

def test_multi_group_hub_bot_votes_the_minority_side_of_the_three_groups() -> None:
    groups = [["P02", "P03", "P04"], ["P05", "P06", "P07"], ["P08", "P09", "P10"]]
    bot = MultiGroupHubBot("P01", groups, seed=1)
    all_members = [m for g in groups for m in g] + ["P01", "P11", "P12"]
    vote = bot.commit(_player("P01"), round_num=1, vote_num=1, visible_state=_vs(all_members))

    # 各組(3人)の指示はround_num=1,vote_num=1でshift=0 → 先頭2人YES・残り1人NO
    # 3組で YES=6・NO=3。少ない側(NO)に入れる
    assert vote == Vote.NO


def test_multi_group_hub_bot_default_negotiate_never_signs_profit_share() -> None:
    """本人は山分けの契約に署名しない（既定のpass）"""
    groups = [["P02", "P03", "P04"]]
    bot = MultiGroupHubBot("P01", groups, seed=1)
    pending = _vs(["P01", "P02", "P03", "P04"], contracts_pending=[
        {
            "contract_id": "C1", "proposer": "P02", "round_created": 1,
            "parties": ["P01", "P02", "P03", "P04"], "signed_by": ["P02"],
        },
    ])
    action = bot.negotiate(_player("P01"), round_num=1, vote_num=1, turn=1, visible_state=pending)
    assert isinstance(action, PassAction)


# ---------------------------------------------------------------------------
# SitInBot（V6、サイクル4.1新規）
# ---------------------------------------------------------------------------

def test_sit_in_bot_random_when_remaining_is_not_four() -> None:
    bot = SitInBot("P09", group_members=["P01", "P02", "P03", "P04"], seed=1)
    vote = bot.commit(_player("P09"), round_num=1, vote_num=1, visible_state=_vs(["P01", "P02", "P09", "P10", "P11"]))
    assert vote in (Vote.YES, Vote.NO)


def test_sit_in_bot_splits_outsiders_evenly_when_remaining_four_is_group_two_plus_outside_two() -> None:
    group = ["P01", "P02", "P03", "P04"]
    bot_a = SitInBot("P09", group_members=group, seed=1)
    bot_b = SitInBot("P10", group_members=group, seed=2)
    remaining = _vs(["P01", "P02", "P09", "P10"])  # 組2人(P01,P02)＋外2人(P09,P10)

    v_a = bot_a.commit(_player("P09"), round_num=1, vote_num=1, visible_state=remaining)
    v_b = bot_b.commit(_player("P10"), round_num=1, vote_num=1, visible_state=remaining)
    assert v_a == Vote.YES  # 外側でID最小
    assert v_b == Vote.NO
    assert v_a != v_b  # 必ず割れる（同数維持→打ち切りに持ち込む）


# ---------------------------------------------------------------------------
# OversellBot / SignerBot（V10、サイクル4.1新規）
# ---------------------------------------------------------------------------

def test_oversell_bot_proposes_three_separate_contracts_at_r1v1_turn_1_to_3() -> None:
    bot = OversellBot("P01", signers=["P02", "P03", "P04"], seed=1, num_rounds=4)
    actions = [
        bot.negotiate(_player("P01"), round_num=1, vote_num=1, turn=t, visible_state=_vs([]))
        for t in (1, 2, 3, 4)
    ]
    assert isinstance(actions[0], ContractProposeAction) and actions[0].with_players == ["P02"]
    assert isinstance(actions[1], ContractProposeAction) and actions[1].with_players == ["P03"]
    assert isinstance(actions[2], ContractProposeAction) and actions[2].with_players == ["P04"]
    assert isinstance(actions[3], PassAction)  # 4巡目は提案済みなのでpass

    # 各契約はR1〜R4の4義務、いずれも50%・target_player=本人
    for action in actions[:3]:
        assert len(action.terms) == 4
        assert {t["round_num"] for t in action.terms} == {1, 2, 3, 4}
        for t in action.terms:
            assert t["details"]["share_percent"] == 50
            assert t["details"]["condition"]["target_player"] == "P01"


def test_oversell_bot_only_proposes_at_r1v1() -> None:
    bot = OversellBot("P01", signers=["P02", "P03", "P04"], seed=1)
    action = bot.negotiate(_player("P01"), round_num=2, vote_num=1, turn=1, visible_state=_vs([]))
    assert isinstance(action, PassAction)


def test_signer_bot_signs_only_proposers_pending_contracts() -> None:
    bot = SignerBot("P02", proposer_id="P01", seed=1)
    pending = _vs([], contracts_pending=[
        {"contract_id": "C1", "proposer": "P03", "signed_by": []},  # 別の提案者
        {"contract_id": "C2", "proposer": "P01", "signed_by": []},
    ])
    action = bot.negotiate(_player("P02"), round_num=1, vote_num=1, turn=1, visible_state=pending)
    assert isinstance(action, ContractSignAction)
    assert action.contract_id == "C2"


# ---------------------------------------------------------------------------
# 借入額固定Bot（LoanFixedBot、サイクル4.1でクラスを一本化）
# ---------------------------------------------------------------------------

def test_loan_fixed_bot_choose_loan_returns_specified_amount() -> None:
    config = GameConfig.default_12()
    assert LoanFixedBot(loan=config.loan_min).choose_loan(config) == config.loan_min
    assert LoanFixedBot(loan=4_000_000).choose_loan(config) == 4_000_000
    assert LoanFixedBot(loan=config.loan_max).choose_loan(config) == config.loan_max


def test_loan_fixed_bot_never_repays() -> None:
    """LoanFixedBotは常にpass（繰上げ返済なし）"""
    bot = LoanFixedBot(loan=4_000_000, seed=1)
    p = _player("P01", cash=4_000_000, debt_pre=4_000_000)
    for round_num in range(1, 5):
        action = bot.negotiate(p, round_num=round_num, vote_num=1, turn=1, visible_state={})
        assert isinstance(action, PassAction)


def test_loan_fixed_bot_votes_are_random_but_reproducible() -> None:
    bot_a = LoanFixedBot(loan=1_200_000, seed=5)
    bot_b = LoanFixedBot(loan=1_200_000, seed=5)
    p = _player("P01")
    votes_a = [bot_a.commit(p, round_num=r, vote_num=1, visible_state={}) for r in range(1, 5)]
    votes_b = [bot_b.commit(p, round_num=r, vote_num=1, visible_state={}) for r in range(1, 5)]
    assert votes_a == votes_b
