"""SQLite persistence and queue management for profile collection."""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from skimmer.config import PROJECT_ROOT

DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "skimmer.db"
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
    connection = sqlite3.connect(db_path or database_path(), timeout=30)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
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
            youtube_channel_id TEXT,
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
            needs_review INTEGER NOT NULL DEFAULT 0 CHECK(needs_review IN (0, 1)),
            claimed_by TEXT,
            claimed_at TEXT,
            youtube_channel_id TEXT,
            channel_id_claimed_by TEXT,
            channel_id_claimed_at TEXT,
            youtube_channel_id_attempted_at TEXT,
            youtube_api_failed INTEGER NOT NULL DEFAULT 0 CHECK(youtube_api_failed IN (0, 1)),
            youtube_api_attempted_at TEXT,
            youtube_api_success_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_profile_queue_work
            ON profile_queue(digested, needs_review, assigned_source, claimed_at);

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

        CREATE TABLE IF NOT EXISTS bronze_vidiq_channel_profiles (
            id INTEGER PRIMARY KEY,
            channel_id TEXT NOT NULL,
            channel_name TEXT,
            joined_at TEXT,
            location TEXT,
            category TEXT,
            videos_total,
            subscribers_total,
            views_total,
            estimated_monthly_earnings,
            content_period TEXT,
            long_form_uploads,
            shorts_uploads,
            long_form_views,
            shorts_views,
            ranking_30_day_country TEXT,
            ranking_30_day_worldwide TEXT,
            data_digest TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS bronze_socialblade_channel_stats (
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

        CREATE TABLE IF NOT EXISTS collection_errors (
            id INTEGER PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('vidiq', 'socialblade')),
            channel_id TEXT,
            source_url TEXT,
            error_type TEXT NOT NULL,
            status_code INTEGER,
            message TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_collection_errors_source_time
            ON collection_errors(source, occurred_at);

        CREATE TABLE IF NOT EXISTS collection_attempts (
            id INTEGER PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('vidiq', 'socialblade')),
            channel_key TEXT NOT NULL,
            identifier TEXT NOT NULL,
            identifier_kind TEXT NOT NULL CHECK(identifier_kind IN ('handle', 'channel_id')),
            source_url TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK(outcome IN ('succeeded', 'failed')),
            failure_type TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_collection_attempts_channel
            ON collection_attempts(channel_key, source, occurred_at);

        CREATE TABLE IF NOT EXISTS youtube_api_quota_usage (
            quota_date TEXT PRIMARY KEY,
            units_used INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS bronze_youtubeapi_channel_stats (
            id INTEGER PRIMARY KEY,
            collected_at TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT,
            subscribers,
            subscribers_change,
            views,
            views_change,
            video_count,
            country TEXT,
            channel_published_at TEXT,
            uploads_playlist_id TEXT,
            data_digest TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_bronze_youtubeapi_channel_stats_channel_time
            ON bronze_youtubeapi_channel_stats(channel_id, collected_at);

        CREATE TABLE IF NOT EXISTS bronze_youtubeapi_video_stats (
            id INTEGER PRIMARY KEY,
            collected_at TEXT NOT NULL,
            video_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            title TEXT,
            published_at TEXT,
            duration_seconds,
            category_id TEXT,
            views,
            likes,
            comments,
            data_digest TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_bronze_youtubeapi_video_stats_video_time
            ON bronze_youtubeapi_video_stats(video_id, collected_at);
        CREATE INDEX IF NOT EXISTS idx_bronze_youtubeapi_video_stats_channel
            ON bronze_youtubeapi_video_stats(channel_id);
        """
    )


def _legacy_tables(connection):
    expected_columns = {
        "bronze_youtube_skimmed": "record_digest",
        "bronze_vidiq_channel_stats": "data_digest",
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


def _migrate_socialblade_table_name(connection):
    stats_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(bronze_socialblade_channel_stats)"
        )
    }
    if stats_columns and "subscribers_total" not in stats_columns:
        connection.execute("DROP TABLE bronze_socialblade_channel_stats")

    daily_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(bronze_socialblade_daily_channel_metrics)"
        )
    }
    if daily_columns:
        stats_exists = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'bronze_socialblade_channel_stats'
            """
        ).fetchone()
        if stats_exists:
            connection.execute("DROP TABLE bronze_socialblade_daily_channel_metrics")
        else:
            connection.execute(
                """
                ALTER TABLE bronze_socialblade_daily_channel_metrics
                RENAME TO bronze_socialblade_channel_stats
                """
            )


def _ensure_profile_queue_columns(connection):
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(profile_queue)")
    }
    if "claimed_by" not in columns:
        connection.execute("ALTER TABLE profile_queue ADD COLUMN claimed_by TEXT")
    if "claimed_at" not in columns:
        connection.execute("ALTER TABLE profile_queue ADD COLUMN claimed_at TEXT")
    if "youtube_channel_id" not in columns:
        connection.execute("ALTER TABLE profile_queue ADD COLUMN youtube_channel_id TEXT")
    if "channel_id_claimed_by" not in columns:
        connection.execute(
            "ALTER TABLE profile_queue ADD COLUMN channel_id_claimed_by TEXT"
        )
    if "channel_id_claimed_at" not in columns:
        connection.execute(
            "ALTER TABLE profile_queue ADD COLUMN channel_id_claimed_at TEXT"
        )
    if "youtube_channel_id_attempted_at" not in columns:
        connection.execute(
            "ALTER TABLE profile_queue "
            "ADD COLUMN youtube_channel_id_attempted_at TEXT"
        )
    if "youtube_api_failed" not in columns:
        connection.execute(
            "ALTER TABLE profile_queue ADD COLUMN youtube_api_failed INTEGER NOT NULL DEFAULT 0"
        )
    if "youtube_api_attempted_at" not in columns:
        connection.execute(
            "ALTER TABLE profile_queue ADD COLUMN youtube_api_attempted_at TEXT"
        )
    if "youtube_api_success_at" not in columns:
        connection.execute(
            "ALTER TABLE profile_queue ADD COLUMN youtube_api_success_at TEXT"
        )


def _ensure_youtube_feed_columns(connection):
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(bronze_youtube_skimmed)")
    }
    if "youtube_channel_id" not in columns:
        connection.execute(
            "ALTER TABLE bronze_youtube_skimmed ADD COLUMN youtube_channel_id TEXT"
        )


def initialize_database(database_path=None):
    """Create compact, deduplicated storage and migrate legacy collector tables."""
    transfers = []
    with _connection(database_path) as connection:
        legacy = _legacy_tables(connection)
        _migrate_socialblade_table_name(connection)
        _create_tables(connection)
        _ensure_youtube_feed_columns(connection)
        _ensure_profile_queue_columns(connection)
        connection.execute("PRAGMA journal_mode = WAL")
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
            "youtube_channel_id": record.get("youtube_channel_id"),
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
                values["youtube_channel_id"],
                _digest(values),
            )
        )
    with _connection(database_path) as connection:
        before = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO bronze_youtube_skimmed (
                observed_at, video_published_at, source_file, video_name,
                channel_display_name, views, age, channel_id, youtube_channel_id,
                record_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            SELECT channel_id, channel_display_name, MAX(video_published_at),
                   MAX(observed_at), MAX(youtube_channel_id)
            FROM bronze_youtube_skimmed
            GROUP BY LOWER(TRIM(channel_id))
            """
        ).fetchall()
        for channel_id, channel_name, latest_video_at, last_seen_at, youtube_channel_id in channels:
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
                    or last_success < now - timedelta(days=14)
                )
            if existing:
                connection.execute(
                    """
                    UPDATE profile_queue
                    SET channel_id = ?, channel_name = ?, latest_video_at = ?, last_seen_at = ?,
                        youtube_channel_id = COALESCE(?, youtube_channel_id),
                        digested = CASE WHEN needs_review = 1 THEN 1 WHEN ? THEN 0 ELSE digested END
                    WHERE channel_key = ?
                    """,
                    (
                        channel_id,
                        channel_name,
                        latest_video_at,
                        last_seen_at,
                        youtube_channel_id,
                        eligible,
                        key,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO profile_queue (
                        channel_key, channel_id, channel_name, latest_video_at, last_seen_at,
                        assigned_source, youtube_channel_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        channel_id,
                        channel_name,
                        latest_video_at,
                        last_seen_at,
                        initial_source,
                        youtube_channel_id,
                    ),
                )


def get_profile_queue(source, limit=0, database_path=None):
    """Return pending channels assigned to one profile source."""
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    initialize_database(database_path)
    query = """
        SELECT channel_id FROM profile_queue
        WHERE digested = 0 AND needs_review = 0 AND assigned_source = ?
          AND claimed_by IS NULL
          AND youtube_api_attempted_at IS NOT NULL
          AND youtube_api_success_at IS NULL
        ORDER BY latest_video_at DESC, channel_key
    """
    params = [source]
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    with _connection(database_path) as connection:
        return [row[0] for row in connection.execute(query, params)]


def claim_profile_batch(source, worker_id, limit=100, database_path=None):
    """Atomically lease pending source-assigned profiles to one worker."""
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    if not worker_id:
        raise ValueError("worker_id is required.")
    if limit < 1:
        raise ValueError("limit must be at least one.")
    initialize_database(database_path)
    now = _now()
    expired_at = _timestamp(now - timedelta(hours=2))
    claimed_at = _timestamp(now)
    with _connection(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT channel_key,
                   CASE WHEN ? = 'socialblade' THEN youtube_channel_id ELSE channel_id END
            FROM profile_queue
            WHERE digested = 0
              AND needs_review = 0
              AND assigned_source = ?
              AND (? != 'socialblade' OR youtube_channel_id IS NOT NULL)
              AND (claimed_at IS NULL OR claimed_at < ?)
              AND youtube_api_attempted_at IS NOT NULL
              AND youtube_api_success_at IS NULL
            ORDER BY latest_video_at DESC, channel_key
            LIMIT ?
            """,
            (source, source, source, expired_at, limit),
        ).fetchall()
        if rows:
            keys = [row[0] for row in rows]
            placeholders = ", ".join("?" for _ in keys)
            connection.execute(
                f"""
                UPDATE profile_queue
                SET claimed_by = ?, claimed_at = ?
                WHERE channel_key IN ({placeholders})
                """,
                (worker_id, claimed_at, *keys),
            )
        return [row[1] for row in rows]


