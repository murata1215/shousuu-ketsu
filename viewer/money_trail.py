"""
お金の出入りの復元モジュール（サイクル4.3新設、v0.4/v0.5対応）

`logs/` の記録イベントだけから、席ごとの現金・借金の時系列と、お金の出入りの
記録（本人のみ、§7.5相当）を1回のイテレーションで作る（判定ロジックを
2か所に書かない、CLAUDE.md過去の落とし穴④）。

`engine.config.GameConfig` の既定値は一切読まない（過去の落とし穴⑦:
ビューアがConfigの既定値を読むと、ルール変更で過去の記録の表示が静かに狂う）。
積み上げる値はすべて記録そのもの（LOAN_REVEALEDの額・ENTRY_FEE_COLLECTEDの
paid/borrowed等）から取る。

積み上げの順（1イベント＝1変化、記録に出てくる順そのまま）:
  LOAN_REVEALED（借入公開）→ ENTRY_FEE_COLLECTED（参加費）→
  EXTENSION_FEE_COLLECTED（延長料）→ ROUND_PAYOUT（山の受け取り）→
  CONTRACT_PAYMENT（契約の支払い。義務者-／相手方+、山分けも同じ扱い）→
  TRANSFER（送金）→ REPAYMENT（返済）→ INTEREST（利息、新しい残高を直接採用）。

CONTRACT_PAYMENT の連続（同じ投票・ラウンドの精算内）で現金が負になった
義務者には、その精算の終わりで「精算での借入」（SETTLEMENT_BORROWING、
engine/settlement.py::apply_payments と同じ規則）を1件補う。これは
PAYABLE_LIMIT_FIXED で事前に上限を固定しているため実際には稀だが、
記録だけからの再現であり、エンジンの内部状態を直接は見ないため必要。

本番7試合（t12_r4_4401b・t12_r4_4501b・t12_r4_4501c・t12_r2_4302・
t12_r1_4301・t12_r2_4201・c42b_r1_4201）で、本人向け財務通知
（FINANCE_NOTICE）・最終資産（GAME_END）と1円単位で一致することを確認済み
（doc/devlog/2026-10-14_*参照）。
"""

from __future__ import annotations

from typing import Any

# 「精算の途中」を示すイベント種別（この種別が続く間はSETTLEMENT_BORROWINGの
# 判定を保留し、精算のまとまりが終わった時点でまとめて負の現金を解消する）
_SETTLEMENT_PAYMENT_TYPES = frozenset({"CONTRACT_PAYMENT"})


def _label_for(kind: str, data: dict[str, Any]) -> str:
    """お金の出入りの記録1件の短い見出し（日本語）"""
    labels = {
        "initial_loan": "開始前の借入",
        "entry_fee": "参加費",
        "extension_fee": "延長料",
        "pot_payout": "山の受け取り",
        "contract_payment_paid": "契約の支払い",
        "contract_payment_received": "契約の受け取り",
        "transfer_out": "送金（支払い）",
        "transfer_in": "送金（受け取り）",
        "repayment": "返済",
        "interest": "利息",
        "settlement_borrowing": "精算での借入",
    }
    return labels.get(kind, kind)


class _Seat:
    __slots__ = ("cash", "debt_pre", "debt_post")

    def __init__(self) -> None:
        self.cash = 0
        self.debt_pre = 0
        self.debt_post = 0

    def asset(self) -> int:
        return self.cash - self.debt_pre - self.debt_post

    def snapshot(self) -> dict[str, int]:
        return {"cash": self.cash, "debt_pre": self.debt_pre, "debt_post": self.debt_post, "asset": self.asset()}


