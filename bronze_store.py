"""SQLite persistence and queue management for profile collection."""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent / "data" / "skimmer.db"
PROFILE_SOURCES = {"vidiq", "socialblade"}


def database_path(database_path=None):
    path = Path(
        database_path or os.environ.get("SKIMMER_DB_PATH") or DEFAULT_DATABASE_PATH
    ).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now():
    return datetime.now(timezone.utc)


def _timestamp(value=None):
    return (value or _now()).replace(microsecond=0).isoformat()


def _connection(db_path=None):
    connection = sqlite3.connect(db_path or database_path())
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _digest(values):
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _channel_key(channel_id):
    return channel_id.strip().lower()


def _published_at(age, observed_at):
    match = re.fullmatch(
        r"\s*(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago\s*",
        age or "",
        re.IGNORECASE,
    )
    if not match:
        return observed_at
    amount = int(match.group(1))
    seconds = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
        "week": 604800,
        "month": 2592000,
        "year": 31536000,
    }[match.group(2).lower()]
    return observed_at - timedelta(seconds=amount * seconds)


def _create_tables(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS bronze_youtube_skimmed (
            id INTEGER PRIMARY KEY,
            observed_at TEXT NOT NULL,
            video_published_at TEXT NOT NULL,
            source_file TEXT NOT NULL,
            video_name TEXT,
            channel_display_name TEXT,
            views TEXT,
            age TEXT,
            channel_id TEXT NOT NULL,
            record_digest TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_youtube_feed_retention
            ON bronze_youtube_skimmed(observed_at);
        CREATE INDEX IF NOT EXISTS idx_youtube_feed_channel
            ON bronze_youtube_skimmed(channel_id);

        CREATE TABLE IF NOT EXISTS profile_queue (
            channel_key TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            channel_name TEXT,
            latest_video_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_success_at TEXT,
            digested INTEGER NOT NULL DEFAULT 0 CHECK(digested IN (0, 1)),
            assigned_source TEXT NOT NULL CHECK(assigned_source IN ('vidiq', 'socialblade')),
            vidiq_failed INTEGER NOT NULL DEFAULT 0 CHECK(vidiq_failed IN (0, 1)),
            socialblade_failed INTEGER NOT NULL DEFAULT 0 CHECK(socialblade_failed IN (0, 1)),
            needs_review INTEGER NOT NULL DEFAULT 0 CHECK(needs_review IN (0, 1))
        );
        CREATE INDEX IF NOT EXISTS idx_profile_queue_work
            ON profile_queue(digested, needs_review, assigned_source);

        CREATE TABLE IF NOT EXISTS bronze_vidiq_channel_stats (
            id INTEGER PRIMARY KEY,
            channel_id TEXT NOT NULL,
            channel_name TEXT,
            subscribers,
            subscribers_change,
            views,
            views_change,
            earnings_low,
            earnings_high,
            engagement,
            upload_frequency,
            average_length,
            data_digest TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS bronze_socialblade_channel_stats (
            id INTEGER PRIMARY KEY,
            channel_id TEXT NOT NULL,
            subscribers,
            subscribers_change,
            subscribers_change_percentage,
            views,
            views_change,
            views_change_percentage,
            data_digest TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS bronze_socialblade_daily_channel_metrics (
            id INTEGER PRIMARY KEY,
            channel_id TEXT NOT NULL,
            subscribers_change,
            subscribers_total,
            views_change,
            views_total,
            videos_change,
            videos_total,
            earnings_low,
            earnings_high,
            data_digest TEXT NOT NULL UNIQUE
        );
        """
    )


def _legacy_tables(connection):
    expected_columns = {
        "bronze_youtube_skimmed": "record_digest",
        "bronze_vidiq_channel_stats": "data_digest",
        "bronze_socialblade_channel_stats": "data_digest",
        "bronze_socialblade_daily_channel_metrics": "data_digest",
    }
    legacy = []
    for table, expected_column in expected_columns.items():
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if columns and expected_column not in columns:
            legacy_name = f"{table}_legacy"
            connection.execute(f"DROP TABLE IF EXISTS {legacy_name}")
            connection.execute(f"ALTER TABLE {table} RENAME TO {legacy_name}")
            legacy.append((table, legacy_name))
    return legacy


def initialize_database(database_path=None):
    """Create compact, deduplicated storage and migrate legacy collector tables."""
    transfers = []
    with _connection(database_path) as connection:
        legacy = _legacy_tables(connection)
        _create_tables(connection)
        for table, legacy_name in legacy:
            rows = connection.execute(f"SELECT * FROM {legacy_name}").fetchall()
            columns = [
                row[1] for row in connection.execute(f"PRAGMA table_info({legacy_name})")
            ]
            for row in rows:
                record = dict(zip(columns, row))
                transfers.append((table, record))
            connection.execute(f"DROP TABLE {legacy_name}")
    for table, record in transfers:
        if table == "bronze_youtube_skimmed" and record.get("channel_id"):
            insert_youtube_skimmed(
                [{
                    "video_name": record.get("video_name"),
                    "channel_display_name": record.get("channel_display_name"),
                    "views": record.get("views"),
                    "age": record.get("age"),
                    "channel_id": record["channel_id"],
                }],
                record.get("source_file") or "legacy",
                database_path,
            )
        elif table == "bronze_vidiq_channel_stats" and record.get("channel_id"):
            insert_vidiq_channel_stats(record, database_path)
        elif table == "bronze_socialblade_channel_stats" and record.get("channel_id"):
            insert_socialblade_channel_stats(record, database_path)
        elif table == "bronze_socialblade_daily_channel_metrics" and record.get("channel_id"):
            insert_socialblade_daily_channel_metrics([record], database_path)


def insert_youtube_skimmed(records, source_file, database_path=None):
    """Store unseen feed records and retain only the last 14 days."""
    records = list(records)
    if not records:
        return 0
    initialize_database(database_path)
    observed_at = _now()
    rows = []
    for record in records:
        channel_id = record.get("channel_id")
        if not channel_id or not channel_id.strip():
            continue
        published_at = _published_at(record.get("age"), observed_at)
        values = {
            "channel_id": channel_id,
            "video_name": record.get("video_name"),
            "channel_display_name": record.get("channel_display_name"),
            "views": record.get("views"),
            "age": record.get("age"),
            "video_published_at": _timestamp(published_at),
        }
        rows.append(
            (
                _timestamp(observed_at),
                values["video_published_at"],
                source_file,
                values["video_name"],
                values["channel_display_name"],
                values["views"],
                values["age"],
                channel_id,
                _digest(values),
            )
        )
    with _connection(database_path) as connection:
        before = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO bronze_youtube_skimmed (
                observed_at, video_published_at, source_file, video_name,
                channel_display_name, views, age, channel_id, record_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.execute(
            "DELETE FROM bronze_youtube_skimmed WHERE observed_at < ?",
            (_timestamp(observed_at - timedelta(days=14)),),
        )
        return connection.total_changes - before


def refresh_profile_queue(database_path=None):
    """Refresh queue eligibility from retained feed records after each feed run."""
    initialize_database(database_path)
    now = _now()
    with _connection(database_path) as connection:
        connection.execute(
            "DELETE FROM bronze_youtube_skimmed WHERE observed_at < ?",
            (_timestamp(now - timedelta(days=14)),),
        )
        channels = connection.execute(
            """
            SELECT channel_id, channel_display_name, MAX(video_published_at), MAX(observed_at)
            FROM bronze_youtube_skimmed
            GROUP BY LOWER(TRIM(channel_id))
            """
        ).fetchall()
        for channel_id, channel_name, latest_video_at, last_seen_at in channels:
            key = _channel_key(channel_id)
            existing = connection.execute(
                "SELECT last_success_at, digested, needs_review FROM profile_queue WHERE channel_key = ?",
                (key,),
            ).fetchone()
            initial_source = "vidiq" if int(_digest(key)[0], 16) % 2 else "socialblade"
            eligible = existing is None
            if existing and not existing[2]:
                last_success = (
                    datetime.fromisoformat(existing[0]) if existing[0] else None
                )
                eligible = (
                    last_success is None
                    or last_success < now - timedelta(days=7)
                    or datetime.fromisoformat(latest_video_at)
                    >= now - timedelta(days=14)
                )
            if existing:
                connection.execute(
                    """
                    UPDATE profile_queue
                    SET channel_id = ?, channel_name = ?, latest_video_at = ?, last_seen_at = ?,
                        digested = CASE WHEN needs_review = 1 THEN 1 WHEN ? THEN 0 ELSE digested END
                    WHERE channel_key = ?
                    """,
                    (channel_id, channel_name, latest_video_at, last_seen_at, eligible, key),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO profile_queue (
                        channel_key, channel_id, channel_name, latest_video_at, last_seen_at,
                        assigned_source
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (key, channel_id, channel_name, latest_video_at, last_seen_at, initial_source),
                )


def get_profile_queue(source, limit=0, database_path=None):
    """Return pending channels assigned to one profile source."""
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    initialize_database(database_path)
    query = """
        SELECT channel_id FROM profile_queue
        WHERE digested = 0 AND needs_review = 0 AND assigned_source = ?
        ORDER BY latest_video_at DESC, channel_key
    """
    params = [source]
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    with _connection(database_path) as connection:
        return [row[0] for row in connection.execute(query, params)]


def mark_profile_succeeded(channel_id, source, database_path=None):
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    initialize_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            """
            UPDATE profile_queue
            SET digested = 1, last_success_at = ?, needs_review = 0
            WHERE channel_key = ? AND assigned_source = ?
            """,
            (_timestamp(), _channel_key(channel_id), source),
        )


def mark_profile_failed(channel_id, source, database_path=None):
    """Fail over once; preserve a failed-both channel for manual review."""
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    other = "socialblade" if source == "vidiq" else "vidiq"
    failed_column = f"{source}_failed"
    with _connection(database_path) as connection:
        connection.execute(
            f"UPDATE profile_queue SET {failed_column} = 1 WHERE channel_key = ?",
            (_channel_key(channel_id),),
        )
        row = connection.execute(
            "SELECT vidiq_failed, socialblade_failed FROM profile_queue WHERE channel_key = ?",
            (_channel_key(channel_id),),
        ).fetchone()
        if row and all(row):
            connection.execute(
                "UPDATE profile_queue SET digested = 1, needs_review = 1 WHERE channel_key = ?",
                (_channel_key(channel_id),),
            )
        else:
            connection.execute(
                "UPDATE profile_queue SET assigned_source = ? WHERE channel_key = ?",
                (other, _channel_key(channel_id)),
            )


def _insert_metric(table, record, columns, database_path=None):
    initialize_database(database_path)
    values = {column: record.get(column) for column in columns}
    values["channel_id"] = record["channel_id"]
    data_digest = _digest(values)
    placeholders = ", ".join("?" for _ in (*columns, "data_digest"))
    with _connection(database_path) as connection:
        before = connection.total_changes
        connection.execute(
            f"INSERT OR IGNORE INTO {table} ({', '.join((*columns, 'data_digest'))}) VALUES ({placeholders})",
            tuple(values[column] for column in columns) + (data_digest,),
        )
        return connection.total_changes - before


def insert_vidiq_channel_stats(record, database_path=None):
    return _insert_metric(
        "bronze_vidiq_channel_stats",
        record,
        (
            "channel_id", "channel_name", "subscribers", "subscribers_change", "views",
            "views_change", "earnings_low", "earnings_high", "engagement",
            "upload_frequency", "average_length",
        ),
        database_path,
    )


def insert_socialblade_channel_stats(record, database_path=None):
    return _insert_metric(
        "bronze_socialblade_channel_stats",
        record,
        (
            "channel_id", "subscribers", "subscribers_change",
            "subscribers_change_percentage", "views", "views_change",
            "views_change_percentage",
        ),
        database_path,
    )


def insert_socialblade_daily_channel_metrics(records, database_path=None):
    return sum(
        _insert_metric(
            "bronze_socialblade_daily_channel_metrics",
            record,
            (
                "channel_id", "subscribers_change", "subscribers_total",
                "views_change", "views_total", "videos_change", "videos_total",
                "earnings_low", "earnings_high",
            ),
            database_path,
        )
        for record in records
    )
