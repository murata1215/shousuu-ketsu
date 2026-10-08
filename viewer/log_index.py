"""
LLM呼び出し索引（サイクル3.0新設）

`{game_id}_llm_calls.jsonl` は本戦で約40MB（system_prompt/user_prompt/response_text
の全文を含む）。ビューアはこの全文を画面に出さない（§データの扱い「送った指示文
（プロンプト）の全文は表示しない」）ため、都度全文を読み込む必要はない。

ここでは「公開してよい列だけ」のホワイトリストで抜き出した軽い索引を
`logs/viewer_index/{game_id}.json` に一度だけ作って再利用する
（CLAUDE.mdの精神・gentei-janken `viewer/log_parser.py::LogCache` と同じ
mtime+sizeの差分検知）。索引ファイルには`system_prompt`/`user_prompt`/
`response_text`のいずれも一切持たない（deny-by-default、構造的に持ち出せない）。

`response_text`はJSONとしてパースした結果（strategy/memory/action等）だけを
拾い、生テキストは索引完成後に捨てる。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llm.response_parser import extract_json

INDEX_SUBDIR = "viewer_index"

# 索引に残す呼び出しメタデータの列（promptや生のresponse_textは含めない）
_CALL_META_KEYS = (
    "round_num", "player_id", "turn", "phase", "model_id",
    "emotion", "error_type", "error", "invalid_response", "reason",
    "elapsed_ms", "cost_usd",
)


def _parsed_fields(response_text: str | None) -> dict[str, Any]:
    """response_textをJSONとして解釈し、strategy/memory/action/commentだけを残す。

    解釈できない・無い場合は空dict（索引側は「無かった」と「壊れていた」を
    区別しない。発言本文の全文表示はどちらにせよ行わない）。
    """
    if not response_text:
        return {}
    obj = extract_json(response_text)
    if not isinstance(obj, dict):
        return {}
    out: dict[str, Any] = {}
    strategy = obj.get("strategy")
    if isinstance(strategy, dict):
        out["strategy"] = {
            k: strategy.get(k) for k in ("vote_plan", "reason", "current_goal", "emotion")
            if k in strategy
        }
    action = obj.get("action")
    if isinstance(action, dict):
        out["action"] = dict(action)
    memory = obj.get("memory")
    if isinstance(memory, str):
        out["memory"] = memory
    comment = obj.get("comment")
    if isinstance(comment, str):
        out["comment"] = comment
    loan_amount = obj.get("loan_amount")
    if loan_amount is not None:
        out["loan_amount"] = loan_amount
        out["loan_reason"] = obj.get("reason") if isinstance(obj.get("reason"), str) else None
    return out


def build_entries(llm_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`_load_jsonl`で読んだllm_calls行から索引エントリ一覧を作る（純関数、ファイルI/Oなし）"""
    entries = []
    for row in llm_calls:
        entry = {k: row.get(k) for k in _CALL_META_KEYS}
        entry.update(_parsed_fields(row.get("response_text")))
        entries.append(entry)
    return entries


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 書きかけの最終行はスキップ
    return rows


def _fingerprint(path: Path) -> str | None:
    """mtime+sizeの文字列表現（ファイルが無ければNone）"""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return f"{stat.st_mtime}-{stat.st_size}"


def index_path(game_id: str, log_dir: Path) -> Path:
    return log_dir / INDEX_SUBDIR / f"{game_id}.json"


def load_or_build_index(game_id: str, log_dir: Path) -> list[dict[str, Any]]:
    """
    索引を読む。無い・元の`{game_id}_llm_calls.jsonl`より古い場合は作り直して保存する。

    Returns:
        索引エントリのリスト（build_entries()の戻り値と同形）
    """
    calls_path = log_dir / f"{game_id}_llm_calls.jsonl"
    fp = _fingerprint(calls_path)
    idx_path = index_path(game_id, log_dir)

    if idx_path.exists():
        try:
            cached = json.loads(idx_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cached = None
        if isinstance(cached, dict) and cached.get("source_fingerprint") == fp:
            return cached.get("entries", [])

    entries = build_entries(_load_jsonl(calls_path))
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(
        json.dumps({"source_fingerprint": fp, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return entries


def main() -> None:
    """`python -m viewer.log_index --game-id <id> [--log-dir logs/llm]` で索引を手動生成する"""
    import argparse

    parser = argparse.ArgumentParser(description="LLM呼び出し索引を作成する")
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--log-dir", default="logs/llm")
    args = parser.parse_args()

    entries = load_or_build_index(args.game_id, Path(args.log_dir))
    print(f"索引エントリ数: {len(entries)}")
    print(f"保存先: {index_path(args.game_id, Path(args.log_dir))}")


if __name__ == "__main__":
    main()
