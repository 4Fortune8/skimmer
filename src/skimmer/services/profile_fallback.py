"""Run one vidiq -> socialblade fallback pass for profiles that YouTube API missed.

Triggered once per YouTube feed cycle by workflow.py. Always drains vidiq's
current queue first; a channel only becomes eligible for socialblade once
vidiq fails to collect it (see bronze.mark_profile_failed). Respects a
persisted Cloudflare cooldown for socialblade across separate invocations,
since each trigger runs in a fresh subprocess.
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from skimmer.config import PROJECT_ROOT

VIDIQ_MODULE = "skimmer.collectors.vidiq"
SOCIALBLADE_MODULE = "skimmer.collectors.socialblade"
CLOUDFLARE_BLOCK_EXIT_CODE = 3
DEFAULT_SOCIALBLADE_CLOUDFLARE_BACKOFF_SECONDS = 6 * 60 * 60
COOLDOWN_PATH = PROJECT_ROOT / ".socialblade-cloudflare-cooldown"


def socialblade_cloudflare_backoff_seconds():
    value = os.environ.get(
        "SOCIALBLADE_CLOUDFLARE_BACKOFF_SECONDS",
        str(DEFAULT_SOCIALBLADE_CLOUDFLARE_BACKOFF_SECONDS),
    )
    try:
        seconds = int(value)
    except ValueError as exc:
        raise ValueError(
            "SOCIALBLADE_CLOUDFLARE_BACKOFF_SECONDS must be an integer."
        ) from exc
    if seconds < 1:
        raise ValueError(
            "SOCIALBLADE_CLOUDFLARE_BACKOFF_SECONDS must be at least one second."
        )
    return seconds


def read_cooldown_until(path=COOLDOWN_PATH):
    try:
        return float(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_cooldown_until(timestamp, path=COOLDOWN_PATH):
    path.write_text(str(timestamp))


def drain_profile_source(module_name, runner=subprocess.run):
    """Repeatedly run a profile collector module until its queue is empty.

    Loops silently while the module reports it made progress (exit code 0).
    Returns the exit code that stopped the drain: 2 for an empty queue, or
    the module's failure exit code (e.g. socialblade's Cloudflare block).
    """
    while True:
        result = runner(
            [sys.executable, "-m", module_name],
            cwd=PROJECT_ROOT,
            env=os.environ.copy(),
            check=False,
        )
        if result.returncode == 0:
            continue
        return result.returncode


def main(runner=subprocess.run, now=time.time):
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting vidiq fallback pass.")
    drain_profile_source(VIDIQ_MODULE, runner)

    cooldown_until = read_cooldown_until()
    if cooldown_until is not None and now() < cooldown_until:
        print("Social Blade is in a Cloudflare cooldown; skipping this pass.")
        return 0

    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting socialblade fallback pass.")
    exit_code = drain_profile_source(SOCIALBLADE_MODULE, runner)
    if exit_code == CLOUDFLARE_BLOCK_EXIT_CODE:
        backoff = socialblade_cloudflare_backoff_seconds()
        write_cooldown_until(now() + backoff)
        print(f"Social Blade is in a Cloudflare cooldown; pausing for {backoff}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
