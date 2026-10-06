"""
生データの保存・読み込み・足し合わせモジュール（サイクル1.2続き、新規実装）

sim.metrics.collect_raw() の戻り値（生データ）をJSONとして
data/sim/（.gitignore済み、gitの管理外）へ保存し、再実行時は読み込むだけに
する。1条件が終わるたびに保存し、保存済みの条件は回し直さない
（計画「回し方を変える」）。

生データの各項目はすべて「和」で合成できる設計（カウンタの加算・リストの
連結）になっているため、同じシード範囲を分割して回しても、1回で回した
場合と完全に同じ数字になる（merge_raw()、tests/test_store.py で固定）。
JSONは辞書キーを文字列化してしまうため、読み込み時に
minority_count（1..5の整数または"none"）とrecovery_by_position
（1/2/3の整数）のキーを元の型へ戻す。これにより sim/metrics.py は無改変で
使える。
"""

import json
from pathlib import Path

from engine.config import GameConfig
from sim.metrics import collect_raw

# JSON復元時に int キーへ戻す必要があるフィールドと、その変換規則
_MINORITY_KEYS: list[int | str] = [1, 2, 3, 4, 5, "none"]
_RECOVERY_KEYS: list[int] = [1, 2, 3]

_RAW_COUNTER_KEYS: list[str] = [
    "n_games", "n_rounds_total", "type_b_kept", "type_b_violated",
    "shortfall_total", "king_making_hits", "king_making_total",
]
_RAW_LIST_KEYS: list[str] = [
    "carryovers", "destroyed_per_game", "final_assets_all",
    "positive_count_per_game", "gap_1_2_per_game", "gap_1_last_per_game",
    "gains", "debt_cap_hits_per_game",
]
_RAW_DEFAULTDICT_LIST_KEYS: list[str] = [
    "assets_by_label", "rank_by_label", "interest_pre_by_label",
    "interest_post_by_label", "recovery_by_position",
]
_RAW_DEFAULTDICT_INT_KEYS: list[str] = ["minority_count", "rank1_by_label"]


def shard_path(out_dir: Path, key: str, seed_start: int, games: int) -> Path:
    """保存先パスを決める（例: data/sim/S3k3_seed1_n1000.json）"""
    return Path(out_dir) / f"{key}_seed{seed_start}_n{games}.json"


def _raw_to_jsonable(raw: dict) -> dict:
    """collect_raw()の戻り値をJSONに書けるdictへ変換する（キーをすべてstrにする）"""
    out: dict = {}
    for k in _RAW_COUNTER_KEYS:
        out[k] = raw[k]
    for k in _RAW_LIST_KEYS:
        out[k] = list(raw[k])
    for k in _RAW_DEFAULTDICT_INT_KEYS:
        out[k] = {str(kk): vv for kk, vv in raw[k].items()}
    for k in _RAW_DEFAULTDICT_LIST_KEYS:
        out[k] = {str(kk): list(vv) for kk, vv in raw[k].items()}
    return out


def _jsonable_to_raw(data: dict) -> dict:
    """JSONから読み込んだdictを、collect_raw()と同じ型のdictへ戻す"""
    from collections import defaultdict

    raw: dict = {}
    for k in _RAW_COUNTER_KEYS:
        raw[k] = data[k]
    for k in _RAW_LIST_KEYS:
        raw[k] = list(data[k])

    minority_count: dict = defaultdict(int)
    for key in _MINORITY_KEYS:
        v = data["minority_count"].get(str(key))
        if v is not None:
            minority_count[key] = v
    raw["minority_count"] = minority_count

    rank1_by_label: dict = defaultdict(int)
    for kk, vv in data["rank1_by_label"].items():
        rank1_by_label[kk] = vv
    raw["rank1_by_label"] = rank1_by_label

    for k in ("assets_by_label", "rank_by_label", "interest_pre_by_label", "interest_post_by_label"):
        d: dict = defaultdict(list)
        for kk, vv in data[k].items():
            d[kk] = list(vv)
        raw[k] = d

    recovery_by_position: dict = defaultdict(list)
    for key in _RECOVERY_KEYS:
        v = data["recovery_by_position"].get(str(key))
        if v is not None:
            recovery_by_position[key] = list(v)
    raw["recovery_by_position"] = recovery_by_position

    return raw


def save_raw(path: Path, raw: dict) -> None:
    """生データをJSONとして保存する（親ディレクトリが無ければ作る）"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_raw_to_jsonable(raw), ensure_ascii=False, indent=2), encoding="utf-8")


def load_raw(path: Path) -> dict:
    """保存済みJSONを読み込み、collect_raw()と同じ型のdictへ戻す"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _jsonable_to_raw(data)


def merge_raw(raws: list[dict]) -> dict:
    """
    複数の生データ（同じscenario_key・連続しないシード範囲でも可）を足し合わせる

    全項目がカウンタの加算・リストの連結で合成できる設計のため、同じ
    シード範囲を分割して回しても、1回で回した場合と完全に同じ結果になる
    （tests/test_store.py で固定）。
    """
    from collections import defaultdict

    merged: dict = {
        "n_games": 0, "n_rounds_total": 0, "type_b_kept": 0, "type_b_violated": 0,
        "shortfall_total": 0, "king_making_hits": 0, "king_making_total": 0,
        "minority_count": defaultdict(int),
        "carryovers": [], "destroyed_per_game": [], "final_assets_all": [],
        "positive_count_per_game": [], "gap_1_2_per_game": [], "gap_1_last_per_game": [],
        "gains": [], "debt_cap_hits_per_game": [],
        "assets_by_label": defaultdict(list), "rank_by_label": defaultdict(list),
        "rank1_by_label": defaultdict(int),
        "interest_pre_by_label": defaultdict(list), "interest_post_by_label": defaultdict(list),
        "recovery_by_position": defaultdict(list),
    }
    for raw in raws:
        for k in _RAW_COUNTER_KEYS:
            merged[k] += raw[k]
        for k in _RAW_LIST_KEYS:
            merged[k].extend(raw[k])
        for k, v in raw["minority_count"].items():
            merged["minority_count"][k] += v
        for k, v in raw["rank1_by_label"].items():
            merged["rank1_by_label"][k] += v
        for field in ("assets_by_label", "rank_by_label", "interest_pre_by_label", "interest_post_by_label"):
            for k, v in raw[field].items():
                merged[field][k].extend(v)
        for k, v in raw["recovery_by_position"].items():
            merged["recovery_by_position"][k].extend(v)
    return merged


def load_or_compute(
    scenario_key: str,
    games: int,
    seed_start: int,
    config: GameConfig,
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
        config: ゲーム設定
        out_dir: 保存先ディレクトリ（例: data/sim）
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
        raw = collect_raw(scenario_key, games, seed_start, config)
    else:
        raws = []
        offset = 0
        while offset < games:
            n = min(shard_size, games - offset)
            raws.append(collect_raw(scenario_key, n, seed_start + offset, config))
            offset += n
        raw = merge_raw(raws)

    save_raw(path, raw)
    return raw
