import os
import unittest
from unittest.mock import patch

from skimmer.services import profile_fallback


class ProfileFallbackTests(unittest.TestCase):
    def test_drain_profile_source_stops_on_empty_queue(self):
        calls = []

        results = iter([0, 0, 2])

        def runner(command, **kwargs):
            calls.append(command[-1])

            class Result:
                returncode = next(results)

            return Result()

        exit_code = profile_fallback.drain_profile_source(
            "skimmer.collectors.vidiq", runner
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(len(calls), 3)

    def test_main_runs_vidiq_then_socialblade_in_order(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command[-1])

            class Result:
                returncode = 2

            return Result()

        with patch.object(profile_fallback, "read_cooldown_until", return_value=None):
            self.assertEqual(profile_fallback.main(runner, now=lambda: 0), 0)

        self.assertEqual(
            calls,
            ["skimmer.collectors.vidiq", "skimmer.collectors.socialblade"],
        )

    def test_main_skips_socialblade_during_cloudflare_cooldown(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command[-1])

            class Result:
                returncode = 2

            return Result()

        with patch.object(
            profile_fallback, "read_cooldown_until", return_value=1_000.0
        ):
            self.assertEqual(profile_fallback.main(runner, now=lambda: 500.0), 0)

        self.assertEqual(calls, ["skimmer.collectors.vidiq"])

    def test_main_records_cooldown_when_socialblade_blocked(self):
        results = iter([2, profile_fallback.CLOUDFLARE_BLOCK_EXIT_CODE])

        def runner(command, **kwargs):
            class Result:
                returncode = next(results)

            return Result()

        written = {}

        def fake_write(timestamp, path=None):
            written["timestamp"] = timestamp

        with patch.object(profile_fallback, "read_cooldown_until", return_value=None), \
            patch.object(profile_fallback, "write_cooldown_until", fake_write), \
            patch.dict(os.environ, {"SOCIALBLADE_CLOUDFLARE_BACKOFF_SECONDS": "1234"}):
            profile_fallback.main(runner, now=lambda: 100.0)

        self.assertEqual(written["timestamp"], 100.0 + 1234)


if __name__ == "__main__":
    unittest.main()
