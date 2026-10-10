"""
sim.store（保存・読み込み・足し合わせ）のテスト（サイクル1.2で新規実装。
サイクル4.1でv0.4の生データ構造（試合ごとの記録のリスト、汎用マージ）に
合わせて書き換えた）。

- 保存→読み込みの往復で生データが完全一致する（JSON往復の確認。v0.4は
  値がすべてint/list/str/bool/dictなのでint復元の特別扱いは不要）。
- 分割して回しても、一括で回した場合と完全に同じ数字になる（merge_raw）。
- load_or_compute()は、保存済みファイルがあれば読むだけで同じ結果を返す。
"""

from pathlib import Path

from sim.metrics import collect_raw
from sim.store import _merge_value, load_or_compute, load_raw, merge_raw, save_raw, shard_path


def test_save_and_load_roundtrip_preserves_raw_data(tmp_path: Path) -> None:
    raw = collect_raw("V8break_pen100", games=8, seed_start=1)

    path = tmp_path / "V8break_pen100_seed1_n8.json"
    save_raw(path, raw)
    loaded = load_raw(path)

    assert loaded == raw


def test_merge_raw_of_split_shards_equals_single_run() -> None:
    """同じシード範囲を5+5で分割しても、10試合を一括で回した場合と完全に同じ数字になる"""
    raw_whole = collect_raw("V8break_pen100", games=10, seed_start=1)

    raw_part1 = collect_raw("V8break_pen100", games=5, seed_start=1)
    raw_part2 = collect_raw("V8break_pen100", games=5, seed_start=6)
    raw_merged = merge_raw([raw_part1, raw_part2])

    # 試合の順序はシード順になるはずなので、games一覧まで含めて一致する
    assert raw_merged == raw_whole


def test_merge_raw_handles_scenarios_without_contracts() -> None:
    """契約が無いシナリオ（V1）でも、分割と一括の結果が一致する"""
    raw_whole = collect_raw("V1", games=6, seed_start=1)
    raw_part1 = collect_raw("V1", games=3, seed_start=1)
    raw_part2 = collect_raw("V1", games=3, seed_start=4)
    raw_merged = merge_raw([raw_part1, raw_part2])

    assert raw_merged == raw_whole


def test_merge_value_sums_ints_and_concatenates_lists() -> None:
    assert _merge_value(3, 4) == 7
    assert _merge_value([1, 2], [3]) == [1, 2, 3]
    assert _merge_value({"a": 1, "b": [1]}, {"a": 2, "c": [9]}) == {"a": 3, "b": [1], "c": [9]}


def test_load_or_compute_writes_file_then_reads_it_back(tmp_path: Path) -> None:
    out_dir = tmp_path / "sim"

    raw1 = load_or_compute("V1", games=5, seed_start=1, out_dir=out_dir)
    path = shard_path(out_dir, "V1", 1, 5)
    assert path.exists()

    raw2 = load_or_compute("V1", games=5, seed_start=1, out_dir=out_dir)
    assert raw1 == raw2


def test_load_or_compute_with_shard_size_matches_single_shard(tmp_path: Path) -> None:
    """shard_sizeで分割実行しても、分割しない場合と同じ結果になる"""
    raw_single = load_or_compute(
        "V3g3", games=10, seed_start=1, out_dir=tmp_path / "a", shard_size=0,
    )
    raw_sharded = load_or_compute(
        "V3g3", games=10, seed_start=1, out_dir=tmp_path / "b", shard_size=4,
    )
    assert raw_single == raw_sharded


def test_load_or_compute_does_not_recompute_when_file_exists(tmp_path: Path, monkeypatch) -> None:
    """保存済みファイルがあればcollect_raw()を呼ばない（回し直さない）"""
    import sim.store as store_mod

    out_dir = tmp_path / "sim"
    load_or_compute("V1", games=3, seed_start=1, out_dir=out_dir)

    def _boom(*args, **kwargs):
        raise AssertionError("保存済みなのにcollect_raw()が呼ばれた")

    monkeypatch.setattr(store_mod, "collect_raw", _boom)
    # 例外が起きなければ、再計算せずファイルから読んだということ
    load_or_compute("V1", games=3, seed_start=1, out_dir=out_dir)


def test_load_or_compute_force_recomputes(tmp_path: Path) -> None:
    out_dir = tmp_path / "sim"
    raw1 = load_or_compute("V1", games=3, seed_start=1, out_dir=out_dir)
    raw2 = load_or_compute("V1", games=3, seed_start=1, out_dir=out_dir, force=True)
    assert raw1 == raw2
