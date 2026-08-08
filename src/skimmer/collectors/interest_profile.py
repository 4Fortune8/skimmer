"""Dedicated warmed Firefox profile for the area-of-interest crawl.

See `docs/interest_crawl_plan.md`. The interest crawl harvests recommendation
rails, and those rails are personalised. A fresh profile regresses toward
general popularity -- which strategy 0 showed is exactly the entertainment mass
that drowns this domain -- so the crawl drives a persistent profile warmed by
watching on-topic videos.

Isolation note. `collectors/youtube.py:create_driver()` sets no profile, so the
feed skimmer launches Firefox with a fresh anonymous temporary profile every
run. This module *adds* persistence in its own directory rather than modifying
anything the feed skimmer touches, so warming cannot leak into scan A.

Warming through a logged-out profile personalises via cookies only. That is
real but weak; a logged-in profile would tune considerably harder, at the cost
of storing credentials, which is out of scope.
"""

from __future__ import annotations

import fcntl
import os
import random
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service

from skimmer.collectors.youtube import (
    resolve_firefox_binary_path,
    resolve_geckodriver_path,
)
from skimmer.config import PROJECT_ROOT
from skimmer.domain.interest_pools import DEFAULT_WARM_SHARE, WARM, partition_by_pool
from skimmer.domain.liveness import filter_dead
from skimmer.domain.normalization import normalize_title
from skimmer.domain.shorts import filter_out_shorts, network_probe

DEFAULT_PROFILE_DIRNAME = ".interest-firefox-profile"
DEFAULT_DWELL_SECONDS = 45
DEFAULT_WARM_LIMIT = 24
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"


def profile_directory():
    directory = Path(
        os.environ.get(
            "INTEREST_FIREFOX_PROFILE_DIR", PROJECT_ROOT / DEFAULT_PROFILE_DIRNAME
        )
    ).expanduser()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


@contextmanager
def interest_profile_lock():
    """Guard the profile against concurrent use.

    Firefox refuses to share a profile between processes, and the crawl runs
    from the orchestrator while warming may be run by hand. Mirrors
    socialblade_profile_lock().
    """
    directory = profile_directory()
    lock_path = directory.with_name(f"{directory.name}.lock")
    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"Interest profile {directory} is already in use by another process."
            ) from exc
        try:
            yield directory
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def create_driver(headless=None, allow_playback=True):
    """Firefox bound to the persistent interest profile.

    `allow_playback` must be True for warming and **False for crawling**.
    Warming needs watch time, since that is what drives personalisation --
    Firefox blocks autoplay by default, which would leave every visit paused at
    currentTime 0 and make the run a silent no-op.

    Crawling wants the opposite. The crawl opens a watch page only to harvest
    its recommendation rail, and at an expected majority-off-topic yield,
    accumulating watch history from wherever those rails lead would erode the
    warm a little more every run until the profile no longer represents the
    domain it was tuned for.
    """
    options = Options()
    options.set_preference("media.volume_scale", "0.0")
    if allow_playback:
        options.set_preference("media.autoplay.default", 0)
        options.set_preference("media.autoplay.blocking_policy", 0)
    else:
        options.set_preference("media.autoplay.default", 5)  # block audible+inaudible
        options.set_preference("media.autoplay.blocking_policy", 2)
    options.binary_location = resolve_firefox_binary_path()
    options.add_argument("-profile")
    options.add_argument(str(profile_directory().resolve()))

    if headless is None:
        configured = os.environ.get("INTEREST_HEADLESS")
        if configured is None:
            headless = not bool(
                os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
            )
        else:
            headless = configured.strip().lower() in {"1", "true", "yes", "on"}
    if headless:
        options.add_argument("-headless")

    return webdriver.Firefox(
        service=Service(resolve_geckodriver_path()), options=options
    )


# The SQL is a coarse prefilter only. The #shorts tag is optional and the
# duration ceiling is three minutes, so Shorts still get through here and are
# removed over the network afterwards. Channel and title rules are applied in
# Python because they differ between warming and seeding and because title
# comparison needs normalisation SQL cannot express.
CANDIDATE_SQL = """
WITH latest_video AS (
    SELECT video_id, channel_id, title, views, duration_seconds,
           ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY collected_at DESC) rn
    FROM bronze_youtubeapi_video_stats
)
SELECT v.video_id, v.channel_id, v.title, CAST(v.views AS REAL) AS views,
       CAST(v.duration_seconds AS REAL) AS duration_seconds,
       l.topic, l.confidence
FROM video_topic_labels l
JOIN latest_video v ON v.video_id = l.video_id AND v.rn = 1
WHERE l.classifier_version = ?
  AND l.confidence >= ?
  AND CAST(v.views AS REAL) >= ?
  AND v.duration_seconds IS NOT NULL
  AND CAST(v.duration_seconds AS REAL) > ?
  AND LOWER(v.title) NOT LIKE '%#shorts%'
  AND LOWER(v.title) NOT LIKE '%#short %'
ORDER BY l.confidence DESC, CAST(v.views AS REAL) DESC
"""

