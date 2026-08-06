import sqlite3
import tempfile
import unittest
from pathlib import Path

from skimmer.collectors.youtube_recommendations import (
    DiscoverySeed,
    HighViewsPerSubscriberSelector,
    collect_recommendation_recovery,
    extract_recommended_records,
)
from skimmer.domain import exclusions
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


class SeedExclusionTests(unittest.TestCase):
    """Seed selection must refuse topics the operator has already ruled out."""

    def setUp(self):
        self.queries = []

    @staticmethod
    def _record(video_id, title, score=10.0):
        return {
            "channel_id": f"UC{video_id}",
            "channel_name": "Channel",
            "subscribers": 1_000,
            "channel_views": 100_000,
            "video_id": video_id,
            "title": title,
            "published_at": "2026-07-28T00:00:00+00:00",
            "video_views": 50_000,
            "score": score,
        }

    def _patched_select(self, selector, rows, database_path=None):
        import skimmer.collectors.youtube_recommendations as module

        original = module.select_high_views_per_subscriber_seeds

        def fake_query(limit=None, **_ignored):
            self.queries.append(limit)
            return rows[:limit]

        module.select_high_views_per_subscriber_seeds = fake_query
        try:
            return selector.select(database_path)
        finally:
            module.select_high_views_per_subscriber_seeds = original

    def test_seeds_matching_a_rule_are_skipped(self):
        rows = [
            self._record("v1", "FIFA 26 gameplay reveal"),
            self._record("v2", "How I built a shed"),
            self._record("v3", "DUNE | Official Trailer"),
            self._record("v4", "Second normal video"),
        ]
        selector = HighViewsPerSubscriberSelector(
            limit=2,
            exclusion_rules=[
                exclusions.make_rule("fifa"),
                exclusions.make_rule("official trailer"),
            ],
        )

        seeds = self._patched_select(selector, rows)

        self.assertEqual([seed.video_id for seed in seeds], ["v2", "v4"])

    def test_query_is_over_fetched_so_filtering_still_fills_the_limit(self):
        rows = [self._record(f"v{index}", "FIFA highlights") for index in range(20)]
        rows[10] = self._record("keeper", "A normal video")
        selector = HighViewsPerSubscriberSelector(
            limit=1,
            exclusion_rules=[exclusions.make_rule("fifa")],
            exclusion_over_fetch=15,
        )

        seeds = self._patched_select(selector, rows)

        self.assertEqual(self.queries, [15])
        self.assertEqual([seed.video_id for seed in seeds], ["keeper"])

    def test_without_rules_the_query_is_not_over_fetched(self):
        rows = [self._record(f"v{index}", "A normal video") for index in range(5)]
        selector = HighViewsPerSubscriberSelector(limit=3)

        seeds = self._patched_select(selector, rows)

        self.assertEqual(self.queries, [3])
        self.assertEqual(len(seeds), 3)

    def test_both_scopes_are_applied_during_collection(self):
        """Either scope means "no more of this"; only the analysis cares which."""

        rows = [
            self._record("v1", "FIFA 26 gameplay reveal"),
            self._record("v2", "God Says: A God Message For You"),
            self._record("v3", "A normal video"),
        ]
        selector = HighViewsPerSubscriberSelector(
            limit=3,
            exclusion_rules=[
                exclusions.make_rule("fifa", scope="leads"),
                exclusions.make_rule("god message", scope="corpus"),
            ],
        )

        seeds = self._patched_select(selector, rows)

        self.assertEqual([seed.video_id for seed in seeds], ["v3"])

    def test_partial_titles_and_missing_titles_are_kept(self):
        rows = [self._record("v1", None), self._record("v2", "")]
        selector = HighViewsPerSubscriberSelector(
            limit=2, exclusion_rules=[exclusions.make_rule("fifa")]
        )

        seeds = self._patched_select(selector, rows)

        self.assertEqual([seed.video_id for seed in seeds], ["v1", "v2"])

    def test_over_fetch_must_be_positive(self):
        with self.assertRaises(ValueError):
            HighViewsPerSubscriberSelector(exclusion_over_fetch=0)


if __name__ == "__main__":
    unittest.main()
