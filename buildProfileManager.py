"""Run independent, rate-limited profile collection workers."""

import fcntl
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
WORKERS = {
    "youtube-channel-id": "resolveYouTubeChannelIds.py",
    "vidiq": "buildIDProfile.py",
    "socialblade": "buildIDProfile-old.py",
}
DEFAULT_BATCH_SIZE = 100
DEFAULT_EMPTY_QUEUE_SECONDS = 60
DEFAULT_ERROR_BACKOFF_SECONDS = 60 * 60


def worker_loop(source, script_name, runner=subprocess.run, sleeper=time.sleep):
    batch_size = int(os.environ.get("SKIMMER_BATCH_SIZE", DEFAULT_BATCH_SIZE))
    empty_queue_seconds = int(
        os.environ.get("SKIMMER_EMPTY_QUEUE_SECONDS", DEFAULT_EMPTY_QUEUE_SECONDS)
    )
    error_backoff_seconds = int(
        os.environ.get(
            "SKIMMER_SOURCE_ERROR_BACKOFF_SECONDS", DEFAULT_ERROR_BACKOFF_SECONDS
        )
    )
    worker_id = f"{source}-{os.getpid()}"
    environment = os.environ.copy()
    environment["SKIMMER_BATCH_SIZE"] = str(batch_size)
    environment["SKIMMER_WORKER_ID"] = worker_id

    while True:
        result = runner(
            [sys.executable, script_name],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
        )
        if result.returncode == 0:
            continue
        if result.returncode == 2:
            print(f"{source} worker has no queued profiles; sleeping {empty_queue_seconds}s.")
            sleeper(empty_queue_seconds)
            continue
        print(
            f"{source} worker failed; sleeping {error_backoff_seconds}s before retrying."
        )
        sleeper(error_backoff_seconds)


def main():
    lock_path = PROJECT_ROOT / ".profile-manager.lock"
    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Profile manager is already running; exiting.")
            return 0

        threads = [
            threading.Thread(
                target=worker_loop,
                args=(source, script_name),
                name=f"{source}-worker",
            )
            for source, script_name in WORKERS.items()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
