"""
少数決 AI試合 結果要約コマンド（サイクル2.0新設、サイクル2.1で拡張）

`scripts/llm_trial.py` が書き残した記録（{game_id}_events.jsonl /
{game_id}_llm_calls.jsonl / {game_id}_seat_map.json）だけから要約を作る。
ゲームを再実行しない。試合が途中で止まっていても、そこまでの記録で
要約できる。

サイクル2.1で追加: 冒頭の席ごとのモデル・借入額一覧、`--transcript`/`--round`
での会話の書き出し（全体発言・DM・送金・契約の提案/署名/成立・投票結果を
時系列で並べる）。発言本文はイベント自体（`NEGOTIATION_ACTION.data.message`、
サイクル2.1以降の新しい記録）にあればそれを使い、無い古い記録
（r1_12p_2001等）は`*_llm_calls.jsonl`のレスポンス本文から復元する。

使用方法:
    uv run python scripts/summarize_trial.py --game-id smoke4_2001
    uv run python scripts/summarize_trial.py --game-id r1_12p_2001 --log-dir logs/llm --json
    uv run python scripts/summarize_trial.py --game-id r1_12p_2001 --round 1
    uv run python scripts/summarize_trial.py --game-id r1_12p_2001 --transcript --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.response_parser import extract_json  # noqa: E402


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
) -> dict[tuple[int, str, int], list[dict[str, Any]]]:
    """
    (round_num, player_id, turn) -> そのキーで実際に応答が成功した action群（ファイル順）

    再試行で複数行あることがある（`invalid_response`の注記行は除く）。会話の
    書き出しで、本文がイベントに残っていない古い記録（サイクル2.1より前）から
    本文を復元するための索引（§取りこぼし防止: イベントの行為種別と一致する
    「最後の」要素を使う＝退けられた途中案ではなく、実際に採用された応答）。
    """
    index: dict[tuple[int, str, int], list[dict[str, Any]]] = {}
    for entry in llm_calls:
        if entry.get("phase") != "negotiation" or entry.get("invalid_response"):
            continue
        rn, pid, turn = entry.get("round_num"), entry.get("player_id"), entry.get("turn")
        if rn is None or pid is None or turn is None:
            continue
        obj = extract_json(entry.get("response_text") or "")
        if not isinstance(obj, dict):
            continue
        action = obj.get("action")
        if not isinstance(action, dict):
            continue
        index.setdefault((rn, pid, turn), []).append(action)
    return index


def _lookup_message(
    index: dict[tuple[int, str, int], list[dict[str, Any]]],
    round_num: int, pid: str | None, turn: int | None, action_type: str,
) -> str | None:
    """本文がイベントに無い場合のみ使う復元経路（llm_callsからの最後の一致を採る）"""
    if pid is None or turn is None:
        return None
    for action in reversed(index.get((round_num, pid, turn), [])):
        if action.get("type") == action_type:
            message = action.get("message")
            return message if isinstance(message, str) else None
    return None


_TRANSCRIPT_EVENT_TYPES = {
    "NEGOTIATION_ACTION", "TRANSFER", "CONTRACT_PROPOSED",
    "CONTRACT_SIGNED", "CONTRACT_ESTABLISHED", "VOTE_REVEALED", "MINORITY_RESOLVED",
}


def build_transcript(
    events: list[dict[str, Any]], llm_calls: list[dict[str, Any]], rounds: list[int] | None = None,
) -> list[dict[str, Any]]:
    """
    ラウンドごとに、全体発言・DM・送金・契約の提案/署名/成立・投票結果を時系列で並べる

    `rounds`指定時はそのラウンドだけ返す（指定順ではなく、試合内の進行順）。
    投票結果はVOTE_REVEALEDとMINORITY_RESOLVEDを1件に畳む。

    Returns:
        [{"round_num", "question", "carryover", "entries": [...]}]
    """
    neg_index = index_negotiation_actions(llm_calls)
    round_filter = set(rounds) if rounds else None
    by_round: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    pending_vote: dict[int, dict[str, Any]] = {}

    def _get_round(rn: int) -> dict[str, Any]:
        if rn not in by_round:
            by_round[rn] = {"round_num": rn, "question": None, "carryover": 0, "entries": []}
            order.append(rn)
        return by_round[rn]

    for e in events:
        et = e.get("event_type")
        rn = e.get("round_num", 0)
        if rn <= 0 or (round_filter is not None and rn not in round_filter):
            continue
        data = e.get("data") or {}

        if et == "ROUND_OPEN":
            row = _get_round(rn)
            row["question"] = data.get("question")
            row["carryover"] = data.get("carryover", 0)
            continue

        if et not in _TRANSCRIPT_EVENT_TYPES:
            continue
        row = _get_round(rn)
        turn = data.get("turn")

        if et == "NEGOTIATION_ACTION":
            action = data.get("action")
            if action not in ("dm", "broadcast") or data.get("success") is False:
                continue  # 失敗した行動・発言を伴わない行動（transfer/repay等）は対象外
            pid = data.get("player_id")
            message = data.get("message")
            if message is None:
                # 本文がイベントに無い古い記録（サイクル2.1より前）のみllm_callsから復元する
                message = _lookup_message(neg_index, rn, pid, turn, action)
            row["entries"].append({
                "turn": turn, "kind": action, "from": pid,
                "to": data.get("to"), "message": message,
            })
        elif et == "TRANSFER":
            row["entries"].append({
                "turn": turn, "kind": "transfer",
                "from": data.get("player_id"), "to": data.get("to"), "amount": data.get("amount"),
            })
        elif et == "CONTRACT_PROPOSED":
            row["entries"].append({
                "turn": turn, "kind": "contract_propose",
                "contract_id": data.get("contract_id"), "proposer": data.get("proposer"),
                "parties": data.get("parties"), "obligations": data.get("obligations"),
            })
        elif et == "CONTRACT_SIGNED":
            row["entries"].append({
                "turn": turn, "kind": "contract_sign",
                "contract_id": data.get("contract_id"), "signer": data.get("signer"),
            })
        elif et == "CONTRACT_ESTABLISHED":
            row["entries"].append({
                "turn": None, "kind": "contract_established",
                "contract_id": data.get("contract_id"), "parties": data.get("parties"),
                "contract_seq": data.get("contract_seq"),
            })
        elif et == "VOTE_REVEALED":
            entry = {
                "turn": None, "kind": "vote_result", "votes": data.get("votes", {}),
                "minority_side": None, "minority_ids": [], "payout_per_minority": 0,
                "carryover_after": None,
            }
            row["entries"].append(entry)
            pending_vote[rn] = entry
        elif et == "MINORITY_RESOLVED":
            entry = pending_vote.pop(rn, None)
            if entry is None:
                entry = {"turn": None, "kind": "vote_result", "votes": {}}
                row["entries"].append(entry)
            entry["minority_side"] = data.get("minority_side")
            entry["minority_ids"] = data.get("minority_ids", [])
            entry["payout_per_minority"] = data.get("payout_per_minority", 0)
            entry["carryover_after"] = data.get("carryover_after", 0)

    return [by_round[rn] for rn in order]


def summarize_rounds(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ラウンドごとの票の割れ方・配当・持ち越し・成立契約・違反・払いきれずをまとめる"""
    by_round: dict[int, dict[str, Any]] = {}

    def _row(round_num: int) -> dict[str, Any]:
        return by_round.setdefault(round_num, {
            "round_num": round_num, "votes": {}, "minority_side": None,
            "minority_ids": [], "payout_per_minority": 0, "carryover_after": 0,
            "auto_commit_ids": [], "established_contract_seqs": [],
            "type_b_violator_ids": [], "payment_shortfall_ids": [],
        })

    for e in events:
        et = e.get("event_type")
        rn = e.get("round_num", 0)
        data = e.get("data") or {}
        if et == "VOTE_REVEALED":
            _row(rn)["votes"] = data.get("votes", {})
        elif et == "MINORITY_RESOLVED":
            row = _row(rn)
            row["minority_side"] = data.get("minority_side")
            row["minority_ids"] = data.get("minority_ids", [])
            row["payout_per_minority"] = data.get("payout_per_minority", 0)
            row["carryover_after"] = data.get("carryover_after", 0)
        elif et == "AUTO_COMMIT":
            _row(rn)["auto_commit_ids"].append(data.get("player_id"))
        elif et == "CONTRACT_ESTABLISHED":
            _row(rn)["established_contract_seqs"].append(data.get("contract_seq"))
        elif et == "TYPE_B_VIOLATION":
            _row(rn)["type_b_violator_ids"].extend(data.get("player_ids", []))
        elif et == "PAYMENT_SHORTFALL":
            _row(rn)["payment_shortfall_ids"].extend(data.get("player_ids", []))

    return [by_round[rn] for rn in sorted(by_round) if rn > 0]


