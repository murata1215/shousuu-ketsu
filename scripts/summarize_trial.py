"""
少数決 AI試合 結果要約コマンド（サイクル2.0新設、サイクル2.1で拡張、サイクル4.2bでv0.4対応）

`scripts/llm_trial.py` が書き残した記録（{game_id}_events.jsonl /
{game_id}_llm_calls.jsonl / {game_id}_seat_map.json）だけから要約を作る。
ゲームを再実行しない。試合が途中で止まっていても、そこまでの記録で
要約できる。

v0.4（ラウンド1〜4の中に投票1〜6が入る二重構造）に対応し、ラウンドごとに
各投票の票の割れ方・退場者・決着/やり直し/打ち切りと、ラウンドの勝ち残り・
受取額・打ち切りと持ち越しを分けて出す。契約の集計は型ごとの成立・失効の
本数・型B違反・払いきれなかった者・割合（share_percent）で払う契約の本数を
出す。v0.3の記録（`ROUND_OPEN`/`MINORITY_RESOLVED`の存在で判定）を渡された
場合は、その旨を表示して終了コード1で止める（v0.3の要約はgitタグ
v0.3-season1のコードで作れる。本対応は必須にしない）。

サイクル2.1で追加: 冒頭の席ごとのモデル・借入額一覧、`--transcript`/`--round`
での会話の書き出し（全体発言・DM・送金・契約の提案/署名/成立・投票結果を
時系列で並べる）。発言本文はイベント自体（`NEGOTIATION_ACTION.data.message`、
サイクル2.1以降の新しい記録）にあればそれを使い、無い古い記録は
`*_llm_calls.jsonl`のレスポンス本文から復元する。

使用方法:
    uv run python scripts/summarize_trial.py --game-id smoke4_2001 --log-dir logs/llm_v0_4
    uv run python scripts/summarize_trial.py --game-id smoke4_2001 --log-dir logs/llm_v0_4 --round 1
    uv run python scripts/summarize_trial.py --game-id smoke4_2001 --log-dir logs/llm_v0_4 --transcript --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.response_parser import extract_json  # noqa: E402

# v0.3形式の記録を検出するための種別（v0.4には存在しない）
_V0_3_ONLY_EVENT_TYPES = {"ROUND_OPEN", "MINORITY_RESOLVED"}


class V0_3_RecordError(Exception):
    """v0.3形式の記録を渡された（v0.4のイベント種別と構造が違う）ことを表す"""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 途中で止まった最終行の壊れかけJSONは無視する
    return rows


def _check_not_v0_3(events: list[dict[str, Any]]) -> None:
    """v0.3形式の記録（ROUND_OPEN/MINORITY_RESOLVED）を検出したら止める"""
    found = {e.get("event_type") for e in events} & _V0_3_ONLY_EVENT_TYPES
    if found:
        raise V0_3_RecordError(
            f"この記録はv0.3形式です（{', '.join(sorted(found))}が見つかりました）。"
            "v0.4対応のscripts/summarize_trial.pyでは要約できません。"
            "v0.3の記録の要約は、gitタグ v0.3-season1 のコードで作れます。",
        )


def load_trial_records(game_id: str, log_dir: str | Path) -> dict[str, Any]:
    """3つの記録ファイルを読み込む（存在しないファイルは空扱い）"""
    log_dir = Path(log_dir)
    return {
        "events": _load_jsonl(log_dir / f"{game_id}_events.jsonl"),
        "llm_calls": _load_jsonl(log_dir / f"{game_id}_llm_calls.jsonl"),
        "seat_map": json.loads((log_dir / f"{game_id}_seat_map.json").read_text(encoding="utf-8"))
        if (log_dir / f"{game_id}_seat_map.json").exists() else {},
    }


def short_model_label(model_id: str | None) -> str:
    """モデルIDからベンダ接頭辞（"devrelay/"等）を落とす（例: "devrelay/claude-opus-5" → "claude-opus-5"）"""
    if not model_id:
        return model_id or ""
    return model_id.rsplit("/", 1)[-1]


def seat_label(pid: str | None, seat_map: dict[str, str]) -> str:
    """席IDにモデル名を添える（例: "P08(claude-opus-5)"）。seat_mapに無ければ席IDのみ"""
    if pid is None:
        return ""
    model_id = seat_map.get(pid)
    if not model_id:
        return pid
    return f"{pid}({short_model_label(model_id)})"


def summarize_loans(events: list[dict[str, Any]]) -> dict[str, int]:
    """LOAN_REVEALED（round_num=0）から席ごとの借入額を取り出す"""
    for e in events:
        if e.get("event_type") == "LOAN_REVEALED":
            return dict((e.get("data") or {}).get("loans", {}))
    return {}


def summarize_roster(seat_map: dict[str, str], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """席ごとのモデルと借入額の一覧（席ID昇順）"""
    loans = summarize_loans(events)
    return [
        {"player_id": pid, "model_id": seat_map[pid], "loan": loans.get(pid)}
        for pid in sorted(seat_map)
    ]


def index_negotiation_actions(
    llm_calls: list[dict[str, Any]],
) -> dict[tuple[int, int | None, str, int], list[dict[str, Any]]]:
    """
    (round_num, vote_num, player_id, turn) -> そのキーで実際に応答が成功した action群（ファイル順）

    再試行で複数行あることがある（`invalid_response`の注記行は除く）。会話の
    書き出しで、本文がイベントに残っていない古い記録から本文を復元するための
    索引（§取りこぼし防止: イベントの行為種別と一致する「最後の」要素を使う＝
    退けられた途中案ではなく、実際に採用された応答）。vote_numはサイクル4.2bで
    キーに足した（§1.1、1ラウンドに複数の投票があるため）。
    """
    index: dict[tuple[int, int | None, str, int], list[dict[str, Any]]] = {}
    for entry in llm_calls:
        if entry.get("phase") != "negotiation" or entry.get("invalid_response"):
            continue
        rn, vn, pid, turn = (
            entry.get("round_num"), entry.get("vote_num"), entry.get("player_id"), entry.get("turn"),
        )
        if rn is None or pid is None or turn is None:
            continue
        obj = extract_json(entry.get("response_text") or "")
        if not isinstance(obj, dict):
            continue
        action = obj.get("action")
        if not isinstance(action, dict):
            continue
        index.setdefault((rn, vn, pid, turn), []).append(action)
    return index


def _lookup_message(
    index: dict[tuple[int, int | None, str, int], list[dict[str, Any]]],
    round_num: int, vote_num: int | None, pid: str | None, turn: int | None, action_type: str,
) -> str | None:
    """本文がイベントに無い場合のみ使う復元経路（llm_callsからの最後の一致を採る）"""
    if pid is None or turn is None:
        return None
    for action in reversed(index.get((round_num, vote_num, pid, turn), [])):
        if action.get("type") == action_type:
            message = action.get("message")
            return message if isinstance(message, str) else None
    return None


_TRANSCRIPT_EVENT_TYPES = {
    "NEGOTIATION_ACTION", "TRANSFER", "CONTRACT_PROPOSED",
    "CONTRACT_SIGNED", "CONTRACT_ESTABLISHED", "VOTE_REVEALED", "VOTE_RESOLVED",
}


def build_transcript(
    events: list[dict[str, Any]], llm_calls: list[dict[str, Any]], rounds: list[int] | None = None,
) -> list[dict[str, Any]]:
    """
    ラウンドごとに、各投票（vote_num）の全体発言・DM・送金・契約の提案/署名/成立・
    投票結果を時系列で並べる（サイクル4.2b: 投票番号と巡がわかる形にする）。

    `rounds`指定時はそのラウンドだけ返す（指定順ではなく、試合内の進行順）。
    投票結果はVOTE_REVEALEDとVOTE_RESOLVEDを1件に畳む。

    Returns:
        [{"round_num", "carryover_in", "pot",
          "votes": [{"vote_num", "question", "entries": [...]}]}]
    """
    neg_index = index_negotiation_actions(llm_calls)
    round_filter = set(rounds) if rounds else None
    by_round: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    pending_reveal: dict[tuple[int, int | None], dict[str, Any]] = {}

    def _get_round(rn: int) -> dict[str, Any]:
        if rn not in by_round:
            by_round[rn] = {"round_num": rn, "carryover_in": 0, "pot": 0, "votes": {}}
            order.append(rn)
        return by_round[rn]

    def _get_vote(rn: int, vn: int | None) -> dict[str, Any]:
        row = _get_round(rn)
        if vn not in row["votes"]:
            row["votes"][vn] = {"vote_num": vn, "question": None, "entries": []}
        return row["votes"][vn]

    for e in events:
        et = e.get("event_type")
        rn = e.get("round_num", 0)
        vn = e.get("vote_num")
        if rn <= 0 or (round_filter is not None and rn not in round_filter):
            continue
        data = e.get("data") or {}

        if et == "ROUND_START":
            row = _get_round(rn)
            row["carryover_in"] = data.get("carryover_in", 0)
            row["pot"] = data.get("pot", 0)
            continue
        if et == "VOTE_OPEN":
            _get_vote(rn, vn)["question"] = data.get("question")
            continue

        if et not in _TRANSCRIPT_EVENT_TYPES:
            continue
        vrow = _get_vote(rn, vn)
        turn = data.get("turn")

        if et == "NEGOTIATION_ACTION":
            action = data.get("action")
            if action not in ("dm", "broadcast") or data.get("success") is False:
                continue  # 失敗した行動・発言を伴わない行動（transfer/repay等）は対象外
            pid = data.get("player_id")
            message = data.get("message")
            if message is None:
                # 本文がイベントに無い古い記録のみllm_callsから復元する
                message = _lookup_message(neg_index, rn, vn, pid, turn, action)
            vrow["entries"].append({
                "turn": turn, "kind": action, "from": pid,
                "to": data.get("to"), "message": message,
            })
        elif et == "TRANSFER":
            vrow["entries"].append({
                "turn": turn, "kind": "transfer",
                "from": data.get("player_id"), "to": data.get("to"), "amount": data.get("amount"),
            })
        elif et == "CONTRACT_PROPOSED":
            vrow["entries"].append({
                "turn": turn, "kind": "contract_propose",
                "contract_id": data.get("contract_id"), "proposer": data.get("proposer"),
                "parties": data.get("parties"), "obligations": data.get("obligations"),
            })
        elif et == "CONTRACT_SIGNED":
            vrow["entries"].append({
                "turn": turn, "kind": "contract_sign",
                "contract_id": data.get("contract_id"), "signer": data.get("signer"),
            })
        elif et == "CONTRACT_ESTABLISHED":
            vrow["entries"].append({
                "turn": None, "kind": "contract_established",
                "contract_id": data.get("contract_id"), "parties": data.get("parties"),
                "contract_seq": data.get("contract_seq"),
            })
        elif et == "VOTE_REVEALED":
            entry = {
                "turn": None, "kind": "vote_result", "votes": data.get("votes", {}),
                "result": None, "eliminated_ids": [], "remaining_ids": [],
                "extension_fee_collected": 0,
            }
            vrow["entries"].append(entry)
            pending_reveal[(rn, vn)] = entry
        elif et == "VOTE_RESOLVED":
            entry = pending_reveal.pop((rn, vn), None)
            if entry is None:
                entry = {"turn": None, "kind": "vote_result", "votes": {}}
                vrow["entries"].append(entry)
            entry["result"] = data.get("result")
            entry["eliminated_ids"] = data.get("eliminated_ids", [])
            entry["remaining_ids"] = data.get("remaining_ids", [])
            entry["extension_fee_collected"] = data.get("extension_fee_collected", 0)

    result = []
    for rn in order:
        row = by_round[rn]
        votes_list = [row["votes"][vn] for vn in sorted(row["votes"], key=lambda v: (v is None, v))]
        result.append({
            "round_num": rn, "carryover_in": row["carryover_in"], "pot": row["pot"], "votes": votes_list,
        })
    return result


def summarize_rounds(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    ラウンドごとに、各投票の票の割れ方・退場者・決着/やり直し/打ち切りと、
    ラウンドの勝ち残り・受取額・打ち切りと持ち越しをまとめる（サイクル4.2b）。
    """
    by_round: dict[int, dict[str, Any]] = {}
    votes_by_round: dict[int, dict[int, dict[str, Any]]] = {}

    def _row(round_num: int) -> dict[str, Any]:
        return by_round.setdefault(round_num, {
            "round_num": round_num, "carryover_in": 0, "pot": 0,
            "aborted": False, "winner_ids": [], "payout_per_winner": 0,
            "carryover_out": 0, "destroyed_pot": 0,
            "auto_commit_ids": [], "established_contract_seqs": [],
            "type_b_violator_ids": [], "payment_shortfall_ids": [],
        })

    def _vote(round_num: int, vote_num: int) -> dict[str, Any]:
        return votes_by_round.setdefault(round_num, {}).setdefault(vote_num, {
            "vote_num": vote_num, "votes": {}, "result": None,
            "eliminated_ids": [], "remaining_ids": [], "consecutive_ties_after": None,
            "extension_fee_collected": 0,
        })

    for e in events:
        et = e.get("event_type")
        rn = e.get("round_num", 0)
        vn = e.get("vote_num")
        data = e.get("data") or {}
        if rn <= 0:
            continue
        if et == "ROUND_START":
            row = _row(rn)
            row["carryover_in"] = data.get("carryover_in", 0)
            row["pot"] = data.get("pot", 0)
        elif et == "VOTE_REVEALED":
            _vote(rn, vn)["votes"] = data.get("votes", {})
        elif et == "VOTE_RESOLVED":
            v = _vote(rn, vn)
            v["result"] = data.get("result")
            v["eliminated_ids"] = data.get("eliminated_ids", [])
            v["remaining_ids"] = data.get("remaining_ids", [])
            v["consecutive_ties_after"] = data.get("consecutive_ties_after")
            v["extension_fee_collected"] = data.get("extension_fee_collected", 0)
        elif et == "AUTO_COMMIT":
            _row(rn)["auto_commit_ids"].append(data.get("player_id"))
        elif et == "CONTRACT_ESTABLISHED":
            _row(rn)["established_contract_seqs"].append(data.get("contract_seq"))
        elif et == "TYPE_B_VIOLATION":
            _row(rn)["type_b_violator_ids"].extend(data.get("player_ids", []))
        elif et == "PAYMENT_SHORTFALL":
            _row(rn)["payment_shortfall_ids"].extend(data.get("player_ids", []))
        elif et == "ROUND_RESOLVED":
            row = _row(rn)
            row["aborted"] = data.get("aborted", False)
            row["winner_ids"] = data.get("winner_ids", [])
            row["payout_per_winner"] = data.get("payout_per_winner", 0)
            row["carryover_out"] = data.get("carryover_out", 0)
            row["destroyed_pot"] = data.get("destroyed_pot", 0)

    result = []
    for rn in sorted(by_round):
        row = dict(by_round[rn])
        votes = votes_by_round.get(rn, {})
        row["votes"] = [votes[vn] for vn in sorted(votes)]
        result.append(row)
    return result


