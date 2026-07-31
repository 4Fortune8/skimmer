from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.RecomendationAnalysis import checkpoint


pytest.importorskip("pyarrow")


@pytest.fixture()
def fake_db(tmp_path, monkeypatch):
    db_path = tmp_path / "skimmer.db"
    db_path.write_bytes(b"initial")
    return db_path


@pytest.fixture()
def cache_dir(tmp_path):
    return tmp_path / "cache"


def _frame(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame({"video_id": [f"v{index}" for index in range(rows)], "score": range(rows)})


def test_write_and_read_round_trip_preserves_attrs(cache_dir):
    frame = _frame()
    frame.attrs["algorithm_failures"] = {"velocity": "boom"}

    checkpoint.write_frames("analysis_frame-abc", {"full_df": frame, "snapshots": None}, cache_dir=cache_dir)
    loaded = checkpoint.read_frames("analysis_frame-abc", cache_dir=cache_dir)

    assert loaded is not None
    assert loaded["snapshots"] is None
    pd.testing.assert_frame_equal(loaded["full_df"], frame)
    assert loaded["full_df"].attrs["algorithm_failures"] == {"velocity": "boom"}


def test_read_missing_entry_returns_none(cache_dir):
    assert checkpoint.read_frames("analysis_frame-missing", cache_dir=cache_dir) is None


def test_read_corrupt_manifest_returns_none(cache_dir):
    checkpoint.write_frames("analysis_frame-abc", {"full_df": _frame()}, cache_dir=cache_dir)
    (cache_dir / "analysis_frame-abc" / checkpoint.MANIFEST_NAME).write_text("{not json", encoding="utf-8")

    assert checkpoint.read_frames("analysis_frame-abc", cache_dir=cache_dir) is None


def test_read_rejects_other_cache_version(cache_dir):
    checkpoint.write_frames("analysis_frame-abc", {"full_df": _frame()}, cache_dir=cache_dir)
    manifest_path = cache_dir / "analysis_frame-abc" / checkpoint.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = checkpoint.CACHE_VERSION + 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert checkpoint.read_frames("analysis_frame-abc", cache_dir=cache_dir) is None


def test_key_changes_with_db_mtime_and_params(fake_db):
    baseline = checkpoint.make_key("analysis_frame", fake_db, {"exclude_shorts": True})
    assert baseline == checkpoint.make_key("analysis_frame", fake_db, {"exclude_shorts": True})

    other_params = checkpoint.make_key("analysis_frame", fake_db, {"exclude_shorts": False})
    assert other_params != baseline

    fake_db.write_bytes(b"more data after ingest")
    assert checkpoint.make_key("analysis_frame", fake_db, {"exclude_shorts": True}) != baseline


def test_key_ignores_param_ordering(fake_db):
    first = checkpoint.make_key("analysis_frame", fake_db, {"a": 1, "b": 2})
    second = checkpoint.make_key("analysis_frame", fake_db, {"b": 2, "a": 1})
    assert first == second


def test_key_changes_with_source_fingerprint(fake_db, monkeypatch):
    baseline = checkpoint.make_key("analysis_frame", fake_db, {})
    monkeypatch.setattr(checkpoint, "source_fingerprint", lambda: "different-source")
    assert checkpoint.make_key("analysis_frame", fake_db, {}) != baseline


def test_load_analysis_frame_cached_computes_then_reuses(fake_db, cache_dir):
    calls: list[dict] = []

    def loader(db_path=None, **kwargs):
        calls.append(kwargs)
        return _frame(), _frame(2)

    first_df, first_snapshots, from_cache = checkpoint.load_analysis_frame_cached(
        fake_db, cache_dir=cache_dir, loader=loader, exclude_shorts=True
    )
    assert from_cache is False
    assert len(calls) == 1

    second_df, second_snapshots, from_cache = checkpoint.load_analysis_frame_cached(
        fake_db, cache_dir=cache_dir, loader=loader, exclude_shorts=True
    )
    assert from_cache is True
    assert len(calls) == 1
    pd.testing.assert_frame_equal(second_df, first_df)
    pd.testing.assert_frame_equal(second_snapshots, first_snapshots)


def test_load_analysis_frame_cached_refresh_recomputes(fake_db, cache_dir):
    calls: list[int] = []

    def loader(db_path=None, **kwargs):
        calls.append(1)
        return _frame(), None

    checkpoint.load_analysis_frame_cached(fake_db, cache_dir=cache_dir, loader=loader)
    checkpoint.load_analysis_frame_cached(fake_db, cache_dir=cache_dir, loader=loader, refresh=True)
    assert len(calls) == 2


def test_load_analysis_frame_cached_disabled_skips_disk(fake_db, cache_dir):
    calls: list[int] = []

    def loader(db_path=None, **kwargs):
        calls.append(1)
        return _frame(), None

    checkpoint.load_analysis_frame_cached(fake_db, cache_dir=cache_dir, loader=loader, use_cache=False)
    checkpoint.load_analysis_frame_cached(fake_db, cache_dir=cache_dir, loader=loader, use_cache=False)
    assert len(calls) == 2
    assert not cache_dir.exists()


def test_load_analysis_frame_cached_key_tracks_frame_kwargs(fake_db, cache_dir):
    calls: list[dict] = []

    def loader(db_path=None, **kwargs):
        calls.append(kwargs)
        return _frame(), None

    checkpoint.load_analysis_frame_cached(fake_db, cache_dir=cache_dir, loader=loader, exclude_shorts=True)
    checkpoint.load_analysis_frame_cached(fake_db, cache_dir=cache_dir, loader=loader, exclude_shorts=False)
    assert len(calls) == 2


def test_run_algorithms_cached_round_trip(fake_db, cache_dir):
    calls: list[int] = []

    def runner(df, snapshots=None, params=None, **kwargs):
        calls.append(1)
        result = _frame(2)
        result.attrs["algorithm_failures"] = {"topics": "kaboom"}
        return {"breakout_outliers": result}

    df = _frame(5)
    results, from_cache = checkpoint.run_algorithms_cached(
        df, db_path=fake_db, cache_dir=cache_dir, params={"topics": {"use_sklearn": False}}, runner=runner
    )
    assert from_cache is False
    assert set(results) == {"breakout_outliers"}

    cached, from_cache = checkpoint.run_algorithms_cached(
        df, db_path=fake_db, cache_dir=cache_dir, params={"topics": {"use_sklearn": False}}, runner=runner
    )
    assert from_cache is True
    assert len(calls) == 1
    assert cached["breakout_outliers"].attrs["algorithm_failures"] == {"topics": "kaboom"}


def test_run_algorithms_cached_key_tracks_params(fake_db, cache_dir):
    calls: list[int] = []

    def runner(df, snapshots=None, params=None, **kwargs):
        calls.append(1)
        return {"breakout_outliers": _frame(1)}

    df = _frame(5)
    checkpoint.run_algorithms_cached(df, db_path=fake_db, cache_dir=cache_dir, params={"topics": {"use_sklearn": False}}, runner=runner)
    checkpoint.run_algorithms_cached(df, db_path=fake_db, cache_dir=cache_dir, params={"topics": {"use_sklearn": True}}, runner=runner)
    assert len(calls) == 2


def test_write_failure_is_non_fatal(fake_db, cache_dir, monkeypatch):
    def explode(frame, path):
        raise OSError("disk full")

    monkeypatch.setattr(checkpoint, "_write_parquet", explode)

    def loader(db_path=None, **kwargs):
        return _frame(), None

    df, snapshots, from_cache = checkpoint.load_analysis_frame_cached(fake_db, cache_dir=cache_dir, loader=loader)
    assert from_cache is False
    assert len(df) == 3
    assert not (cache_dir / "analysis_frame-tmp").exists()


def test_write_parquet_stringifies_mixed_object_columns(tmp_path):
    frame = pd.DataFrame({"video_id": ["a", "b"], "mixed": [{"x": 1}, [1, 2]]})
    path = tmp_path / "mixed.parquet"

    checkpoint._write_parquet(frame, path)

    restored = pd.read_parquet(path)
    assert list(restored["video_id"]) == ["a", "b"]
    assert restored["mixed"].notna().all()


def test_clear_and_describe_cache(cache_dir):
    checkpoint.write_frames("analysis_frame-a", {"full_df": _frame()}, cache_dir=cache_dir)
    checkpoint.write_frames("algorithm_results-b", {"breakout_outliers": _frame()}, cache_dir=cache_dir)

    described = checkpoint.describe_cache(cache_dir)
    assert set(described["key"]) == {"analysis_frame-a", "algorithm_results-b"}

    assert checkpoint.clear_cache(cache_dir, kind="analysis_frame") == 1
    assert set(checkpoint.describe_cache(cache_dir)["key"]) == {"algorithm_results-b"}
    assert checkpoint.clear_cache(cache_dir) == 1
    assert checkpoint.describe_cache(cache_dir).empty


def test_read_frames_preserves_insertion_order(cache_dir):
    ordered = {"velocity": _frame(1), "breakout_outliers": _frame(2), "topics": _frame(3)}
    checkpoint.write_frames("algorithm_results-order", ordered, cache_dir=cache_dir)

    loaded = checkpoint.read_frames("algorithm_results-order", cache_dir=cache_dir)

    assert list(loaded) == list(ordered)


def _make_sqlite_db(path, video_rows: int) -> None:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bronze_youtubeapi_video_stats (id INTEGER PRIMARY KEY, video_id TEXT);
        CREATE TABLE IF NOT EXISTS collection_attempts (id INTEGER PRIMARY KEY, note TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO bronze_youtubeapi_video_stats (video_id) VALUES (?)",
        [(f"v{index}",) for index in range(video_rows)],
    )
    conn.commit()
    conn.close()


def test_content_fingerprint_ignores_unrelated_table_writes(tmp_path):
    import sqlite3

    db_path = tmp_path / "content.db"
    _make_sqlite_db(db_path, video_rows=3)
    baseline = checkpoint.make_key("analysis_frame", db_path, {})

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO collection_attempts (note) VALUES ('collector still running')")
    conn.commit()
    conn.close()

    assert checkpoint.make_key("analysis_frame", db_path, {}) == baseline


def test_content_fingerprint_detects_new_source_rows(tmp_path):
    import sqlite3

    db_path = tmp_path / "content.db"
    _make_sqlite_db(db_path, video_rows=3)
    baseline = checkpoint.make_key("analysis_frame", db_path, {})

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO bronze_youtubeapi_video_stats (video_id) VALUES ('v99')")
    conn.commit()
    conn.close()

    assert checkpoint.make_key("analysis_frame", db_path, {}) != baseline


def test_mtime_mode_detects_any_write(tmp_path):
    db_path = tmp_path / "content.db"
    _make_sqlite_db(db_path, video_rows=3)
    baseline = checkpoint.make_key("analysis_frame", db_path, {}, db_fingerprint_mode="mtime")

    db_path.write_bytes(db_path.read_bytes() + b"padding")

    assert checkpoint.make_key("analysis_frame", db_path, {}, db_fingerprint_mode="mtime") != baseline


def test_path_mode_ignores_data_changes(tmp_path):
    db_path = tmp_path / "content.db"
    _make_sqlite_db(db_path, video_rows=3)
    baseline = checkpoint.make_key("analysis_frame", db_path, {}, db_fingerprint_mode="path")

    _make_sqlite_db(db_path, video_rows=5)

    assert checkpoint.make_key("analysis_frame", db_path, {}, db_fingerprint_mode="path") == baseline


def test_content_fingerprint_falls_back_when_not_sqlite(fake_db):
    fingerprint = checkpoint.database_fingerprint(fake_db, mode="content")
    assert fingerprint["mode"] == "mtime"
    assert "mtime_ns" in fingerprint


def test_unknown_fingerprint_mode_raises(fake_db):
    with pytest.raises(ValueError):
        checkpoint.database_fingerprint(fake_db, mode="nonsense")
