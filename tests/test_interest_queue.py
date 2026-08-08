import os
import tempfile
import unittest

from skimmer.storage.bronze import (
    get_interest_queue,
    initialize_database,
    insert_interest_crawl_results,
    interest_crawl_yield,
    mark_interest_queue_status,
    refresh_interest_queue,
)

VERSION = "terms-v1"


def result(seed, video_id, channel_key, topic=None, confidence=None, depth=1, position=0):
    return {
        "seed_video_id": seed,
        "video_id": video_id,
        "channel_id": channel_key,
        "channel_key": channel_key,
        "title": f"title {video_id}",
        "rail_position": position,
        "matched_topic": topic,
        "confidence": confidence,
        "depth": depth,
    }


class InterestQueueTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.database_path)
        initialize_database(self.database_path)

    def tearDown(self):
        if os.path.exists(self.database_path):
            os.unlink(self.database_path)

    def _insert(self, records):
        return insert_interest_crawl_results(
            records, VERSION, database_path=self.database_path
        )

    def test_off_topic_results_are_retained_for_yield(self):
        self._insert(
            [
                result("seedA", "v1", "chan1", topic="health", confidence=3),
                result("seedA", "v2", "chan2"),
                result("seedA", "v3", "chan3"),
                result("seedA", "v4", "chan4"),
            ]
        )
        rates = interest_crawl_yield(VERSION, database_path=self.database_path)
        self.assertEqual(len(rates), 1)
        self.assertEqual(rates[0]["total"], 4)
        self.assertEqual(rates[0]["on_topic"], 1)
        self.assertAlmostEqual(rates[0]["yield_rate"], 0.25)

    def test_only_on_topic_results_enter_the_queue(self):
        self._insert(
            [
                result("seedA", "v1", "chan1", topic="health", confidence=3),
                result("seedA", "v2", "chan2"),
            ]
        )
        refresh_interest_queue(VERSION, database_path=self.database_path)
        queue = get_interest_queue(database_path=self.database_path)
        self.assertEqual([row["channel_key"] for row in queue], ["chan1"])

    def test_seed_count_counts_distinct_seeds_not_rows(self):
        # Same channel reached from three different seeds, plus a duplicate row
        # from one of them: seed_count is 3, hit_count counts distinct videos.
        self._insert(
            [
                result("seedA", "v1", "chan1", topic="health", confidence=3),
                result("seedB", "v2", "chan1", topic="health", confidence=4),
                result("seedC", "v3", "chan1", topic="health", confidence=2),
                result("seedA", "v1", "chan1", topic="health", confidence=3),
            ]
        )
        refresh_interest_queue(VERSION, database_path=self.database_path)
        entry = get_interest_queue(database_path=self.database_path)[0]
        self.assertEqual(entry["seed_count"], 3)
        self.assertEqual(entry["hit_count"], 3)
        self.assertEqual(entry["best_confidence"], 4)

    def test_ranking_prefers_more_distinct_seeds(self):
        self._insert(
            [
                result("seedA", "v1", "broad", topic="health", confidence=2),
                result("seedB", "v2", "broad", topic="health", confidence=2),
                result("seedC", "v3", "broad", topic="health", confidence=2),
                result("seedA", "v4", "narrow", topic="health", confidence=4),
            ]
        )
        refresh_interest_queue(VERSION, database_path=self.database_path)
        queue = get_interest_queue(database_path=self.database_path)
        self.assertEqual(queue[0]["channel_key"], "broad")

    def test_refresh_is_idempotent(self):
        records = [result("seedA", "v1", "chan1", topic="health", confidence=3)]
        self._insert(records)
        refresh_interest_queue(VERSION, database_path=self.database_path)
        first = get_interest_queue(database_path=self.database_path)[0]
        refresh_interest_queue(VERSION, database_path=self.database_path)
        second = get_interest_queue(database_path=self.database_path)[0]
        self.assertEqual(first["hit_count"], second["hit_count"])
        self.assertEqual(first["seed_count"], second["seed_count"])

    def test_promoted_entries_do_not_revert_to_pending_on_refresh(self):
        self._insert([result("seedA", "v1", "chan1", topic="health", confidence=3)])
        refresh_interest_queue(VERSION, database_path=self.database_path)
        mark_interest_queue_status(
            ["chan1"], "promoted", database_path=self.database_path
        )
        # A later run sees the channel again.
        self._insert([result("seedB", "v2", "chan1", topic="health", confidence=3)])
        refresh_interest_queue(VERSION, database_path=self.database_path)

        self.assertEqual(get_interest_queue(database_path=self.database_path), [])
        promoted = get_interest_queue(status="promoted", database_path=self.database_path)
        self.assertEqual(len(promoted), 1)
        # ...but its evidence still updates.
        self.assertEqual(promoted[0]["seed_count"], 2)

    def test_depth_records_the_shortest_path(self):
        self._insert(
            [
                result("seedA", "v1", "chan1", topic="health", confidence=3, depth=3),
                result("seedB", "v2", "chan1", topic="health", confidence=3, depth=1),
            ]
        )
        refresh_interest_queue(VERSION, database_path=self.database_path)
        self.assertEqual(
            get_interest_queue(database_path=self.database_path)[0]["depth"], 1
        )

    def test_yield_is_reported_per_depth(self):
        self._insert(
            [
                result("seedA", "v1", "chan1", topic="health", confidence=3, depth=1),
                result("seedA", "v2", "chan2", depth=1),
                result("seedC", "v3", "chan3", depth=2),
                result("seedC", "v4", "chan4", depth=2),
            ]
        )
        rates = {row["depth"]: row for row in interest_crawl_yield(VERSION, database_path=self.database_path)}
        self.assertAlmostEqual(rates[1]["yield_rate"], 0.5)
        self.assertAlmostEqual(rates[2]["yield_rate"], 0.0)

    def test_rejected_status_is_validated(self):
        with self.assertRaises(ValueError):
            mark_interest_queue_status(
                ["chan1"], "banished", database_path=self.database_path
            )

    def test_empty_insert_is_a_noop(self):
        self.assertEqual(self._insert([]), 0)


if __name__ == "__main__":
    unittest.main()
