"""
本人のみ・当事者のみのイベント種別 → visible_stateの対応表（§7.5・§8.2、サイクル4.2c新設）

CLAUDE.md落とし穴⑫（本人・当事者だけの記録も、渡す相手に届いているかを
確かめる。公開の記録が届いていなかった落とし穴⑩のself/parties版）の
再発防止。`tests/test_event_visibility.py::FIXED_VISIBILITY` の self・
parties種別全数に対応するvisible_stateの項目名を固定し、新設したイベント
種別に対応を書き忘れたら落ちるようにする（tests/test_public_disclosure.py::
PUBLIC_EVENT_DELIVERYのself/parties版）。

あわせて、お金が動くイベント種別→お金の出入りの記録の種別（LedgerEntryKind）
の対応表（MONEY_EVENT_TO_LEDGER_KINDS）も固定し、1試合ぶんを実際に突き合わせる。
"""

from __future__ import annotations

import random

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import (
    Action, CashMovement, LedgerEntryKind, ObligationPayment, ObligationType,
    PassAction, PlayerState, TransferAction, Vote,
)
from engine.negotiation import PlayerAgent
from bots.random_contract_bot import RandomContractBot
from tests.helpers import make_roster
from tests.test_event_visibility import FIXED_VISIBILITY, NEGOTIATION_ACTION_VISIBILITY_BY_KIND

# --- 横断表1: self/partiesのイベント種別 → visible_stateの項目名 ---

PRIVATE_EVENT_DELIVERY: dict[str, tuple[str, ...]] = {
    "ENTRY_FEE_COLLECTED": ("my_cash_flow",),
    "EXTENSION_FEE_COLLECTED": ("my_cash_flow",),
    "REPAYMENT": ("my_cash_flow",),
    "INTEREST": ("my_cash_flow",),
    "RANK_NOTIFIED": ("my_rank",),
    "FINANCE_NOTICE": ("my_finance",),
    "CONTRACT_REJECTED": ("my_last_action_error",),
    "TRANSFER": ("my_cash_flow",),
    "CONTRACT_PAYMENT": ("my_cash_flow", "my_contract_status"),
    "CONTRACT_PROPOSED": ("contracts_pending",),
    "CONTRACT_SIGNED": ("contracts_pending",),
    "CONTRACT_ESTABLISHED": ("my_contract_status",),
    "CONTRACT_EXPIRED": ("my_expired_proposals",),
    "NEGOTIATION_ACTION:dm": ("messages",),
    # 公開（public）区分だが§8.2で本人にも渡すことになっているもの
    "ROUND_PAYOUT": ("my_cash_flow", "round_results"),
    "TYPE_B_VIOLATION": ("my_contract_status", "round_vote_results"),
    "LOAN_REVEALED": ("my_cash_flow", "initial_loans"),
}

# 渡さないと決めた種別とその理由（self/parties区分のうち、本人・当事者に
# 渡さないもの。spectator区分はこの表の対象外——もともとプレイヤーに
# 渡らない区分であり、§7.5/§8.2の「本人・当事者だけの記録」の対象ではない）
PRIVATE_EVENT_NOT_DELIVERED: dict[str, str] = {
    "COMMIT": "自分の投票先は、その手番で自分が決めた値そのもの。締切後は"
              "round_vote_results（公開）で全員に渡る。本人向けの別項目を"
              "設けると同じ事実が2か所になる",
}

# 公開（public）区分だが、§8.2により本人にも別途渡すことになっている種別
_PUBLIC_BUT_ALSO_PRIVATE = {"LOAN_REVEALED", "ROUND_PAYOUT", "TYPE_B_VIOLATION"}


def _self_or_parties_event_types() -> set[str]:
    types = {t for t, v in FIXED_VISIBILITY.items() if v in ("self", "parties")}
    types |= {
        f"NEGOTIATION_ACTION:{kind}"
        for kind, vis in NEGOTIATION_ACTION_VISIBILITY_BY_KIND.items()
        if vis == "parties"
    }
    types |= _PUBLIC_BUT_ALSO_PRIVATE
    return types


def test_every_self_or_parties_event_type_has_a_visible_state_field() -> None:
    """
    self・partiesのイベント種別すべてがPRIVATE_EVENT_DELIVERYか
    PRIVATE_EVENT_NOT_DELIVEREDのどちらかに挙げられており、挙げた項目名が
    実際にvisible_stateのキーとして存在することを確かめる。
    """
    covered = set(PRIVATE_EVENT_DELIVERY) | set(PRIVATE_EVENT_NOT_DELIVERED)
    expected = _self_or_parties_event_types()
    assert covered == expected, (
        f"対応表に抜けがある: 表に無い種別={expected - covered}, "
        f"表にあるが実在しない種別={covered - expected}"
    )

    agents = make_roster({}, num_players=12)
    config = GameConfig.dev_small(num_players=12, num_rounds=2)
    game = Game(config=config, agents=agents, seed=1, logger=EventLogger(), stop_after_round=1)
    game.run()
    sample_state = game._build_visible_state(2, 1, for_player_id="P01")

    for event_type, fields in PRIVATE_EVENT_DELIVERY.items():
        for field in fields:
            assert field in sample_state, f"{event_type} -> {field!r} not in visible_state"


