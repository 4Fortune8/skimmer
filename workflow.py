"""Run the YouTube and profile collection workflow on a fixed interval."""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
WORKFLOW_SCRIPTS = (
    "youtubeSkimmer.py",
    "buildProfileManager.py",
    "buildIDProfile.py",
    "buildIDProfile-old.py",
)
DEFAULT_CYCLE_SECONDS = 30 * 60


def cycle_seconds():
    value = os.environ.get("SKIMMER_CYCLE_SECONDS", str(DEFAULT_CYCLE_SECONDS))
    try:
        seconds = int(value)
    except ValueError as exc:
        raise ValueError("SKIMMER_CYCLE_SECONDS must be an integer.") from exc
    if seconds < 1:
        raise ValueError("SKIMMER_CYCLE_SECONDS must be at least one second.")
    return seconds


def run_script(script_name, runner=subprocess.run):
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting {script_name}.")
    result = runner(
        [sys.executable, script_name],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode:
        print(f"{script_name} failed with exit code {result.returncode}.")
        return False
    return True


def run_cycle(runner=subprocess.run):
    """Run one ordered workflow cycle and report whether it fully succeeded."""
    if not run_script(WORKFLOW_SCRIPTS[0], runner):
        return False
    return all(run_script(script_name, runner) for script_name in WORKFLOW_SCRIPTS[1:])


def main(sleeper=time.sleep):
    interval = cycle_seconds()
    while True:
        run_cycle()
        print(f"Sleeping for {interval} seconds before the next workflow cycle.")
        sleeper(interval)


if __name__ == "__main__":
    main()
