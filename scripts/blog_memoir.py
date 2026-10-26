#!/usr/bin/env python3
"""
本人執筆手記の生成CLI（サイクル6.0新設、1コマンド1席。サイクル7.3で`--date`対応）

CLAUDE.md過去の落とし穴⑤・⑮: 長い処理・実際のAI呼び出しはバックグラウンド化・一括化せず、
1席ずつフォアグラウンドで回し、完了を確認してから次に進む。このスクリプトは**常に1席だけ**
処理する（全席をまとめて回すオプションは意図的に持たない）。

使い方:
    uv run python scripts/blog_memoir.py --date 20261023 --pid P01 --dump-prompt out.md
    uv run python scripts/blog_memoir.py --date 20261023 --pid P01 --dry-run
    uv run python scripts/blog_memoir.py --date 20261023 --pid P01
    uv run python scripts/blog_memoir.py --date 20261023 --pid P01 --attempt 2

`--date`省略時は`blog.match_config.DEFAULT_DATE`（現行サイクルの試合）を使う。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import re  # noqa: E402

from blog.match_config import DEFAULT_DATE, LOG_DIR, MATCHES  # noqa: E402
from blog.seats import _registry_info_for, load_seat_map  # noqa: E402
from blog.self_memoir import MAX_TOTAL_CHARS_DEFAULT, SelfMemoirMaterial, build_self_memoir_material  # noqa: E402
from llm.adapters import create_adapter  # noqa: E402
from llm.costing import usage_cost  # noqa: E402

# ブログ生成時のみ全席300秒下限へ延長する（本戦のMODEL_REGISTRYは無変更。
# DevRelay席は契約上180秒でクランプ。dangou-card scripts/blog.py::_prep_blog_model と同じ方針）
BLOG_TIMEOUT_SECONDS = 300
DEVRELAY_MAX_TIMEOUT_SECONDS = 180
MAX_TOKENS = 6000
REASONING_MAX_TOKENS = 12000
REASONING_TIMEOUT_SECONDS = 300


def _is_reasoning_model(model) -> bool:
    """思考ダンプが長く生成が重いモデルか判定する（Kimi）。"""
    return "kimi" in model.model_id.lower()


def _prep_blog_model(model):
    """記事生成用にモデル設定を調整し (調整後モデル, max_tokens) を返す。
    グローバルのMODEL_REGISTRYは一切書き換えない（replace()でコピーを作る）。
    """
    if model.adapter_type == "devrelay_http":
        blog_timeout = min(DEVRELAY_MAX_TIMEOUT_SECONDS, max(model.timeout_seconds, BLOG_TIMEOUT_SECONDS))
    else:
        blog_timeout = max(model.timeout_seconds, BLOG_TIMEOUT_SECONDS)
    if _is_reasoning_model(model):
        adapted = replace(model, timeout_seconds=max(blog_timeout, REASONING_TIMEOUT_SECONDS))
        return adapted, REASONING_MAX_TOKENS
    adapted = replace(model, timeout_seconds=blog_timeout)
    return adapted, MAX_TOKENS


def _strip_code_fence(text: str) -> str:
    m = re.match(r"\s*```(?:markdown|md)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
    return m.group(1) if m else text


def generate_memoir_article(
    match, pid: str, material: SelfMemoirMaterial, dry_run: bool = False, attempt: int = 1,
) -> tuple[str, dict]:
    seat_map = load_seat_map(LOG_DIR, match.game_id)
    model_info = _registry_info_for(seat_map[pid])

    if dry_run:
        s = material.stats
        md = (
            f"# 【ドライラン】{pid}の試合振り返り\n\n"
            f"## これはダミー記事です\n\n"
            f"(system={s['system_chars']}字 / user={s['user_chars']}字 / 合計={s['total_chars']}字。"
            f"実際の記事は本人モデルが生成します)\n"
        )
        meta = {
            "model": model_info.model_id, "model_name": model_info.name, "dry_run": True,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "attempt": attempt,
        }
        return md, meta

    gen_model, max_tokens = _prep_blog_model(model_info)
    adapter = create_adapter(gen_model)
    # DevRelay席はseatKeyを明示bindしないと既定値"P00"を全席で共有してしまう
    # （llm/llm_agent.py::LLMAgentと同じダックタイピング。他アダプタはbind_seatを持たないため無害）。
    bind_seat = getattr(adapter, "bind_seat", None)
    if callable(bind_seat):
        bind_seat(pid)

    text, usage = adapter.complete(
        system=material.system_prompt,
        messages=[{"role": "user", "content": material.user_prompt}],
        max_tokens=max_tokens,
        temperature=0.8,
    )
    cost = usage_cost(gen_model, usage)
    text = _strip_code_fence(text)
    meta = {
        "model": model_info.model_id, "model_name": model_info.name, "dry_run": False,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": round(cost, 6),
        "attempt": attempt,
        "finish_reason": usage.get("finish_reason"),
    }
    return text.strip() + "\n", meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=DEFAULT_DATE, choices=sorted(MATCHES), help="対象試合の日付（既定: 現行サイクルの試合）")
    ap.add_argument("--pid", required=True, help="席ID（例: P06）")
    ap.add_argument("--dry-run", action="store_true", help="APIを呼ばずダミー記事を保存する")
    ap.add_argument("--dump-prompt", default=None, help="APIを呼ばず素材全文をこのパスへ書き出して終了する")
    ap.add_argument("--attempt", type=int, default=1, help="試行回数（生出力ファイル名に使う。既定1）")
    ap.add_argument("--max-input-chars", type=int, default=MAX_TOTAL_CHARS_DEFAULT)
    args = ap.parse_args()

    match = MATCHES[args.date]
    if args.pid not in match.seat_order:
        raise SystemExit(f"[エラー] {match.date}（{match.game_id}）に席 {args.pid} は無い。有効な席: {sorted(match.seat_order)}")

    material = build_self_memoir_material(
        LOG_DIR, match.game_id, args.pid, max_total_chars=args.max_input_chars,
        name_by_model_only=(match.match_no != 1),
    )

    if args.dump_prompt:
        out_path = Path(args.dump_prompt)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        s = material.stats
        lines = [
            f"# self-memoir prompt dump: {material.pid}（{material.model_label}）", "",
            f"- system_chars: {s['system_chars']}", f"- user_chars: {s['user_chars']}",
            f"- total_chars: {s['total_chars']}",
            f"- strategy_selected/omitted: {s['strategy_selected']}/{s['strategy_omitted']}",
            f"- message_selected/omitted: {s['message_selected']}/{s['message_omitted']}",
            f"- memory_selected/omitted: {s['memory_selected']}/{s['memory_omitted']}",
            f"- post_game_reflection_submitted: {s['post_game_reflection_submitted']}",
            f"- scene_links: {len(material.scene_links)}", "",
            "## system", "```", material.system_prompt, "```", "",
            "## user", "```", material.user_prompt, "```",
        ]
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"プロンプトダンプ: {out_path}  (合計{s['total_chars']}字)")
        return

    md, meta = generate_memoir_article(match, args.pid, material, dry_run=args.dry_run, attempt=args.attempt)

    match.raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = match.raw_dir / f"{args.pid}_attempt{args.attempt}.md"
    raw_path.write_text(md, encoding="utf-8")

    print(f"=== self-memoir: {match.date} {args.pid}（{meta.get('model_name')}） attempt{args.attempt} ===")
    print(f"コスト: ${meta.get('cost_usd', 0):.4f}  入力トークン: {meta.get('input_tokens')}  出力トークン: {meta.get('output_tokens')}")
    print(f"入力: 合計{material.stats['total_chars']}字")
    print(f"出力: {len(md)}字  finish_reason={meta.get('finish_reason')}")
    print(f"生出力: {raw_path}")
    if not md.strip() or len(md.strip()) < 100:
        print("[警告] 出力がほぼ空です。本文が空のまま保存されています。再試行を検討してください。")


if __name__ == "__main__":
    main()
