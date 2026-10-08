"""
ログパーサ — JSONL→構造化データ（サイクル3.0新設）

`logs/llm/{game_id}_events.jsonl` を読み取り専用で読み、ビューア用の構造化
データに変換する。ファイルは書き換えない。

`scripts/summarize_trial.py`（サイクル2.0/2.1）にある集計関数
（`summarize_roster`/`summarize_contracts`/`summarize_final`/
`summarize_post_game_reflections`/`short_model_label`等）を書き換えずに
importして使う。内心メモ・strategyは`viewer/log_index.py`が作る軽い索引
（prompt全文を持たない）から取る。

発言本文・内心メモ・振り返りなど、AIの出力に由来する値は、API応答として返す
直前に必ず`viewer.redact.redact_value`を通す（§データの扱い）。

資産推移はイベントに記録されていない（§7.2「本人だけに届く財務通知」のため）。
LOAN_REVEALED → ENTRY_FEE_COLLECTED → MINORITY_RESOLVED（配当＋参加費の戻り）
→ CONTRACT_PAYMENT → INTEREST の順に現金・借金を畳み込んで復元する
（`fold_assets`。本戦l12r12_2002の全12ラウンドでGAME_END/RANK_PUBLISHEDと
1円単位で一致済み、doc/analysis/l12r12_2002_facts.md「検証」参照）。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from engine.config import GameConfig
from scripts.summarize_trial import (
    short_model_label,
    summarize_contracts,
    summarize_final,
    summarize_post_game_reflections,
    summarize_roster,
)
from viewer.log_index import load_or_build_index
from viewer.redact import redact_value

# 本戦・Bot試合いずれも既定の参加費（§4.1/§10）で動いている前提
# （GameConfig()はコンストラクタ引数なしなら§10の確定値を返す）。
_ENTRY_FEE = GameConfig().entry_fee

# 試合一覧に出す条件（§作るもの1「席の割り当てがあるものだけ」）:
# seat_mapの席数がこの値以上であること。Bot試合・動作確認（smoke4=4席、
# preflight=0件）を自動的に除外する（仕様書に無い判断。完了報告に記載）。
_MIN_SEATS_FOR_MATCH = 12


class LogCache:
    """ファイル差分キャッシュ（mtime+sizeベース。gentei-janken viewer/log_parser.py::LogCacheと同じ方式）"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, int, list[dict[str, Any]]]] = {}

    def read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        key = str(path)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return []
        cached = self._cache.get(key)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]

        events: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 書きかけの最終行はスキップ
        self._cache[key] = (stat.st_mtime, stat.st_size, events)
        return events


_cache = LogCache()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _events(log_dir: Path, game_id: str) -> list[dict[str, Any]]:
    return _cache.read_jsonl(log_dir / f"{game_id}_events.jsonl")


def _seat_map(log_dir: Path, game_id: str) -> dict[str, str]:
    return _load_json(log_dir / f"{game_id}_seat_map.json")


def _has_transcript(events: list[dict[str, Any]]) -> bool:
    """発言本文がイベントに残っているか（§データの扱い「古い記録は会話は表示できません」）"""
    return any(
        e.get("event_type") == "NEGOTIATION_ACTION" and e.get("data", {}).get("message") is not None
        for e in events
    )


def list_games(log_dir: Path) -> list[dict[str, Any]]:
    """試合一覧を返す（席の割り当てがある試合だけ、新しい順）"""
    if not log_dir.exists():
        return []
    rows = []
    for path in log_dir.glob("*_events.jsonl"):
        game_id = path.name[: -len("_events.jsonl")]
        seat_map = _seat_map(log_dir, game_id)
        if len(seat_map) < _MIN_SEATS_FOR_MATCH:
            continue
        events = _events(log_dir, game_id)
        if not any(e.get("event_type") == "GAME_START" for e in events):
            continue
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        completed = any(e.get("event_type") == "GAME_END" for e in events)
        final = summarize_final(events) if completed else None
        winner_pid = None
        if final:
            ranks = final.get("final_ranks", {})
            winner_pid = next((pid for pid, r in ranks.items() if r == 1), None)
        rows.append({
            "game_id": game_id,
            "num_players": len(seat_map),
            "last_round": max((e.get("round_num", 0) for e in events), default=0),
            "completed": completed,
            "transcript_available": _has_transcript(events),
            "winner_player_id": winner_pid,
            "winner_model_label": short_model_label(seat_map.get(winner_pid)) if winner_pid else None,
            "_mtime": mtime,
        })
    rows.sort(key=lambda r: r["_mtime"], reverse=True)
    for r in rows:
        r.pop("_mtime")
    return rows


