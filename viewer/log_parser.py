"""
ログパーサ — JSONL→構造化データ（サイクル3.0新設、サイクル4.3でv0.4/v0.5対応に作り直し）

`logs/{game_id}_events.jsonl` を読み取り専用で読み、ビューア用の構造化
データに変換する。ファイルは書き換えない。

サイクル4.3: v0.4（ラウンドの中に投票が1〜6回入る二重構造、§1.1）・v0.5
（型Cに山分け pool_share、§6.4）に対応させた。v0.3形式の記録
（`ROUND_OPEN`/`MINORITY_RESOLVED`が存在する。`scripts/summarize_trial.py`
と同じ判定基準）は「旧形式」として一覧には残すが、詳細は席・モデル・
最終資産・順位だけを返す（CLAUDE.md過去の落とし穴⑦・⑨: ルールの意味が
変わった記録を同じ関数で両対応させると、どちらの形式として読んでいるかが
分からなくなる）。

`scripts/summarize_trial.py`（サイクル2.0/2.1/4.2b）にある集計関数
（`summarize_roster`/`summarize_final`/`summarize_post_game_reflections`/
`short_model_label`等）を書き換えずにimportして使う。内心メモ・strategyは
`viewer/log_index.py`が作る軽い索引（prompt全文を持たない）から取る。

発言本文・内心メモ・振り返りなど、AIの出力に由来する値は、API応答として返す
直前に必ず`viewer.redact.redact_value`を通す（§データの扱い）。

お金の推移・お金の出入りの記録は`viewer/money_trail.py::build_money_trail`
（記録イベントだけから復元、Configの既定値を参照しない）に一本化した
（CLAUDE.md過去の落とし穴⑦の再発防止）。

試合ごとに、記録中のイベント行番号（1始まり、ファイルの行そのもの）を
`_line`として全イベントに付与する。場面への直リンクの`event=`パラメータは
この行番号を指す。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.summarize_trial import (
    short_model_label,
    summarize_contracts,
    summarize_final,
    summarize_post_game_reflections,
    summarize_roster,
)
from viewer.highlights import build_highlights
from viewer.log_index import load_or_build_index
from viewer.money_trail import asset_timeline_from_trail, build_money_trail
from viewer.redact import redact_value

# 試合一覧に出す条件（§作るもの1「席の割り当てがあるものだけ」）:
# seat_mapの席数がこの値以上であること。Bot試合・動作確認（smoke4=4席、
# preflight=0件）を自動的に除外する（仕様書に無い判断。完了報告に記載）。
_MIN_SEATS_FOR_MATCH = 12

# v0.3形式の記録を検出するための種別（v0.4には存在しない。
# scripts/summarize_trial.py::_V0_3_ONLY_EVENT_TYPESと同じ基準）
_V0_3_ONLY_EVENT_TYPES = frozenset({"ROUND_OPEN", "MINORITY_RESOLVED"})

# ビューアが扱うイベント種別の全数（tests/test_event_visibility.py::
# FIXED_VISIBILITYのキー + NEGOTIATION_ACTION）。新しいイベント種別を
# engine側に足したら、ここにも追加しないとtest_viewer_event_coverage.pyが
# 落ちる（CLAUDE.md過去の落とし穴⑩と同型の対策）。
KNOWN_EVENT_TYPES = frozenset({
    "GAME_START", "LOAN_REVEALED", "GAME_END", "POST_GAME_REFLECTION",
    "ROUND_START", "ENTRY_FEE_COLLECTED", "POT_UPDATED", "VOTE_OPEN",
    "RANK_NOTIFIED", "FINANCE_NOTICE", "TURN_CONTRACTS_ESTABLISHED",
    "CONTRACT_REJECTED", "CONTRACT_EXPIRED", "CONTRACT_PROPOSED",
    "CONTRACT_SIGNED", "CONTRACT_ESTABLISHED", "TRANSFER", "REPAYMENT",
    "AUTO_COMMIT", "COMMIT", "RANK_PUBLISHED", "NEGOTIATION_EARLY_END",
    "VOTE_REVEALED", "EXTENSION_FEE_COLLECTED", "VOTE_RESOLVED",
    "ROUND_ABORTED", "TYPE_B_VIOLATION", "PAYABLE_LIMIT_FIXED",
    "CONTRACT_PAYMENT", "PAYMENT_SHORTFALL", "ROUND_RESOLVED",
    "ROUND_PAYOUT", "INTEREST", "NEGOTIATION_ACTION",
})

# =============================================================================
# 置き場（複数対応、サイクル4.3）
# =============================================================================

def _as_roots(log_dir: Path | list[Path] | tuple[Path, ...]) -> list[Path]:
    """単一Pathでも複数指定でも受ける（既存テスト・サーバー両対応）"""
    if isinstance(log_dir, (list, tuple)):
        return list(log_dir)
    return [log_dir]


def _find_root_for_game(log_dir: Path | list[Path], game_id: str) -> Path | None:
    """game_idの記録がある置き場を順に探す（最初に見つかったもの）"""
    for root in _as_roots(log_dir):
        if (root / f"{game_id}_events.jsonl").exists():
            return root
    return None


def _index_for(log_dir: Path | list[Path], game_id: str) -> list[dict[str, Any]]:
    """game_idのLLM呼び出し索引を読む（置き場が見つからなければ空）"""
    root = _find_root_for_game(log_dir, game_id)
    if root is None:
        return []
    return load_or_build_index(game_id, root)


# =============================================================================
# ファイルI/O（mtime+sizeキャッシュ）
# =============================================================================

class LogCache:
    """ファイル差分キャッシュ（mtime+sizeベース。gentei-janken viewer/log_parser.py::LogCacheと同じ方式）"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, int, list[dict[str, Any]]]] = {}

    def read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        """JSONLを読み、各イベントに`_line`（1始まりのファイル内行番号）を付ける"""
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
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 書きかけの最終行はスキップ
                event["_line"] = line_no
                events.append(event)
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


