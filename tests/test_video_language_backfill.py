from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from skimmer.collectors import video_languages
from skimmer.storage.bronze import (
    initialize_database,
    insert_youtubeapi_video_stats,
    update_youtubeapi_video_languages,
    videos_missing_language,
)


def _video(video_id, channel_id, published_at, **extra):
    record = {
        "collected_at": "2026-08-01T10:00:00+00:00",
        "video_id": video_id,
        "channel_id": channel_id,
        "title": video_id,
        "published_at": published_at,
        "views": 10,
    }
    record.update(extra)
    return record


class VideoLanguageBackfillTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.database_path = Path(self._tmp.name) / "skimmer.db"
        initialize_database(self.database_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _rows(self, video_id):
        with sqlite3.connect(self.database_path) as connection:
            return connection.execute(
                "SELECT default_audio_language, default_language "
                "FROM bronze_youtubeapi_video_stats WHERE video_id = ?",
                (video_id,),
            ).fetchall()

    def test_collector_stores_language_fields(self):
        insert_youtubeapi_video_stats(
            [_video("v1", "c1", "2026-07-01T00:00:00+00:00",
                    default_audio_language="en-GB", default_language="en")],
            self.database_path,
        )
        self.assertEqual(self._rows("v1"), [("en-GB", "en")])

    def test_language_is_excluded_from_the_dedupe_digest(self):
        """Adding language must not make already-stored snapshots look new."""

        record = _video("v1", "c1", "2026-07-01T00:00:00+00:00")
        self.assertEqual(insert_youtubeapi_video_stats([record], self.database_path), 1)
        tagged = dict(record, default_audio_language="hi")
        self.assertEqual(insert_youtubeapi_video_stats([tagged], self.database_path), 0)
        self.assertEqual(len(self._rows("v1")), 1)

    def test_backfill_updates_every_snapshot_of_a_video(self):
        for collected_at in ("2026-07-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"):
            insert_youtubeapi_video_stats(
                [_video("v1", "c1", "2026-06-01T00:00:00+00:00", collected_at=collected_at)],
                self.database_path,
            )
        self.assertEqual(len(self._rows("v1")), 2)

        updated = update_youtubeapi_video_languages(
            [{"video_id": "v1", "default_audio_language": "hi", "default_language": None}],
            self.database_path,
        )
        self.assertEqual(updated, 2)
        self.assertEqual(self._rows("v1"), [("hi", None), ("hi", None)])

    def test_backfill_never_overwrites_collected_values(self):
        insert_youtubeapi_video_stats(
            [_video("v1", "c1", "2026-07-01T00:00:00+00:00", default_audio_language="en")],
            self.database_path,
        )
        update_youtubeapi_video_languages(
            [{"video_id": "v1", "default_audio_language": "hi", "default_language": "hi"}],
            self.database_path,
        )
        self.assertEqual(self._rows("v1"), [("en", "hi")])

    def test_missing_language_sampling_is_capped_per_channel(self):
        """Coverage across channels beats depth on the few prolific ones."""

        records = []
        for index in range(6):
            records.append(_video(f"a{index}", "channel-a", f"2026-07-0{index + 1}T00:00:00+00:00"))
        records.append(_video("b0", "channel-b", "2026-07-01T00:00:00+00:00"))
        records.append(_video("c0", "channel-c", "2026-07-01T00:00:00+00:00",
                              default_audio_language="en"))
        insert_youtubeapi_video_stats(records, self.database_path)

        sampled = videos_missing_language(videos_per_channel=2, database_path=self.database_path)
        self.assertEqual(len(sampled), 3, "two from channel-a, one from channel-b, none tagged")
        self.assertIn("b0", sampled)
        self.assertNotIn("c0", sampled, "already tagged videos are not refetched")
        # Newest first, so the cap keeps the most recent uploads.
        self.assertEqual([video for video in sampled if video.startswith("a")], ["a5", "a4"])

    def test_backfill_keeps_partial_progress_when_quota_runs_out(self):
        """Running out mid-run must keep what was written so the next run resumes."""

        # Two batches worth: the API is called in groups of 50 video ids.
        insert_youtubeapi_video_stats(
            [
                _video(f"v{index}", f"c{index}", "2026-07-01T00:00:00+00:00")
                for index in range(60)
            ],
            self.database_path,
        )
        calls = []

        def fake_request(endpoint, params, database_path=None, budget=None):
            calls.append(params["id"].split(","))
            if len(calls) > 1:
                raise video_languages.QuotaExceeded("budget spent")
            return {
                "items": [
                    {"id": video_id, "snippet": {"defaultAudioLanguage": "es"}}
                    for video_id in calls[-1]
                ]
            }

        original = video_languages._request_json
        video_languages._request_json = fake_request
        try:
            summary = video_languages.backfill_video_languages(
                videos_per_channel=1, database_path=self.database_path
            )
        finally:
            video_languages._request_json = original

        self.assertEqual(summary["requested"], 60)
        self.assertTrue(summary["quota_exhausted"])
        self.assertEqual(summary["tagged"], 50, "the first batch survived")
        self.assertEqual(summary["rows_updated"], 50)
        remaining = videos_missing_language(
            videos_per_channel=1, database_path=self.database_path
        )
        self.assertEqual(len(remaining), 10, "the next run picks up only what is left")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