def claim_channel_id_resolution_batch(worker_id, limit=100, database_path=None):
    """Atomically lease unresolved SocialBlade handles for YouTube ID resolution."""
    if not worker_id:
        raise ValueError("worker_id is required.")
    if limit < 1:
        raise ValueError("limit must be at least one.")
    initialize_database(database_path)
    now = _now()
    claimed_at = _timestamp(now)
    expired_at = _timestamp(now - timedelta(hours=2))
    retry_at = _timestamp(now - timedelta(hours=1))
    with _connection(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT channel_key, channel_id
            FROM profile_queue
            WHERE digested = 0
              AND needs_review = 0
              AND assigned_source = 'socialblade'
              AND youtube_channel_id IS NULL
              AND (channel_id_claimed_at IS NULL OR channel_id_claimed_at < ?)
              AND (
                  youtube_channel_id_attempted_at IS NULL
                  OR youtube_channel_id_attempted_at < ?
              )
            ORDER BY latest_video_at DESC, channel_key
            LIMIT ?
            """,
            (expired_at, retry_at, limit),
        ).fetchall()
        if rows:
            keys = [row[0] for row in rows]
            placeholders = ", ".join("?" for _ in keys)
            connection.execute(
                f"""
                UPDATE profile_queue
                SET channel_id_claimed_by = ?, channel_id_claimed_at = ?,
                    youtube_channel_id_attempted_at = ?
                WHERE channel_key IN ({placeholders})
                """,
                (worker_id, claimed_at, claimed_at, *keys),
            )
        return rows


def store_youtube_channel_id(channel_key, youtube_channel_id, database_path=None):
    if not re.fullmatch(r"UC[\w-]{20,}", youtube_channel_id or ""):
        raise ValueError("youtube_channel_id must be a canonical UC channel ID.")
    with _connection(database_path) as connection:
        connection.execute(
            """
            UPDATE profile_queue
            SET youtube_channel_id = ?, channel_id_claimed_by = NULL,
                channel_id_claimed_at = NULL
            WHERE channel_key = ?
            """,
            (youtube_channel_id, _channel_key(channel_key)),
        )


def release_channel_id_resolution_batch(worker_id, database_path=None):
    with _connection(database_path) as connection:
        connection.execute(
            """
            UPDATE profile_queue
            SET channel_id_claimed_by = NULL, channel_id_claimed_at = NULL
            WHERE channel_id_claimed_by = ? AND youtube_channel_id IS NULL
            """,
            (worker_id,),
        )


def release_profile_batch(source, worker_id, database_path=None):
    """Release unprocessed work after a source-level failure."""
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    with _connection(database_path) as connection:
        connection.execute(
            """
            UPDATE profile_queue
            SET claimed_by = NULL, claimed_at = NULL
            WHERE assigned_source = ? AND claimed_by = ? AND digested = 0
            """,
            (source, worker_id),
        )


def profile_identifier_candidates(source, channel_id, database_path=None):
    """Return source-preferred identifiers for a queued channel."""
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    with _connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT channel_key, channel_id, youtube_channel_id
            FROM profile_queue
            WHERE channel_key = ? OR youtube_channel_id = ?
            """,
            (_channel_key(channel_id), channel_id),
        ).fetchone()
    if row is None:
        return [(channel_id, "channel_id" if channel_id.startswith("UC") else "handle")]

    channel_key, handle, youtube_channel_id = row
    identifiers = (
        ((handle, "handle"), (youtube_channel_id, "channel_id"))
        if source == "vidiq"
        else ((youtube_channel_id, "channel_id"), (handle, "handle"))
    )
    candidates = []
    for identifier, identifier_kind in identifiers:
        candidate = (identifier, identifier_kind)
        if identifier and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def record_collection_attempt(
    source,
    channel_id,
    identifier,
    identifier_kind,
    source_url,
    outcome,
    failure_type=None,
    database_path=None,
):
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    if outcome not in {"succeeded", "failed"}:
        raise ValueError("outcome must be succeeded or failed.")
    with _connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO collection_attempts (
                occurred_at, source, channel_key, identifier, identifier_kind,
                source_url, outcome, failure_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _timestamp(),
                source,
                _channel_key(channel_id),
                identifier,
                identifier_kind,
                source_url,
                outcome,
                failure_type,
            ),
        )


