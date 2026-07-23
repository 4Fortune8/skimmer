"""Run frequent YouTube feed discovery and daily API snapshots."""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from skimmer.config import PROJECT_ROOT

YOUTUBE_MODULE = "skimmer.collectors.youtube"
YOUTUBE_API_MODULE = "skimmer.collectors.youtube_api"
PROFILE_MANAGER_MODULE = "skimmer.services.profile_manager"
DEFAULT_FEED_CYCLE_SECONDS = 15 * 60
DEFAULT_YOUTUBE_API_CYCLE_SECONDS = 24 * 60 * 60


def cycle_seconds():
    value = os.environ.get(
        "SKIMMER_FEED_CYCLE_SECONDS",
        os.environ.get("SKIMMER_CYCLE_SECONDS", str(DEFAULT_FEED_CYCLE_SECONDS)),
    )
    try:
        seconds = int(value)
    except ValueError as exc:
        raise ValueError("SKIMMER_FEED_CYCLE_SECONDS must be an integer.") from exc
    if seconds < 1:
        raise ValueError("SKIMMER_FEED_CYCLE_SECONDS must be at least one second.")
    return seconds


def youtube_api_cycle_seconds():
    value = os.environ.get(
        "YOUTUBE_API_CYCLE_SECONDS", str(DEFAULT_YOUTUBE_API_CYCLE_SECONDS)
    )
    try:
        seconds = int(value)
    except ValueError as exc:
        raise ValueError("YOUTUBE_API_CYCLE_SECONDS must be an integer.") from exc
    if seconds < 1:
        raise ValueError("YOUTUBE_API_CYCLE_SECONDS must be at least one second.")
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


def main(
    sleeper=time.sleep,
    popen=subprocess.Popen,
    runner=subprocess.run,
    monotonic=time.monotonic,
):
    feed_interval = cycle_seconds()
    api_interval = youtube_api_cycle_seconds()
    profile_manager = None
    next_api_run = None
    while True:
        run_module(YOUTUBE_MODULE, runner)
        if next_api_run is None or monotonic() >= next_api_run:
            run_module(YOUTUBE_API_MODULE, runner)
            next_api_run = monotonic() + api_interval
        if profile_manager is None or profile_manager.poll() is not None:
            print(f"[{datetime.now(timezone.utc).isoformat()}] Starting fallback profile workers.")
            profile_manager = start_profile_manager(popen)
        print(
            f"Sleeping for {feed_interval} seconds before the next YouTube feed collection."
        )
        sleeper(feed_interval)


if __name__ == "__main__":
    main()
