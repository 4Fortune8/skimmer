import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bronze_store import (
    get_profile_queue,
    initialize_database,
    insert_socialblade_channel_stats,
    insert_socialblade_daily_channel_metrics,
    insert_vidiq_channel_stats,
    insert_youtube_skimmed,
    mark_profile_failed,
    mark_profile_succeeded,
    refresh_profile_queue,
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

    def test_metric_records_are_deduplicated_without_raw_or_ingestion_columns(self):
        vidiq_record = {
            "channel_id": "@channel",
            "channel_name": "A channel",
            "subscribers": 1000,
            "subscribers_change": None,
            "views": 2000,
            "views_change": None,
            "earnings_low": 1,
            "earnings_high": 2,
            "engagement": None,
            "upload_frequency": None,
            "average_length": None,
        }
        self.assertEqual(insert_vidiq_channel_stats(vidiq_record, self.database_path), 1)
        self.assertEqual(insert_vidiq_channel_stats(vidiq_record, self.database_path), 0)

        daily_record = {
            "channel_id": "@channel",
            "metric_date": "2026-07-15",
            "subscribers_change": 10,
            "subscribers_total": 1000,
            "views_change": 20,
            "views_total": 2000,
            "videos_change": 1,
            "videos_total": 10,
            "earnings_low": 1,
            "earnings_high": 2,
        }
        self.assertEqual(
            insert_socialblade_daily_channel_metrics([daily_record], self.database_path),
            1,
        )
        self.assertEqual(
            insert_socialblade_daily_channel_metrics([daily_record], self.database_path),
            0,
        )

        with sqlite3.connect(self.database_path) as connection:
            vidiq_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(bronze_vidiq_channel_stats)")
            }
            daily_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(bronze_socialblade_daily_channel_metrics)"
                )
            }
        self.assertNotIn("create_dt", vidiq_columns)
        self.assertNotIn("ingested_at", vidiq_columns)
        self.assertNotIn("raw_record_json", vidiq_columns)
        self.assertNotIn("metric_date", daily_columns)
        self.assertNotIn("raw_record_json", daily_columns)

    def test_queue_refreshes_and_fails_over_once(self):
        insert_youtube_skimmed(
            [{
                "video_name": "New video",
                "channel_display_name": "A channel",
                "views": "1K views",
                "age": "1 hour ago",
                "channel_id": "@channel",
            }],
            "youtube.com",
            self.database_path,
        )
        refresh_profile_queue(self.database_path)
        initial_source = (
            "vidiq"
            if get_profile_queue("vidiq", database_path=self.database_path)
            else "socialblade"
        )
        other_source = "socialblade" if initial_source == "vidiq" else "vidiq"

        mark_profile_failed("@channel", initial_source, self.database_path)
        self.assertEqual(
            get_profile_queue(other_source, database_path=self.database_path), ["@channel"]
        )
        mark_profile_failed("@channel", other_source, self.database_path)
        self.assertEqual(get_profile_queue("vidiq", database_path=self.database_path), [])
        self.assertEqual(
            get_profile_queue("socialblade", database_path=self.database_path), []
        )

    def test_success_is_requeued_after_seven_days_or_recent_video(self):
        insert_youtube_skimmed(
            [{
                "video_name": "Old video",
                "channel_display_name": "A channel",
                "views": "1K views",
                "age": "20 days ago",
                "channel_id": "@channel",
            }],
            "youtube.com",
            self.database_path,
        )
        refresh_profile_queue(self.database_path)
        source = (
            "vidiq"
            if get_profile_queue("vidiq", database_path=self.database_path)
            else "socialblade"
        )
        mark_profile_succeeded("@channel", source, self.database_path)
        self.assertEqual(get_profile_queue(source, database_path=self.database_path), [])

        stale = (datetime.now(timezone.utc) - timedelta(days=8)).replace(
            microsecond=0
        ).isoformat()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE profile_queue SET last_success_at = ? WHERE channel_key = '@channel'",
                (stale,),
            )
        refresh_profile_queue(self.database_path)
        self.assertEqual(
            get_profile_queue(source, database_path=self.database_path), ["@channel"]
        )

    def test_feed_records_older_than_fourteen_days_are_pruned(self):
        insert_youtube_skimmed(
            [{
                "video_name": "Old video",
                "channel_display_name": "A channel",
                "views": "1K views",
                "age": "20 days ago",
                "channel_id": "@channel",
            }],
            "youtube.com",
            self.database_path,
        )
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=15)).replace(
            microsecond=0
        ).isoformat()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE bronze_youtube_skimmed SET observed_at = ?", (old_timestamp,)
            )
        refresh_profile_queue(self.database_path)
        with sqlite3.connect(self.database_path) as connection:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM bronze_youtube_skimmed"
            ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_socialblade_routes_handles(self):
        self.assertEqual(
            socialblade_collector.socialblade_url("UClgRkhTL3_hImCAmdLfDE4g"),
            "https://socialblade.com/youtube/channel/UClgRkhTL3_hImCAmdLfDE4g",
        )
        self.assertEqual(
            socialblade_collector.socialblade_url("@mrbeast"),
            "https://socialblade.com/youtube/handle/mrbeast",
        )


if __name__ == "__main__":
    unittest.main()