def _events(log_dir: Path | list[Path], game_id: str) -> list[dict[str, Any]]:
    root = _find_root_for_game(log_dir, game_id)
    if root is None:
        return []
    return _cache.read_jsonl(root / f"{game_id}_events.jsonl")


def _seat_map(log_dir: Path | list[Path], game_id: str) -> dict[str, str]:
    root = _find_root_for_game(log_dir, game_id)
    if root is None:
        return {}
    return _load_json(root / f"{game_id}_seat_map.json")


def _has_transcript(events: list[dict[str, Any]]) -> bool:
    """発言本文がイベントに残っているか（§データの扱い「古い記録は会話は表示できません」）"""
    return any(
        e.get("event_type") == "NEGOTIATION_ACTION" and e.get("data", {}).get("message") is not None
        for e in events
    )


def is_v0_3_format(events: list[dict[str, Any]]) -> bool:
    """v0.3形式の記録（ROUND_OPEN/MINORITY_RESOLVED）かどうか"""
    return bool({e.get("event_type") for e in events} & _V0_3_ONLY_EVENT_TYPES)


def list_games(log_dir: Path | list[Path]) -> list[dict[str, Any]]:
    """
    試合一覧を返す（席の割り当てがある試合だけ、新しい順）

    複数の置き場を指定できる（サイクル4.3: logs/llm_v0_4とlogs/llmを両方見る）。
    同じgame_idが複数の置き場にあれば、先に見つかった置き場を使う。
    v0.3形式の記録は除外しない。`"format": "v0.3"`の札を付けて返す
    （詳細（ラウンド・契約等）は席・モデル・最終資産・順位だけに制限する、
    CLAUDE.md過去の落とし穴⑦・⑨の再発防止。§v0.3の試合の扱い案B）。
    """
    roots = [r for r in _as_roots(log_dir) if r.exists()]
    rows = []
    seen_ids: set[str] = set()
    for root in roots:
        for path in root.glob("*_events.jsonl"):
            game_id = path.name[: -len("_events.jsonl")]
            if game_id in seen_ids:
                continue
            seat_map = _load_json(root / f"{game_id}_seat_map.json")
            if len(seat_map) < _MIN_SEATS_FOR_MATCH:
                continue
            events = _cache.read_jsonl(path)
            if not any(e.get("event_type") == "GAME_START" for e in events):
                continue
            seen_ids.add(game_id)
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
            legacy = is_v0_3_format(events)
            num_rounds = max((e.get("round_num", 0) for e in events), default=0)
            num_votes = 0 if legacy else max((e.get("vote_num") or 0 for e in events), default=0)
            rows.append({
                "game_id": game_id,
                "format": "v0.3" if legacy else "v0.4",
                "num_players": len(seat_map),
                "last_round": num_rounds,
                "last_vote": num_votes,
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


# =============================================================================
# 概要タブ（§作るもの1）
# =============================================================================

def _round_vote_summaries(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    ラウンドごとに、各投票の票の割れ方・決着/やり直し/打ち切りと、
    ラウンドの勝ち残り・受取額・打ち切りと持ち越しをまとめる（概要タブ用）
    """
    by_round: dict[int, dict[str, Any]] = {}
    votes_by_round: dict[int, dict[int, dict[str, Any]]] = {}

    def _row(rn: int) -> dict[str, Any]:
        return by_round.setdefault(rn, {
            "round_num": rn, "carryover_in": 0, "pot_initial": 0,
            "aborted": False, "winner_ids": [], "payout_per_winner": 0,
            "carryover_out": 0, "destroyed_pot": 0, "pot_final": 0,
            "established_contract_seqs": [],
        })

    def _vote(rn: int, vn: int) -> dict[str, Any]:
        return votes_by_round.setdefault(rn, {}).setdefault(vn, {
            "vote_num": vn, "question": None, "votes": {}, "yes_count": 0, "no_count": 0,
            "result": None, "eliminated_ids": [], "remaining_ids": [],
            "extension_fee_collected": 0,
        })

    for e in events:
        t = e.get("event_type")
        rn = e.get("round_num", 0)
        vn = e.get("vote_num")
        d = e.get("data") or {}
        if rn <= 0:
            continue
        if t == "ROUND_START":
            row = _row(rn)
            row["carryover_in"] = d.get("carryover_in", 0)
            row["pot_initial"] = d.get("pot", 0)
        elif t == "VOTE_OPEN":
            _vote(rn, vn)["question"] = d.get("question")
        elif t == "VOTE_REVEALED":
            v = _vote(rn, vn)
            votes = d.get("votes", {})
            v["votes"] = votes
            v["yes_count"] = sum(1 for x in votes.values() if x == "YES")
            v["no_count"] = sum(1 for x in votes.values() if x == "NO")
        elif t == "VOTE_RESOLVED":
            v = _vote(rn, vn)
            v["result"] = d.get("result")
            v["eliminated_ids"] = d.get("eliminated_ids", [])
            v["remaining_ids"] = d.get("remaining_ids", [])
            v["extension_fee_collected"] = d.get("extension_fee_collected", 0)
        elif t == "CONTRACT_ESTABLISHED":
            _row(rn)["established_contract_seqs"].append(d.get("contract_seq"))
        elif t == "ROUND_RESOLVED":
            row = _row(rn)
            row["aborted"] = d.get("aborted", False)
            row["winner_ids"] = d.get("winner_ids", [])
            row["payout_per_winner"] = d.get("payout_per_winner", 0)
            row["carryover_out"] = d.get("carryover_out", 0)
            row["destroyed_pot"] = d.get("destroyed_pot", 0)
            row["pot_final"] = d.get("pot_final", 0)

    result = []
    for rn in sorted(by_round):
        row = dict(by_round[rn])
        votes = votes_by_round.get(rn, {})
        row["votes"] = [votes[vn] for vn in sorted(votes)]
        result.append(row)
    return result


def _accident_summary(index_entries: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, int]:
    """事故の件数（時間切れ・サーバーエラー・読み取りエラー・自動代行）"""
    return {
        "timeouts": sum(1 for e in index_entries if e.get("error_type") == "timeout"),
        "server_errors": sum(1 for e in index_entries if e.get("error_type") == "server_error"),
        "invalid_responses": sum(1 for e in index_entries if e.get("invalid_response")),
        "auto_commits": sum(1 for e in events if e.get("event_type") == "AUTO_COMMIT"),
    }


def _legacy_overview(log_dir: Path | list[Path], game_id: str, events: list[dict[str, Any]], *, redact: bool) -> dict[str, Any]:
    """v0.3形式: 席・モデル・最終資産・順位だけを返す（§v0.3の試合の扱い案B）"""
    seat_map = _seat_map(log_dir, game_id)
    final = summarize_final(events)
    final_assets = (final or {}).get("final_assets", {})
    final_ranks = (final or {}).get("final_ranks", {})
    roster = [
        {
            "player_id": r["player_id"], "model_id": r["model_id"],
            "model_label": short_model_label(r["model_id"]), "loan": r["loan"],
            "final_assets": final_assets.get(r["player_id"]),
            "final_rank": final_ranks.get(r["player_id"]),
        }
        for r in summarize_roster(seat_map, events)
    ]
    result = {
        "game_id": game_id, "format": "v0.3", "completed": final is not None,
        "roster": roster,
        "transcript_available": _has_transcript(events),
        "legacy_notice": (
            "この記録はv0.3形式です。ラウンド・契約・お金の推移の詳細は、"
            "git タグ v0.3-season1 のビューアで見られます。"
        ),
    }
    return redact_value(result) if redact else result


def get_overview(log_dir: Path | list[Path], game_id: str, *, redact: bool = True) -> dict[str, Any] | None:
    """試合ページ: 席・モデル・借入額・最終資産・順位、ラウンドの流れ、資産推移、事故件数"""
    events = _events(log_dir, game_id)
    if not events:
        return None
    if is_v0_3_format(events):
        return _legacy_overview(log_dir, game_id, events, redact=redact)

    seat_map = _seat_map(log_dir, game_id)
    index_entries = _index_for(log_dir, game_id)

    final = summarize_final(events)
    final_assets = (final or {}).get("final_assets", {})
    final_ranks = (final or {}).get("final_ranks", {})

    pot_payouts: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "total": 0})
    for e in events:
        if e.get("event_type") == "ROUND_PAYOUT":
            d = e.get("data") or {}
            pid = d.get("player_id")
            pot_payouts[pid]["count"] += 1
            pot_payouts[pid]["total"] += d.get("amount", 0)

    roster = [
        {
            "player_id": r["player_id"], "model_id": r["model_id"],
            "model_label": short_model_label(r["model_id"]), "loan": r["loan"],
            "final_assets": final_assets.get(r["player_id"]),
            "final_rank": final_ranks.get(r["player_id"]),
            "pot_payout_count": pot_payouts[r["player_id"]]["count"],
            "pot_payout_total": pot_payouts[r["player_id"]]["total"],
        }
        for r in summarize_roster(seat_map, events)
    ]

    rounds = _round_vote_summaries(events)
    trail = build_money_trail(events)
    asset_timeline = asset_timeline_from_trail(trail, sorted(seat_map))
    contracts = summarize_contracts(events)

    result = {
        "game_id": game_id, "format": "v0.4", "completed": final is not None,
        "num_rounds": max((r["round_num"] for r in rounds), default=0),
        "roster": roster,
        "rounds": rounds,
        "asset_timeline": asset_timeline,
        "established_contracts": len(contracts["established"]),
        "contract_payments": len(contracts["payments"]),
        "accidents": _accident_summary(index_entries, events),
        "transcript_available": _has_transcript(events),
        "money_reconciled": trail["mismatches"] == [],
    }
    return redact_value(result) if redact else result


# =============================================================================
# ラウンドタブ（§作るもの2・3・4）
# =============================================================================

_INDEXED_PHASE = "negotiation"


def _index_by_round_vote_player_turn(
    entries: list[dict[str, Any]],
) -> dict[tuple[int, int, str, int], list[dict[str, Any]]]:
    """(round_num, vote_num, player_id, turn) -> その手番で実際に成功した応答群"""
    idx: dict[tuple[int, int, str, int], list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        if e.get("phase") != _INDEXED_PHASE or e.get("error_type") or e.get("invalid_response"):
            continue
        key = (e.get("round_num"), e.get("vote_num"), e.get("player_id"), e.get("turn"))
        if None in key:
            continue
        idx[key].append(e)
    return idx


def _memo_for_action(
    idx: dict[tuple[int, int, str, int], list[dict[str, Any]]],
    round_num: int, vote_num: int, pid: str | None, turn: int | None, action_type: str | None,
) -> dict[str, Any] | None:
    """その行動に対応する内心メモ（strategy）を索引から引く。見つからなければNone"""
    if pid is None or turn is None:
        return None
    candidates = idx.get((round_num, vote_num, pid, turn), [])
    for e in reversed(candidates):
        action = e.get("action") or {}
        if action.get("type") == action_type:
            return e.get("strategy")
    if candidates:
        return candidates[-1].get("strategy")
    return None


def _commit_strategy(
    entries: list[dict[str, Any]], round_num: int, vote_num: int, pid: str,
) -> dict[str, Any] | None:
    """投票直前の考え（commitフェイズのstrategy）を索引から引く（§作るもの4）"""
    for e in entries:
        if (
            e.get("phase") == "commit" and e.get("round_num") == round_num
            and e.get("vote_num") == vote_num and e.get("player_id") == pid
            and not e.get("error_type") and not e.get("invalid_response")
        ):
            return e.get("strategy")
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
                "vote_num": e.get("vote_num"), "turn": e.get("turn"), "phase": e.get("phase"),
            })
        elif e.get("invalid_response"):
            out.append({
                "kind": "invalid_response", "player_id": e.get("player_id"),
                "vote_num": e.get("vote_num"), "turn": e.get("turn"), "phase": e.get("phase"),
                "reason": e.get("reason"),
            })
    return out


def get_round(log_dir: Path | list[Path], game_id: str, round_num: int, *, redact: bool = True) -> dict[str, Any] | None:
    """
    ラウンドのページ: 投票（R?V?）ごとの質問・交渉・内心メモ・投票公開・事故の
    目印と、ラウンドの精算（山の支払い・山分けの支払い・利息）をまとめる
    （サイクル4.3: v0.4のラウンド→投票の二重構造に対応）
    """
    events = _events(log_dir, game_id)
    if not events or is_v0_3_format(events):
        return None
    seat_map = _seat_map(log_dir, game_id)
    max_round = max((e.get("round_num", 0) for e in events), default=0)
    if max_round <= 0:
        return None
    round_num = max(1, min(round_num, max_round))  # 範囲外は最も近い有効値にクランプ

    index_entries = _index_for(log_dir, game_id)
    neg_idx = _index_by_round_vote_player_turn(index_entries)
    lies = {
        (h["round_num"], h["vote_num"], h["player_id"])
        for h in build_highlights(events, index_entries)["lies"]
    }

    # 契約の義務の中身は提案イベント（CONTRACT_PROPOSED）にしか無く、別ラウンドで
    # 提案されたまま成立することがあるため、ラウンド絞り込みの前に全イベントから
    # contract_id -> obligations を引けるようにしておく（契約タブと同じ中身を出す）。
    obligations_by_contract: dict[str, list[dict[str, Any]]] = {
        e["data"]["contract_id"]: e["data"].get("obligations", [])
        for e in events
        if e.get("event_type") == "CONTRACT_PROPOSED" and (e.get("data") or {}).get("contract_id")
    }

    carryover_in = 0
    pot_initial = 0
    votes: dict[int, dict[str, Any]] = {}
    round_reflections: dict[int, dict[str, str]] = {}
    round_settlement: dict[str, Any] = {
        "aborted": False, "winner_ids": [], "payout_per_winner": 0,
        "forfeited_remainder": 0, "carryover_out": 0, "destroyed_pot": 0, "pot_final": 0,
        "payments": [], "interest": [], "shortfall_ids": [], "public_ranks": None,
        "resolved": False,
        # サイクル7.0: ROUND_ABORTEDイベントがあれば"consecutive_ties"/"vote_cap"で
        # 上書きされる（無ければ打ち切りではないラウンドなのでNoneのまま）。
        "abort_reason": None, "abort_max_votes_per_round": None,
    }

    def _vote(vn: int) -> dict[str, Any]:
        return votes.setdefault(vn, {
            "vote_num": vn, "question": None, "remaining_ids": [], "eliminated_ids_before": [],
            "pot": 0, "consecutive_ties": 0, "timeline": [], "vote_result": None,
        })

    current_vote_num: int | None = None

    for e in events:
        if e.get("round_num") != round_num:
            continue
        t = e.get("event_type")
        d = e.get("data") or {}
        vn = e.get("vote_num")
        line = e.get("_line")

        if t == "ROUND_START":
            carryover_in = d.get("carryover_in", 0)
            pot_initial = d.get("pot", 0)
            continue
        if t == "GAME_START" or t == "GAME_END" or t == "POST_GAME_REFLECTION":
            continue

        if vn is not None:
            current_vote_num = vn
            v = _vote(vn)

        if t == "VOTE_OPEN":
            v["question"] = d.get("question")
            v["remaining_ids"] = d.get("remaining_ids", [])
            v["eliminated_ids_before"] = d.get("eliminated_ids", [])
            v["pot"] = d.get("pot", 0)
            v["consecutive_ties"] = d.get("consecutive_ties", 0)
        elif t == "RANK_NOTIFIED" or t == "FINANCE_NOTICE" or t == "TURN_CONTRACTS_ESTABLISHED":
            pass  # 本人のみ・集計用の情報はラウンドタブでは出さない（お金タブ・概要で扱う）
        elif t == "NEGOTIATION_ACTION":
            action = d.get("action")
            pid = d.get("player_id")
            turn = d.get("turn")
            memo = (
                _memo_for_action(neg_idx, round_num, vn, pid, turn, action)
                if action in ("dm", "broadcast", "pass") else None
            )
            v["timeline"].append({
                "line": line, "kind": "negotiation", "turn": turn, "player_id": pid, "action": action,
                "to": d.get("to"), "message": d.get("message"),
                "success": d.get("success"), "reason": d.get("reason"), "memo": memo,
            })
        elif t == "CONTRACT_PROPOSED":
            v["timeline"].append({
                "line": line, "kind": "contract_propose", "turn": d.get("turn"),
                "contract_id": d.get("contract_id"), "proposer": d.get("proposer"),
                "parties": d.get("parties"), "obligations": d.get("obligations"),
            })
        elif t == "CONTRACT_REJECTED":
            v["timeline"].append({
                "line": line, "kind": "contract_rejected", "turn": d.get("turn"),
                "player_id": d.get("player_id"), "reason": d.get("reason"),
            })
        elif t == "CONTRACT_SIGNED":
            v["timeline"].append({
                "line": line, "kind": "contract_sign", "turn": d.get("turn"),
                "contract_id": d.get("contract_id"), "signer": d.get("signer"),
            })
        elif t == "CONTRACT_ESTABLISHED":
            v["timeline"].append({
                "line": line, "kind": "contract_established", "turn": None,
                "contract_id": d.get("contract_id"), "parties": d.get("parties"),
                "contract_seq": d.get("contract_seq"),
                "obligations": obligations_by_contract.get(d.get("contract_id"), []),
            })
        elif t == "CONTRACT_EXPIRED":
            v["timeline"].append({
                "line": line, "kind": "contract_expired", "turn": None,
                "contract_id": d.get("contract_id"), "proposer": d.get("proposer"),
                "parties": d.get("parties"), "signed_by": d.get("signed_by"),
            })
        elif t == "NEGOTIATION_EARLY_END":
            v["timeline"].append({"line": line, "kind": "negotiation_early_end", "turn": d.get("turn")})
        elif t == "COMMIT":
            pid = d.get("player_id")
            v["timeline"].append({
                "line": line, "kind": "commit", "turn": None, "player_id": pid, "vote": d.get("vote"),
                "strategy": _commit_strategy(index_entries, round_num, vn, pid),
                "lied": (round_num, vn, pid) in lies,
            })
        elif t == "AUTO_COMMIT":
            v["timeline"].append({
                "line": line, "kind": "accident", "accident_type": "auto_commit", "turn": None,
                "player_id": d.get("player_id"), "vote": d.get("vote"),
            })
        elif t == "VOTE_REVEALED":
            v["vote_result"] = {
                "line": line, "kind": "vote_result", "votes": d.get("votes", {}),
                "result": None, "eliminated_ids": [], "remaining_ids": [],
                "extension_fee_collected": 0, "lied_ids": [
                    pid for (rn2, vn2, pid) in lies if rn2 == round_num and vn2 == vn
                ],
            }
            v["timeline"].append(v["vote_result"])
        elif t == "VOTE_RESOLVED":
            if v["vote_result"] is None:
                v["vote_result"] = {"line": line, "kind": "vote_result", "votes": {}}
                v["timeline"].append(v["vote_result"])
            v["vote_result"].update({
                "result": d.get("result"), "eliminated_ids": d.get("eliminated_ids", []),
                "remaining_ids": d.get("remaining_ids", []),
                "consecutive_ties_after": d.get("consecutive_ties_after"),
                "extension_fee_collected": d.get("extension_fee_collected", 0),
            })
        elif t == "EXTENSION_FEE_COLLECTED":
            v["timeline"].append({
                "line": line, "kind": "extension_fee", "turn": None,
                "player_id": d.get("player_id"), "paid": d.get("paid"), "borrowed": d.get("borrowed"),
            })
        elif t == "ROUND_ABORTED":
            # reason: "consecutive_ties"（既定の打ち切り）または "vote_cap"
            # （サイクル7.0新設。投票がmax_votes_per_round回に達しても決着しない
            # 場合）。本サイクル以前の記録にはreasonが無いため、既定の
            # "consecutive_ties" にフォールバックする（当時はそれしか無かった）。
            reason = d.get("reason", "consecutive_ties")
            v["timeline"].append({
                "line": line, "kind": "round_aborted", "turn": None,
                "consecutive_ties": d.get("consecutive_ties"),
                "reason": reason,
                "max_votes_per_round": d.get("max_votes_per_round"),
            })
            round_settlement["abort_reason"] = reason
            round_settlement["abort_max_votes_per_round"] = d.get("max_votes_per_round")
        elif t == "TYPE_B_VIOLATION":
            v["timeline"].append({"line": line, "kind": "type_b_violation", "turn": None,
                                   "player_ids": d.get("player_ids", [])})
        elif t == "PAYMENT_SHORTFALL":
            v["timeline"].append({"line": line, "kind": "payment_shortfall", "turn": None,
                                   "player_ids": d.get("player_ids", [])})
        elif t == "CONTRACT_PAYMENT":
            entry = {
                "line": line, "kind": "contract_payment", "turn": None,
                "contract_id": d.get("contract_id"), "contract_seq": d.get("contract_seq"),
                "obligor": d.get("obligor"), "counterparty": d.get("counterparty"),
                "ob_type": d.get("ob_type"), "condition_type": d.get("condition_type"),
                "promised": d.get("promised"), "paid": d.get("paid"),
            }
            if e.get("phase") == "round_settlement":
                round_settlement["payments"].append(entry)
            else:
                v["timeline"].append(entry)
        elif t == "PAYABLE_LIMIT_FIXED":
            pass  # 観戦者向けの内部値（spectator）。画面には出さない
        elif t == "ENTRY_FEE_COLLECTED":
            pass  # ラウンド開始の一斉徴収。お金タブで扱う
        elif t == "ROUND_RESOLVED":
            round_settlement.update({
                "resolved": True, "aborted": d.get("aborted", False),
                "winner_ids": d.get("winner_ids", []), "payout_per_winner": d.get("payout_per_winner", 0),
                "forfeited_remainder": d.get("forfeited_remainder", 0),
                "carryover_out": d.get("carryover_out", 0), "destroyed_pot": d.get("destroyed_pot", 0),
                "pot_final": d.get("pot_final", 0), "line": line,
            })
        elif t == "ROUND_PAYOUT":
            round_settlement.setdefault("payouts", []).append({
                "line": line, "player_id": d.get("player_id"), "amount": d.get("amount"),
            })
        elif t == "INTEREST":
            round_settlement["interest"].append({
                "line": line, "player_id": d.get("player_id"),
                "interest_pre": d.get("interest_pre"), "interest_post": d.get("interest_post"),
                "new_debt_pre": d.get("new_debt_pre"), "new_debt_post": d.get("new_debt_post"),
            })
        elif t == "RANK_PUBLISHED":
            round_settlement["public_ranks"] = d.get("ranks", {})
        elif t == "POT_UPDATED":
            if e.get("phase") == "open":
                pot_initial = d.get("pot", pot_initial)

    round_settlement.setdefault("payouts", [])

    # この投票（ラウンド末尾も含む）の振り返り（内心メモ）
    for entry in index_entries:
        if entry.get("phase") == "reflect" and entry.get("round_num") == round_num and entry.get("memory"):
            round_reflections.setdefault(entry.get("vote_num"), {})[entry.get("player_id")] = entry.get("memory")

    votes_list = [votes[vn] for vn in sorted(votes)]
    for v in votes_list:
        v["reflections"] = round_reflections.get(v["vote_num"], {})

    result = {
        "game_id": game_id, "round_num": round_num, "max_round": max_round,
        "carryover_in": carryover_in, "pot_initial": pot_initial,
        "votes": votes_list,
        "round_settlement": round_settlement,
        "accidents": _round_accidents(index_entries, round_num),
        "seat_ids": sorted(seat_map),
    }
    return redact_value(result) if redact else result


# =============================================================================
# 契約タブ（§作るもの5）
# =============================================================================

def _pool_share_label(ob: dict[str, Any], round_established: int | None, num_rounds: int) -> str:
    share = (ob.get("details") or {}).get("share_percent")
    span = f"R{round_established}〜R{num_rounds}" if round_established else "成立後〜最終ラウンド"
    return f"[山分け] 当事者全員が、山を受け取ったら受取額の{share}%をほかの当事者で等分して払う（{span}、毎ラウンド）"


def _obligation_outcome(
    ob: dict[str, Any], ob_index: int, contract_id: str,
    payments_by_key: dict[tuple[str, int], list[dict[str, Any]]], max_settled_round: int,
) -> str:
    """
    義務1本の結果を判定する（§結果区分: 履行／違約金支払い／支払い発生／条件が外れた）

    山分け（pool_share）は常に「これから」（PENDING）のまま
    （CLAUDE.md v0.5の節: 1つの義務が複数ラウンド・複数の支払い相手に
    わたって評価されるため、単一の結果に落とし込めない。実際の支払いの
    一覧はpaymentsとして別に持つ）。
    """
    if ob.get("ob_type") == "type_c_conditional" and (ob.get("details") or {}).get("condition_type") == "pool_share":
        return "これから（毎ラウンド評価）"

    payments = payments_by_key.get((contract_id, ob_index), [])
    ob_type = ob.get("ob_type")
    round_num = ob.get("round_num")
    settled = round_num is not None and round_num <= max_settled_round

    if ob_type == "type_b_vote":
        if payments:
            return "違約金支払い"
        return "履行" if settled else "未定（今後）"
    if ob_type == "type_c_conditional":
        if payments:
            return "支払い発生"
        return "条件が外れた" if settled else "未定（今後）"
    if ob_type == "type_a_payment":
        if payments:
            total_paid = sum(p.get("paid", 0) for p in payments)
            promised = payments[0].get("promised", 0)
            return "支払い発生" if total_paid >= promised else "部分払い"
        return "履行なし" if settled else "未定（今後）"
    return "不明"


def get_contracts(log_dir: Path | list[Path], game_id: str, *, redact: bool = True) -> dict[str, Any] | None:
    """契約の一覧: 成立順・当事者・義務の中身・結果。提案だけで不成立の分は別枠"""
    events = _events(log_dir, game_id)
    if not events or is_v0_3_format(events):
        return None

    proposed: dict[str, dict[str, Any]] = {}
    established_seq: dict[str, int | None] = {}
    established_round: dict[str, int] = {}
    expired: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    payments_by_key: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    max_settled_round = 0
    num_rounds = 0

    for e in events:
        t = e.get("event_type")
        d = e.get("data") or {}
        rn = e.get("round_num", 0)
        num_rounds = max(num_rounds, rn)
        if t == "CONTRACT_PROPOSED":
            proposed[d["contract_id"]] = d
        elif t == "CONTRACT_ESTABLISHED":
            established_seq[d["contract_id"]] = d.get("contract_seq")
            established_round[d["contract_id"]] = rn
        elif t == "CONTRACT_EXPIRED":
            expired.append({**d, "line": e.get("_line"), "round_num": rn, "vote_num": e.get("vote_num")})
        elif t == "CONTRACT_REJECTED":
            rejected.append({**d, "line": e.get("_line"), "round_num": rn, "vote_num": e.get("vote_num")})
        elif t == "CONTRACT_PAYMENT":
            ob_index = d.get("ob_index")
            if ob_index is not None:
                payments_by_key[(d["contract_id"], ob_index)].append({
                    **d, "line": e.get("_line"), "round_num": rn, "vote_num": e.get("vote_num"),
                })
        elif t == "VOTE_REVEALED":
            max_settled_round = max(max_settled_round, rn)

    established = []
    for contract_id, seq in established_seq.items():
        prop = proposed.get(contract_id, {})
        round_established = established_round.get(contract_id)
        obligations = []
        for i, ob in enumerate(prop.get("obligations", [])):
            outcome = _obligation_outcome(ob, i, contract_id, payments_by_key, max_settled_round)
            is_pool_share = (
                ob.get("ob_type") == "type_c_conditional"
                and (ob.get("details") or {}).get("condition_type") == "pool_share"
            )
            obligations.append({
                **ob, "outcome": outcome,
                "pool_share_label": _pool_share_label(ob, round_established, num_rounds) if is_pool_share else None,
                "payments": payments_by_key.get((contract_id, i), []),
            })
        established.append({
            "contract_id": contract_id,
            "contract_seq": seq,
            "round_established": round_established,
            "proposer": prop.get("proposer"),
            "parties": prop.get("parties"),
            "obligations": obligations,
        })
    established.sort(key=lambda c: (c["contract_seq"] is None, c["contract_seq"]))

    result = {
        "game_id": game_id,
        "established": established,
        "expired": expired,
        "rejected": rejected,
        "proposed_total": len(proposed),
    }
    return redact_value(result) if redact else result


# =============================================================================
# お金タブ（§作るもの6）
# =============================================================================

def get_money(log_dir: Path | list[Path], game_id: str, *, redact: bool = True) -> dict[str, Any] | None:
    """お金タブ: 席ごとの現金・借金の推移と、お金の出入りの記録、記録との照合"""
    events = _events(log_dir, game_id)
    if not events or is_v0_3_format(events):
        return None
    seat_map = _seat_map(log_dir, game_id)
    trail = build_money_trail(events)

    result = {
        "game_id": game_id,
        "snapshots": trail["snapshots"],
        "ledger": {pid: trail["ledger"].get(pid, []) for pid in sorted(seat_map)},
        "mismatches": trail["mismatches"],
        "reconciled": trail["mismatches"] == [],
    }
    return redact_value(result) if redact else result


# =============================================================================
# 見どころタブ（§作るもの7）
# =============================================================================

def get_highlights(log_dir: Path | list[Path], game_id: str, *, redact: bool = True) -> dict[str, Any] | None:
    """見どころ一覧: 嘘・単独勝ち・型Bの違反・山分けの支払い・やり直し/打ち切り"""
    events = _events(log_dir, game_id)
    if not events or is_v0_3_format(events):
        return None
    index_entries = _index_for(log_dir, game_id)
    result = {"game_id": game_id, **build_highlights(events, index_entries)}
    return redact_value(result) if redact else result


# =============================================================================
# 席タブ
# =============================================================================

def get_seat(log_dir: Path | list[Path], game_id: str, pid: str, *, redact: bool = True) -> dict[str, Any] | None:
    """席のページ: 投票（R?V?）ごとの票・発言・関わった契約・資産推移・振り返り"""
    events = _events(log_dir, game_id)
    if not events or is_v0_3_format(events):
        return None
    seat_map = _seat_map(log_dir, game_id)
    if pid not in seat_map:
        return None

    votes: dict[tuple[int, int], str] = {}
    messages: list[dict[str, Any]] = []
    contract_ids: set[str] = set()

    for e in events:
        t = e.get("event_type")
        d = e.get("data") or {}
        rn = e.get("round_num", 0)
        vn = e.get("vote_num")
        if t in ("COMMIT", "AUTO_COMMIT") and d.get("player_id") == pid and vn is not None:
            votes[(rn, vn)] = d.get("vote")
        elif t == "NEGOTIATION_ACTION":
            action = d.get("action")
            if action == "broadcast" and d.get("player_id") == pid:
                messages.append({
                    "line": e.get("_line"), "round_num": rn, "vote_num": vn, "turn": d.get("turn"),
                    "kind": "broadcast", "from": pid, "to": None, "message": d.get("message"),
                })
            elif action == "dm" and (d.get("player_id") == pid or d.get("to") == pid):
                messages.append({
                    "line": e.get("_line"), "round_num": rn, "vote_num": vn, "turn": d.get("turn"),
                    "kind": "dm", "from": d.get("player_id"), "to": d.get("to"), "message": d.get("message"),
                })
        elif t in ("CONTRACT_PROPOSED", "CONTRACT_ESTABLISHED") and pid in (d.get("parties") or []):
            contract_ids.add(d["contract_id"])

    trail = build_money_trail(events)
    asset_timeline = [
        {"round_num": snap["round_num"], "vote_num": snap["vote_num"], **snap["by_seat"][pid]}
        for snap in trail["snapshots"] if pid in snap["by_seat"]
    ]
    ledger = trail["ledger"].get(pid, [])

    index_entries = _index_for(log_dir, game_id)
    round_memos: dict[tuple[int, int], str] = {}
    for entry in index_entries:
        if entry.get("phase") == "reflect" and entry.get("player_id") == pid and entry.get("memory"):
            round_memos[(entry.get("round_num"), entry.get("vote_num"))] = entry.get("memory")

    final = summarize_final(events) or {}
    post_game = summarize_post_game_reflections(events).get(pid)

    result = {
        "game_id": game_id,
        "player_id": pid,
        "model_id": seat_map.get(pid),
        "model_label": short_model_label(seat_map.get(pid)),
        "votes": [{"round_num": rn, "vote_num": vn, "vote": v} for (rn, vn), v in sorted(votes.items())],
        "messages": messages,
        "contract_ids": sorted(contract_ids),
        "asset_timeline": asset_timeline,
        "cash_flow": ledger,
        "round_memos": [
            {"round_num": rn, "vote_num": vn, "memory": memo}
            for (rn, vn), memo in sorted(round_memos.items())
        ],
        "post_game_reflection": post_game,
        "final_assets": (final.get("final_assets") or {}).get(pid),
        "final_rank": (final.get("final_ranks") or {}).get(pid),
    }
    return redact_value(result) if redact else result


# =============================================================================
# 記録の行番号から場面を解決する（§作るもの8 直リンク）
# =============================================================================

def resolve_event(log_dir: Path | list[Path], game_id: str, line: int) -> dict[str, Any] | None:
    """
    記録ファイルの行番号から、その行のイベントと「どの場面か」
    （タブ・ラウンド・投票・巡・席）を解決する。

    動画の台本づくりで、記録の行番号から直接その場面に飛べるようにする
    （§作るもの8）。
    """
    events = _events(log_dir, game_id)
    if not events:
        return None
    event = next((e for e in events if e.get("_line") == line), None)
    if event is None:
        return None

    d = event.get("data") or {}
    rn = event.get("round_num") or None
    vn = event.get("vote_num")
    turn = d.get("turn")
    pid = d.get("player_id") or d.get("signer") or d.get("obligor")

    t = event.get("event_type")
    if t in ("CONTRACT_PROPOSED", "CONTRACT_SIGNED", "CONTRACT_ESTABLISHED", "CONTRACT_EXPIRED"):
        tab = "round" if rn else "contracts"
        contract_id = d.get("contract_id")
    else:
        tab = "round" if rn else "overview"
        contract_id = None

    scene = {
        "tab": tab, "round": rn, "vote": vn, "turn": turn, "player": pid, "contract": contract_id,
    }
    return {"event": redact_value(event), "scene": scene}
