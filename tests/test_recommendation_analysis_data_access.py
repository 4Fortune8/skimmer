from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.RecomendationAnalysis import data_access


@pytest.fixture()
def analysis_db():
    workdir = Path(__file__).parent / ".test_artifacts"
    workdir.mkdir(exist_ok=True)
    db_path = workdir / "recommendation_analysis.sqlite"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE bronze_youtubeapi_video_stats (
            id INTEGER PRIMARY KEY,
            video_id TEXT,
            channel_id TEXT,
            title TEXT,
            published_at TEXT,
            collected_at TEXT,
            duration_seconds TEXT,
            category_id TEXT,
            views TEXT,
            likes TEXT,
            comments TEXT,
            default_audio_language TEXT,
            default_language TEXT
        );
        CREATE TABLE bronze_youtubeapi_channel_stats (
            id INTEGER PRIMARY KEY,
            channel_id TEXT,
            channel_name TEXT,
            subscribers TEXT,
            subscribers_change TEXT,
            views TEXT,
            video_count TEXT,
            country TEXT,
            channel_published_at TEXT,
            collected_at TEXT
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO bronze_youtubeapi_video_stats
        (id, video_id, channel_id, title, published_at, collected_at, duration_seconds, category_id, views, likes, comments)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "v1", "c1", "old", "2026-07-21T15:40:43Z", "2026-07-22T00:00:00Z", "60", "22", "100", "10", "1"),
            (2, "v1", "c1", "mid", "2026-07-21T15:40:43.142162Z", "2026-07-23T00:00:00Z", "60", "22", "200", "N/A", ""),
            (3, "v1", "c1", "new", "2026-07-21T15:40:43Z", "2026-07-24T00:00:00Z", "60", "22", "300", "30", "3"),
            (4, "orphan", "missing", "orphan", "2026-07-21T15:40:43.142162Z", "2026-07-24T00:00:00Z", "bad", "22", "N/A", "", "x"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO bronze_youtubeapi_channel_stats
        (id, channel_id, channel_name, subscribers, subscribers_change, views, video_count, country, channel_published_at, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "c1", "old channel", "1000", "5", "5000", "49", "US", "2020-01-01T00:00:00Z", "2026-07-22T00:00:00Z"),
            (2, "c1", "new channel", "N/A", "", "7000", "50", "US", "2020-01-01T00:00:00.142162Z", "2026-07-24T00:00:00Z"),
        ],
    )
    conn.commit()
    conn.close()
    try:
        yield db_path
    finally:
        if db_path.exists():
            db_path.unlink()
        shutil.rmtree(workdir, ignore_errors=True)


def test_resolve_db_path_precedence_and_missing(monkeypatch, analysis_db):
    workdir = analysis_db.parent
    env_db = workdir / "env.sqlite"
    default_db = workdir / "default.sqlite"
    explicit_db = workdir / "explicit.sqlite"
    for path in (env_db, default_db, explicit_db):
        path.write_bytes(analysis_db.read_bytes())

    monkeypatch.setattr(data_access, "DEFAULT_DB_PATH", default_db)
    assert data_access.resolve_db_path() == default_db

    monkeypatch.setenv("SKIMMER_DB_PATH", str(env_db))
    assert data_access.resolve_db_path() == env_db

    assert data_access.resolve_db_path(explicit_db) == explicit_db

    monkeypatch.delenv("SKIMMER_DB_PATH", raising=False)
    with pytest.raises(FileNotFoundError):
        data_access.resolve_db_path(analysis_db.with_name("missing.sqlite"))


def test_connect_opens_read_only_connection(analysis_db):
    conn = data_access.connect(analysis_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE cannot_write (id INTEGER)")
    finally:
        conn.close()


def test_loaders_coerce_numeric_parse_timestamps_and_dedupe(analysis_db):
    conn = sqlite3.connect(analysis_db)
    try:
        snapshots = data_access.load_video_snapshots(conn=conn)
        assert pd.api.types.is_float_dtype(snapshots["views"])
        assert pd.api.types.is_float_dtype(snapshots["likes"])
        assert pd.api.types.is_float_dtype(snapshots["comments"])
        mid = snapshots.loc[snapshots["title"].eq("mid")].iloc[0]
        assert np.isnan(mid["likes"])
        assert np.isnan(mid["comments"])
        orphan = snapshots.loc[snapshots["video_id"].eq("orphan")].iloc[0]
        assert np.isnan(orphan["duration_seconds"])
        assert np.isnan(orphan["views"])
        assert np.isnan(orphan["likes"])
        assert np.isnan(orphan["comments"])
        whole_second = snapshots.loc[snapshots["title"].eq("old"), "published_at"].iloc[0]
        fractional_second = snapshots.loc[snapshots["title"].eq("mid"), "published_at"].iloc[0]
        assert pd.notna(whole_second)
        assert pd.notna(fractional_second)
        assert str(snapshots["published_at"].dt.tz) == "UTC"

        latest_videos = data_access.load_latest_videos(conn=conn)
        v1 = latest_videos.loc[latest_videos["video_id"].eq("v1")]
        assert len(v1) == 1
        assert v1.iloc[0]["title"] == "new"
        assert v1.iloc[0]["views"] == 300

        channels = data_access.load_latest_channels(conn=conn)
        c1 = channels.loc[channels["channel_id"].eq("c1")]
        assert len(c1) == 1
        assert c1.iloc[0]["channel_name"] == "new channel"
        assert c1.iloc[0]["channel_views"] == 7000
        assert c1.iloc[0]["video_count"] == 50
        assert np.isnan(c1.iloc[0]["subscribers"])
        assert np.isnan(c1.iloc[0]["subscribers_change"])
        assert pd.notna(c1.iloc[0]["channel_published_at"])
        assert str(channels["channel_published_at"].dt.tz) == "UTC"
    finally:
        conn.close()


def test_load_joined_left_join_and_url_helpers(analysis_db):
    conn = sqlite3.connect(analysis_db)
    try:
        joined = data_access.load_joined(conn=conn)
    finally:
        conn.close()

    orphan = joined.loc[joined["video_id"].eq("orphan")].iloc[0]
    assert orphan["channel_id"] == "missing"
    assert pd.isna(orphan["channel_name"])
    assert data_access.video_url("abc") == "https://www.youtube.com/watch?v=abc"
    assert data_access.channel_url("UC123") == "https://www.youtube.com/channel/UC123"
