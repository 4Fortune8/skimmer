import json
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bronze_store import (
    get_unprocessed_youtube_channel_ids,
    initialize_database,
    insert_socialblade_daily_channel_metrics,
    insert_socialblade_channel_stats,
    insert_vidiq_channel_stats,
    insert_youtube_skimmed,
)

SOCIALBLADE_MODULE_SPEC = importlib.util.spec_from_file_location(
    "socialblade_collector",
    Path(__file__).parents[1] / "buildIDProfile-old.py",
)
socialblade_collector = importlib.util.module_from_spec(SOCIALBLADE_MODULE_SPEC)
SOCIALBLADE_MODULE_SPEC.loader.exec_module(socialblade_collector)


class BronzeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "skimmer.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_collectors_store_raw_snapshots_in_bronze_tables(self):
        youtube_record = {
            "video_name": "A video",
            "channel_display_name": "A channel",
            "views": "1K views",
            "age": "1 hour ago",
            "channel_id": "@channel",
        }
        initialize_database(self.database_path)
        inserted = insert_youtube_skimmed(
            [youtube_record],
            "youtube.com",
            self.database_path,
        )
        insert_vidiq_channel_stats(
            {
                "channel_name": "A channel",
                "subscribers": 1000,
                "subscribers_change": "+10",
                "views": "2K",
                "views_change": "+20",
                "earnings_low": 1,
                "earnings_high": 2,
                "engagement": "5%",
                "upload_frequency": "weekly",
                "average_length": "10 minutes",
                "channel_id": "channel",
            },
            self.database_path,
        )
        insert_socialblade_channel_stats(
            {
                "channel_id": "channel",
                "subscribers": 1000,
                "subscribers_change": 10,
                "subscribers_change_percentage": "1%",
                "views_change": 2000,
                "views_change_percentage": "2%",
                "raw_profile": ["channel", 1000, 10, "1%", 2000, "2%"],
            },
            self.database_path,
        )

        with sqlite3.connect(self.database_path) as connection:
            saved_youtube = connection.execute(
                "SELECT source_file, create_dt, raw_record_json "
                "FROM bronze_youtube_skimmed"
            ).fetchone()
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM bronze_youtube_skimmed),
                    (SELECT COUNT(*) FROM bronze_vidiq_channel_stats),
                    (SELECT COUNT(*) FROM bronze_socialblade_channel_stats)
                """
            ).fetchone()
            create_dt_counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM bronze_youtube_skimmed WHERE create_dt IS NOT NULL),
                    (SELECT COUNT(*) FROM bronze_vidiq_channel_stats WHERE create_dt IS NOT NULL),
                    (SELECT COUNT(*) FROM bronze_socialblade_channel_stats WHERE create_dt IS NOT NULL)
                """
            ).fetchone()

        self.assertEqual(inserted, 1)
        self.assertEqual(saved_youtube[0], "youtube.com")
        self.assertTrue(saved_youtube[1])
        self.assertEqual(json.loads(saved_youtube[2]), youtube_record)
        self.assertEqual(counts, (1, 1, 1))
        self.assertEqual(create_dt_counts, (1, 1, 1))

    def test_unprocessed_channels_are_returned_for_each_destination(self):
        insert_youtube_skimmed(
            [
                {
                    "video_name": "A video",
                    "channel_display_name": "A channel",
                    "views": "1K views",
                    "age": "1 hour ago",
                    "channel_id": "@channel",
                }
            ],
            "youtube.com",
            self.database_path,
        )

        self.assertEqual(
            get_unprocessed_youtube_channel_ids(
                "bronze_vidiq_channel_stats", self.database_path
            ),
            ["@channel"],
        )
        with self.assertRaises(ValueError):
            get_unprocessed_youtube_channel_ids(
                "not_a_bronze_table", self.database_path
            )

    def test_existing_bronze_tables_receive_create_dt(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE bronze_youtube_skimmed (
                    id INTEGER PRIMARY KEY,
                    ingested_at TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    video_name TEXT,
                    channel_display_name TEXT,
                    views TEXT,
                    age TEXT,
                    channel_id TEXT,
                    raw_record_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO bronze_youtube_skimmed (
                    ingested_at, source_file, raw_record_json
                ) VALUES ('2026-07-15T00:00:00+00:00', 'youtube.com', '{}')
                """
            )

        initialize_database(self.database_path)

        with sqlite3.connect(self.database_path) as connection:
            create_dt = connection.execute(
                "SELECT create_dt FROM bronze_youtube_skimmed"
            ).fetchone()[0]

        self.assertEqual(create_dt, "2026-07-15T00:00:00+00:00")

    def test_socialblade_routes_and_only_accepts_rendered_change_values(self):
        self.assertEqual(
            socialblade_collector.socialblade_url("UClgRkhTL3_hImCAmdLfDE4g"),
            "https://socialblade.com/youtube/channel/UClgRkhTL3_hImCAmdLfDE4g",
        )
        self.assertEqual(
            socialblade_collector.socialblade_url("@mrbeast"),
            "https://socialblade.com/youtube/handle/mrbeast",
        )
        self.assertIsNone(
            socialblade_collector.value_before_label(
                ["Subscribers for the last 30 days"],
                "Subscribers for the last 30 days",
            )
        )
        self.assertEqual(
            socialblade_collector.value_before_label(
                ["42K", "Subscribers for the last 30 days"],
                "Subscribers for the last 30 days",
            ),
            "42K",
        )

    def test_socialblade_daily_metrics_retain_all_source_columns(self):
        inserted = insert_socialblade_daily_channel_metrics(
            [
                {
                    "channel_id": "UClgRkhTL3_hImCAmdLfDE4g",
                    "metric_date": "2026-07-15",
                    "subscribers_change": None,
                    "subscribers_total": 141_000_000,
                    "views_change": None,
                    "views_total": 31_566_836_666,
                    "videos_change": None,
                    "videos_total": 459,
                    "earnings_low": 0,
                    "earnings_high": 0,
                    "raw_row": ["Wed2026-07-15", "--", "141M"],
                }
            ],
            self.database_path,
        )

        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT metric_date, subscribers_total, views_total, videos_total,
                       earnings_low, earnings_high, create_dt
                FROM bronze_socialblade_daily_channel_metrics
                """
            ).fetchone()

        self.assertEqual(inserted, 1)
        self.assertEqual(
            row[:6], ("2026-07-15", 141_000_000, 31_566_836_666, 459, 0, 0)
        )
        self.assertTrue(row[6])


if __name__ == "__main__":
    unittest.main()