def mark_profile_succeeded(channel_id, source, database_path=None):
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    initialize_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            """
            UPDATE profile_queue
            SET digested = 1, last_success_at = ?, needs_review = 0,
                claimed_by = NULL, claimed_at = NULL
            WHERE (channel_key = ? OR youtube_channel_id = ?) AND assigned_source = ?
            """,
            (_timestamp(), _channel_key(channel_id), channel_id, source),
        )


def mark_profile_failed(channel_id, source, database_path=None):
    """Fail over once; preserve a failed-both channel for manual review."""
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    other = "socialblade" if source == "vidiq" else "vidiq"
    failed_column = f"{source}_failed"
    with _connection(database_path) as connection:
        connection.execute(
            f"""
            UPDATE profile_queue
            SET {failed_column} = 1, claimed_by = NULL, claimed_at = NULL
            WHERE channel_key = ? OR youtube_channel_id = ?
            """,
            (_channel_key(channel_id), channel_id),
        )
        row = connection.execute(
            """
            SELECT vidiq_failed, socialblade_failed
            FROM profile_queue
            WHERE channel_key = ? OR youtube_channel_id = ?
            """,
            (_channel_key(channel_id), channel_id),
        ).fetchone()
        if row and all(row):
            connection.execute(
                """
                UPDATE profile_queue
                SET digested = 1, needs_review = 1
                WHERE channel_key = ? OR youtube_channel_id = ?
                """,
                (_channel_key(channel_id), channel_id),
            )
        else:
            connection.execute(
                """
                UPDATE profile_queue
                SET assigned_source = ?
                WHERE channel_key = ? OR youtube_channel_id = ?
                """,
                (other, _channel_key(channel_id), channel_id),
            )


