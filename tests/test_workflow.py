import os
import unittest
from unittest.mock import patch

import workflow


class WorkflowTests(unittest.TestCase):
    def test_run_cycle_runs_collectors_in_order(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command[1])

            class Result:
                returncode = 0

            return Result()

        self.assertTrue(workflow.run_cycle(runner))
        self.assertEqual(calls, list(workflow.WORKFLOW_SCRIPTS))

    def test_run_cycle_skips_profiles_after_youtube_failure(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command[1])

            class Result:
                returncode = 1

            return Result()

        self.assertFalse(workflow.run_cycle(runner))
        self.assertEqual(calls, ["youtubeSkimmer.py"])

    def test_cycle_seconds_defaults_to_thirty_minutes(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(workflow.cycle_seconds(), 1800)


if __name__ == "__main__":
    unittest.main()
