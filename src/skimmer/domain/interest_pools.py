"""Disjoint warm/seed partition for the area-of-interest crawl.

Warming and seeding draw from the same small labelled pool. If a video is used
for both, the crawl harvests a rail moments after that exact video was watched,
so the results measure watch-history recency rather than topic association and
the yield rate is inflated into meaninglessness.

The split is by channel rather than by video: YouTube's rail is influenced by
channel affinity, so watching *any* video from a channel contaminates seeds
from that same channel.

Assignment is a hash of the channel id, not a random draw, so the partition is
stable across runs and processes. A channel that lands in the warm pool today
must not drift into the seed pool next week, or the disjointness silently
decays as the profile accumulates history.
"""

from __future__ import annotations

import hashlib

WARM = "warm"
SEED = "seed"

# Warming needs enough channels to build a topical signal; seeding needs the
# larger share because it drives discovery and burns through cooldown.
DEFAULT_WARM_SHARE = 0.35
_BUCKETS = 1000


def _bucket(channel_id):
    digest = hashlib.sha256(str(channel_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % _BUCKETS


def channel_pool(channel_id, warm_share=DEFAULT_WARM_SHARE):
    """Return WARM or SEED for a channel, deterministically.

    hashlib rather than hash(): the builtin is salted per process, so the
    partition would change on every run.
    """
    if not channel_id:
        return SEED
    return WARM if _bucket(channel_id) < warm_share * _BUCKETS else SEED


def partition_by_pool(videos, warm_share=DEFAULT_WARM_SHARE, key="channel_id"):
    """Split video mappings into {WARM: [...], SEED: [...]}."""
    pools = {WARM: [], SEED: []}
    for video in videos:
        pools[channel_pool(video.get(key), warm_share=warm_share)].append(video)
    return pools