def record_collection_error(
    source,
    error_type,
    message,
    channel_id=None,
    source_url=None,
    status_code=None,
    database_path=None,
):
    if source not in PROFILE_SOURCES:
        raise ValueError(f"Unsupported profile source: {source}")
    initialize_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO collection_errors (
                occurred_at, source, channel_id, source_url, error_type,
                status_code, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _timestamp(),
                source,
                channel_id,
                source_url,
                error_type,
                status_code,
                message,
            ),
        )


def _quota_date_key(occurred_at=None):
    moment = (occurred_at or _now()).astimezone(ZoneInfo("America/Los_Angeles"))
    return moment.date().isoformat()


def record_youtube_api_quota_usage(units, occurred_at=None, database_path=None):
    if units < 0:
        raise ValueError("units must be non-negative.")
    initialize_database(database_path)
    quota_date = _quota_date_key(occurred_at)
    with _connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO youtube_api_quota_usage (quota_date, units_used)
            VALUES (?, ?)
            ON CONFLICT(quota_date) DO UPDATE SET
                units_used = units_used + excluded.units_used
            """,
            (quota_date, units),
        )


def get_youtube_api_quota_usage(occurred_at=None, database_path=None):
    initialize_database(database_path)
    quota_date = _quota_date_key(occurred_at)
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT units_used FROM youtube_api_quota_usage WHERE quota_date = ?",
            (quota_date,),
        ).fetchone()
    return row[0] if row else 0


def reserve_youtube_api_quota_unit(budget, occurred_at=None, database_path=None):
    """Atomically reserve one quota unit without exceeding the local budget."""
    if budget < 1:
        raise ValueError("budget must be at least one.")
    initialize_database(database_path)
    quota_date = _quota_date_key(occurred_at)
    with _connection(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO youtube_api_quota_usage (quota_date, units_used)
            VALUES (?, 0)
            ON CONFLICT(quota_date) DO NOTHING
            """,
            (quota_date,),
        )
        result = connection.execute(
            """
            UPDATE youtube_api_quota_usage
            SET units_used = units_used + 1
            WHERE quota_date = ? AND units_used < ?
            """,
            (quota_date, budget),
        )
        return result.rowcount == 1


