import os
import tempfile
import unittest

from skimmer.domain.interest_pools import (
    SEED,
    WARM,
    channel_pool,
    partition_by_pool,
)
from skimmer.domain.liveness import filter_dead, resolve_liveness
from skimmer.domain.normalization import normalize_title
from skimmer.storage.bronze import (
    get_video_liveness,
    initialize_database,
    upsert_video_liveness,
)


class NormalizeTitleTests(unittest.TestCase):
    def test_reposts_differing_only_in_punctuation_collapse(self):
        a = "This 99 Cent Food Mimics Ozempic — Here's How (And How to Use It)"
        b = "This 99 Cent Food Mimics Ozempic! Here's How"
        self.assertEqual(normalize_title(a), normalize_title(b))

    def test_emoji_and_hashtag_runs_are_stripped(self):
        self.assertEqual(
            normalize_title("Willpower is Finite 🤯 #motivation #mindset"),
            normalize_title("Willpower is Finite"),
        )

    def test_trailing_attribution_is_dropped(self):
        self.assertEqual(
            normalize_title("The Psychology of Respect | Chase Hughes"),
            normalize_title("The Psychology of Respect"),
        )

    def test_distinct_titles_stay_distinct(self):
        self.assertNotEqual(
            normalize_title("Do statins actually work?"),
            normalize_title("Do supplements actually work?"),
        )

    def test_empty_input_is_safe(self):
        self.assertEqual(normalize_title(None), "")
        self.assertEqual(normalize_title(""), "")


class ChannelPoolTests(unittest.TestCase):
    def test_assignment_is_stable_across_calls(self):
        first = [channel_pool(f"UC{i}") for i in range(50)]
        second = [channel_pool(f"UC{i}") for i in range(50)]
        self.assertEqual(first, second)

    def test_pools_are_disjoint(self):
        channels = [f"UC{i}" for i in range(400)]
        pools = partition_by_pool([{"channel_id": c} for c in channels])
        warm = {v["channel_id"] for v in pools[WARM]}
        seed = {v["channel_id"] for v in pools[SEED]}
        self.assertEqual(warm & seed, set())
        self.assertEqual(warm | seed, set(channels))

    def test_share_is_approximately_honoured(self):
        channels = [{"channel_id": f"UC{i}"} for i in range(2000)]
        warm = partition_by_pool(channels, warm_share=0.35)[WARM]
        self.assertAlmostEqual(len(warm) / 2000, 0.35, delta=0.05)

    def test_missing_channel_defaults_to_seed(self):
        self.assertEqual(channel_pool(None), SEED)


class LivenessTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.database_path)
        initialize_database(self.database_path)
        self.checked = []

    def tearDown(self):
        if os.path.exists(self.database_path):
            os.unlink(self.database_path)

    def _checker(self, verdicts):
        def check(video_ids):
            self.checked.extend(video_ids)
            return {v: verdicts.get(v) for v in video_ids}

        return check

    def test_verdicts_are_persisted_and_reused(self):
        resolve_liveness(
            [{"video_id": "a"}],
            checker=self._checker({"a": True}),
            database_path=self.database_path,
        )
        self.checked.clear()
        again = resolve_liveness(
            [{"video_id": "a"}],
            checker=self._checker({"a": True}),
            database_path=self.database_path,
        )
        self.assertEqual(again, {"a": True})
        self.assertEqual(self.checked, [])

    def test_dead_videos_are_dropped(self):
        kept = filter_dead(
            [{"video_id": "a"}, {"video_id": "b"}],
            checker=self._checker({"a": True, "b": False}),
            database_path=self.database_path,
        )
        self.assertEqual([v["video_id"] for v in kept], ["a"])

    def test_unknown_fails_open(self):
        kept = filter_dead(
            [{"video_id": "a"}],
            checker=self._checker({"a": None}),
            database_path=self.database_path,
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(
            get_video_liveness(["a"], database_path=self.database_path), {}
        )

    def test_stale_positive_is_rechecked_but_dead_is_permanent(self):
        upsert_video_liveness(
            [
                {"video_id": "live", "is_live": True},
                {"video_id": "gone", "is_live": False},
            ],
            database_path=self.database_path,
        )
        # max_age_days=0 makes every positive stale immediately.
        known = get_video_liveness(
            ["live", "gone"], max_age_days=0, database_path=self.database_path
        )
        self.assertNotIn("live", known, "stale positive should be re-checked")
        self.assertIn("gone", known, "a deleted video does not come back")

    def test_storing_unknown_is_refused(self):
        with self.assertRaises(ValueError):
            upsert_video_liveness(
                [{"video_id": "x", "is_live": None}], database_path=self.database_path
            )


if __name__ == "__main__":
    unittest.main()
