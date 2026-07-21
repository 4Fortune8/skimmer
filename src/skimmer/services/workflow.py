"""Run hourly YouTube collection alongside persistent profile workers."""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from skimmer.config import PROJECT_ROOT

YOUTUBE_MODULE = "skimmer.collectors.youtube"
PROFILE_MANAGER_MODULE = "skimmer.services.profile_manager"
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


def run_module(module_name, runner=subprocess.run):
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting {module_name}.")
    command = [sys.executable, "-m", module_name]
    youtube_cpu = os.environ.get("SKIMMER_YOUTUBE_CPU")
    if module_name == YOUTUBE_MODULE and youtube_cpu:
        command = ["taskset", "-c", youtube_cpu, *command]
    result = runner(
        command,
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode:
        print(f"{module_name} failed with exit code {result.returncode}.")
        return False
    return True


def start_profile_manager(popen=subprocess.Popen):
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting {PROFILE_MANAGER_MODULE}.")
    return popen(
        [sys.executable, "-m", PROFILE_MANAGER_MODULE],
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
        run_module(YOUTUBE_MODULE, runner)
        print(f"Sleeping for {interval} seconds before the next YouTube collection.")
        sleeper(interval)


if __name__ == "__main__":
    main()
