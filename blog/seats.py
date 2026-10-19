"""
ブログ用の席テーブル（サイクル6.0新設）

手書きの席リストを持たない。試合の記録（`seat_map.json`・`events.jsonl`・
`viewer/log_index.py`の索引）から、席番号・モデル・会社・最終順位・表紙絵の表情を
すべて自動で組み立てる。談合カードの `scripts/cards_seat.py::SEATS`（手書きの辞書）
とは違い、試合が変わっても手で表を書き直さなくてよい。

表情の割り当て規則（決定的・毎回同じ結果になる）:
1. 席ごとの感情の出現回数（`viewer/log_index.py`の索引の`emotion`列、JP1字）を数える
2. 最終順位の高い席（1位）から順に処理する
3. その席の出現回数の多い感情から試し、**同じ会社（vendor）の中で未使用**の
   感情が見つかったら採用する（キャラ絵が `viewer/static/emotions/{vendor}_{emotion}.png`
   の形でベンダー単位のみのため、同じ会社の複数席が同じ絵にならないようにする）
4. 会社が1社1席だけなら、常に最頻値がそのまま採用される
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm.models import MODEL_REGISTRY, ModelInfo

# ModelInfo.name の末尾の括弧注記（例: " (L)"・" (DevRelay experimental seat)"・
# " (evaluation only)"）は登録表の内部向け注記であり、記事の表示名には出さない。
_NAME_SUFFIX_RE = re.compile(r"\s*\([^()]*\)\s*$")

# JP1字の感情ラベル → viewer/static/emotions/ のファイル名キー
# （viewer/static/index.html の EMOTION_EN、dangou-card scripts/cards_seat.py::EMOTION_EN と同じ対応）
EMOTION_EN: dict[str, str] = {
    "喜": "joy", "怒": "anger", "哀": "sadness", "楽": "ease",
    "焦": "panic", "疑": "doubt", "奸": "smirk", "平静": "ease",
}

# 感情を試す優先順（最頻値が同数のときの決定的なタイブレーク用）
_EMOTION_TRY_ORDER: tuple[str, ...] = ("奸", "疑", "楽", "焦", "哀", "怒", "喜")


def _short_model_label(model_id: str) -> str:
    """モデルIDからベンダ接頭辞（"devrelay/"等）を落とす。"""
    return model_id.rsplit("/", 1)[-1]


def _registry_info_for(model_id: str) -> ModelInfo:
    """seat_map.jsonのmodel_idから、MODEL_REGISTRYの該当するModelInfoを逆引きする。

    複数の登録キーが同じmodel_idを持つ場合（例: kimi-k2.6はM5/L5、deepseek-v4-flashは
    M6/L6）があるため、登録キーの昇順で最初に見つかったものを使う（表示名・会社・
    ベンダーはどちらを採っても同じ値になるため実害はない）。
    """
    for key in sorted(MODEL_REGISTRY):
        info = MODEL_REGISTRY[key]
        if info.model_id == model_id:
            return info
    raise ValueError(f"MODEL_REGISTRYに model_id={model_id!r} が見つかりません")


@dataclass(frozen=True)
class SeatInfo:
    pid: str
    model_id: str          # seat_map.json の値（例: "devrelay/gpt-5.6-sol"）
    short_label: str        # ベンダ接頭辞を落とした表示用ID（例: "gpt-5.6-sol"）
    display_name: str       # ModelInfo.name から括弧注記を落とした表示名（例: "GPT-5.6 Sol"）
    provider: str           # ModelInfo.provider（例: "OpenAI"）
    vendor: str              # emotion画像のベンダーキー（例: "openai"）
    is_devrelay: bool        # DevRelay実験席か（adapter_type=="devrelay_http"）
    final_rank: int
    final_assets: int
    emotion_en: str          # 採用した表情の英語キー（emotions/{vendor}_{emotion_en}.png）
    emotion_jp: str          # 採用した表情の日本語1字


def load_seat_map(log_dir: Path, game_id: str) -> dict[str, str]:
    path = log_dir / f"{game_id}_seat_map.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_events(log_dir: Path, game_id: str) -> list[dict[str, Any]]:
    path = log_dir / f"{game_id}_events.jsonl"
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            e["_line"] = i
            events.append(e)
    return events


def final_ranks_and_assets(events: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    for e in events:
        if e.get("event_type") == "GAME_END":
            d = e["data"]
            return dict(d["final_ranks"]), dict(d["final_assets"])
    raise ValueError("GAME_END イベントが見つかりません")


def _emotion_counts_by_pid(index_entries: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for e in index_entries:
        pid = e.get("player_id")
        emo = e.get("emotion")
        if not pid or not emo or emo not in EMOTION_EN:
            continue
        counts.setdefault(pid, {}).setdefault(emo, 0)
        counts[pid][emo] += 1
    return counts


def _assign_emotions(
    pids_by_rank: list[str], vendor_of: dict[str, str], emotion_counts: dict[str, dict[str, int]],
) -> dict[str, str]:
    """順位の高い席から順に、同じ会社内で重ならない感情(JP1字)を割り当てる。"""
    used_by_vendor: dict[str, set[str]] = {}
    chosen: dict[str, str] = {}
    for pid in pids_by_rank:
        vendor = vendor_of[pid]
        used = used_by_vendor.setdefault(vendor, set())
        counts = emotion_counts.get(pid, {})
        # 出現数の多い順（同数は_EMOTION_TRY_ORDERで決定的にタイブレーク）
        candidates = sorted(
            counts.keys(),
            key=lambda emo: (-counts[emo], _EMOTION_TRY_ORDER.index(emo) if emo in _EMOTION_TRY_ORDER else 99),
        )
        picked = None
        for emo in candidates:
            if emo not in used:
                picked = emo
                break
        if picked is None:
            # そのpidの感情記録が無い、または会社内の全感情を使い尽くした場合のフォールバック
            picked = candidates[0] if candidates else "楽"
        used.add(picked)
        chosen[pid] = picked
    return chosen


def build_seat_table(
    log_dir: Path, game_id: str, index_entries: list[dict[str, Any]] | None = None,
) -> list[SeatInfo]:
    """試合の記録から、最終順位昇順（1位→12位）の席テーブルを組み立てる。"""
    seat_map = load_seat_map(log_dir, game_id)
    events = load_events(log_dir, game_id)
    final_ranks, final_assets = final_ranks_and_assets(events)

    if index_entries is None:
        from viewer.log_index import load_or_build_index
        index_entries = load_or_build_index(game_id, log_dir)
    emotion_counts = _emotion_counts_by_pid(index_entries)

    pids_by_rank = sorted(seat_map, key=lambda pid: final_ranks[pid])
    vendor_of = {pid: _registry_info_for(seat_map[pid]).vendor for pid in seat_map}
    emotion_jp = _assign_emotions(pids_by_rank, vendor_of, emotion_counts)

    seats: list[SeatInfo] = []
    for pid in pids_by_rank:
        model_id = seat_map[pid]
        info = _registry_info_for(model_id)
        jp = emotion_jp[pid]
        seats.append(SeatInfo(
            pid=pid, model_id=model_id, short_label=_short_model_label(model_id),
            display_name=_NAME_SUFFIX_RE.sub("", info.name), provider=info.provider, vendor=info.vendor,
            is_devrelay=(info.adapter_type == "devrelay_http"),
            final_rank=final_ranks[pid], final_assets=final_assets[pid],
            emotion_en=EMOTION_EN[jp], emotion_jp=jp,
        ))
    return seats