SHORTS_MAX_DURATION_SECONDS = 60
# Warming wants a strong topical signal, and YouTube's rail responds to channel
# affinity, so several videos from one clearly on-topic channel tune harder
# than one each from several marginal channels. Seeding wants the opposite --
# one per channel, to reach distinct neighbourhoods.
WARM_PER_CHANNEL = 2
WARM_PER_CHANNEL_HIGH_CONFIDENCE = 3
HIGH_CONFIDENCE = 4

# The view floor exists for *rail* quality -- a low-view video has thin co-view
# data and returns a generic or empty recommendation rail. That matters only for
# seeds. Warming just needs watch time on on-topic content, so a floor there is
# pure cost: with the disjoint partition applied to a pool this thin, a 1000
# floor drops warming to 13 videos and loses education entirely.
WARM_MIN_VIEWS = 0


def load_candidates(
    database_path,
    classifier_version="terms-v1",
    min_confidence=3,
    min_views=1000,
    shorts_max_duration=SHORTS_MAX_DURATION_SECONDS,
):
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            CANDIDATE_SQL,
            (classifier_version, min_confidence, min_views, shorts_max_duration),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def _dedupe_and_cap(candidates, per_channel, per_topic):
    """Apply title dedupe, per-channel caps, then per-topic caps."""
    seen_titles = set()
    channel_counts = {}
    topic_counts = {}
    selected = []
    for video in candidates:
        title_key = normalize_title(video.get("title"))
        if title_key and title_key in seen_titles:
            continue
        channel_id = video.get("channel_id")
        cap = per_channel
        if (video.get("confidence") or 0) >= HIGH_CONFIDENCE:
            cap = max(per_channel, WARM_PER_CHANNEL_HIGH_CONFIDENCE)
        if channel_counts.get(channel_id, 0) >= cap:
            continue
        topic = video.get("topic")
        if topic_counts.get(topic, 0) >= per_topic:
            continue
        if title_key:
            seen_titles.add(title_key)
        channel_counts[channel_id] = channel_counts.get(channel_id, 0) + 1
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
        selected.append(video)
    return selected


def select_warm_videos(
    database_path,
    classifier_version="terms-v1",
    min_confidence=3,
    min_views=WARM_MIN_VIEWS,
    per_topic=8,
    shorts_max_duration=SHORTS_MAX_DURATION_SECONDS,
    verify_shorts=True,
    verify_liveness=True,
    warm_share=DEFAULT_WARM_SHARE,
    per_channel=WARM_PER_CHANNEL,
):
    """On-topic videos to watch, drawn only from the warm half of the partition.

    Channels are split warm/seed by hash so warming never touches a channel the
    crawl will later seed from; see domain.interest_pools for why.

    Topic balancing is capped rather than proportional: health dominates the
    labelled pool and unbalanced warming would tune the profile into one topic
    rather than the domain.
    """
    candidates = load_candidates(
        database_path,
        classifier_version=classifier_version,
        min_confidence=min_confidence,
        min_views=min_views,
        shorts_max_duration=shorts_max_duration,
    )
    warm_pool = partition_by_pool(candidates, warm_share=warm_share)[WARM]

    if verify_liveness:
        warm_pool = filter_dead(warm_pool, database_path=database_path)
    if verify_shorts:
        warm_pool = filter_out_shorts(
            warm_pool, probe=network_probe(), database_path=database_path
        )

    return _dedupe_and_cap(warm_pool, per_channel=per_channel, per_topic=per_topic)


PLAYER_STATE_JS = """
const video = document.querySelector('video');
if (!video) { return {ready: false, unavailable: false, currentTime: 0}; }
const text = document.body ? (document.body.innerText || '') : '';
return {
    ready: video.readyState >= 2 || !!video.src,
    unavailable: /isn't available|is not available|unavailable|has been removed/i.test(text),
    currentTime: video.currentTime,
    paused: video.paused
};
"""

PLAY_JS = """
const video = document.querySelector('video');
if (!video) { return false; }
video.muted = true;
const promise = video.play();
if (promise && promise.catch) { promise.catch(() => {}); }
return true;
"""


def _state(driver):
    try:
        return driver.execute_script(PLAYER_STATE_JS) or {}
    except Exception:  # noqa: BLE001 - transient during navigation
        return {}


def _start_playback(driver, ready_timeout=25, play_attempts=3):
    """Wait for the player to initialise, then start playback.

    A single fire-and-forget play() after a fixed settle is unreliable: on a
    loaded host the element exists long before YouTube attaches a media source,
    and play() against readyState 0 / empty src silently does nothing. So poll
    for readiness first, then retry play() until currentTime actually advances.

    Returns "playing", "unavailable" (deleted or private), or "stalled".
    """
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        state = _state(driver)
        if state.get("unavailable"):
            return "unavailable"
        if state.get("ready"):
            break
        time.sleep(1.0)
    else:
        return "stalled"

    for _ in range(play_attempts):
        try:
            driver.execute_script(PLAY_JS)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2.0)
        if (_state(driver).get("currentTime") or 0) > 0.5:
            return "playing"
    return "stalled"


