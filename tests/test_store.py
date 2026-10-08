"""
sim.store（保存・読み込み・足し合わせ）のテスト（サイクル1.2続き、新規実装）

- 保存→読み込みの往復でsummarize()の結果が一致する（JSONキーのint復元の確認）。
- 分割して回しても、一括で回した場合と完全に同じ数字になる（merge_raw）。
- load_or_compute()は、保存済みファイルがあれば読むだけで同じ結果を返す。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(
    reason="sim.metrics/sim.storeのv0.4対応（L12R4V6）はサイクル4.1",
)

from engine.config import GameConfig
from sim.metrics import collect_raw, summarize
from sim.store import load_or_compute, load_raw, merge_raw, save_raw, shard_path


def test_save_and_load_roundtrip_preserves_summary(tmp_path: Path) -> None:
    config = GameConfig.default_12()
    raw = collect_raw("S4", games=8, seed_start=1, config=config)

    path = tmp_path / "S4_seed1_n8.json"
    save_raw(path, raw)
    loaded = load_raw(path)

    assert summarize(loaded) == summarize(raw)


def test_merge_raw_of_split_shards_equals_single_run() -> None:
    """同じシード範囲を5+5で分割しても、10試合を一括で回した場合と完全に同じ数字になる"""
    config = GameConfig.default_12()

    raw_whole = collect_raw("S4", games=10, seed_start=1, config=config)

    raw_part1 = collect_raw("S4", games=5, seed_start=1, config=config)
    raw_part2 = collect_raw("S4", games=5, seed_start=6, config=config)
    raw_merged = merge_raw([raw_part1, raw_part2])

    assert summarize(raw_merged) == summarize(raw_whole)


def test_merge_raw_handles_scenarios_without_contracts() -> None:
    """契約・利息が無いシナリオ（S1）でも、分割と一括の結果が一致する"""
    config = GameConfig.default_12()

    raw_whole = collect_raw("S1", games=6, seed_start=1, config=config)
    raw_part1 = collect_raw("S1", games=3, seed_start=1, config=config)
    raw_part2 = collect_raw("S1", games=3, seed_start=4, config=config)
    raw_merged = merge_raw([raw_part1, raw_part2])

    assert summarize(raw_merged) == summarize(raw_whole)


def test_load_or_compute_writes_file_then_reads_it_back(tmp_path: Path) -> None:
    config = GameConfig.default_12()
    out_dir = tmp_path / "sim"

    raw1 = load_or_compute("S1", games=5, seed_start=1, config=config, out_dir=out_dir)
    path = shard_path(out_dir, "S1", 1, 5)
    assert path.exists()

    # 2回目はファイルから読むだけ（再計算しても同じ値になるはずなので、
    # ここでは「ファイルが存在し、かつ結果が一致する」ことを確認する）
    raw2 = load_or_compute("S1", games=5, seed_start=1, config=config, out_dir=out_dir)
    assert summarize(raw1) == summarize(raw2)


def test_load_or_compute_with_shard_size_matches_single_shard(tmp_path: Path) -> None:
    """shard_sizeで分割実行しても、分割しない場合と同じ結果になる"""
    config = GameConfig.default_12()

    raw_single = load_or_compute(
        "S3k3", games=10, seed_start=1, config=config, out_dir=tmp_path / "a", shard_size=0,
    )
    raw_sharded = load_or_compute(
        "S3k3", games=10, seed_start=1, config=config, out_dir=tmp_path / "b", shard_size=4,
    )
    assert summarize(raw_single) == summarize(raw_sharded)


def test_load_or_compute_does_not_recompute_when_file_exists(tmp_path: Path, monkeypatch) -> None:
    """保存済みファイルがあればcollect_raw()を呼ばない（回し直さない）"""
    import sim.store as store_mod

    config = GameConfig.default_12()
    out_dir = tmp_path / "sim"
    load_or_compute("S1", games=3, seed_start=1, config=config, out_dir=out_dir)

    def _boom(*args, **kwargs):
        raise AssertionError("保存済みなのにcollect_raw()が呼ばれた")

    monkeypatch.setattr(store_mod, "collect_raw", _boom)
    # 例外が起きなければ、再計算せずファイルから読んだということ
    load_or_compute("S1", games=3, seed_start=1, config=config, out_dir=out_dir)


def test_load_or_compute_force_recomputes(tmp_path: Path) -> None:
    config = GameConfig.default_12()
    out_dir = tmp_path / "sim"
    raw1 = load_or_compute("S1", games=3, seed_start=1, config=config, out_dir=out_dir)
    raw2 = load_or_compute("S1", games=3, seed_start=1, config=config, out_dir=out_dir, force=True)
    assert summarize(raw1) == summarize(raw2)
