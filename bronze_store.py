"""SQLite persistence for raw collector output.

Bronze tables retain source values without applying business transformations.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent / "data" / "skimmer.db"


def database_path(database_path=None):
    """Return the configured SQLite database path, creating its parent directory."""
    path = Path(
        database_path
        or os.environ.get("SKIMMER_DB_PATH")
        or DEFAULT_DATABASE_PATH
    ).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _ingested_at():
    return datetime.now(timezone.utc).isoformat()


def _connection(db_path=None):
    connection = sqlite3.connect(db_path or database_path())
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path=None):
    """Create the local raw-data tables if they do not already exist."""
    with _connection(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS bronze_youtube_skimmed (
                id INTEGER PRIMARY KEY,
                create_dt TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                source_file TEXT NOT NULL,
                video_name TEXT,
                channel_display_name TEXT,
                views TEXT,
                age TEXT,
                channel_id TEXT,
                raw_record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bronze_youtube_skimmed_channel_id
                ON bronze_youtube_skimmed(channel_id);

            CREATE TABLE IF NOT EXISTS bronze_vidiq_channel_stats (
                id INTEGER PRIMARY KEY,
                create_dt TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                channel_name TEXT,
                subscribers,
                subscribers_change TEXT,
                views,
                views_change TEXT,
                earnings_low,
                earnings_high,
                engagement TEXT,
                upload_frequency TEXT,
                average_length TEXT,
                channel_id TEXT,
                raw_record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bronze_vidiq_channel_stats_channel_id
                ON bronze_vidiq_channel_stats(channel_id);

            CREATE TABLE IF NOT EXISTS bronze_socialblade_channel_stats (
                id INTEGER PRIMARY KEY,
                create_dt TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                channel_id TEXT,
                subscribers,
                subscribers_change,
                subscribers_change_percentage,
                views_change,
                views_change_percentage,
                raw_record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bronze_socialblade_channel_stats_channel_id
                ON bronze_socialblade_channel_stats(channel_id);

            CREATE TABLE IF NOT EXISTS bronze_socialblade_daily_channel_metrics (
                id INTEGER PRIMARY KEY,
                create_dt TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                metric_date TEXT NOT NULL,
                subscribers_change,
                subscribers_total,
                views_change,
                views_total,
                videos_change,
                videos_total,
                earnings_low,
                earnings_high,
                raw_record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bronze_socialblade_daily_metrics_channel_date
                ON bronze_socialblade_daily_channel_metrics(channel_id, metric_date);
            """
        )
        for table_name in (
            "bronze_youtube_skimmed",
            "bronze_vidiq_channel_stats",
            "bronze_socialblade_channel_stats",
            "bronze_socialblade_daily_channel_metrics",
        ):
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table_name})")
            }
            if "create_dt" not in columns:
                connection.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN create_dt TEXT"
                )
                connection.execute(
                    f"""
                    UPDATE {table_name}
                    SET create_dt = ingested_at
                    WHERE create_dt IS NULL
                    """
                )


def get_unprocessed_youtube_channel_ids(destination_table, database_path=None):
    """Return YouTube channel IDs that have no snapshot in a destination bronze table."""
    destination_tables = {
        "bronze_vidiq_channel_stats",
        "bronze_socialblade_channel_stats",
    }
    if destination_table not in destination_tables:
        raise ValueError(f"Unsupported destination table: {destination_table}")

    initialize_database(database_path)
    with _connection(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT youtube.channel_id
            FROM bronze_youtube_skimmed AS youtube
            WHERE youtube.channel_id IS NOT NULL
              AND TRIM(youtube.channel_id) <> ''
              AND NOT EXISTS (
                  SELECT 1
                  FROM {destination_table} AS destination
                  WHERE LOWER(TRIM(destination.channel_id))
                      = LOWER(TRIM(youtube.channel_id))
              )
            ORDER BY youtube.channel_id
            """
        ).fetchall()
    return [row[0] for row in rows]


def insert_youtube_skimmed(records, source_file, database_path=None):
    """Store YouTube feed rows in the bronze_youtube_skimmed table."""
    records = list(records)
    if not records:
        return 0

    create_dt = _ingested_at()
    rows = [
        (
            create_dt,
            create_dt,
            source_file,
            record["video_name"],
            record["channel_display_name"],
            record["views"],
            record["age"],
            record["channel_id"],
            json.dumps(record, ensure_ascii=False),
        )
        for record in records
    ]
    initialize_database(database_path)
    with _connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO bronze_youtube_skimmed (
                create_dt, ingested_at, source_file, video_name,
                channel_display_name, views, age, channel_id, raw_record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def insert_vidiq_channel_stats(record, database_path=None):
    """Store one vidIQ channel-statistics snapshot without normalizing it."""
    columns = (
        "channel_name",
        "subscribers",
        "subscribers_change",
        "views",
        "views_change",
        "earnings_low",
        "earnings_high",
        "engagement",
        "upload_frequency",
        "average_length",
        "channel_id",
    )
    create_dt = _ingested_at()
    initialize_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO bronze_vidiq_channel_stats (
                create_dt, ingested_at, channel_name, subscribers,
                subscribers_change, views, views_change, earnings_low, earnings_high,
                engagement, upload_frequency, average_length, channel_id,
                raw_record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                create_dt,
                create_dt,
                *(record[column] for column in columns),
                json.dumps(record, ensure_ascii=False),
            ),
        )


def insert_socialblade_channel_stats(record, database_path=None):
    """Store one Social Blade channel-statistics snapshot without normalizing it."""
    columns = (
        "channel_id",
        "subscribers",
        "subscribers_change",
        "subscribers_change_percentage",
        "views_change",
        "views_change_percentage",
    )
    create_dt = _ingested_at()
    initialize_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO bronze_socialblade_channel_stats (
                create_dt, ingested_at, channel_id, subscribers,
                subscribers_change, subscribers_change_percentage, views_change,
                views_change_percentage, raw_record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                create_dt,
                create_dt,
                *(record[column] for column in columns),
                json.dumps(record, ensure_ascii=False),
            ),
        )


def insert_socialblade_daily_channel_metrics(records, database_path=None):
    """Store rendered Social Blade daily channel metrics as raw bronze rows."""
    records = list(records)
    if not records:
        return 0

    create_dt = _ingested_at()
    columns = (
        "channel_id",
        "metric_date",
        "subscribers_change",
        "subscribers_total",
        "views_change",
        "views_total",
        "videos_change",
        "videos_total",
        "earnings_low",
        "earnings_high",
    )
    rows = [
        (
            create_dt,
            create_dt,
            *(record[column] for column in columns),
            json.dumps(record, ensure_ascii=False),
        )
        for record in records
    ]
    initialize_database(database_path)
    with _connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO bronze_socialblade_daily_channel_metrics (
                create_dt, ingested_at, channel_id, metric_date,
                subscribers_change, subscribers_total, views_change, views_total,
                videos_change, videos_total, earnings_low, earnings_high,
                raw_record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)
