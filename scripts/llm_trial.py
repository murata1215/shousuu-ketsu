"""
少数決 AI試合 実行スクリプト（サイクル2.0新設、サイクル4.2bでv0.4対応）

質問生成 → 借入額の選択（全員同時）→ 各ラウンド（Open→Negotiation→Commit→
Settlement→Finance→振り返りを1〜6回の投票ぶん繰り返す）→ 試合後の振り返り、
までを1本で回す。既定はL12R4V6（12人・4ラウンド・最大6投票）。
gentei-janken `scripts/llm_trial.py`（B分類）の骨格（ロスター指定・preflight・
予算キャップ・JSONLログ）をコピーし、少数決向けに差し替えた（veteran機能は
少数決の仕様に存在しないため全削除、CLAUDE.md過去の落とし穴③の回避）。

v0.4の記録は `logs/llm_v0_4/` に出す（既定）。公開中のビューアは`logs/llm/`
（v0.3の記録）を読んでおり、サイクル4.3で対応するまでv0.4の記録を混ぜない。

使用方法:
    # 事前の疎通確認だけ（APIを1コールずつ叩いて成否・所要時間を表にする）
    uv run python scripts/llm_trial.py --roster "L1,L1,L1,L1,L1,L1,L1,L1,L1,L1,L1,L1" \\
        --seed 1 --preflight-only

    # 小さな通し確認（4席・R1だけ・交渉の巡をラウンド最初2/決着後2/やり直し1に縮小）
    uv run python scripts/llm_trial.py --roster "L3,L6,DR_HAIKU,DR_LUNA" \\
        --seed 2001 --stop-after-round 1 \\
        --negotiation-max-turns-first 2 --negotiation-max-turns-next 2 --negotiation-max-turns-retry 1 \\
        --parallel 2 --per-seat-cap-usd 0.05 --game-cap-usd 0.12 --game-id smoke4_2001

    # 12席・R1だけの本走り（人間がフォアグラウンドで起動する想定）
    uv run python scripts/llm_trial.py \\
        --roster "DR_FABLE,DR_OPUS,DR_SONNET5,DR_SOL,DR_TERRA,DR_LUNA,L1,L2,L3,L4,L5,L6" \\
        --seed 2001 --stop-after-round 1 --parallel 4 \\
        --per-seat-cap-usd 0.5 --game-cap-usd 3.0 --game-id r1_12p_2001
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from engine.config import GameConfig  # noqa: E402
from engine.events import EventLogger  # noqa: E402
from engine.game import Game  # noqa: E402
from llm.adapters import AdapterError, create_adapter  # noqa: E402
from llm.constants import DEFAULT_TEMPERATURE  # noqa: E402
from llm.game_cost_budget import GameCostBudget  # noqa: E402
from llm.llm_agent import LLMAgent, build_json_object_request_options  # noqa: E402
from llm.llm_logger import LLMLogger  # noqa: E402
from llm.models import get_model  # noqa: E402
from llm.questions import DEFAULT_HISTORY_PATH, append_question, generate_questions, load_questions_file  # noqa: E402
from llm.response_parser import extract_json  # noqa: E402

_PREFLIGHT_PROBE_USER_MESSAGE = (
    "接続確認です。以下のJSON形式のみで応答してください"
    '（前置き・説明文・コードブロックの装飾は不要）:\n{"action": {"type": "pass"}}'
)


def parse_roster(roster_str: str) -> list[str]:
    """
    "L1,L1,M3,M3" または "L6:6"（L6を6席分）のカンマ区切りモデルキー列を解釈する

    gentei-janken `scripts/simulate.py`/`scripts/llm_trial.py` と同じ ":count" 記法。
    モデルキーの妥当性はllm.models.get_model()が呼び出し元で検証するため、
    ここでは展開のみ行う。
    """
    result: list[str] = []
    for part in roster_str.split(","):
        part = part.strip()
        if not part:
            continue
        key, _, count_str = part.partition(":")
        key = key.strip()
        count = int(count_str) if count_str else 1
        result.extend([key] * count)
    return result


def assign_seats(model_keys: list[str], seed: int) -> dict[str, str]:
    """
    モデルキー列をシードで混ぜ、P01〜P{n}へ割り当てる（同じseedなら同じ割り当て）

    `engine/rng.py::GameRng` とは独立した乱数源を使う（ゲーム内乱数の消費順を
    1つでも変えると既存の再現性テストが壊れるため、席の割り当てには専用の
    `random.Random(seed)` を新たに起こす。CLAUDE.md「仕様書に書いていないこと」
    の一覧に記載）。
    """
    pids = [f"P{i:02d}" for i in range(1, len(model_keys) + 1)]
    shuffled = list(model_keys)
    random.Random(seed).shuffle(shuffled)
    return dict(zip(pids, shuffled))


def build_agents(
    seat_models: dict[str, str],
    llm_logger: LLMLogger,
    config: GameConfig,
    game_cost_budget: GameCostBudget,
    on_call_done: Any = None,
    adapter_factory: Any = create_adapter,
) -> dict[str, LLMAgent]:
    """
    席ID→モデルキーの割り当てから、LLMAgent一式を組み立てる

    adapter_factory: ModelInfo -> アダプタ（既定: llm.adapters.create_adapter）。
    テストから偽アダプタ（tests/helpers.py::FakeAdapter）を注入できるよう
    引数化している（AIを呼ばないテストで本物のアダプタを生成しないため）。
    """
    agents: dict[str, LLMAgent] = {}
    for pid, model_key in seat_models.items():
        model_info = get_model(model_key)
        adapter = adapter_factory(model_info)
        agents[pid] = LLMAgent(
            pid, model_info, adapter, llm_logger, config, game_cost_budget,
            on_call_done=on_call_done,
        )
    return agents


# ----------------------------------------------------------------------
# 事前の疎通確認（preflight）
# ----------------------------------------------------------------------

def _preflight_probe(player_id: str, agent: LLMAgent) -> dict[str, Any]:
    """
    1席をJSON応答まで実際に検証する（gentei-janken `scripts/llm_trial.py` の
    `_preflight_probe` と同じ狙い: HTTP 200のままロールプレイを拒否する事故
    クラスを、呼び出し失敗とは別に検出する）。

    LLMAgentが実際に使うsystem prompt（`agent._system_prompt`。DevRelay席は
    provider側が匿名化行を付与するよう既にinclude_anonymization=Falseで
    構築済み）をそのまま使う。GameCostBudgetは経由しない（本番の予算/席
    キャップを誤って消費させないため）。llm_loggerへも記録しない。

    Returns:
        {"player_id", "model_id", "ok", "elapsed_ms", "reason"}（reasonはok時None）
    """
    system = agent._system_prompt
    request_options = build_json_object_request_options(
        agent.model_info, system, _PREFLIGHT_PROBE_USER_MESSAGE,
        player_id=player_id, phase="preflight",
    )
    started = time.monotonic()
    try:
        text, _usage = agent.adapter.complete(
            system=system,
            messages=[{"role": "user", "content": _PREFLIGHT_PROBE_USER_MESSAGE}],
            max_tokens=200,
            temperature=DEFAULT_TEMPERATURE,
            request_options=request_options,
        )
    except AdapterError as e:
        elapsed_ms = (time.monotonic() - started) * 1000
        return {
            "player_id": player_id, "model_id": agent.model_info.model_id,
            "ok": False, "elapsed_ms": elapsed_ms, "reason": f"AdapterError: {e}",
        }
    elapsed_ms = (time.monotonic() - started) * 1000
    if not text:
        return {
            "player_id": player_id, "model_id": agent.model_info.model_id,
            "ok": False, "elapsed_ms": elapsed_ms, "reason": "空応答",
        }
    obj = extract_json(text)
    action = obj.get("action") if isinstance(obj, dict) else None
    if not isinstance(action, dict) or action.get("type") != "pass":
        return {
            "player_id": player_id, "model_id": agent.model_info.model_id,
            "ok": False, "elapsed_ms": elapsed_ms, "reason": f"JSON不正または拒否応答: {text[:200]!r}",
        }
    return {
        "player_id": player_id, "model_id": agent.model_info.model_id,
        "ok": True, "elapsed_ms": elapsed_ms, "reason": None,
    }


def run_preflight(agents: dict[str, LLMAgent], max_parallel: int) -> list[dict[str, Any]]:
    """
    全席の疎通確認を行い、player_id順に並べて返す。

    max_parallelは「同時に呼ぶ席数」の上限。DevRelay席は同じサブスクの枠を
    複数席で分け合うため、大きすぎる値を渡すと429（レート制限）が出うる
    （呼び出し側=本スクリプトの引数で選ぶ。--helpの説明を参照）。
    """
    results: list[dict[str, Any]] = []
    if max_parallel > 1 and len(agents) > 1:
        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            futures = {
                pool.submit(_preflight_probe, pid, agent): pid for pid, agent in agents.items()
            }
            for future in as_completed(futures):
                results.append(future.result())
    else:
        for pid, agent in agents.items():
            results.append(_preflight_probe(pid, agent))
    results.sort(key=lambda r: r["player_id"])
    return results


def _print_preflight_table(results: list[dict[str, Any]]) -> None:
    print(f"{'席':<6}{'モデルID':<36}{'結果':<6}{'所要時間':>10}  理由")
    for r in results:
        status = "OK" if r["ok"] else "FAIL"
        reason = r["reason"] or ""
        print(f"{r['player_id']:<6}{r['model_id']:<36}{status:<6}{r['elapsed_ms']:>8.0f}ms  {reason}")


# ----------------------------------------------------------------------
# 途中経過の表示
# ----------------------------------------------------------------------

class ProgressPrinter:
    """
    LLMAgent.on_call_done フック経由で、呼び出しごとに1行ずつ画面へ出す
    （ラウンド・投票・手番・席・所要時間・失敗。並列実行時でも行が交差しないよう
    ロックで直列化する）。

    サイクル4.2bでvote_numを行に足した（例: `[P07] R1V2 T3 negotiation …`）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.fail_counts: dict[str, int] = {}

    def __call__(self, info: dict[str, Any]) -> None:
        vote_part = f"V{info['vote_num']}" if info.get("vote_num") is not None else ""
        turn_part = f" T{info['turn']}" if info.get("turn") is not None else ""
        status = "OK" if info["ok"] else f"FAIL({info['error_type']})"
        line = (
            f"  [{info['player_id']}] R{info['round_num']}{vote_part}{turn_part} {info['phase']:<10} "
            f"{info['elapsed_ms']:>7.0f}ms {status}"
        )
        with self._lock:
            if not info["ok"]:
                self.fail_counts[info["player_id"]] = self.fail_counts.get(info["player_id"], 0) + 1
            # 出力先がファイル（nohup等）でもブロックバッファに溜め込まず
            # 1行ごとにすぐ書き出す（サイクル2.1。長い試合をtail -fで追えるように）。
            print(line, flush=True)


