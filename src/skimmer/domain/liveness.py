"""Detect and tag videos that no longer exist.

Roughly 2.6 % of the labelled corpus is already deleted or private. That is a
small tax on a 533-video pool, but a dead video in the *seed* set is worse than
a wasted slot: it returns an empty recommendation rail, which the crawl records
as a seed with zero yield rather than as a broken seed. That systematically
biases the yield rate downward -- the one number the go/no-go gate turns on.

The oEmbed endpoint is the cheap check: it returns 200 with JSON for a live
video and 404 for one that is gone. No API quota, no browser, no auth.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from skimmer.storage.bronze import get_video_liveness, upsert_video_liveness

OEMBED_URL = "https://www.youtube.com/oembed"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_WORKERS = 8
# Positive verdicts are re-checked after this long; negatives are permanent.
DEFAULT_MAX_AGE_DAYS = 30


def check_video(video_id, timeout=DEFAULT_TIMEOUT):
    """Return True (live), False (gone), or None (undetermined)."""
    query = urllib.parse.urlencode(
        {"url": WATCH_URL.format(video_id=video_id), "format": "json"}
    )
    request = urllib.request.Request(
        f"{OEMBED_URL}?{query}", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        # 404 is the deleted/private signal. 401/403 mean embedding is
        # restricted, which is not the same as gone, so they stay undetermined.
        if exc.code == 404:
            return False
        return None
    except Exception:  # noqa: BLE001 - network failure is undetermined, not dead
        return None


def check_videos(video_ids, max_workers=DEFAULT_MAX_WORKERS, timeout=DEFAULT_TIMEOUT):
    video_ids = [v for v in dict.fromkeys(video_ids) if v]
    if not video_ids:
        return {}
    workers = max(1, min(max_workers, len(video_ids)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        verdicts = executor.map(lambda v: check_video(v, timeout=timeout), video_ids)
        return dict(zip(video_ids, verdicts))


def resolve_liveness(
    videos,
    checker=None,
    database_path=None,
    max_age_days=DEFAULT_MAX_AGE_DAYS,
    persist=True,
):
    """Return {video_id: bool | None}, consulting and updating the stored tags."""
    ids = [v.get("video_id") if isinstance(v, dict) else v for v in videos]
    ids = [v for v in dict.fromkeys(ids) if v]
    if not ids:
        return {}

    resolved = {}
    for video_id, label in get_video_liveness(
        ids, max_age_days=max_age_days, database_path=database_path
    ).items():
        resolved[video_id] = label["is_live"]

    remaining = [v for v in ids if v not in resolved]
    if remaining:
        checker = checker or check_videos
        new_labels = []
        for video_id, is_live in checker(remaining).items():
            resolved[video_id] = is_live
            if is_live is not None:
                new_labels.append({"video_id": video_id, "is_live": is_live})
        if persist and new_labels:
            upsert_video_liveness(new_labels, database_path=database_path)

    for video_id in ids:
        resolved.setdefault(video_id, None)
    return resolved


def filter_dead(
    videos,
    checker=None,
    database_path=None,
    max_age_days=DEFAULT_MAX_AGE_DAYS,
    keep_unknown=True,
):
    """Drop videos known to be gone.

    Fails open by default: an unreachable oEmbed endpoint must not empty a
    selection, since undetermined is not the same as dead.
    """
    videos = list(videos)
    verdicts = resolve_liveness(
        videos,
        checker=checker,
        database_path=database_path,
        max_age_days=max_age_days,
    )
    kept = []
    for video in videos:
        verdict = verdicts.get(video.get("video_id"))
        if verdict is False:
            continue
        if verdict is None and not keep_unknown:
            continue
        kept.append(video)
    return kept