def summarize_contracts(events: list[dict[str, Any]]) -> dict[str, Any]:
    """成立した契約と、その支払い（CONTRACT_PAYMENT）をまとめる"""
    established: list[dict[str, Any]] = []
    payments: list[dict[str, Any]] = []
    for e in events:
        et = e.get("event_type")
        data = e.get("data") or {}
        if et == "CONTRACT_ESTABLISHED":
            established.append({
                "round_num": e.get("round_num"), "contract_id": data.get("contract_id"),
                "contract_seq": data.get("contract_seq"), "parties": data.get("parties"),
            })
        elif et == "CONTRACT_PAYMENT":
            payments.append({
                "round_num": e.get("round_num"), "contract_id": data.get("contract_id"),
                "contract_seq": data.get("contract_seq"), "obligor": data.get("obligor"),
                "counterparty": data.get("counterparty"), "ob_type": data.get("ob_type"),
                "promised": data.get("promised"), "paid": data.get("paid"),
            })
    established.sort(key=lambda c: (c["contract_seq"] is None, c["contract_seq"]))
    return {"established": established, "payments": payments}


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
) -> list[dict[str, Any]]:
    """
    席ごとの呼び出し回数・無効な応答・時間切れ・拒否(429)・自動代行・予算停止・
    所要時間・費用をまとめる（§完了報告「席ごとの呼び出し回数、無効な応答、
    時間切れ、自動代行、所要時間、費用」）。
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

    by_seat: dict[str, dict[str, Any]] = {}
    for pid in sorted(seat_map):
        by_seat[pid] = {
            "player_id": pid, "model_id": seat_map[pid], "calls": 0,
            "invalid_responses": 0, "timeouts": 0, "rate_limited": 0, "other_errors": 0,
            "auto_commits": auto_commit_counts.get(pid, 0),
            "budget_blocked": budget_blocked_counts.get(pid, 0),
            "elapsed_ms_total": 0.0, "cost_usd_total": 0.0,
        }

    for entry in llm_calls:
        pid = entry.get("player_id")
        if pid is None:
            continue
        row = by_seat.setdefault(pid, {
            "player_id": pid, "model_id": entry.get("model_id"), "calls": 0,
            "invalid_responses": 0, "timeouts": 0, "rate_limited": 0, "other_errors": 0,
            "auto_commits": auto_commit_counts.get(pid, 0),
            "budget_blocked": budget_blocked_counts.get(pid, 0),
            "elapsed_ms_total": 0.0, "cost_usd_total": 0.0,
        })
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


def _print_transcript(transcript: list[dict[str, Any]], seat_map: dict[str, str]) -> None:
    print("\n--- 会話の書き出し ---")
    if not transcript:
        print("  対象ラウンドの記録なし")
        return
    for r in transcript:
        question = r["question"] or "(不明)"
        print(f"【R{r['round_num']}】質問: {question}（持ち越し {r['carryover']:,}円）")
        for entry in r["entries"]:
            turn = entry.get("turn")
            turn_part = f"T{turn:<3}" if turn is not None else "    "
            kind = entry["kind"]
            if kind in ("dm", "broadcast"):
                frm = seat_label(entry["from"], seat_map)
                msg = entry["message"] if entry["message"] is not None else "(本文なし)"
                if kind == "dm":
                    to = seat_label(entry["to"], seat_map)
                    print(f"  {turn_part} DM   {frm} → {to}: {msg}")
                else:
                    print(f"  {turn_part} 全体 {frm}: {msg}")
            elif kind == "transfer":
                frm = seat_label(entry["from"], seat_map)
                to = seat_label(entry["to"], seat_map)
                print(f"  {turn_part} 送金 {frm} → {to}: {entry['amount']:,}円")
            elif kind == "contract_propose":
                proposer = seat_label(entry["proposer"], seat_map)
                parties = ", ".join(seat_label(p, seat_map) for p in (entry["parties"] or []))
                print(f"  {turn_part} 契約提案 {entry['contract_id']} 提案者 {proposer} 当事者 {parties}")
                for ob in entry.get("obligations") or []:
                    obligor = seat_label(ob.get("obligor"), seat_map)
                    counterparty = seat_label(ob.get("counterparty"), seat_map)
                    print(
                        f"         義務 {obligor}→{counterparty} "
                        f"[{ob.get('ob_type')}] R{ob.get('round_num')} {ob.get('details')}",
                    )
            elif kind == "contract_sign":
                signer = seat_label(entry["signer"], seat_map)
                print(f"  {turn_part} 契約署名 {entry['contract_id']} 署名者 {signer}")
            elif kind == "contract_established":
                parties = ", ".join(seat_label(p, seat_map) for p in (entry["parties"] or []))
                print(f"       契約成立 seq{entry['contract_seq']} {entry['contract_id']} 当事者 {parties}")
            elif kind == "vote_result":
                votes = entry["votes"]
                n_yes = sum(1 for v in votes.values() if v == "YES")
                n_no = sum(1 for v in votes.values() if v == "NO")
                if entry.get("minority_side") is None:
                    print(f"       投票結果 YES {n_yes}人 / NO {n_no}人 → 少数派なし")
                else:
                    minority = ", ".join(seat_label(p, seat_map) for p in entry["minority_ids"])
                    print(
                        f"       投票結果 YES {n_yes}人 / NO {n_no}人 "
                        f"→ 少数派={entry['minority_side']}（{minority}）"
                        f"1人あたり+{entry['payout_per_minority']:,}円",
                    )


def _print_rounds_table(rounds: list[dict[str, Any]]) -> None:
    print("--- ラウンドごとの結果 ---")
    for r in rounds:
        votes = r["votes"]
        n_yes = sum(1 for v in votes.values() if v == "YES")
        n_no = sum(1 for v in votes.values() if v == "NO")
        if r["minority_side"] is None:
            line = f"R{r['round_num']:>2}: YES {n_yes:>2}人 / NO {n_no:>2}人 → 少数派なし。持ち越し {r['carryover_after']:,}円"
        else:
            line = (
                f"R{r['round_num']:>2}: YES {n_yes:>2}人 / NO {n_no:>2}人 "
                f"→ 少数派={r['minority_side']}（{', '.join(r['minority_ids'])}）"
                f"1人あたり+{r['payout_per_minority']:,}円"
            )
        if r["auto_commit_ids"]:
            line += f" [AUTO: {', '.join(sorted(r['auto_commit_ids']))}]"
        if r["established_contract_seqs"]:
            line += f" [成立: seq{sorted(r['established_contract_seqs'])}]"
        if r["type_b_violator_ids"]:
            line += f" [型B違反: {', '.join(sorted(set(r['type_b_violator_ids'])))}]"
        if r["payment_shortfall_ids"]:
            line += f" [払いきれず: {', '.join(sorted(set(r['payment_shortfall_ids'])))}]"
        print(line)


def _print_contracts(contracts: dict[str, Any]) -> None:
    print("\n--- 契約 ---")
    if not contracts["established"]:
        print("  成立した契約なし")
    for c in contracts["established"]:
        print(f"  seq{c['contract_seq']} {c['contract_id']} (R{c['round_num']}成立, 当事者: {', '.join(c['parties'] or [])})")
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
    print(f"{'席':<6}{'モデルID':<36}{'呼出':>5}{'無効':>5}{'時間切れ':>7}{'拒否429':>7}"
          f"{'自動代行':>7}{'予算停止':>7}{'所要時間':>10}{'費用($)':>9}")
    for s in seats:
        print(
            f"{s['player_id']:<6}{(s['model_id'] or ''):<36}{s['calls']:>5}{s['invalid_responses']:>5}"
            f"{s['timeouts']:>7}{s['rate_limited']:>7}{s['auto_commits']:>7}{s['budget_blocked']:>7}"
            f"{s['elapsed_ms_total']:>8.0f}ms{s['cost_usd_total']:>9.4f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="少数決 AI試合 結果要約")
    parser.add_argument("--game-id", type=str, required=True)
    parser.add_argument("--log-dir", type=str, default="logs/llm")
    parser.add_argument("--json", action="store_true", help="表の代わりにJSONで出力する")
    parser.add_argument("--transcript", action="store_true",
                         help="会話の書き出し（全体発言・DM・送金・契約・投票結果）を出す")
    parser.add_argument("--round", type=int, action="append", default=None,
                         help="会話の書き出しをこのラウンドだけに絞る（複数指定可。"
                              "指定時は--transcriptを付けなくても自動的に有効になる）")
    args = parser.parse_args()

    records = load_trial_records(args.game_id, args.log_dir)
    roster = summarize_roster(records["seat_map"], records["events"])
    rounds = summarize_rounds(records["events"])
    contracts = summarize_contracts(records["events"])
    final = summarize_final(records["events"])
    reflections = summarize_post_game_reflections(records["events"])
    seats = summarize_seats(records["events"], records["llm_calls"], records["seat_map"])

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
    _print_rounds_table(rounds)
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
