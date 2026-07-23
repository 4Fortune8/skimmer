import os
import unittest
from unittest.mock import patch

from skimmer.services import workflow


class WorkflowTests(unittest.TestCase):
    def test_main_runs_feed_and_api_before_starting_fallback_manager(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command[-1])

            class Result:
                returncode = 0

            return Result()

        class Process:
            def poll(self):
                return None

        def popen(command, **kwargs):
            calls.append(command[-1])
            return Process()

        def sleeper(_):
            raise RuntimeError("stop test loop")

        with self.assertRaisesRegex(RuntimeError, "stop test loop"):
            workflow.main(sleeper, popen, runner)
        self.assertEqual(
            calls,
            [
                "skimmer.collectors.youtube",
                "skimmer.collectors.youtube_api",
                "skimmer.services.profile_manager",
            ],
        )

    def test_youtube_uses_configured_cpu(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)

            class Result:
                returncode = 0

            return Result()

        with patch.dict(os.environ, {"SKIMMER_YOUTUBE_CPU": "0"}):
            self.assertTrue(workflow.run_module("skimmer.collectors.youtube", runner))
        self.assertEqual(
            calls,
            [[
                "taskset",
                "-c",
                "0",
                workflow.sys.executable,
                "-m",
                "skimmer.collectors.youtube",
            ]],
        )

    def test_feed_cycle_seconds_defaults_to_fifteen_minutes(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(workflow.cycle_seconds(), 900)

    def test_youtube_api_cycle_seconds_defaults_to_daily(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(workflow.youtube_api_cycle_seconds(), 86400)


if __name__ == "__main__":
    unittest.main()