def _dwell(driver, seconds, sample_every=5.0):
    """Sleep while sampling playback, returning the furthest point reached.

    Sampling rather than reading once at the end: if the player resets or
    re-sources mid-dwell, a single final read reports 0 for a video that was
    actually watched.
    """
    deadline = time.monotonic() + seconds
    furthest = 0.0
    while time.monotonic() < deadline:
        time.sleep(min(sample_every, max(0.5, deadline - time.monotonic())))
        current = _state(driver).get("currentTime") or 0
        furthest = max(furthest, float(current))
    return furthest


def warm_profile(driver, videos, dwell_seconds=DEFAULT_DWELL_SECONDS, jitter=0.25):
    """Watch each video long enough to register in watch history.

    Dwell is jittered because a fixed interval across dozens of loads is an
    obvious automation signature.
    """
    try:
        driver.set_page_load_timeout(60)
    except Exception:  # noqa: BLE001 - not fatal if the driver rejects it
        pass

    watched = []
    outcomes = {"playing": 0, "stalled": 0, "unavailable": 0, "load_failed": 0}
    for index, video in enumerate(videos, start=1):
        video_id = video["video_id"]
        title = (video.get("title") or "")[:58]
        prefix = f"[{index}/{len(videos)}]"
        try:
            driver.get(WATCH_URL.format(video_id=video_id))
        except Exception as exc:  # noqa: BLE001 - one bad video must not abort warming
            outcomes["load_failed"] += 1
            print(f"{prefix} load-failed  {video_id} ({exc.__class__.__name__})")
            continue

        outcome = _start_playback(driver)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome != "playing":
            # No point dwelling on a video that will never play.
            print(f"{prefix} {outcome:<12} {title}")
            continue

        delay = dwell_seconds * (1 + random.uniform(-jitter, jitter))
        # Never dwell past the end of the video. YouTube auto-advances when one
        # finishes, which both resets currentTime (so a fully-watched short
        # video measures as 0s) and accumulates watch history for a video we did
        # not choose -- polluting the very profile we are trying to aim.
        duration = video.get("duration_seconds")
        if duration:
            delay = min(delay, max(5.0, float(duration) - 5.0))
        elapsed = _dwell(driver, max(5.0, delay))
        print(f"{prefix} {video['topic']:<12} t={elapsed:>4.0f}s  {title}")
        watched.append(video_id)

    total = sum(outcomes.values())
    print(f"\noutcomes: {outcomes}")
    if total and len(watched) / total < 0.6:
        print(
            f"WARNING: only {len(watched)}/{total} videos actually played. "
            "The profile is weakly warmed; re-run before relying on it."
        )
    return watched


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Warm the interest crawl profile.")
    parser.add_argument("--db", default=str(PROJECT_ROOT / "data" / "skimmer.db"))
    parser.add_argument("--classifier-version", default="terms-v1")
    parser.add_argument("--min-confidence", type=int, default=3)
    # Defaults must come from the module constants, not be repeated here --
    # a literal 1000 silently shadowed WARM_MIN_VIEWS and cut warming from 24
    # videos to 13, losing education entirely.
    parser.add_argument("--min-views", type=int, default=WARM_MIN_VIEWS)
    parser.add_argument("--warm-share", type=float, default=DEFAULT_WARM_SHARE)
    parser.add_argument("--per-topic", type=int, default=8)
    parser.add_argument("--dwell", type=int, default=DEFAULT_DWELL_SECONDS)
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_WARM_LIMIT, help="Max videos to watch."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the videos that would be watched without starting Firefox.",
    )
    args = parser.parse_args(argv)

    videos = select_warm_videos(
        args.db,
        classifier_version=args.classifier_version,
        min_confidence=args.min_confidence,
        min_views=args.min_views,
        per_topic=args.per_topic,
        warm_share=args.warm_share,
    )
    if not videos:
        print("No labelled videos matched; run scripts/classify_corpus_topics.py --write.")
        return 1

    random.shuffle(videos)
    videos = videos[: args.limit]

    by_topic = {}
    for video in videos:
        by_topic[video["topic"]] = by_topic.get(video["topic"], 0) + 1
    print(f"profile: {profile_directory()}")
    print(f"warming on {len(videos)} videos: {by_topic}")
    estimate = len(videos) * args.dwell / 60
    print(f"estimated runtime: ~{estimate:.0f} min\n")

    if args.dry_run:
        for video in videos:
            print(f"  {video['topic']:<13} {(video.get('title') or '')[:80]}")
        return 0

    with interest_profile_lock():
        driver = create_driver()
        try:
            watched = warm_profile(driver, videos, dwell_seconds=args.dwell)
        finally:
            driver.quit()
    print(f"\nwarmed on {len(watched)} videos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