_VOTE_RESULT_LABEL = {"decisive": "決着", "retry": "やり直し", "abort": "打ち切り"}


class VoteProgressPrinter:
    """
    EventLogger.on_event フック経由で、投票が決まるたびに1行だけ画面へ出す
    （サイクル4.2b新設）。

    例:
      R1V2 決着 YES3対NO2 退場: P01,P04,P09
      R1V2 やり直し YES6対NO6 連続1回 延長料60万
      R1V3 打ち切り YES6対NO6 山1,560万をR2へ持ち越し

    「打ち切り」は、そのラウンドの持ち越し/消滅の額がROUND_RESOLVED
    （投票のすぐ後に続く）で確定するまで1行の表示を待ち合わせる。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_votes: dict[str, str] = {}
        self._pending_abort: tuple[int, int, int, int] | None = None

    def __call__(self, event: Any) -> None:
        if event.event_type == "VOTE_REVEALED":
            with self._lock:
                self._last_votes = event.data.get("votes") or {}
            return
        if event.event_type == "VOTE_RESOLVED":
            self._on_vote_resolved(event)
            return
        if event.event_type == "ROUND_RESOLVED":
            self._on_round_resolved(event)
            return

    def _on_vote_resolved(self, event: Any) -> None:
        d = event.data
        with self._lock:
            votes = self._last_votes
        yes_n = sum(1 for v in votes.values() if v == "YES")
        no_n = sum(1 for v in votes.values() if v == "NO")
        result = d["result"]
        label = _VOTE_RESULT_LABEL[result]
        if result == "decisive":
            elim = ",".join(d.get("eliminated_ids") or [])
            print(f"R{event.round_num}V{event.vote_num} {label} YES{yes_n}対NO{no_n} 退場: {elim}", flush=True)
        elif result == "retry":
            ext_man = (d.get("extension_fee_collected") or 0) // 10_000
            print(
                f"R{event.round_num}V{event.vote_num} {label} YES{yes_n}対NO{no_n} "
                f"連続{d.get('consecutive_ties_after')}回 延長料{ext_man}万", flush=True,
            )
        else:  # abort — 持ち越し/消滅の額はROUND_RESOLVEDで分かるまで待つ
            with self._lock:
                self._pending_abort = (event.round_num, event.vote_num, yes_n, no_n)

    def _on_round_resolved(self, event: Any) -> None:
        d = event.data
        if not d.get("aborted"):
            return
        with self._lock:
            pending = self._pending_abort
            self._pending_abort = None
        if pending is None:
            return
        round_num, vote_num, yes_n, no_n = pending
        if d.get("destroyed_pot"):
            pot_text = f"山{d['destroyed_pot'] // 10_000:,}万は消滅"
        else:
            pot_man = (d.get("carryover_out") or 0) // 10_000
            pot_text = f"山{pot_man:,}万をR{round_num + 1}へ持ち越し"
        print(f"R{round_num}V{vote_num} 打ち切り YES{yes_n}対NO{no_n} {pot_text}", flush=True)


def _enable_line_buffering(stream: Any) -> bool:
    """
    標準出力がファイルへリダイレクトされていても、1行ごとに書き出されるようにする

    （サイクル2.1）。`nohup ... > file` のようにリダイレクトすると、Pythonは
    端末向けの行バッファから完全バッファへ自動的に切り替わり、途中経過が
    バッファに溜まって`tail -f`で見えなくなる。`reconfigure(line_buffering=True)`
    （Python 3.7+の`io.TextIOWrapper`が持つ）で明示的に行バッファへ戻す。

    `reconfigure` を持たないストリーム（テストで差し替える`io.StringIO`等）には
    何もせず `False` を返す（pytestのcapsys差し替え等で壊れないようにするため）。

    Returns:
        reconfigureを実際に呼べた（＝適用できた）ならTrue
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return False
    reconfigure(line_buffering=True)
    return True


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="少数決 AI試合 実行スクリプト")
    parser.add_argument("--roster", type=str, required=True,
                         help='モデルキーのカンマ区切り（例: "L1,L1,L3,L3,H1,H1" または '
                              '":count"で同モデル複数席をまとめて指定 "L6:6"）')
    parser.add_argument("--seed", type=int, default=42,
                         help="ゲーム乱数シード。席の割り当て・質問生成の予備選択もこの値から導出する")
    parser.add_argument("--num-rounds", type=int, default=None,
                         help="ルール上の総ラウンド数。未指定ならGameConfigの既定（L12R4V6: 4ラウンド）")
    parser.add_argument("--stop-after-round", type=int, default=None,
                         help="ルール上のラウンド数は変えずに、実行だけこのラウンドで打ち切る"
                              "（is_final判定・持ち越し消滅はnum_rounds基準のまま変わらない）")
    parser.add_argument("--negotiation-max-turns", type=int, default=None,
                         help="交渉の最大巡数を3種類（最初/決着後/やり直し）すべて同じ値に上書きする"
                              "短縮形。個別の--negotiation-max-turns-{first,next,retry}が"
                              "指定されていればそちらを優先する")
    parser.add_argument("--negotiation-max-turns-first", type=int, default=None,
                         help="ラウンド最初の投票（V1）の交渉の最大巡数を上書きする（既定: 10）")
    parser.add_argument("--negotiation-max-turns-next", type=int, default=None,
                         help="決着の後の投票の交渉の最大巡数を上書きする（既定: 6）")
    parser.add_argument("--negotiation-max-turns-retry", type=int, default=None,
                         help="やり直しの再投票の交渉の最大巡数を上書きする（既定: 3）")
    parser.add_argument("--game-id", type=str, default=None)
    parser.add_argument("--log-dir", type=str, default="logs/llm_v0_4",
                         help="記録の出力先（既定: logs/llm_v0_4）。logs/llm はv0.3本戦の記録専用"
                              "のため、サイクル4.3でビューアが対応するまでv0.4の記録を混ぜない")
    parser.add_argument("--per-seat-cap-usd", type=float, default=0.5,
                         help="1席が1試合で使える実績コスト上限（既定: 0.5ドル）")
    parser.add_argument("--game-cap-usd", type=float, default=3.0,
                         help="試合全体で使える実績コスト上限（既定: 3.0ドル）")
    parser.add_argument("--parallel", type=int, default=1,
                         help="借入・投票・振り返り・試合後振り返りで同時に呼ぶ席数の上限"
                              "（既定1=逐次。交渉は常に逐次で対象外）。DevRelay席は同じ"
                              "サブスクの枠を複数席で分け合うため、大きすぎる値は429の"
                              "おそれがある。控えめな値を選ぶこと")
    parser.add_argument("--no-preflight", action="store_true",
                         help="試合開始前の全席疎通確認をスキップする")
    parser.add_argument("--preflight-only", action="store_true",
                         help="疎通確認だけを行い、試合は実行せず終了する（--no-preflightより優先）")
    parser.add_argument("--questions", type=str, default=None,
                         help="固定の質問セットファイル（1行1問）。指定時は出題AIを呼ばない")
    parser.add_argument("--question-model", type=str, default=None,
                         help="出題AIのモデルキー（既定: GameConfig.question_model = DR_HAIKU）")
    parser.add_argument("--question-history-path", type=str, default=str(DEFAULT_HISTORY_PATH),
                         help="直近30問の取得元・追記先（既定: data/question_history.jsonl）")
    return parser