def claim_youtube_api_batch(worker_id, limit=100, database_path=None):
    if not worker_id:
        raise ValueError("worker_id is required.")
    if limit < 1:
        raise ValueError("limit must be at least one.")
    initialize_database(database_path)
    now = _now()
    claimed_at = _timestamp(now)
    expired_at = _timestamp(now - timedelta(hours=2))
    retry_at = _timestamp(now - timedelta(hours=1))
    with _connection(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT channel_key, channel_id, youtube_channel_id
            FROM profile_queue
            WHERE (claimed_by IS NULL OR claimed_at < ?)
              AND (
                  youtube_api_success_at IS NULL
                  OR youtube_api_attempted_at IS NULL
                  OR youtube_api_attempted_at < ?
              )
            ORDER BY
                CASE WHEN youtube_api_success_at IS NULL THEN 0 ELSE 1 END,
                COALESCE(youtube_api_success_at, youtube_api_attempted_at, last_seen_at) ASC,
                latest_video_at DESC,
                channel_key
            LIMIT ?
            """,
            (expired_at, retry_at, limit),
        ).fetchall()
        if rows:
            keys = [row[0] for row in rows]
            placeholders = ", ".join("?" for _ in keys)
            connection.execute(
                f"""
                UPDATE profile_queue
                SET claimed_by = ?, claimed_at = ?, youtube_api_attempted_at = ?
                WHERE channel_key IN ({placeholders})
                """,
                (worker_id, claimed_at, claimed_at, *keys),
            )
        return rows


def release_youtube_api_batch(worker_id, database_path=None):
    with _connection(database_path) as connection:
        connection.execute(
            """
            UPDATE profile_queue
            SET claimed_by = NULL, claimed_at = NULL
            WHERE claimed_by = ? AND youtube_api_success_at IS NULL
            """,
            (worker_id,),
        )


def mark_youtube_api_succeeded(channel_id, database_path=None):
    initialize_database(database_path)
    now = _timestamp()
    with _connection(database_path) as connection:
        connection.execute(
            """
            UPDATE profile_queue
            SET youtube_api_failed = 0, youtube_api_success_at = ?,
                last_success_at = ?, digested = 1, needs_review = 0,
                claimed_by = NULL, claimed_at = NULL
            WHERE channel_key = ? OR youtube_channel_id = ?
            """,
            (now, now, _channel_key(channel_id), channel_id),
        )


def mark_youtube_api_failed(channel_id, database_path=None):
    initialize_database(database_path)
    now = _timestamp()
    with _connection(database_path) as connection:
        connection.execute(
            """
            UPDATE profile_queue
            SET youtube_api_failed = 1, youtube_api_attempted_at = ?,
                claimed_by = NULL, claimed_at = NULL
            WHERE channel_key = ? OR youtube_channel_id = ?
            """,
            (now, _channel_key(channel_id), channel_id),
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


def insert_youtubeapi_channel_stats(record, database_path=None):
    initialize_database(database_path)
    columns = (
        "collected_at",
        "channel_id",
        "channel_name",
        "subscribers",
        "subscribers_change",
        "views",
        "views_change",
        "video_count",
        "country",
        "channel_published_at",
        "uploads_playlist_id",
    )
    values = {column: record.get(column) for column in columns}
    digest_values = dict(values)
    if digest_values.get("collected_at"):
        digest_values["collected_at"] = digest_values["collected_at"][:10]
    digest_values["channel_id"] = record["channel_id"]
    data_digest = _digest(digest_values)
    with _connection(database_path) as connection:
        before = connection.total_changes
        connection.execute(
            """
            INSERT OR IGNORE INTO bronze_youtubeapi_channel_stats (
                collected_at, channel_id, channel_name, subscribers,
                subscribers_change, views, views_change, video_count, country,
                channel_published_at, uploads_playlist_id, data_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(values[column] for column in columns) + (data_digest,),
        )
        return connection.total_changes - before


def insert_youtubeapi_video_stats(records, database_path=None):
    records = list(records)
    if not records:
        return 0
    initialize_database(database_path)
    rows = []
    for record in records:
        values = {
            "collected_at": record.get("collected_at"),
            "video_id": record["video_id"],
            "channel_id": record["channel_id"],
            "title": record.get("title"),
            "published_at": record.get("published_at"),
            "duration_seconds": record.get("duration_seconds"),
            "category_id": record.get("category_id"),
            "views": record.get("views"),
            "likes": record.get("likes"),
            "comments": record.get("comments"),
        }
        digest_values = dict(values)
        if digest_values.get("collected_at"):
            digest_values["collected_at"] = digest_values["collected_at"][:10]
        rows.append(
            (
                values["collected_at"],
                values["video_id"],
                values["channel_id"],
                values["title"],
                values["published_at"],
                values["duration_seconds"],
                values["category_id"],
                values["views"],
                values["likes"],
                values["comments"],
                _digest(digest_values),
            )
        )
    with _connection(database_path) as connection:
        before = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO bronze_youtubeapi_video_stats (
                collected_at, video_id, channel_id, title, published_at,
                duration_seconds, category_id, views, likes, comments, data_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return connection.total_changes - before


def insert_vidiq_channel_profile(record, database_path=None):
    return _insert_metric(
        "bronze_vidiq_channel_profiles",
        record,
        (
            "channel_id", "channel_name", "joined_at", "location", "category",
            "videos_total", "subscribers_total", "views_total",
            "estimated_monthly_earnings", "content_period", "long_form_uploads",
            "shorts_uploads", "long_form_views", "shorts_views",
            "ranking_30_day_country", "ranking_30_day_worldwide",
        ),
        database_path,
    )


def insert_socialblade_channel_stats(records, database_path=None):
    return sum(
        _insert_metric(
            "bronze_socialblade_channel_stats",
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
