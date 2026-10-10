"""
生データの保存・読み込み・足し合わせモジュール（サイクル1.2で新規実装。
サイクル4.1でv0.4の生データ構造（固定キーの集計カウンタではなく、
試合ごとの記録のリスト、sim/metrics.py::collect_raw参照）に合わせて
汎用化した）。

sim.metrics.collect_raw() の戻り値（生データ）をJSONとして
data/sim_v0_4/（.gitignore済み、gitの管理外）へ保存し、再実行時は読み込むだけに
する。1条件が終わるたびに保存し、保存済みの条件は回し直さない。

サイクル1.2〜4.0は「カウンタの加算・リストの連結」を固定キーの表
（_RAW_COUNTER_KEYS等）で管理していたが、新しい指標を増やすたびに表を
増やす必要があり、しかも int キーの辞書（minority_count等）だけ特別扱いが
要るなど壊れやすかった。v0.4の生データは
`{"n_games": int, "games": [試合ごとの記録dict, ...]}` という単純な形
（値の型は int・list・str・bool・dictのみ、辞書キーは常にstr）にしたため、
マージは「同じ型なら足す／連結する」という型ベースの汎用規則だけで済む
（_merge_value()）。これにより、sim/metrics.pyが新しい指標を追加しても
本モジュールを変更する必要がない。

全項目が「和」で合成できる設計のため、同じシード範囲を分割して回しても、
1回で回した場合と完全に同じ結果になる（merge_raw()、tests/test_store.py
で固定）。
"""

import json
from pathlib import Path
from typing import Any

from sim.metrics import collect_raw


def shard_path(out_dir: Path, key: str, seed_start: int, games: int) -> Path:
    """保存先パスを決める（例: data/sim_v0_4/V8break_pen300_seed1_n1000.json）"""
    return Path(out_dir) / f"{key}_seed{seed_start}_n{games}.json"


def _merge_value(a: Any, b: Any) -> Any:
    """同じ型の値を1つに合成する（int→加算、list→連結、dict→キーごとに再帰）"""
    if isinstance(a, bool) or isinstance(b, bool):
        raise TypeError(f"bool is not a mergeable raw value: {a!r}, {b!r}")
    if isinstance(a, int) and isinstance(b, int):
        return a + b
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _merge_value(out[k], v) if k in out else v
        return out
    raise TypeError(f"cannot merge values of type {type(a)} and {type(b)}")


def merge_raw(raws: list[dict]) -> dict:
    """
    複数の生データ（同じscenario_key・連続しないシード範囲でも可）を足し合わせる

    全項目が加算・連結で合成できる設計のため、同じシード範囲を分割して
    回しても、1回で回した場合と完全に同じ結果になる（tests/test_store.py）。
    """
    if not raws:
        return {"n_games": 0, "games": []}
    merged = raws[0]
    for raw in raws[1:]:
        merged = _merge_value(merged, raw)
    return merged


def save_raw(path: Path, raw: dict) -> None:
    """生データをJSONとして保存する（親ディレクトリが無ければ作る）"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def load_raw(path: Path) -> dict:
    """保存済みJSONを読み込む（生データの値はすべてint/list/str/bool/dictなので変換不要）"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_or_compute(
    scenario_key: str,
    games: int,
    seed_start: int,
    out_dir: Path,
    shard_size: int = 0,
    force: bool = False,
) -> dict:
    """
    保存済みの生データがあれば読み込むだけ、無ければ計算して保存する

    Args:
        scenario_key: sim.scenarios.SCENARIO_KEYS のいずれか
        games: 試合数
        seed_start: 最初の試合のシード
        out_dir: 保存先ディレクトリ（例: data/sim_v0_4）
        shard_size: 0なら分割せず一括で回す。0より大きければそのサイズごとに
            分割して回し、merge_raw()で足し合わせる（1回の実行が長くなる
            条件向け。同じシード範囲なので結果は一括実行と同じになる）。
        force: Trueなら保存済みファイルを無視して回し直す

    Returns:
        collect_raw()と同じ型の生データ辞書
    """
    path = shard_path(out_dir, scenario_key, seed_start, games)
    if path.exists() and not force:
        return load_raw(path)

    if shard_size <= 0 or shard_size >= games:
        raw = collect_raw(scenario_key, games, seed_start)
    else:
        raws = []
        offset = 0
        while offset < games:
            n = min(shard_size, games - offset)
            raws.append(collect_raw(scenario_key, n, seed_start + offset))
            offset += n
        raw = merge_raw(raws)

    save_raw(path, raw)
    return raw