def run_trial(args: argparse.Namespace, adapter_factory: Any = create_adapter) -> dict[str, Any]:
    """
    引数一式から1試合（または疎通確認だけ）を実行する。

    `main()` から呼ばれる本体。`adapter_factory` を引数化しているのは、
    テストから `tests/helpers.py::FakeAdapter` を注入してAIを呼ばずに
    通しテストできるようにするため（本番は既定の `create_adapter` のまま）。

    Returns:
        {"exit_code", "result"（GameResult|None）, "game_id", "log_dir",
         "preflight_results"（疎通確認を行った場合のみ）}
    """
    model_keys = parse_roster(args.roster)
    config_kwargs: dict[str, Any] = {"num_players": len(model_keys)}
    if args.num_rounds is not None:
        config_kwargs["num_rounds"] = args.num_rounds
    config = GameConfig(**config_kwargs)

    # 交渉の巡の上限: --negotiation-max-turns は3種類同時指定の短縮形。
    # 個別の--negotiation-max-turns-{first,next,retry}が指定されていれば
    # そちらを優先する（CLAUDE.md: 既存の--negotiation-max-turnsは
    # 存在しないフィールドに書き込んでいて無言で効いていなかったバグを、
    # 実在する3フィールドへ書き込むよう直した）。
    turn_updates: dict[str, int] = {}
    if args.negotiation_max_turns is not None:
        turn_updates["negotiation_max_turns_first"] = args.negotiation_max_turns
        turn_updates["negotiation_max_turns_next"] = args.negotiation_max_turns
        turn_updates["negotiation_max_turns_retry"] = args.negotiation_max_turns
    if args.negotiation_max_turns_first is not None:
        turn_updates["negotiation_max_turns_first"] = args.negotiation_max_turns_first
    if args.negotiation_max_turns_next is not None:
        turn_updates["negotiation_max_turns_next"] = args.negotiation_max_turns_next
    if args.negotiation_max_turns_retry is not None:
        turn_updates["negotiation_max_turns_retry"] = args.negotiation_max_turns_retry
    if turn_updates:
        config = config.model_copy(update=turn_updates)

    game_id = args.game_id or f"trial_seed{args.seed}_{len(model_keys)}p"
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    seat_models = assign_seats(model_keys, args.seed)
    print(f"=== 少数決 AI試合: {game_id} ===")
    print(f"シード: {args.seed} / 席数: {len(model_keys)} / ルール上のラウンド数: {config.num_rounds}")
    if args.stop_after_round is not None:
        print(f"実行の打ち切り: R{args.stop_after_round}まで（ルール上はR{config.num_rounds}）")
    print(f"同時呼び出し数(--parallel): {args.parallel}")
    print("--- 席の割り当て ---")
    for pid in sorted(seat_models):
        model_info = get_model(seat_models[pid])
        print(f"  {pid}: {seat_models[pid]} ({model_info.model_id})")
    print("---")

    logger = EventLogger(
        output_path=log_dir / f"{game_id}_events.jsonl", on_event=VoteProgressPrinter(),
    )
    llm_logger = LLMLogger(log_dir, game_id=game_id)
    game_cost_budget = GameCostBudget(
        per_player_cap_usd=args.per_seat_cap_usd, game_cap_usd=args.game_cap_usd, event_logger=logger,
    )
    progress = ProgressPrinter()

    agents = build_agents(
        seat_models, llm_logger, config, game_cost_budget,
        on_call_done=progress, adapter_factory=adapter_factory,
    )

    seat_map_path = log_dir / f"{game_id}_seat_map.json"
    seat_map_path.write_text(
        json.dumps({pid: agents[pid].model_info.model_id for pid in seat_models}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.preflight_only:
        print(f"[preflight] 全{len(agents)}席の疎通確認中...")
        results = run_preflight(agents, args.parallel)
        _print_preflight_table(results)
        failures = [r for r in results if not r["ok"]]
        if failures:
            print(f"\n[preflight] 失敗: {len(failures)}/{len(agents)}席")
            return {"exit_code": 1, "result": None, "game_id": game_id, "log_dir": log_dir,
                    "preflight_results": results}
        print(f"\n[preflight] 全{len(agents)}席OK")
        return {"exit_code": 0, "result": None, "game_id": game_id, "log_dir": log_dir,
                "preflight_results": results}

    preflight_results: list[dict[str, Any]] | None = None
    if args.no_preflight:
        print("[preflight] スキップ（--no-preflight指定）")
    else:
        print(f"[preflight] 全{len(agents)}席の疎通確認中...")
        preflight_results = run_preflight(agents, args.parallel)
        _print_preflight_table(preflight_results)
        failures = [r for r in preflight_results if not r["ok"]]
        if failures:
            print(f"\n[preflight] 失敗: {len(failures)}/{len(agents)}席")
            print("設定ミスの席を混ぜたまま本番を回すと、その席は気づかれずに"
                  "全ターンpassし続ける「置物」になります。上記を解消してから再実行してください。"
                  "（一時的にこの確認を飛ばす場合は --no-preflight を指定）")
            return {"exit_code": 1, "result": None, "game_id": game_id, "log_dir": log_dir,
                    "preflight_results": preflight_results}
        print(f"[preflight] 全{len(agents)}席OK")
    print("---")

    # --- 質問生成（§5） ---
    if args.questions:
        questions = load_questions_file(args.questions, expected=config.questions_per_game)
        question_info = {"source": "fixed_file", "path": args.questions, "questions": questions}
        print(f"[questions] 固定セット {args.questions} を使用（{len(questions)}問）")
    else:
        qset = generate_questions(
            config, model_key=args.question_model, seed=args.seed,
            history_path=args.question_history_path,
        )
        questions = qset.questions
        question_info = {
            "source": "generated", "model_key": args.question_model or config.question_model,
            "questions": qset.questions, "sources": qset.sources,
            "fallback_used": qset.fallback_used, "raw_text": qset.raw_text, "error": qset.error,
        }
        print(f"[questions] AI採用: {qset.sources.count('ai')}問 / 予備補完: {qset.fallback_used}問"
              + (f" / エラー: {qset.error}" if qset.error else ""))
        for i, (q, src) in enumerate(zip(qset.questions, qset.sources), start=1):
            print(f"  {i:2d}. [{src}] {q}")
    (log_dir / f"{game_id}_questions.json").write_text(
        json.dumps(question_info, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print("---")

    def on_question_published(round_num: int, vote_num: int, question: str) -> None:
        append_question(
            question, game_id=game_id, round_num=round_num, vote_num=vote_num,
            history_path=args.question_history_path,
        )

    game = Game(
        config=config, agents=agents, seed=args.seed, logger=logger,
        questions=questions, on_question_published=on_question_published,
        max_parallel_agents=args.parallel, stop_after_round=args.stop_after_round,
    )

    try:
        result = game.run()
    except Exception:
        print("\n[エラー] 試合が途中で停止しました。ここまでの記録は残っています。")
        traceback.print_exc()
        llm_logger.save()
        return {"exit_code": 1, "result": None, "game_id": game_id, "log_dir": log_dir,
                "preflight_results": preflight_results}

    llm_logger.save()

    if args.stop_after_round is not None and args.stop_after_round < config.num_rounds:
        print(
            f"\n[post_game] 実行をR{args.stop_after_round}で打ち切ったため、"
            f"試合後の振り返りは行いません（ルール上はR{config.num_rounds}）。",
        )

    print("\n=== 結果 ===")
    print(json.dumps({
        "final_ranks": result.final_ranks, "final_assets": result.final_assets,
        "total_interest": result.total_interest,
        "total_destroyed_pot": result.total_destroyed_pot,
        "total_forfeited_remainder": result.total_forfeited_remainder,
        "post_game_reflections": result.post_game_reflections,
    }, ensure_ascii=False, indent=2))
    print("\n=== コスト ===")
    print(json.dumps(game_cost_budget.snapshot(), ensure_ascii=False, indent=2))
    if progress.fail_counts:
        print("\n=== 呼び出し失敗（席ごと件数） ===")
        for pid, count in sorted(progress.fail_counts.items()):
            print(f"  {pid}: {count}件")
    print(f"\nJSONLイベントログ: {log_dir / f'{game_id}_events.jsonl'}")
    print(f"JSONL呼び出しログ: {log_dir / f'{game_id}_llm_calls.jsonl'}")
    print(f"座席マップ: {seat_map_path}")
    print(f"\n要約コマンド: uv run python scripts/summarize_trial.py --game-id {game_id} --log-dir {args.log_dir}")

    return {"exit_code": 0, "result": result, "game_id": game_id, "log_dir": log_dir,
            "preflight_results": preflight_results}


def main() -> None:
    _enable_line_buffering(sys.stdout)
    parser = _build_arg_parser()
    args = parser.parse_args()
    outcome = run_trial(args)
    sys.exit(outcome["exit_code"])


if __name__ == "__main__":
    main()
