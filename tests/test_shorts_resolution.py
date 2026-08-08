import os
import tempfile
import unittest

from skimmer.domain.shorts import filter_out_shorts, resolve_shorts
from skimmer.storage.bronze import (
    get_video_format_labels,
    initialize_database,
    upsert_video_format_labels,
)


def video(video_id, duration=None):
    return {"video_id": video_id, "duration_seconds": duration}


class ResolveShortsTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.database_path)
        initialize_database(self.database_path)
        self.probed = []

    def tearDown(self):
        if os.path.exists(self.database_path):
            os.unlink(self.database_path)

    def _probe(self, verdicts):
        def probe(video_ids):
            self.probed.extend(video_ids)
            return {v: verdicts.get(v) for v in video_ids}

        return probe

    def test_long_videos_skip_the_network(self):
        result = resolve_shorts(
            [video("long", 684)], probe=self._probe({}), database_path=self.database_path
        )
        self.assertEqual(result, {"long": False})
        self.assertEqual(self.probed, [])

    def test_videos_inside_the_shorts_band_are_probed(self):
        # 179s clears a 60s cutoff but is still Shorts-eligible.
        result = resolve_shorts(
            [video("maybe", 179)],
            probe=self._probe({"maybe": True}),
            database_path=self.database_path,
        )
        self.assertEqual(result, {"maybe": True})
        self.assertEqual(self.probed, ["maybe"])

    def test_results_are_persisted_and_reused(self):
        resolve_shorts(
            [video("v1", 100)],
            probe=self._probe({"v1": True}),
            database_path=self.database_path,
        )
        self.probed.clear()
        again = resolve_shorts(
            [video("v1", 100)],
            probe=self._probe({"v1": True}),
            database_path=self.database_path,
        )
        self.assertEqual(again, {"v1": True})
        self.assertEqual(self.probed, [], "cached video should not be re-probed")

    def test_failed_probe_is_not_persisted(self):
        result = resolve_shorts(
            [video("flaky", 100)],
            probe=self._probe({"flaky": None}),
            database_path=self.database_path,
        )
        self.assertIsNone(result["flaky"])
        self.assertEqual(
            get_video_format_labels(["flaky"], database_path=self.database_path),
            {},
            "a transient failure must not be recorded as a verdict",
        )

    def test_unknown_is_not_coerced_to_false(self):
        result = resolve_shorts(
            [video("nodata", None)], probe=None, database_path=self.database_path
        )
        self.assertIsNone(result["nodata"])

    def test_storing_an_unknown_verdict_is_refused(self):
        with self.assertRaises(ValueError):
            upsert_video_format_labels(
                [{"video_id": "x", "is_short": None}], database_path=self.database_path
            )

    def test_duration_short_circuit_is_only_a_negative_test(self):
        # Under the ceiling proves nothing, so it must still be probed.
        resolve_shorts(
            [video("short_but_long_form", 120)],
            probe=self._probe({"short_but_long_form": False}),
            database_path=self.database_path,
        )
        self.assertEqual(self.probed, ["short_but_long_form"])


class FilterOutShortsTests(ResolveShortsTests):
    def test_shorts_are_dropped_and_others_kept(self):
        videos = [video("a", 684), video("b", 90), video("c", 700)]
        kept = filter_out_shorts(
            videos, probe=self._probe({"b": True}), database_path=self.database_path
        )
        self.assertEqual([v["video_id"] for v in kept], ["a", "c"])

    def test_unknown_fails_open_by_default(self):
        kept = filter_out_shorts(
            [video("u", 90)],
            probe=self._probe({"u": None}),
            database_path=self.database_path,
        )
        self.assertEqual(len(kept), 1)

    def test_unknown_can_fail_closed(self):
        kept = filter_out_shorts(
            [video("u", 90)],
            probe=self._probe({"u": None}),
            database_path=self.database_path,
            keep_unknown=False,
        )
        self.assertEqual(kept, [])


if __name__ == "__main__":
    unittest.main()
