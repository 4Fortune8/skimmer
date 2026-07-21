import os
import unittest
from unittest.mock import patch

import workflow


class WorkflowTests(unittest.TestCase):
    def test_main_starts_manager_and_runs_youtube_independently(self):
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
            calls, ["buildProfileManager.py", "youtubeSkimmer.py"]
        )

    def test_youtube_uses_configured_cpu(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)

            class Result:
                returncode = 0

            return Result()

        with patch.dict(os.environ, {"SKIMMER_YOUTUBE_CPU": "0"}):
            self.assertTrue(workflow.run_script("youtubeSkimmer.py", runner))
        self.assertEqual(
            calls, [["taskset", "-c", "0", workflow.sys.executable, "youtubeSkimmer.py"]]
        )

    def test_cycle_seconds_defaults_to_one_hour(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(workflow.cycle_seconds(), 3600)


if __name__ == "__main__":
    unittest.main()