def fold_assets(events: list[dict[str, Any]]) -> dict[int, dict[str, dict[str, int]]]:
    """
    各ラウンドのFinance（利息計上）後の、席ごとの現金・借金・資産を畳み込みで復元する

    Returns:
        {round_num: {player_id: {"cash": int, "debt": int, "asset": int}}}
    """
    cash: dict[str, int] = defaultdict(int)
    debt: dict[str, int] = {}
    snapshots: dict[int, dict[str, dict[str, int]]] = {}

    for e in events:
        t = e.get("event_type")
        d = e.get("data") or {}
        rn = e.get("round_num", 0)

        if t == "LOAN_REVEALED":
            for pid, amount in d.get("loans", {}).items():
                cash[pid] = amount
                debt[pid] = amount
        elif t == "ENTRY_FEE_COLLECTED":
            cash[d["player_id"]] += -d.get("paid", 0) + d.get("borrowed", 0)
        elif t == "MINORITY_RESOLVED":
            for pid in d.get("minority_ids", []):
                cash[pid] += _ENTRY_FEE + d.get("payout_per_minority", 0)
        elif t == "CONTRACT_PAYMENT":
            cash[d["obligor"]] -= d.get("paid", 0)
            cash[d["counterparty"]] += d.get("paid", 0)
        elif t == "INTEREST":
            pid = d["player_id"]
            debt[pid] = d.get("new_debt_pre", 0) + d.get("new_debt_post", 0)
            snap = snapshots.setdefault(rn, {})
            snap[pid] = {
                "cash": cash[pid],
                "debt": debt.get(pid, 0),
                "asset": cash[pid] - debt.get(pid, 0),
            }
    return snapshots