# --- 横断表2: お金が動くイベント種別 → お金の出入りの記録の種別 ---

MONEY_EVENT_TO_LEDGER_KINDS: dict[str, frozenset] = {
    "LOAN_REVEALED": frozenset({LedgerEntryKind.INITIAL_LOAN}),
    "ENTRY_FEE_COLLECTED": frozenset({LedgerEntryKind.ENTRY_FEE}),
    "EXTENSION_FEE_COLLECTED": frozenset({LedgerEntryKind.EXTENSION_FEE}),
    "TRANSFER": frozenset({LedgerEntryKind.TRANSFER_OUT, LedgerEntryKind.TRANSFER_IN}),
    "REPAYMENT": frozenset({LedgerEntryKind.REPAYMENT}),
    "CONTRACT_PAYMENT": frozenset(
        {LedgerEntryKind.CONTRACT_PAYMENT_PAID, LedgerEntryKind.CONTRACT_PAYMENT_RECEIVED},
    ),
    "ROUND_PAYOUT": frozenset({LedgerEntryKind.POT_PAYOUT}),
    "INTEREST": frozenset({LedgerEntryKind.INTEREST}),
}
# SETTLEMENT_BORROWINGは専用のGameEventを持たない（精算での借入は差分として
# 導出するだけで、独立したイベントとしては記録していない）。LedgerEntryKindの
# 全数カバレッジはこちらで確認する。
NON_MONEY_EVENTS: frozenset[str] = frozenset(set(FIXED_VISIBILITY) - set(MONEY_EVENT_TO_LEDGER_KINDS))


def test_money_event_to_ledger_kind_table_covers_all_fixed_visibility_types() -> None:
    assert set(MONEY_EVENT_TO_LEDGER_KINDS) | NON_MONEY_EVENTS == set(FIXED_VISIBILITY)

    all_mapped_kinds: set = set()
    for kinds in MONEY_EVENT_TO_LEDGER_KINDS.values():
        all_mapped_kinds |= kinds
    all_mapped_kinds.add(LedgerEntryKind.SETTLEMENT_BORROWING)
    assert all_mapped_kinds == set(LedgerEntryKind)


class _ContractAndTransferBot(PlayerAgent):
    """RandomContractBotの契約行動に加え、パスする巡には時々送金する
    （テスト専用。お金が動くイベントを網羅的に発生させる）"""

    def __init__(self, player_id: str, seed: int) -> None:
        self._inner = RandomContractBot(player_id, seed=seed)
        self.player_id = player_id
        self._rng = random.Random(seed * 13 + 1)

    def choose_loan(self, config: GameConfig) -> int:
        return self._inner.choose_loan(config)

    def commit(self, player_state: PlayerState, round_num: int, vote_num: int, visible_state: dict) -> Vote:
        return self._inner.commit(player_state, round_num, vote_num, visible_state)

    def negotiate(
        self, player_state: PlayerState, round_num: int, vote_num: int, turn: int, visible_state: dict,
    ) -> Action:
        action = self._inner.negotiate(player_state, round_num, vote_num, turn, visible_state)
        if not isinstance(action, PassAction):
            return action
        all_ids = sorted(visible_state.get("initial_loans", {}).keys())
        others = [p for p in all_ids if p != self.player_id]
        if others and self._rng.random() < 0.3 and player_state.cash > 0:
            amount = min(player_state.cash, self._rng.choice([10_000, 50_000, 100_000]))
            return TransferAction(player_id=self.player_id, to=self._rng.choice(others), amount=amount)
        return action


