"""
見どころ一覧モジュール（サイクル4.3新設、§作るもの7）

動画のネタ探し用に、記録から次の5種を自動で拾う。各項目は場面への直リンクに
必要な情報（試合内での行番号・ラウンド・投票・席）を持つ。

  1. 嘘: 交渉の最後の巡で言っていた予定票（strategy.vote_plan）と、実際に
     提出した票（COMMIT）が違う人
  2. 単独勝ち: 1人だけで山を取った投票（ROUND_RESOLVEDのwinner_idsが1人）
  3. 型Bの違反: TYPE_B_VIOLATIONイベント
  4. 山分けの支払い: CONTRACT_PAYMENTのcondition_type=="pool_share"
  5. やり直し・打ち切り: VOTE_RESOLVED(result="retry")・ROUND_ABORTED

「嘘」の判定は交渉フェイズ（negotiation）の最後の巡のstrategy.vote_planだけを
見る（commitフェイズ自身のvote_planは、投票提出の直前に決めた値であり
ほぼ常に実際の票と一致するため対象外。本番7試合の実測でも交渉フェイズの
最後の巡との比較では複数件の不一致が見つかるが、commitフェイズとの比較では
0件だった）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _index_negotiation_vote_plans(
    index_entries: list[dict[str, Any]],
) -> dict[tuple[int, int, str], tuple[int, str]]:
    """(round_num, vote_num, player_id) -> (最後の巡の巡数, その巡のvote_plan)"""
    best: dict[tuple[int, int, str], tuple[int, str]] = {}
    for e in index_entries:
        if e.get("phase") != "negotiation" or e.get("error_type") or e.get("invalid_response"):
            continue
        strategy = e.get("strategy") or {}
        vote_plan = strategy.get("vote_plan")
        if not vote_plan:
            continue
        rn, vn, pid, turn = e.get("round_num"), e.get("vote_num"), e.get("player_id"), e.get("turn")
        if rn is None or vn is None or pid is None or turn is None:
            continue
        key = (rn, vn, pid)
        prev = best.get(key)
        if prev is None or turn > prev[0]:
            best[key] = (turn, vote_plan)
    return best


def vote_plan_mismatches(
    events: list[dict[str, Any]], index_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """嘘（最後の巡の予定票と実際の票が違う人）の一覧を返す"""
    plans = _index_negotiation_vote_plans(index_entries)
    out = []
    for e in events:
        if e.get("event_type") != "COMMIT":
            continue
        d = e.get("data") or {}
        rn, vn, pid = e.get("round_num"), e.get("vote_num"), d.get("player_id")
        actual = d.get("vote")
        key = (rn, vn, pid)
        if key not in plans:
            continue
        last_turn, planned = plans[key]
        if planned != actual:
            out.append({
                "line": e.get("_line"), "round_num": rn, "vote_num": vn, "player_id": pid,
                "turn": last_turn, "planned_vote": planned, "actual_vote": actual,
            })
    return out


def solo_wins(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """単独勝ち（1人だけで山を取った投票）の一覧"""
    out = []
    for e in events:
        if e.get("event_type") != "ROUND_RESOLVED":
            continue
        d = e.get("data") or {}
        winner_ids = d.get("winner_ids") or []
        if len(winner_ids) == 1:
            out.append({
                "line": e.get("_line"), "round_num": e.get("round_num"),
                "player_id": winner_ids[0], "amount": d.get("payout_per_winner", 0),
            })
    return out


def type_b_violations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """型Bの違反の一覧"""
    out = []
    for e in events:
        if e.get("event_type") != "TYPE_B_VIOLATION":
            continue
        d = e.get("data") or {}
        out.append({
            "line": e.get("_line"), "round_num": e.get("round_num"), "vote_num": e.get("vote_num"),
            "player_ids": d.get("player_ids", []),
        })
    return out


def pool_share_payouts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """山分けの支払いの一覧"""
    out = []
    for e in events:
        if e.get("event_type") != "CONTRACT_PAYMENT":
            continue
        d = e.get("data") or {}
        if d.get("condition_type") != "pool_share":
            continue
        out.append({
            "line": e.get("_line"), "round_num": e.get("round_num"), "vote_num": e.get("vote_num"),
            "contract_id": d.get("contract_id"), "contract_seq": d.get("contract_seq"),
            "obligor": d.get("obligor"), "counterparty": d.get("counterparty"),
            "paid": d.get("paid", 0),
        })
    return out


def retries_and_aborts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """やり直し・打ち切りの一覧"""
    out = []
    for e in events:
        t = e.get("event_type")
        d = e.get("data") or {}
        if t == "VOTE_RESOLVED" and d.get("result") == "retry":
            out.append({
                "line": e.get("_line"), "round_num": e.get("round_num"), "vote_num": e.get("vote_num"),
                "kind": "retry", "extension_fee_collected": d.get("extension_fee_collected", 0),
                "consecutive_ties_after": d.get("consecutive_ties_after"),
            })
        elif t == "ROUND_ABORTED":
            out.append({
                "line": e.get("_line"), "round_num": e.get("round_num"), "vote_num": e.get("vote_num"),
                "kind": "abort", "consecutive_ties": d.get("consecutive_ties"),
            })
    return out


def build_highlights(
    events: list[dict[str, Any]], index_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """見どころ5種をまとめて返す（viewer/log_parser.py::get_highlightsが使う）"""
    return {
        "lies": vote_plan_mismatches(events, index_entries),
        "solo_wins": solo_wins(events),
        "type_b_violations": type_b_violations(events),
        "pool_share_payouts": pool_share_payouts(events),
        "retries_and_aborts": retries_and_aborts(events),
    }