def summarize_contracts(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    成立・失効した契約と支払いをまとめる（サイクル4.2b: 型ごとの成立本数・
    型B違反・払いきれなかった者・割合（share_percent）で払う契約の本数を追加）。
    """
    proposed_obligations: dict[str, list[dict[str, Any]]] = {}
    established: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    payments: list[dict[str, Any]] = []
    type_b_violator_ids: set[str] = set()
    payment_shortfall_ids: set[str] = set()

    for e in events:
        et = e.get("event_type")
        data = e.get("data") or {}
        if et == "CONTRACT_PROPOSED":
            proposed_obligations[data.get("contract_id")] = data.get("obligations") or []
        elif et == "CONTRACT_ESTABLISHED":
            contract_id = data.get("contract_id")
            established.append({
                "round_num": e.get("round_num"), "vote_num": e.get("vote_num"),
                "contract_id": contract_id, "contract_seq": data.get("contract_seq"),
                "parties": data.get("parties"),
                "obligations": proposed_obligations.get(contract_id, []),
            })
        elif et == "CONTRACT_EXPIRED":
            expired.append({
                "round_num": e.get("round_num"), "vote_num": e.get("vote_num"),
                "contract_id": data.get("contract_id"), "proposer": data.get("proposer"),
                "parties": data.get("parties"), "signed_by": data.get("signed_by"),
            })
        elif et == "CONTRACT_PAYMENT":
            payments.append({
                "round_num": e.get("round_num"), "vote_num": e.get("vote_num"),
                "contract_id": data.get("contract_id"),
                "contract_seq": data.get("contract_seq"), "obligor": data.get("obligor"),
                "counterparty": data.get("counterparty"), "ob_type": data.get("ob_type"),
                "promised": data.get("promised"), "paid": data.get("paid"),
            })
        elif et == "TYPE_B_VIOLATION":
            type_b_violator_ids.update(data.get("player_ids", []))
        elif et == "PAYMENT_SHORTFALL":
            payment_shortfall_ids.update(data.get("player_ids", []))

    established.sort(key=lambda c: (c["contract_seq"] is None, c["contract_seq"]))

    ob_type_counts: dict[str, int] = {}
    share_percent_count = 0
    for c in established:
        for ob in c["obligations"]:
            ob_type = ob.get("ob_type")
            if ob_type:
                ob_type_counts[ob_type] = ob_type_counts.get(ob_type, 0) + 1
            details = ob.get("details")
            if isinstance(details, dict) and "share_percent" in details:
                share_percent_count += 1

    return {
        "established": established, "expired": expired, "payments": payments,
        "ob_type_counts": ob_type_counts, "share_percent_count": share_percent_count,
        "type_b_violator_ids": sorted(type_b_violator_ids),
        "payment_shortfall_ids": sorted(payment_shortfall_ids),
    }


def summarize_final(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """GAME_ENDイベントから最終順位・最終資産を取り出す（試合が終わっていなければNone）"""
    for e in reversed(events):
        if e.get("event_type") == "GAME_END":
            data = e.get("data") or {}
            return {"final_ranks": data.get("final_ranks", {}), "final_assets": data.get("final_assets", {})}
    return None


def summarize_post_game_reflections(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """POST_GAME_REFLECTIONイベントをplayer_id別にまとめる"""
    result: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("event_type") != "POST_GAME_REFLECTION":
            continue
        data = dict(e.get("data") or {})
        pid = data.pop("player_id", None)
        if pid:
            result[pid] = data
    return result


def summarize_seats(
    events: list[dict[str, Any]], llm_calls: list[dict[str, Any]], seat_map: dict[str, str],
    final: dict[str, Any] | None = None, roster: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    席ごとの最終資産・順位・借入額・呼び出し回数・無効な応答・時間切れ・
    拒否(429)・自動代行・予算停止・所要時間・費用をまとめる（サイクル4.2b
    で最終資産・順位・借入額を追加）。
    """
    auto_commit_counts: dict[str, int] = {}
    budget_blocked_counts: dict[str, int] = {}
    for e in events:
        data = e.get("data") or {}
        pid = data.get("player_id")
        if not pid:
            continue
        if e.get("event_type") == "AUTO_COMMIT":
            auto_commit_counts[pid] = auto_commit_counts.get(pid, 0) + 1
        elif e.get("event_type") == "LLM_BUDGET_BLOCKED":
            budget_blocked_counts[pid] = budget_blocked_counts.get(pid, 0) + 1

    loans = {r["player_id"]: r["loan"] for r in (roster or [])}
    final_assets = (final or {}).get("final_assets", {}) if final else {}
    final_ranks = (final or {}).get("final_ranks", {}) if final else {}

    def _base_row(pid: str, model_id: str | None) -> dict[str, Any]:
        return {
            "player_id": pid, "model_id": model_id, "loan": loans.get(pid),
            "final_assets": final_assets.get(pid), "final_rank": final_ranks.get(pid),
            "calls": 0, "invalid_responses": 0, "timeouts": 0, "rate_limited": 0, "other_errors": 0,
            "auto_commits": auto_commit_counts.get(pid, 0),
            "budget_blocked": budget_blocked_counts.get(pid, 0),
            "elapsed_ms_total": 0.0, "cost_usd_total": 0.0,
        }

    by_seat: dict[str, dict[str, Any]] = {pid: _base_row(pid, seat_map[pid]) for pid in sorted(seat_map)}

    for entry in llm_calls:
        pid = entry.get("player_id")
        if pid is None:
            continue
        row = by_seat.setdefault(pid, _base_row(pid, entry.get("model_id")))
        if entry.get("invalid_response"):
            row["invalid_responses"] += 1
            continue  # log_call()で記録済みの呼び出し1回に対する注記行であり、別件の呼び出しではない
        row["calls"] += 1
        row["elapsed_ms_total"] += entry.get("elapsed_ms") or 0.0
        row["cost_usd_total"] += entry.get("cost_usd") or 0.0
        error_type = entry.get("error_type")
        if error_type == "timeout":
            row["timeouts"] += 1
        elif error_type == "rate_limit":
            row["rate_limited"] += 1
        elif error_type is not None:
            row["other_errors"] += 1

    return [by_seat[pid] for pid in sorted(by_seat)]


def _print_roster(roster: list[dict[str, Any]]) -> None:
    print("--- 席とモデル・借入額 ---")
    print(f"{'席':<6}{'モデル':<32}{'借入額':>14}")
    for r in roster:
        loan = r["loan"]
        loan_str = f"{loan:,}円" if loan is not None else "-"
        print(f"{r['player_id']:<6}{short_model_label(r['model_id']):<32}{loan_str:>14}")


_TRANSCRIPT_KIND_LABELS = {
    "dm": "DM", "broadcast": "全体", "transfer": "送金", "contract_propose": "契約提案",
    "contract_sign": "契約署名", "contract_established": "契約成立", "vote_result": "投票結果",
}

_VOTE_RESULT_LABEL = {"decisive": "決着", "retry": "やり直し", "abort": "打ち切り", None: "不明"}


def _print_transcript(transcript: list[dict[str, Any]], seat_map: dict[str, str]) -> None:
    print("\n--- 会話の書き出し ---")
    if not transcript:
        print("  対象ラウンドの記録なし")
        return
    for r in transcript:
        print(f"【R{r['round_num']}】山 {r['pot']:,}円（持ち越し受け取り {r['carryover_in']:,}円）")
        for v in r["votes"]:
            question = v["question"] or "(不明)"
            print(f"  --- V{v['vote_num']} 質問: {question} ---")
            for entry in v["entries"]:
                turn = entry.get("turn")
                turn_part = f"T{turn:<3}" if turn is not None else "    "
                kind = entry["kind"]
                if kind in ("dm", "broadcast"):
                    frm = seat_label(entry["from"], seat_map)
                    msg = entry["message"] if entry["message"] is not None else "(本文なし)"
                    if kind == "dm":
                        to = seat_label(entry["to"], seat_map)
                        print(f"    {turn_part} DM   {frm} → {to}: {msg}")
                    else:
                        print(f"    {turn_part} 全体 {frm}: {msg}")
                elif kind == "transfer":
                    frm = seat_label(entry["from"], seat_map)
                    to = seat_label(entry["to"], seat_map)
                    print(f"    {turn_part} 送金 {frm} → {to}: {entry['amount']:,}円")
                elif kind == "contract_propose":
                    proposer = seat_label(entry["proposer"], seat_map)
                    parties = ", ".join(seat_label(p, seat_map) for p in (entry["parties"] or []))
                    print(f"    {turn_part} 契約提案 {entry['contract_id']} 提案者 {proposer} 当事者 {parties}")
                    for ob in entry.get("obligations") or []:
                        obligor = seat_label(ob.get("obligor"), seat_map)
                        counterparty = seat_label(ob.get("counterparty"), seat_map)
                        vn_part = f"V{ob.get('vote_num')}" if ob.get("vote_num") is not None else ""
                        print(
                            f"             義務 {obligor}→{counterparty} "
                            f"[{ob.get('ob_type')}] R{ob.get('round_num')}{vn_part} {ob.get('details')}",
                        )
                elif kind == "contract_sign":
                    signer = seat_label(entry["signer"], seat_map)
                    print(f"    {turn_part} 契約署名 {entry['contract_id']} 署名者 {signer}")
                elif kind == "contract_established":
                    parties = ", ".join(seat_label(p, seat_map) for p in (entry["parties"] or []))
                    print(f"         契約成立 seq{entry['contract_seq']} {entry['contract_id']} 当事者 {parties}")
                elif kind == "vote_result":
                    votes = entry["votes"]
                    n_yes = sum(1 for v2 in votes.values() if v2 == "YES")
                    n_no = sum(1 for v2 in votes.values() if v2 == "NO")
                    label = _VOTE_RESULT_LABEL.get(entry.get("result"), entry.get("result"))
                    if entry.get("result") == "decisive":
                        elim = ", ".join(seat_label(p, seat_map) for p in entry["eliminated_ids"])
                        print(f"         投票結果 {label} YES {n_yes}人 / NO {n_no}人 → 退場: {elim}")
                    else:
                        print(f"         投票結果 {label} YES {n_yes}人 / NO {n_no}人")


def _print_rounds_table(rounds: list[dict[str, Any]], contracts: dict[str, Any]) -> None:
    print("--- ラウンドごとの結果 ---")
    for r in rounds:
        for v in r["votes"]:
            votes = v["votes"]
            n_yes = sum(1 for vv in votes.values() if vv == "YES")
            n_no = sum(1 for vv in votes.values() if vv == "NO")
            label = _VOTE_RESULT_LABEL.get(v["result"], v["result"])
            line = f"R{r['round_num']:>2}V{v['vote_num']}: {label} YES {n_yes:>2}人 / NO {n_no:>2}人"
            if v["result"] == "decisive":
                line += f" → 退場: {', '.join(v['eliminated_ids'])}"
            elif v["result"] in ("retry", "abort"):
                line += f" → 延長料{v['extension_fee_collected']:,}円（連続{v['consecutive_ties_after']}回）"
            print(line)
        if r["aborted"]:
            if r["destroyed_pot"]:
                print(f"  → R{r['round_num']} 打ち切り。山{r['destroyed_pot']:,}円は消滅")
            else:
                print(f"  → R{r['round_num']} 打ち切り。山{r['carryover_out']:,}円を次ラウンドへ持ち越し")
        else:
            winners = ", ".join(r["winner_ids"])
            print(f"  → R{r['round_num']} 勝ち残り: {winners}（1人あたり{r['payout_per_winner']:,}円）")
        tags = []
        if r["auto_commit_ids"]:
            tags.append(f"AUTO: {', '.join(sorted(set(r['auto_commit_ids'])))}")
        if r["established_contract_seqs"]:
            tags.append(f"成立: seq{sorted(r['established_contract_seqs'])}")
        if r["type_b_violator_ids"]:
            tags.append(f"型B違反: {', '.join(sorted(set(r['type_b_violator_ids'])))}")
        if r["payment_shortfall_ids"]:
            tags.append(f"払いきれず: {', '.join(sorted(set(r['payment_shortfall_ids'])))}")
        if tags:
            print(f"    [{' / '.join(tags)}]")


def _print_contracts(contracts: dict[str, Any]) -> None:
    print("\n--- 契約 ---")
    if not contracts["established"]:
        print("  成立した契約なし")
    for c in contracts["established"]:
        print(
            f"  seq{c['contract_seq']} {c['contract_id']} "
            f"(R{c['round_num']}V{c['vote_num']}成立, 当事者: {', '.join(c['parties'] or [])})",
        )
    if contracts["expired"]:
        print(f"  失効: {len(contracts['expired'])}本")
    print(f"  型ごとの成立本数: {contracts['ob_type_counts']}")
    print(f"  割合（share_percent）で払う義務の本数: {contracts['share_percent_count']}")
    if contracts["type_b_violator_ids"]:
        print(f"  型B違反者: {', '.join(contracts['type_b_violator_ids'])}")
    if contracts["payment_shortfall_ids"]:
        print(f"  払いきれなかった者: {', '.join(contracts['payment_shortfall_ids'])}")
    for p in contracts["payments"]:
        kind = "全額" if p["paid"] == p["promised"] else ("0円" if p["paid"] == 0 else "部分")
        print(f"    R{p['round_num']} seq{p['contract_seq']} {p['obligor']}→{p['counterparty']} "
              f"[{p['ob_type']}] 約束{p['promised']:,}円 → 支払{p['paid']:,}円（{kind}）")


def _print_final(final: dict[str, Any] | None) -> None:
    print("\n--- 最終結果 ---")
    if final is None:
        print("  GAME_ENDが未記録（試合が最後まで終わっていません）")
        return
    ordered = sorted(final["final_ranks"].items(), key=lambda kv: (kv[1], kv[0]))
    print("  順位 | ID | 最終資産")
    for pid, rank in ordered:
        print(f"  {rank:>3}位 | {pid} | {final['final_assets'].get(pid, 0):>12,}円")


def _print_seats(seats: list[dict[str, Any]]) -> None:
    print("\n--- 席ごとの呼び出し統計 ---")
    print(
        f"{'席':<6}{'モデルID':<34}{'最終資産':>13}{'順位':>5}{'借入額':>12}{'呼出':>5}{'無効':>5}"
        f"{'時間切れ':>7}{'拒否429':>7}{'自動代行':>7}{'予算停止':>7}{'所要時間':>10}{'費用($)':>9}",
    )
    for s in seats:
        assets = f"{s['final_assets']:,}" if s["final_assets"] is not None else "-"
        rank = s["final_rank"] if s["final_rank"] is not None else "-"
        loan = f"{s['loan']:,}" if s["loan"] is not None else "-"
        print(
            f"{s['player_id']:<6}{(s['model_id'] or ''):<34}{assets:>13}{rank:>5}{loan:>12}"
            f"{s['calls']:>5}{s['invalid_responses']:>5}"
            f"{s['timeouts']:>7}{s['rate_limited']:>7}{s['auto_commits']:>7}{s['budget_blocked']:>7}"
            f"{s['elapsed_ms_total']:>8.0f}ms{s['cost_usd_total']:>9.4f}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="少数決 AI試合 結果要約（v0.4）")
    parser.add_argument("--game-id", type=str, required=True)
    parser.add_argument("--log-dir", type=str, default="logs/llm_v0_4")
    parser.add_argument("--json", action="store_true", help="表の代わりにJSONで出力する")
    parser.add_argument("--transcript", action="store_true",
                         help="会話の書き出し（全体発言・DM・送金・契約・投票結果。投票番号と巡入り）を出す")
    parser.add_argument("--round", type=int, action="append", default=None,
                         help="会話の書き出しをこのラウンドだけに絞る（複数指定可。"
                              "指定時は--transcriptを付けなくても自動的に有効になる）")
    args = parser.parse_args()

    records = load_trial_records(args.game_id, args.log_dir)

    try:
        _check_not_v0_3(records["events"])
    except V0_3_RecordError as e:
        print(str(e))
        sys.exit(1)

    roster = summarize_roster(records["seat_map"], records["events"])
    rounds = summarize_rounds(records["events"])
    contracts = summarize_contracts(records["events"])
    final = summarize_final(records["events"])
    reflections = summarize_post_game_reflections(records["events"])
    seats = summarize_seats(records["events"], records["llm_calls"], records["seat_map"], final, roster)

    show_transcript = args.transcript or args.round is not None
    transcript = (
        build_transcript(records["events"], records["llm_calls"], rounds=args.round)
        if show_transcript else None
    )

    if args.json:
        payload = {
            "game_id": args.game_id, "roster": roster, "rounds": rounds, "contracts": contracts,
            "final": final, "post_game_reflections": reflections, "seats": seats,
        }
        if show_transcript:
            payload["transcript"] = transcript
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"=== 少数決 AI試合 要約: {args.game_id} ===")
    _print_roster(roster)
    _print_rounds_table(rounds, contracts)
    _print_contracts(contracts)
    _print_final(final)
    _print_seats(seats)
    if reflections:
        print("\n--- 試合後の振り返り ---")
        for pid in sorted(reflections):
            r = reflections[pid]
            print(f"  {pid}: {r.get('comment', '')}")
    if show_transcript:
        _print_transcript(transcript, records["seat_map"])


if __name__ == "__main__":
    main()
