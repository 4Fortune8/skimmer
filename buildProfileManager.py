"""Refresh and inspect the source-balanced profile collection queue."""

import os

from bronze_store import get_profile_queue, refresh_profile_queue


def refresh_profile_stack():
    refresh_profile_queue()
    limit = int(os.environ.get("SKIMMER_CHANNEL_LIMIT", "0"))
    for source in ("vidiq", "socialblade"):
        channel_ids = get_profile_queue(source, limit)
        print(f"{source}: {len(channel_ids)} queued channel(s)")
        for channel_id in channel_ids:
            print(f"  {channel_id}")


if __name__ == "__main__":
    refresh_profile_stack()
