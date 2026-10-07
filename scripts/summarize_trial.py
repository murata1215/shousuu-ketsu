"""
少数決 AI試合 結果要約コマンド（サイクル2.0新設）

`scripts/llm_trial.py` が書き残した記録（{game_id}_events.jsonl /
{game_id}_llm_calls.jsonl / {game_id}_seat_map.json）だけから要約を作る。
ゲームを再実行しない。試合が途中で止まっていても、そこまでの記録で
要約できる。

使用方法:
    uv run python scripts/summarize_trial.py --game-id smoke4_2001
    uv run python scripts/summarize_trial.py --game-id r1_12p_2001 --log-dir logs/llm --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
    args = parser.parse_args()

    records = load_trial_records(args.game_id, args.log_dir)
    rounds = summarize_rounds(records["events"])
    contracts = summarize_contracts(records["events"])
    final = summarize_final(records["events"])
    reflections = summarize_post_game_reflections(records["events"])
    seats = summarize_seats(records["events"], records["llm_calls"], records["seat_map"])

    if args.json:
        print(json.dumps({
            "game_id": args.game_id, "rounds": rounds, "contracts": contracts,
            "final": final, "post_game_reflections": reflections, "seats": seats,
        }, ensure_ascii=False, indent=2))
        return

    print(f"=== 少数決 AI試合 要約: {args.game_id} ===")
    _print_rounds_table(rounds)
    _print_contracts(contracts)
    _print_final(final)
    _print_seats(seats)
    if reflections:
        print("\n--- 試合後の振り返り ---")
        for pid in sorted(reflections):
            r = reflections[pid]
            print(f"  {pid}: {r.get('comment', '')}")


if __name__ == "__main__":
    main()
