import os
import unittest
from unittest.mock import patch

import buildProfileManager


class ProfileManagerTests(unittest.TestCase):
    def test_socialblade_cloudflare_block_uses_dedicated_cooldown(self):
        class BlockedResult:
            returncode = buildProfileManager.CLOUDFLARE_BLOCK_EXIT_CODE

        sleeps = []

        def runner(*_, **__):
            return BlockedResult()

        def sleeper(seconds):
            sleeps.append(seconds)
            raise RuntimeError("stop test loop")

        with patch.dict(
            os.environ,
            {"SOCIALBLADE_CLOUDFLARE_BACKOFF_SECONDS": "1234"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "stop test loop"):
                buildProfileManager.worker_loop(
                    "socialblade", "buildIDProfile-old.py", runner, sleeper
                )

        self.assertEqual(sleeps, [1234])


if __name__ == "__main__":
    unittest.main()