def build_money_trail(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    記録イベントを1回だけ読み、以下の2つを同時に作る。

    Returns:
        {
          "snapshots": [{"round_num", "vote_num", "by_seat": {pid: {cash,debt_pre,debt_post,asset}}}],
              # VOTE_OPEN・ROUND_RESOLVED・GAME_ENDの直後に取ったスナップショット
          "ledger": {pid: [entry, ...]},
              # entry = {"line","round_num","vote_num","phase","kind","label",
              #          "amount","cash_delta","debt_delta","counterparty",
              #          "contract_id","contract_seq","promised","paid"}
          "mismatches": [...],
              # FINANCE_NOTICE/GAME_ENDとの照合でずれが見つかった行（通常は空）
        }
    """
    seats: dict[str, _Seat] = {}
    ledger: dict[str, list[dict[str, Any]]] = {}
    snapshots: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []

    def seat(pid: str) -> _Seat:
        if pid not in seats:
            seats[pid] = _Seat()
        return seats[pid]

    def add_entry(pid: str, *, line: Any, round_num: int, vote_num: int | None, phase: str,
                  kind: str, amount: int, cash_delta: int, debt_delta: int,
                  counterparty: str | None = None, **extra: Any) -> None:
        ledger.setdefault(pid, []).append({
            "line": line, "round_num": round_num, "vote_num": vote_num, "phase": phase,
            "kind": kind, "label": _label_for(kind, extra), "amount": amount,
            "cash_delta": cash_delta, "debt_delta": debt_delta, "counterparty": counterparty,
            **extra,
        })

    def snapshot_now(round_num: int, vote_num: int | None) -> None:
        snapshots.append({
            "round_num": round_num, "vote_num": vote_num,
            "by_seat": {pid: s.snapshot() for pid, s in seats.items()},
        })

    # 精算のまとまり（投票の精算 or ラウンドの精算）の中で現金が負になった
    # 義務者を、まとまりの終わりでまとめて借入処理する
    pending_settlement_negative: set[str] = set()
    in_settlement = False

    def _flush_settlement(round_num: int, vote_num: int | None, phase: str) -> None:
        nonlocal in_settlement
        for pid in sorted(pending_settlement_negative):
            s = seat(pid)
            if s.cash < 0:
                shortfall = -s.cash
                s.debt_post += shortfall
                s.cash = 0
                add_entry(
                    pid, line=None, round_num=round_num, vote_num=vote_num, phase=phase,
                    kind="settlement_borrowing", amount=shortfall,
                    cash_delta=shortfall, debt_delta=shortfall,
                )
        pending_settlement_negative.clear()
        in_settlement = False

    for e in events:
        t = e.get("event_type")
        d = e.get("data") or {}
        rn = e.get("round_num", 0)
        vn = e.get("vote_num")
        phase = e.get("phase", "")
        line = e.get("_line")

        if in_settlement and t not in _SETTLEMENT_PAYMENT_TYPES:
            _flush_settlement(rn, vn, phase)

        if t == "LOAN_REVEALED":
            for pid, amount in d.get("loans", {}).items():
                s = seat(pid)
                s.cash += amount
                s.debt_pre += amount
                add_entry(pid, line=line, round_num=0, vote_num=None, phase="setup",
                          kind="initial_loan", amount=amount, cash_delta=amount, debt_delta=amount)
        elif t == "ENTRY_FEE_COLLECTED":
            pid = d["player_id"]
            paid, borrowed = d.get("paid", 0), d.get("borrowed", 0)
            s = seat(pid)
            s.cash += -(paid - borrowed)
            s.debt_post += borrowed
            add_entry(pid, line=line, round_num=rn, vote_num=None, phase=phase,
                      kind="entry_fee", amount=paid, cash_delta=-(paid - borrowed),
                      debt_delta=borrowed, borrowed=borrowed)
        elif t == "EXTENSION_FEE_COLLECTED":
            pid = d["player_id"]
            paid, borrowed = d.get("paid", 0), d.get("borrowed", 0)
            s = seat(pid)
            s.cash += -(paid - borrowed)
            s.debt_post += borrowed
            add_entry(pid, line=line, round_num=rn, vote_num=vn, phase=phase,
                      kind="extension_fee", amount=paid, cash_delta=-(paid - borrowed),
                      debt_delta=borrowed, borrowed=borrowed)
        elif t == "ROUND_PAYOUT":
            pid = d["player_id"]
            amount = d.get("amount", 0)
            seat(pid).cash += amount
            add_entry(pid, line=line, round_num=rn, vote_num=None, phase=phase,
                      kind="pot_payout", amount=amount, cash_delta=amount, debt_delta=0)
        elif t == "CONTRACT_PAYMENT":
            in_settlement = True
            paid = d.get("paid", 0)
            obligor, counterparty = d.get("obligor"), d.get("counterparty")
            if paid > 0 and obligor and counterparty:
                seat(obligor).cash -= paid
                seat(counterparty).cash += paid
                common = {
                    "contract_id": d.get("contract_id"), "contract_seq": d.get("contract_seq"),
                    "obligation_id": d.get("obligation_id"), "ob_type": d.get("ob_type"),
                    "condition_type": d.get("condition_type"),
                    "promised": d.get("promised"), "paid": paid,
                }
                add_entry(obligor, line=line, round_num=rn, vote_num=vn, phase=phase,
                          kind="contract_payment_paid", amount=paid, cash_delta=-paid, debt_delta=0,
                          counterparty=counterparty, **common)
                add_entry(counterparty, line=line, round_num=rn, vote_num=vn, phase=phase,
                          kind="contract_payment_received", amount=paid, cash_delta=paid, debt_delta=0,
                          counterparty=obligor, **common)
                if seat(obligor).cash < 0:
                    pending_settlement_negative.add(obligor)
        elif t == "TRANSFER":
            pid, to, amount = d["player_id"], d["to"], d.get("amount", 0)
            seat(pid).cash -= amount
            seat(to).cash += amount
            add_entry(pid, line=line, round_num=rn, vote_num=vn, phase=phase,
                      kind="transfer_out", amount=amount, cash_delta=-amount, debt_delta=0, counterparty=to)
            add_entry(to, line=line, round_num=rn, vote_num=vn, phase=phase,
                      kind="transfer_in", amount=amount, cash_delta=amount, debt_delta=0, counterparty=pid)
        elif t == "REPAYMENT":
            pid = d["player_id"]
            actual = d.get("amount", 0)
            from_post = d.get("from_post", 0)
            from_pre = d.get("from_pre", 0)
            s = seat(pid)
            s.cash -= actual
            s.debt_post -= from_post
            s.debt_pre -= from_pre
            if actual > 0:
                add_entry(pid, line=line, round_num=rn, vote_num=vn, phase=phase,
                          kind="repayment", amount=actual, cash_delta=-actual, debt_delta=-actual)
        elif t == "INTEREST":
            pid = d["player_id"]
            s = seat(pid)
            interest_pre, interest_post = d.get("interest_pre", 0), d.get("interest_post", 0)
            s.debt_pre = d.get("new_debt_pre", s.debt_pre)
            s.debt_post = d.get("new_debt_post", s.debt_post)
            if interest_pre > 0:
                add_entry(pid, line=line, round_num=rn, vote_num=None, phase=phase,
                          kind="interest", amount=interest_pre, cash_delta=0, debt_delta=interest_pre,
                          debt_kind="pre")
            if interest_post > 0:
                add_entry(pid, line=line, round_num=rn, vote_num=None, phase=phase,
                          kind="interest", amount=interest_post, cash_delta=0, debt_delta=interest_post,
                          debt_kind="post")
        elif t == "VOTE_OPEN":
            snapshot_now(rn, vn)
        elif t == "FINANCE_NOTICE":
            pid = d.get("player_id")
            s = seats.get(pid)
            if s is not None:
                actual = (s.cash, s.debt_pre, s.debt_post)
                expected = (d.get("cash", 0), d.get("debt_pre", 0), d.get("debt_post", 0))
                if actual != expected:
                    mismatches.append({
                        "line": line, "round_num": rn, "vote_num": vn, "player_id": pid,
                        "reconstructed": actual, "recorded": expected,
                    })
        elif t == "ROUND_RESOLVED":
            snapshot_now(rn, None)
        elif t == "GAME_END":
            for pid, expected in d.get("final_assets", {}).items():
                s = seats.get(pid)
                actual = s.asset() if s is not None else None
                if actual != expected:
                    mismatches.append({
                        "line": line, "round_num": rn, "vote_num": None, "player_id": pid,
                        "reconstructed": actual, "recorded": expected, "scope": "final",
                    })
            snapshot_now(rn, None)

    if in_settlement:
        _flush_settlement(events[-1].get("round_num", 0) if events else 0, None, "round_settlement")

    return {"snapshots": snapshots, "ledger": ledger, "mismatches": mismatches}


def asset_timeline_from_trail(trail: dict[str, Any], seat_ids: list[str]) -> dict[str, Any]:
    """
    概要タブ用: build_money_trail()のsnapshotsを「ラウンド軸・席ごとの資産推移」
    の形に畳む（各ラウンドの最後のスナップショット＝ラウンド終了直後の値を使う）。
    """
    by_round: dict[int, dict[str, int]] = {}
    for snap in trail["snapshots"]:
        if snap["vote_num"] is not None:
            continue  # ラウンド終了時点のスナップショットだけを使う
        rn = snap["round_num"]
        by_round[rn] = {pid: v["asset"] for pid, v in snap["by_seat"].items()}
    rounds = sorted(by_round)
    return {
        "rounds": rounds,
        "by_seat": {pid: [by_round[rn].get(pid) for rn in rounds] for pid in seat_ids},
    }
