"""Data access helpers for YouTube recommendation analysis."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH: Path = Path(__file__).resolve().parents[2] / "data" / "skimmer.db"

VIDEO_COLUMNS = [
    "video_id",
    "channel_id",
    "title",
    "published_at",
    "collected_at",
    "duration_seconds",
    "category_id",
    "views",
    "likes",
    "comments",
]

CHANNEL_COLUMNS = [
    "channel_id",
    "channel_name",
    "subscribers",
    "subscribers_change",
    "channel_views",
    "video_count",
    "country",
    "channel_published_at",
    "collected_at",
    "stats_source",
]


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the SQLite database path using arg, env var, then default."""

    path = Path(db_path or os.environ.get("SKIMMER_DB_PATH") or DEFAULT_DB_PATH).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"Skimmer database not found at {path}. Pass db_path or set SKIMMER_DB_PATH."
        )
    return path


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a read-only SQLite connection to the analysis database."""

    path = resolve_db_path(db_path).resolve()
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load_video_snapshots(
    db_path: str | Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    """Load all YouTube API video snapshot rows with normalized dtypes."""

    sql = """
        SELECT
            video_id,
            channel_id,
            title,
            published_at,
            collected_at,
            duration_seconds,
            category_id,
            views,
            likes,
            comments
        FROM bronze_youtubeapi_video_stats
    """
    return _normalize_videos(_read_sql(sql, db_path=db_path, conn=conn))


def load_latest_videos(
    db_path: str | Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    """Load the newest YouTube API snapshot for each video_id."""

    sql = """
        WITH ranked AS (
            SELECT
                video_id,
                channel_id,
                title,
                published_at,
                collected_at,
                duration_seconds,
                category_id,
                views,
                likes,
                comments,
                ROW_NUMBER() OVER (
                    PARTITION BY video_id
                    ORDER BY collected_at DESC, id DESC
                ) AS rn
            FROM bronze_youtubeapi_video_stats
        )
        SELECT
            video_id,
            channel_id,
            title,
            published_at,
            collected_at,
            duration_seconds,
            category_id,
            views,
            likes,
            comments
        FROM ranked
        WHERE rn = 1
    """
    return _normalize_videos(_read_sql(sql, db_path=db_path, conn=conn))


def load_latest_channels(
    db_path: str | Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    """Load latest channel stats and fill missing video channels from vidiq."""

    api_sql = """
        WITH ranked AS (
            SELECT
                channel_id,
                channel_name,
                subscribers,
                subscribers_change,
                views AS channel_views,
                video_count,
                country,
                channel_published_at,
                collected_at,
                'youtube_api' AS stats_source,
                ROW_NUMBER() OVER (
                    PARTITION BY channel_id
                    ORDER BY collected_at DESC, id DESC
                ) AS rn
            FROM bronze_youtubeapi_channel_stats
        )
        SELECT
            channel_id,
            channel_name,
            subscribers,
            subscribers_change,
            channel_views,
            video_count,
            country,
            channel_published_at,
            collected_at,
            stats_source
        FROM ranked
        WHERE rn = 1
    """
    channels = _normalize_channels(_read_sql(api_sql, db_path=db_path, conn=conn))
    missing_ids = _video_channel_ids_without_stats(channels, db_path=db_path, conn=conn)
    if missing_ids:
        try:
            vidiq_channels = _load_vidiq_channels(missing_ids, db_path=db_path, conn=conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to load vidiq channel fill data: %s", exc)
            vidiq_channels = pd.DataFrame(columns=CHANNEL_COLUMNS)
        if not vidiq_channels.empty:
            channels = pd.concat([channels, vidiq_channels], ignore_index=True)
        else:
            logger.warning("No usable vidiq channel rows found for %d missing channels.", len(missing_ids))
    return channels[CHANNEL_COLUMNS]


def load_joined(
    db_path: str | Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    """Load latest videos left-joined to latest channel attributes."""

    videos = load_latest_videos(db_path=db_path, conn=conn)
    channels = load_latest_channels(db_path=db_path, conn=conn)
    return videos.merge(channels, on="channel_id", how="left")


def video_url(video_id: str) -> str:
    """Return the canonical YouTube watch URL for a video id."""

    return f"https://www.youtube.com/watch?v={video_id}"


def channel_url(channel_id: str) -> str:
    """Return the canonical YouTube channel URL for a channel id."""

    return f"https://www.youtube.com/channel/{channel_id}"


def _read_sql(
    sql: str,
    db_path: str | Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    if conn is not None:
        return pd.read_sql_query(sql, conn)
    owned_conn = connect(db_path)
    try:
        return pd.read_sql_query(sql, owned_conn)
    finally:
        owned_conn.close()


def _normalize_videos(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for column in ("published_at", "collected_at"):
        df[column] = pd.to_datetime(df[column], utc=True, errors="coerce", format="mixed")
    for column in ("duration_seconds", "views", "likes", "comments"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    for column in ("video_id", "channel_id", "title", "category_id"):
        df[column] = df[column].astype("string")
    return df[VIDEO_COLUMNS]


def _normalize_channels(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for column in ("channel_published_at", "collected_at"):
        df[column] = pd.to_datetime(df[column], utc=True, errors="coerce", format="mixed")
    for column in ("subscribers", "subscribers_change", "channel_views", "video_count"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    for column in ("channel_id", "channel_name", "country", "stats_source"):
        df[column] = df[column].astype("string")
    return df[CHANNEL_COLUMNS]


def _video_channel_ids_without_stats(
    channels: pd.DataFrame,
    db_path: str | Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    video_sql = "SELECT DISTINCT channel_id FROM bronze_youtubeapi_video_stats WHERE channel_id IS NOT NULL"
    video_channels = _read_sql(video_sql, db_path=db_path, conn=conn)["channel_id"].dropna().astype(str)
    known_channels = set(channels["channel_id"].dropna().astype(str))
    return sorted(channel_id for channel_id in video_channels if channel_id not in known_channels)


def _load_vidiq_channels(
    missing_ids: Iterable[str],
    db_path: str | Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    missing = pd.DataFrame({"channel_id": list(missing_ids)})
    if missing.empty:
        return pd.DataFrame(columns=CHANNEL_COLUMNS)

    queue_sql = """
        SELECT channel_key, channel_id, youtube_channel_id
        FROM profile_queue
        WHERE youtube_channel_id IS NOT NULL
    """
    profiles_sql = """
        SELECT
            p.channel_id AS vidiq_channel_id,
            p.channel_id,
            p.channel_name,
            p.subscribers_total AS subscribers,
            NULL AS subscribers_change,
            p.views_total AS channel_views,
            p.videos_total AS video_count,
            p.location AS country,
            p.joined_at AS channel_published_at,
            NULL AS collected_at,
            'vidiq' AS stats_source
        FROM bronze_vidiq_channel_profiles p
    """
    stats_sql = """
        SELECT
            s.channel_id AS vidiq_channel_id,
            s.channel_id,
            s.channel_name,
            s.subscribers,
            s.subscribers_change,
            s.views AS channel_views,
            NULL AS video_count,
            NULL AS country,
            NULL AS channel_published_at,
            NULL AS collected_at,
            'vidiq' AS stats_source
        FROM bronze_vidiq_channel_stats s
    """
    queue = _read_sql(queue_sql, db_path=db_path, conn=conn)
    profiles = _read_sql(profiles_sql, db_path=db_path, conn=conn)
    stats = _read_sql(stats_sql, db_path=db_path, conn=conn)
    combined = pd.concat([profiles, stats], ignore_index=True)
    mapping = _vidiq_to_youtube_mapping(queue)
    combined["channel_id"] = combined["vidiq_channel_id"].astype("string").str.lower().map(mapping).fillna(
        combined["channel_id"].where(combined["channel_id"].astype("string").str.startswith("UC"))
    )
    combined = combined.merge(missing, on="channel_id", how="inner")
    if combined.empty:
        return pd.DataFrame(columns=CHANNEL_COLUMNS)
    combined["_priority"] = combined["channel_published_at"].notna().astype(int)
    combined = combined.sort_values(["channel_id", "_priority"], ascending=[True, False])
    combined = combined.drop_duplicates("channel_id", keep="first")
    return _normalize_channels(combined[CHANNEL_COLUMNS])


def _vidiq_to_youtube_mapping(queue: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for _, row in queue.iterrows():
        youtube_channel_id = row["youtube_channel_id"]
        if pd.isna(youtube_channel_id):
            continue
        for column in ("channel_key", "channel_id"):
            key = row[column]
            if pd.notna(key):
                mapping[str(key).lower()] = str(youtube_channel_id)
    return mapping
