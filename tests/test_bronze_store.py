import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from skimmer.storage.bronze import (
    claim_youtube_api_batch,
    get_profile_queue,
    get_youtube_api_quota_usage,
    claim_profile_batch,
    claim_channel_id_resolution_batch,
    initialize_database,
    insert_socialblade_channel_stats,
    insert_youtubeapi_channel_stats,
    insert_youtubeapi_video_stats,
    insert_vidiq_channel_profile,
    insert_vidiq_channel_stats,
    insert_youtube_skimmed,
    mark_profile_failed,
    mark_profile_succeeded,
    mark_youtube_api_failed,
    mark_youtube_api_succeeded,
    profile_identifier_candidates,
    record_collection_attempt,
    record_collection_error,
    record_youtube_api_quota_usage,
    release_channel_id_resolution_batch,
    release_profile_batch,
    release_youtube_api_batch,
    reserve_youtube_api_quota_unit,
    refresh_profile_queue,
    store_youtube_channel_id,
)


from skimmer.collectors import socialblade as socialblade_collector
from skimmer.collectors import vidiq as vidiq_collector


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
            insert_socialblade_channel_stats([daily_record], self.database_path),
            1,
        )
        self.assertEqual(
            insert_socialblade_channel_stats([daily_record], self.database_path),
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
                    "PRAGMA table_info(bronze_socialblade_channel_stats)"
                )
            }
        self.assertNotIn("create_dt", vidiq_columns)
        self.assertNotIn("ingested_at", vidiq_columns)
        self.assertNotIn("raw_record_json", vidiq_columns)
        self.assertNotIn("metric_date", daily_columns)
        self.assertNotIn("raw_record_json", daily_columns)

    def test_profile_detail_records_are_deduplicated(self):
        vidiq_profile = {
            "channel_id": "@channel",
            "channel_name": "A channel",
            "joined_at": "Oct 10, 2013",
            "location": "United Kingdom",
            "category": "Gaming",
            "videos_total": 785,
            "subscribers_total": 4_470_000,
            "views_total": 1_110_000_000,
            "estimated_monthly_earnings": 38_000,
            "content_period": "Since Jan 01, 2026",
            "long_form_uploads": 29,
            "shorts_uploads": 6,
            "long_form_views": 35_340_000,
            "shorts_views": 3_310_000,
            "ranking_30_day_country": "GB#253",
            "ranking_30_day_worldwide": "Worldwide#10,448",
        }
        self.assertEqual(insert_vidiq_channel_profile(vidiq_profile, self.database_path), 1)
        self.assertEqual(insert_vidiq_channel_profile(vidiq_profile, self.database_path), 0)

        with sqlite3.connect(self.database_path) as connection:
            vidiq_row = connection.execute(
                """
                SELECT joined_at, long_form_uploads, shorts_views,
                       ranking_30_day_country, ranking_30_day_worldwide
                FROM bronze_vidiq_channel_profiles
                """
            ).fetchone()
        self.assertEqual(
            vidiq_row,
            ("Oct 10, 2013", 29, 3_310_000, "GB#253", "Worldwide#10,448"),
        )

    def test_vidiq_content_mix_and_rankings_are_parsed(self):
        lines = [
            "Long-form vs Shorts",
            "Since Jan 01, 2026",
            "Uploads",
            "Long-form",
            "29",
            "Shorts",
            "6",
            "83%17%",
            "Views",
            "Long-form",
            "35.34M",
            "Shorts",
            "3.31M",
            "Subscribers",
            "4.47M",
            "Total Video Views",
            "1.11B",
            "Ranking (30 days)",
            "GB#253",
            "Worldwide#10,448",
        ]

        self.assertEqual(
            vidiq_collector.long_form_vs_shorts_metrics(lines),
            {
                "content_period": "Since Jan 01, 2026",
                "long_form_uploads": 29,
                "shorts_uploads": 6,
                "long_form_views": 35_340_000,
                "shorts_views": 3_310_000,
            },
        )
        self.assertEqual(
            vidiq_collector.vidiq_rankings(lines),
            ("GB#253", "Worldwide#10,448"),
        )

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
        mark_youtube_api_failed("@channel", self.database_path)
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

    def test_scraper_success_is_requeued_after_fourteen_days(self):
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
        mark_youtube_api_failed("@channel", self.database_path)
        source = (
        "vidiq"
        if get_profile_queue("vidiq", database_path=self.database_path)
        else "socialblade"
        )
        mark_profile_succeeded("@channel", source, self.database_path)
        self.assertEqual(get_profile_queue(source, database_path=self.database_path), [])

        stale = (datetime.now(timezone.utc) - timedelta(days=15)).replace(
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

    def test_socialblade_daily_table_migrates_to_channel_stats(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE bronze_socialblade_daily_channel_metrics (
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
                )
                """
            )
            connection.execute(
                """
                INSERT INTO bronze_socialblade_daily_channel_metrics (
                    channel_id, subscribers_total, data_digest
                ) VALUES ('@channel', 1000, 'digest')
                """
            )

        initialize_database(self.database_path)

        with sqlite3.connect(self.database_path) as connection:
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            row_count = connection.execute(
                "SELECT COUNT(*) FROM bronze_socialblade_channel_stats"
            ).fetchone()[0]
        self.assertIn("bronze_socialblade_channel_stats", table_names)
        self.assertNotIn("bronze_socialblade_daily_channel_metrics", table_names)
        self.assertEqual(row_count, 1)

    def test_profile_queue_migrates_youtube_api_columns(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE profile_queue (
                    channel_key TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    channel_name TEXT,
                    latest_video_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_success_at TEXT,
                    digested INTEGER NOT NULL DEFAULT 0,
                    assigned_source TEXT NOT NULL,
                    vidiq_failed INTEGER NOT NULL DEFAULT 0,
                    socialblade_failed INTEGER NOT NULL DEFAULT 0,
                    needs_review INTEGER NOT NULL DEFAULT 0,
                    claimed_by TEXT,
                    claimed_at TEXT,
                    youtube_channel_id TEXT,
                    channel_id_claimed_by TEXT,
                    channel_id_claimed_at TEXT,
                    youtube_channel_id_attempted_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO profile_queue (
                    channel_key, channel_id, latest_video_at, last_seen_at, assigned_source
                ) VALUES ('@channel', '@channel', '2026-07-22T00:00:00+00:00',
                          '2026-07-22T00:00:00+00:00', 'vidiq')
                """
            )

        initialize_database(self.database_path)

        with sqlite3.connect(self.database_path) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(profile_queue)")
            }
            row = connection.execute(
                """
                SELECT youtube_api_failed, youtube_api_attempted_at, youtube_api_success_at
                FROM profile_queue
                WHERE channel_key = '@channel'
                """
            ).fetchone()
        self.assertIn("youtube_api_failed", columns)
        self.assertIn("youtube_api_attempted_at", columns)
        self.assertIn("youtube_api_success_at", columns)
        self.assertEqual(row, (0, None, None))

    def test_socialblade_detects_cloudflare_block(self):
        class FakeElement:
            text = "Attention Required! Cloudflare"

        class FakeDriver:
            title = "Attention Required! | Cloudflare"

            def find_element(self, *_):
                return FakeElement()

        self.assertTrue(socialblade_collector.is_cloudflare_blocked(FakeDriver()))

    def test_profile_batches_are_exclusive_and_releasable(self):
        for index in range(3):
            insert_youtube_skimmed(
                [{
                    "video_name": f"Video {index}",
                    "channel_display_name": f"Channel {index}",
                    "views": "1K views",
                    "age": "1 hour ago",
                    "channel_id": f"@channel{index}",
                    "youtube_channel_id": f"UC{'a' * 20}{index}",
                }],
                "youtube.com",
                self.database_path,
            )
        refresh_profile_queue(self.database_path)
        for index in range(3):
            mark_youtube_api_failed(f"@channel{index}", self.database_path)
        source = (
            "vidiq"
            if get_profile_queue("vidiq", database_path=self.database_path)
            else "socialblade"
        )
        claimed = claim_profile_batch(source, "worker-one", 100, self.database_path)
        self.assertGreaterEqual(len(claimed), 1)
        self.assertEqual(
            claim_profile_batch(source, "worker-two", 100, self.database_path), []
        )
        release_profile_batch(source, "worker-one", self.database_path)
        self.assertEqual(
            claim_profile_batch(source, "worker-two", 100, self.database_path),
            claimed,
        )

    def test_youtube_api_snapshots_are_deduplicated_by_day(self):
        channel_record = {
            "collected_at": "2026-07-22T10:00:00+00:00",
            "channel_id": "UC" + "a" * 22,
            "channel_name": "Channel",
            "subscribers": 1000,
            "subscribers_change": None,
            "views": 2000,
            "views_change": None,
            "video_count": 10,
            "country": "US",
            "channel_published_at": "2020-01-01T00:00:00+00:00",
            "uploads_playlist_id": "UU" + "b" * 22,
        }
        self.assertEqual(insert_youtubeapi_channel_stats(channel_record, self.database_path), 1)
        self.assertEqual(insert_youtubeapi_channel_stats(channel_record, self.database_path), 0)

        video_record = {
            "collected_at": "2026-07-22T10:00:00+00:00",
            "video_id": "vid-1",
            "channel_id": channel_record["channel_id"],
            "title": "Video",
            "published_at": "2026-07-21T00:00:00+00:00",
            "duration_seconds": 123,
            "category_id": "20",
            "views": 100,
            "likes": 10,
            "comments": 5,
        }
        self.assertEqual(
            insert_youtubeapi_video_stats([video_record], self.database_path),
            1,
        )
        self.assertEqual(
            insert_youtubeapi_video_stats([video_record], self.database_path),
            0,
        )

    def test_youtube_api_queue_excludes_successful_channels_from_scrapers(self):
        insert_youtube_skimmed(
            [{
                "video_name": "Video",
                "channel_display_name": "Channel",
                "views": "1K views",
                "age": "1 hour ago",
                "channel_id": "@channel",
                "youtube_channel_id": "UC" + "a" * 22,
            }],
            "youtube.com",
            self.database_path,
        )
        refresh_profile_queue(self.database_path)
        claim = claim_youtube_api_batch("api-worker", 10, self.database_path)
        self.assertTrue(claim)
        mark_youtube_api_succeeded("@channel", self.database_path)
        self.assertEqual(
            claim_profile_batch("vidiq", "vidiq-worker", 10, self.database_path),
            [],
        )
        self.assertEqual(
            claim_profile_batch("socialblade", "socialblade-worker", 10, self.database_path),
            [],
        )
        release_youtube_api_batch("api-worker", self.database_path)

    def test_youtube_api_claims_channels_held_for_scraper_review(self):
        insert_youtube_skimmed(
            [{
                "video_name": "Video",
                "channel_display_name": "Channel",
                "views": "1K views",
                "age": "1 hour ago",
                "channel_id": "@channel",
            }],
            "youtube.com",
            self.database_path,
        )
        refresh_profile_queue(self.database_path)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE profile_queue SET needs_review = 1 WHERE channel_key = '@channel'"
            )

        claimed = claim_youtube_api_batch("api-worker", 10, self.database_path)
        self.assertEqual(claimed, [("@channel", "@channel", None)])
        mark_youtube_api_succeeded("@channel", self.database_path)
        with sqlite3.connect(self.database_path) as connection:
            needs_review = connection.execute(
                "SELECT needs_review FROM profile_queue WHERE channel_key = '@channel'"
            ).fetchone()[0]
        self.assertEqual(needs_review, 0)

    def test_youtube_api_failure_and_quota_tracking(self):
        insert_youtube_skimmed(
            [{
                "video_name": "Video",
                "channel_display_name": "Channel",
                "views": "1K views",
                "age": "1 hour ago",
                "channel_id": "@channel",
            }],
            "youtube.com",
            self.database_path,
        )
        refresh_profile_queue(self.database_path)
        mark_youtube_api_failed("@channel", self.database_path)
        with sqlite3.connect(self.database_path) as connection:
            failed = connection.execute(
                "SELECT youtube_api_failed FROM profile_queue WHERE channel_key = '@channel'"
            ).fetchone()[0]
        self.assertEqual(failed, 1)

        self.assertEqual(get_youtube_api_quota_usage(database_path=self.database_path), 0)
        record_youtube_api_quota_usage(3, database_path=self.database_path)
        self.assertEqual(get_youtube_api_quota_usage(database_path=self.database_path), 3)
        record_youtube_api_quota_usage(2, database_path=self.database_path)
        self.assertEqual(get_youtube_api_quota_usage(database_path=self.database_path), 5)

    def test_youtube_api_quota_reservation_never_exceeds_budget(self):
        self.assertTrue(
            reserve_youtube_api_quota_unit(2, database_path=self.database_path)
        )
        self.assertTrue(
            reserve_youtube_api_quota_unit(2, database_path=self.database_path)
        )
        self.assertFalse(
            reserve_youtube_api_quota_unit(2, database_path=self.database_path)
        )
        self.assertEqual(get_youtube_api_quota_usage(database_path=self.database_path), 2)

    def test_socialblade_waits_for_and_uses_canonical_channel_id(self):
        record = {
            "video_name": "Video",
            "channel_display_name": "Channel",
            "views": "1K views",
            "age": "1 hour ago",
            "channel_id": "@channel",
        }
        insert_youtube_skimmed([record], "youtube.com", self.database_path)
        refresh_profile_queue(self.database_path)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE profile_queue
                SET assigned_source = 'socialblade'
                WHERE channel_key = '@channel'
                """
            )

        self.assertEqual(
            claim_profile_batch("socialblade", "socialblade-worker", 100, self.database_path),
            [],
        )
        claimed = claim_channel_id_resolution_batch(
            "resolver-worker", 100, self.database_path
        )
        self.assertEqual(claimed, [("@channel", "@channel")])
        canonical_id = f"UC{'a' * 22}"
        store_youtube_channel_id("@channel", canonical_id, self.database_path)
        release_channel_id_resolution_batch("resolver-worker", self.database_path)
        mark_youtube_api_failed("@channel", self.database_path)
        self.assertEqual(
            claim_profile_batch("socialblade", "socialblade-worker", 100, self.database_path),
            [canonical_id],
        )

    def test_source_identifier_order_and_attempt_history(self):
        canonical_id = f"UC{'a' * 22}"
        insert_youtube_skimmed(
            [{
                "video_name": "Video",
                "channel_display_name": "Channel",
                "views": "1K views",
                "age": "1 hour ago",
                "channel_id": "@channel",
                "youtube_channel_id": canonical_id,
            }],
            "youtube.com",
            self.database_path,
        )
        refresh_profile_queue(self.database_path)
        self.assertEqual(
            profile_identifier_candidates("vidiq", "@channel", self.database_path),
            [("@channel", "handle"), (canonical_id, "channel_id")],
        )
        self.assertEqual(
            profile_identifier_candidates("socialblade", canonical_id, self.database_path),
            [(canonical_id, "channel_id"), ("@channel", "handle")],
        )
        record_collection_attempt(
            "vidiq",
            "@channel",
            "@channel",
            "handle",
            "https://vidiq.com/youtube-stats/channel/@channel",
            "failed",
            "metrics_unavailable",
            self.database_path,
        )
        with sqlite3.connect(self.database_path) as connection:
            attempt = connection.execute(
                """
                SELECT source, identifier, identifier_kind, outcome, failure_type
                FROM collection_attempts
                """
            ).fetchone()
        self.assertEqual(
            attempt,
            ("vidiq", "@channel", "handle", "failed", "metrics_unavailable"),
        )

    def test_collection_errors_preserve_cloudflare_status(self):
        record_collection_error(
            "socialblade",
            "cloudflare_block",
            "Cloudflare blocked the headed browser session.",
            "@channel",
            "https://socialblade.com/youtube/channel/example",
            403,
            self.database_path,
        )
        with sqlite3.connect(self.database_path) as connection:
            error = connection.execute(
                "SELECT source, error_type, status_code FROM collection_errors"
            ).fetchone()
        self.assertEqual(error, ("socialblade", "cloudflare_block", 403))


if __name__ == "__main__":
    unittest.main()