def test_money_events_reconcile_with_cash_flow_ledger_one_game() -> None:
    """
    1試合ぶんのお金が動くイベントを、お金の出入りの記録と1件ずつ突き合わせる
    （サイクル4.2c、§7.5・§8.2）。
    """
    agents = {f"P{i:02d}": _ContractAndTransferBot(f"P{i:02d}", seed=i) for i in range(1, 13)}
    logger = EventLogger()
    game = Game(config=GameConfig.default_12(), agents=agents, seed=42, logger=logger)
    game.run()

    all_entries = [e for entries in game._cash_flow.values() for e in entries]

    def _count_kind(kind: LedgerEntryKind) -> int:
        return sum(1 for e in all_entries if e.kind == kind)

    def _sum_kind(kind: LedgerEntryKind) -> int:
        return sum(e.amount for e in all_entries if e.kind == kind)

    loan_events = [e for e in logger.events if e.event_type == "LOAN_REVEALED"]
    assert len(loan_events) == 1
    assert _count_kind(LedgerEntryKind.INITIAL_LOAN) == 12
    assert _sum_kind(LedgerEntryKind.INITIAL_LOAN) == sum(loan_events[0].data["loans"].values())

    entry_fee_events = [e for e in logger.events if e.event_type == "ENTRY_FEE_COLLECTED"]
    assert len(entry_fee_events) == _count_kind(LedgerEntryKind.ENTRY_FEE)
    assert sum(e.data["paid"] for e in entry_fee_events) == _sum_kind(LedgerEntryKind.ENTRY_FEE)

    extension_fee_events = [e for e in logger.events if e.event_type == "EXTENSION_FEE_COLLECTED"]
    assert len(extension_fee_events) == _count_kind(LedgerEntryKind.EXTENSION_FEE)
    assert sum(e.data["paid"] for e in extension_fee_events) == _sum_kind(LedgerEntryKind.EXTENSION_FEE)

    transfer_events = [e for e in logger.events if e.event_type == "TRANSFER"]
    assert len(transfer_events) == _count_kind(LedgerEntryKind.TRANSFER_OUT)
    assert len(transfer_events) == _count_kind(LedgerEntryKind.TRANSFER_IN)
    assert sum(e.data["amount"] for e in transfer_events) == _sum_kind(LedgerEntryKind.TRANSFER_OUT)
    assert sum(e.data["amount"] for e in transfer_events) == _sum_kind(LedgerEntryKind.TRANSFER_IN)

    payment_events = [e for e in logger.events if e.event_type == "CONTRACT_PAYMENT" and e.data["paid"] > 0]
    assert len(payment_events) == _count_kind(LedgerEntryKind.CONTRACT_PAYMENT_PAID)
    assert len(payment_events) == _count_kind(LedgerEntryKind.CONTRACT_PAYMENT_RECEIVED)
    assert sum(e.data["paid"] for e in payment_events) == _sum_kind(LedgerEntryKind.CONTRACT_PAYMENT_PAID)
    assert sum(e.data["paid"] for e in payment_events) == _sum_kind(LedgerEntryKind.CONTRACT_PAYMENT_RECEIVED)

    payout_events = [e for e in logger.events if e.event_type == "ROUND_PAYOUT"]
    assert len(payout_events) == _count_kind(LedgerEntryKind.POT_PAYOUT)
    assert sum(e.data["amount"] for e in payout_events) == _sum_kind(LedgerEntryKind.POT_PAYOUT)

    interest_events = [
        e for e in logger.events
        if e.event_type == "INTEREST" and (e.data["interest_pre"] > 0 or e.data["interest_post"] > 0)
    ]
    expected_interest_entries = sum(
        (1 if e.data["interest_pre"] > 0 else 0) + (1 if e.data["interest_post"] > 0 else 0)
        for e in interest_events
    )
    assert expected_interest_entries == _count_kind(LedgerEntryKind.INTEREST)
    expected_interest_total = sum(e.data["interest_pre"] + e.data["interest_post"] for e in interest_events)
    assert expected_interest_total == _sum_kind(LedgerEntryKind.INTEREST)


# --- A-4: 同じ精算の中では、支払い・受け取りを先に、借入を最後に記録する ---

def test_settlement_cash_flow_records_payments_before_borrowing() -> None:
    """
    #A-4（サイクル4.2c）: `Game._record_settlement_cash_flow`は、契約の支払い・
    受け取りを先に、「この精算での借入」（SETTLEMENT_BORROWING）を最後に記録
    する。文面はこの記録順をそのまま表示するため、借入の原因になった契約の
    支払いが、借入の行より前に来る必要がある（判定・配当のロジックは
    settle_vote/settle_round側のままで、ここでは記録の順番だけを確認する）。
    """
    agents = make_roster({}, num_players=2)
    config = GameConfig.dev_small(num_players=2, num_rounds=1)
    game = Game(config=config, agents=agents, seed=1, logger=EventLogger())

    movements = [
        CashMovement(
            player_id="P01", kind=LedgerEntryKind.SETTLEMENT_BORROWING,
            amount=50_000, cash_delta=50_000, debt_delta=50_000, borrowed=50_000,
        ),
    ]
    payments = [
        ObligationPayment(
            obligation_id="OB1", contract_id="C1", contract_seq=1, ob_index=0,
            obligor="P01", counterparty="P02", ob_type=ObligationType.TYPE_A_PAYMENT,
            promised=50_000, paid=50_000,
        ),
    ]
    game._record_settlement_cash_flow(
        round_num=1, vote_num=1, phase="settlement",
        movements=movements, payments=payments, scope="vote",
    )

    kinds_p01 = [e.kind for e in game._cash_flow["P01"]]
    assert kinds_p01 == [LedgerEntryKind.CONTRACT_PAYMENT_PAID, LedgerEntryKind.SETTLEMENT_BORROWING]
