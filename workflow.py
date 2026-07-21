"""Run hourly YouTube collection alongside persistent profile workers."""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
YOUTUBE_SCRIPT = "youtubeSkimmer.py"
PROFILE_MANAGER_SCRIPT = "buildProfileManager.py"
DEFAULT_CYCLE_SECONDS = 60 * 60


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
    command = [sys.executable, script_name]
    youtube_cpu = os.environ.get("SKIMMER_YOUTUBE_CPU")
    if script_name == YOUTUBE_SCRIPT and youtube_cpu:
        command = ["taskset", "-c", youtube_cpu, *command]
    result = runner(
        command,
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode:
        print(f"{script_name} failed with exit code {result.returncode}.")
        return False
    return True


def start_profile_manager(popen=subprocess.Popen):
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting {PROFILE_MANAGER_SCRIPT}.")
    return popen(
        [sys.executable, PROFILE_MANAGER_SCRIPT],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
    )


def main(sleeper=time.sleep, popen=subprocess.Popen, runner=subprocess.run):
    interval = cycle_seconds()
    profile_manager = start_profile_manager(popen)
    while True:
        if profile_manager.poll() is not None:
            print("Profile manager exited; restarting it.")
            profile_manager = start_profile_manager(popen)
        run_script(YOUTUBE_SCRIPT, runner)
        print(f"Sleeping for {interval} seconds before the next YouTube collection.")
        sleeper(interval)


if __name__ == "__main__":
    main()
