import sqlite3
import tempfile
import unittest
from pathlib import Path

from skimmer.collectors.youtube_recommendations import (
    DiscoverySeed,
    collect_recommendation_recovery,
    extract_recommended_records,
)
from skimmer.storage.bronze import (
    insert_youtube_skimmed,
    refresh_profile_queue,
)


class YouTubeRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "skimmer.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_extract_recommended_records_returns_driver_records(self):
        expected = [{"channel_id": "@new", "video_id": "video-2"}]

        class Driver:
            def execute_script(self, script):
                self.script = script
                return expected

        driver = Driver()
        self.assertEqual(extract_recommended_records(driver), expected)
        self.assertIn("ytd-compact-video-renderer", driver.script)

    def test_recovery_records_seed_yield_and_source_provenance(self):
        insert_youtube_skimmed(
            [{"channel_id": "@known", "video_name": "Known", "age": "1 hour ago"}],
            "youtube.com",
            self.database_path,
        )
        refresh_profile_queue(self.database_path)
        seed = DiscoverySeed(
            channel_id="UCseed",
            channel_name="Seed",
            subscribers=1_000,
            channel_views=100_000,
            video_id="seed-video",
            title="Seed video",
            published_at="2026-07-28T00:00:00+00:00",
            video_views=50_000,
            score=50.0,
        )

        class Selector:
            name = "high_views_per_subscriber"

            def select(self, database_path):
                return [seed]

        class Surface:
            def collect(self, driver, selected_seed):
                self.selected_seed = selected_seed
                return [
                    {"channel_id": "@known", "video_name": "Known"},
                    {"channel_id": "@new", "video_name": "New"},
                    {"channel_id": "@new", "video_name": "New duplicate"},
                ]

        records = collect_recommendation_recovery(
            object(),
            Selector(),
            Surface(),
            self.database_path,
        )

        self.assertEqual(len(records), 3)
        self.assertEqual(
            {record["source_file"] for record in records},
            {"https://www.youtube.com/watch?v=seed-video"},
        )
        with sqlite3.connect(self.database_path) as connection:
            history = connection.execute(
                """
                SELECT selector, seed_video_id, seed_channel_id,
                       score, discovered_channels
                FROM discovery_seed_history
                """
            ).fetchone()
        self.assertEqual(
            history,
            ("high_views_per_subscriber", "seed-video", "UCseed", 50.0, 1),
        )


if __name__ == "__main__":
    unittest.main()