def _round_overviews(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ラウンドごとの票の割れ方・配当・持ち越し・成立契約の概要（席ページ・試合ページ共通）"""
    by_round: dict[int, dict[str, Any]] = {}

    def _row(rn: int) -> dict[str, Any]:
        return by_round.setdefault(rn, {
            "round_num": rn, "question": None, "carryover_before": 0,
            "votes": {}, "yes_count": 0, "no_count": 0,
            "minority_side": None, "minority_ids": [], "payout_per_minority": 0,
            "carryover_after": 0, "auto_commit_ids": [],
            "established_contract_seqs": [],
        })

    for e in events:
        t = e.get("event_type")
        rn = e.get("round_num", 0)
        if rn <= 0:
            continue
        d = e.get("data") or {}
        if t == "ROUND_OPEN":
            row = _row(rn)
            row["question"] = d.get("question")
            row["carryover_before"] = d.get("carryover", 0)
        elif t == "VOTE_REVEALED":
            row = _row(rn)
            votes = d.get("votes", {})
            row["votes"] = votes
            row["yes_count"] = sum(1 for v in votes.values() if v == "YES")
            row["no_count"] = sum(1 for v in votes.values() if v == "NO")
        elif t == "MINORITY_RESOLVED":
            row = _row(rn)
            row["minority_side"] = d.get("minority_side")
            row["minority_ids"] = d.get("minority_ids", [])
            row["payout_per_minority"] = d.get("payout_per_minority", 0)
            row["carryover_after"] = d.get("carryover_after", 0)
        elif t == "AUTO_COMMIT":
            _row(rn)["auto_commit_ids"].append(d.get("player_id"))
        elif t == "CONTRACT_ESTABLISHED":
            _row(rn)["established_contract_seqs"].append(d.get("contract_seq"))

    return [by_round[rn] for rn in sorted(by_round)]


def _accident_summary(log_dir: Path, game_id: str, events: list[dict[str, Any]]) -> dict[str, int]:
    """事故の件数（時間切れ・サーバーエラー・読み取りエラー・自動代行）"""
    entries = load_or_build_index(game_id, log_dir)
    return {
        "timeouts": sum(1 for e in entries if e.get("error_type") == "timeout"),
        "server_errors": sum(1 for e in entries if e.get("error_type") == "server_error"),
        "invalid_responses": sum(1 for e in entries if e.get("invalid_response")),
        "auto_commits": sum(1 for e in events if e.get("event_type") == "AUTO_COMMIT"),
    }


def get_overview(log_dir: Path, game_id: str, *, redact: bool = True) -> dict[str, Any] | None:
    """試合ページ: 席・モデル・借入額・最終資産・順位、12ラウンドの流れ、資産推移、事故件数"""
    events = _events(log_dir, game_id)
    if not events:
        return None
    seat_map = _seat_map(log_dir, game_id)

    final = summarize_final(events)
    final_assets = (final or {}).get("final_assets", {})
    final_ranks = (final or {}).get("final_ranks", {})
    roster = [
        {
            "player_id": r["player_id"],
            "model_id": r["model_id"],
            "model_label": short_model_label(r["model_id"]),
            "loan": r["loan"],
            "final_assets": final_assets.get(r["player_id"]),
            "final_rank": final_ranks.get(r["player_id"]),
        }
        for r in summarize_roster(seat_map, events)
    ]

    rounds = _round_overviews(events)
    asset_snapshots = fold_assets(events)
    round_axis = sorted(asset_snapshots)
    asset_timeline = {
        pid: [asset_snapshots[rn].get(pid, {}).get("asset") for rn in round_axis]
        for pid in seat_map
    }
    contracts = summarize_contracts(events)

    result = {
        "game_id": game_id,
        "completed": final is not None,
        "num_rounds": max((r["round_num"] for r in rounds), default=0),
        "roster": roster,
        "rounds": rounds,
        "asset_timeline": {"rounds": round_axis, "by_seat": asset_timeline},
        "established_contracts": len(contracts["established"]),
        "contract_payments": len(contracts["payments"]),
        "accidents": _accident_summary(log_dir, game_id, events),
        "transcript_available": _has_transcript(events),
    }
    return redact_value(result) if redact else result


_INDEXED_PHASE = "negotiation"


def _index_by_round_player_turn(entries: list[dict[str, Any]]) -> dict[tuple[int, str, int], list[dict[str, Any]]]:
    """(round_num, player_id, turn) -> その手番で実際に成功した応答群（索引エントリ、ファイル順）"""
    idx: dict[tuple[int, str, int], list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        if e.get("phase") != _INDEXED_PHASE or e.get("error_type") or e.get("invalid_response"):
            continue
        key = (e.get("round_num"), e.get("player_id"), e.get("turn"))
        if None in key:
            continue
        idx[key].append(e)
    return idx


def _memo_for_action(
    idx: dict[tuple[int, str, int], list[dict[str, Any]]],
    round_num: int, pid: str | None, turn: int | None, action_type: str | None,
) -> dict[str, Any] | None:
    """その行動に対応する内心メモ（strategy）を索引から引く。見つからなければNone"""
    if pid is None or turn is None:
        return None
    candidates = idx.get((round_num, pid, turn), [])
    for e in reversed(candidates):
        action = e.get("action") or {}
        if action.get("type") == action_type:
            return e.get("strategy")
    if candidates:
        return candidates[-1].get("strategy")
    return None


def _round_accidents(entries: list[dict[str, Any]], round_num: int) -> list[dict[str, Any]]:
    """記録上の事故（このラウンドのLLM呼び出しに限定）"""
    out = []
    for e in entries:
        if e.get("round_num") != round_num:
            continue
        if e.get("error_type"):
            out.append({
                "kind": e["error_type"], "player_id": e.get("player_id"),
                "turn": e.get("turn"), "phase": e.get("phase"),
            })
        elif e.get("invalid_response"):
            out.append({
                "kind": "invalid_response", "player_id": e.get("player_id"),
                "turn": e.get("turn"), "phase": e.get("phase"), "reason": e.get("reason"),
            })
    return out


def get_round(log_dir: Path, game_id: str, round_num: int, *, redact: bool = True) -> dict[str, Any] | None:
    """ラウンドのページ: 交渉の時系列・内心メモ・投票公開・契約の決済・事故の目印"""
    events = _events(log_dir, game_id)
    if not events:
        return None
    seat_map = _seat_map(log_dir, game_id)
    max_round = max((e.get("round_num", 0) for e in events), default=0)
    if max_round <= 0:
        return None
    round_num = max(1, min(round_num, max_round))  # 範囲外は最も近い有効値にクランプ

    index_entries = load_or_build_index(game_id, log_dir)
    neg_idx = _index_by_round_player_turn(index_entries)
    round_reflections = {
        e["player_id"]: e.get("memory")
        for e in index_entries
        if e.get("phase") == "reflect" and e.get("round_num") == round_num and e.get("memory")
    }

    question = None
    carryover_before = 0
    timeline: list[dict[str, Any]] = []
    pending_vote: dict[str, Any] | None = None

    for e in events:
        if e.get("round_num") != round_num:
            continue
        t = e.get("event_type")
        d = e.get("data") or {}

        if t == "ROUND_OPEN":
            question = d.get("question")
            carryover_before = d.get("carryover", 0)
            continue

        if t == "NEGOTIATION_ACTION":
            action = d.get("action")
            pid = d.get("player_id")
            turn = d.get("turn")
            memo = _memo_for_action(neg_idx, round_num, pid, turn, action) if action in ("dm", "broadcast", "pass") else None
            timeline.append({
                "kind": "negotiation", "turn": turn, "player_id": pid, "action": action,
                "to": d.get("to"), "message": d.get("message"),
                "success": d.get("success"), "reason": d.get("reason"), "memo": memo,
            })
        elif t == "CONTRACT_PROPOSED":
            timeline.append({
                "kind": "contract_propose", "turn": d.get("turn"), "contract_id": d.get("contract_id"),
                "proposer": d.get("proposer"), "parties": d.get("parties"), "obligations": d.get("obligations"),
            })
        elif t == "CONTRACT_SIGNED":
            timeline.append({
                "kind": "contract_sign", "turn": d.get("turn"),
                "contract_id": d.get("contract_id"), "signer": d.get("signer"),
            })
        elif t == "CONTRACT_ESTABLISHED":
            timeline.append({
                "kind": "contract_established", "turn": None, "contract_id": d.get("contract_id"),
                "parties": d.get("parties"), "contract_seq": d.get("contract_seq"),
            })
        elif t == "CONTRACT_EXPIRED":
            timeline.append({
                "kind": "contract_expired", "turn": None, "contract_id": d.get("contract_id"),
                "proposer": d.get("proposer"), "parties": d.get("parties"), "signed_by": d.get("signed_by"),
            })
        elif t == "VOTE_REVEALED":
            pending_vote = {
                "kind": "vote_result", "turn": None, "votes": d.get("votes", {}),
                "minority_side": None, "minority_ids": [], "payout_per_minority": 0, "carryover_after": None,
            }
            timeline.append(pending_vote)
        elif t == "MINORITY_RESOLVED":
            if pending_vote is None:
                pending_vote = {"kind": "vote_result", "turn": None, "votes": {}}
                timeline.append(pending_vote)
            pending_vote.update({
                "minority_side": d.get("minority_side"), "minority_ids": d.get("minority_ids", []),
                "payout_per_minority": d.get("payout_per_minority", 0),
                "carryover_after": d.get("carryover_after", 0),
                "forfeited_remainder": d.get("forfeited_remainder", 0),
            })
        elif t == "CARRYOVER":
            timeline.append({
                "kind": "carryover", "turn": None,
                "carryover_after": d.get("carryover_after"), "destroyed": d.get("destroyed"),
            })
        elif t == "CONTRACT_PAYMENT":
            timeline.append({
                "kind": "contract_payment", "turn": None, "contract_id": d.get("contract_id"),
                "contract_seq": d.get("contract_seq"), "obligor": d.get("obligor"),
                "counterparty": d.get("counterparty"), "ob_type": d.get("ob_type"),
                "promised": d.get("promised"), "paid": d.get("paid"),
            })
        elif t == "INTEREST":
            timeline.append({
                "kind": "interest", "turn": None, "player_id": d.get("player_id"),
                "interest_pre": d.get("interest_pre"), "interest_post": d.get("interest_post"),
                "new_debt_pre": d.get("new_debt_pre"), "new_debt_post": d.get("new_debt_post"),
            })
        elif t == "AUTO_COMMIT":
            timeline.append({
                "kind": "accident", "accident_type": "auto_commit", "turn": None,
                "player_id": d.get("player_id"), "vote": d.get("vote"),
            })
        elif t == "TYPE_B_VIOLATION":
            timeline.append({"kind": "type_b_violation", "turn": None, "player_ids": d.get("player_ids", [])})
        elif t == "PAYMENT_SHORTFALL":
            timeline.append({"kind": "payment_shortfall", "turn": None, "player_ids": d.get("player_ids", [])})
        elif t == "COMMIT":
            timeline.append({"kind": "commit", "turn": None, "player_id": d.get("player_id"), "vote": d.get("vote")})
        elif t == "ENTRY_FEE_COLLECTED":
            timeline.append({
                "kind": "entry_fee", "turn": None, "player_id": d.get("player_id"),
                "paid": d.get("paid"), "borrowed": d.get("borrowed"),
            })
        elif t == "RANK_NOTIFIED":
            timeline.append({
                "kind": "rank_notified", "turn": None, "player_id": d.get("player_id"),
                "rank": d.get("rank"), "tied": d.get("tied"),
            })
        elif t == "RANK_PUBLISHED":
            timeline.append({"kind": "rank_published", "turn": None, "ranks": d.get("ranks", {})})

    result = {
        "game_id": game_id, "round_num": round_num, "max_round": max_round,
        "question": question, "carryover_before": carryover_before,
        "timeline": timeline, "round_reflections": round_reflections,
        "accidents": _round_accidents(index_entries, round_num),
        "seat_ids": sorted(seat_map),
    }
    return redact_value(result) if redact else result


def _obligation_outcome(
    ob: dict[str, Any], ob_index: int, contract_id: str,
    payments_by_key: dict[tuple[str, int], dict[str, Any]], max_settled_round: int,
) -> str:
    """
    義務1本の結果を判定する（§結果区分: 履行／違約金支払い／支払い発生／条件が外れた）

    settlement.py::_collect_due_items() の実挙動に合わせる: 違反しなかった型B・
    条件が成立しなかった型CはCONTRACT_PAYMENTイベント自体が作られない（＝払い自体が
    無い）ため、「そのラウンドが決済済み ∧ 支払いイベントが無い」で判定できる
    （票の値を読み直して再判定するのではなく、エンジンの実際の判定結果だけを見る）。
    """
    payment = payments_by_key.get((contract_id, ob_index))
    ob_type = ob.get("ob_type")
    round_num = ob.get("round_num")
    settled = round_num is not None and round_num <= max_settled_round

    if ob_type == "type_b_vote":
        if payment is not None:
            return "違約金支払い"
        return "履行" if settled else "未定（今後）"
    if ob_type == "type_c_conditional":
        if payment is not None:
            return "支払い発生"
        return "条件が外れた" if settled else "未定（今後）"
    if ob_type == "type_a_payment":
        if payment is not None:
            return "支払い発生" if payment.get("paid", 0) >= payment.get("promised", 0) else "部分払い"
        return "履行なし" if settled else "未定（今後）"
    return "不明"


def get_contracts(log_dir: Path, game_id: str, *, redact: bool = True) -> dict[str, Any] | None:
    """契約の一覧: 成立順・当事者・義務の中身・結果。提案だけで不成立の分は別枠"""
    events = _events(log_dir, game_id)
    if not events:
        return None

    proposed: dict[str, dict[str, Any]] = {}
    established_seq: dict[str, int | None] = {}
    established_round: dict[str, int] = {}
    expired: list[dict[str, Any]] = []
    payments_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    max_settled_round = 0

    for e in events:
        t = e.get("event_type")
        d = e.get("data") or {}
        rn = e.get("round_num", 0)
        if t == "CONTRACT_PROPOSED":
            proposed[d["contract_id"]] = d
        elif t == "CONTRACT_ESTABLISHED":
            established_seq[d["contract_id"]] = d.get("contract_seq")
            established_round[d["contract_id"]] = rn
        elif t == "CONTRACT_EXPIRED":
            expired.append(d)
        elif t == "CONTRACT_PAYMENT":
            ob_index = d.get("ob_index")
            if ob_index is not None:
                payments_by_key[(d["contract_id"], ob_index)] = d
        elif t == "VOTE_REVEALED":
            max_settled_round = max(max_settled_round, rn)

    established = []
    for contract_id, seq in established_seq.items():
        prop = proposed.get(contract_id, {})
        obligations = []
        for i, ob in enumerate(prop.get("obligations", [])):
            outcome = _obligation_outcome(ob, i, contract_id, payments_by_key, max_settled_round)
            obligations.append({**ob, "outcome": outcome})
        established.append({
            "contract_id": contract_id,
            "contract_seq": seq,
            "round_established": established_round.get(contract_id),
            "proposer": prop.get("proposer"),
            "parties": prop.get("parties"),
            "obligations": obligations,
        })
    established.sort(key=lambda c: (c["contract_seq"] is None, c["contract_seq"]))

    result = {
        "game_id": game_id,
        "established": established,
        "expired": expired,
        "proposed_total": len(proposed),
    }
    return redact_value(result) if redact else result


def get_seat(log_dir: Path, game_id: str, pid: str, *, redact: bool = True) -> dict[str, Any] | None:
    """席のページ: 票・発言・契約・資産推移・ラウンドごとの振り返り・試合後の振り返り"""
    events = _events(log_dir, game_id)
    if not events:
        return None
    seat_map = _seat_map(log_dir, game_id)
    if pid not in seat_map:
        return None

    votes: dict[int, str] = {}
    messages: list[dict[str, Any]] = []
    contract_ids: set[str] = set()

    for e in events:
        t = e.get("event_type")
        d = e.get("data") or {}
        rn = e.get("round_num", 0)
        if t in ("COMMIT", "AUTO_COMMIT") and d.get("player_id") == pid:
            votes[rn] = d.get("vote")
        elif t == "NEGOTIATION_ACTION":
            action = d.get("action")
            if action == "broadcast" and d.get("player_id") == pid:
                messages.append({
                    "round_num": rn, "turn": d.get("turn"), "kind": "broadcast",
                    "from": pid, "to": None, "message": d.get("message"),
                })
            elif action == "dm" and (d.get("player_id") == pid or d.get("to") == pid):
                messages.append({
                    "round_num": rn, "turn": d.get("turn"), "kind": "dm",
                    "from": d.get("player_id"), "to": d.get("to"), "message": d.get("message"),
                })
        elif t in ("CONTRACT_PROPOSED", "CONTRACT_ESTABLISHED") and pid in (d.get("parties") or []):
            contract_ids.add(d["contract_id"])

    asset_snapshots = fold_assets(events)
    asset_timeline = [
        {"round_num": rn, **asset_snapshots[rn][pid]}
        for rn in sorted(asset_snapshots) if pid in asset_snapshots[rn]
    ]

    index_entries = load_or_build_index(game_id, log_dir)
    round_memos = {
        e["round_num"]: e.get("memory")
        for e in index_entries
        if e.get("phase") == "reflect" and e.get("player_id") == pid and e.get("memory")
    }

    final = summarize_final(events) or {}
    post_game = summarize_post_game_reflections(events).get(pid)

    result = {
        "game_id": game_id,
        "player_id": pid,
        "model_id": seat_map.get(pid),
        "model_label": short_model_label(seat_map.get(pid)),
        "votes": votes,
        "messages": messages,
        "contract_ids": sorted(contract_ids),
        "asset_timeline": asset_timeline,
        "round_memos": round_memos,
        "post_game_reflection": post_game,
        "final_assets": (final.get("final_assets") or {}).get(pid),
        "final_rank": (final.get("final_ranks") or {}).get(pid),
    }
    return redact_value(result) if redact else result
