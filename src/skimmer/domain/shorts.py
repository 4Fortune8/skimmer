"""Durable Shorts classification.

Duration alone does not identify a Short: eligibility extends to three minutes,
so 78 s and 179 s videos tagged `#shorts` clear a 60 s cutoff, while plenty of
legitimate long-form videos sit in the same band. The definitive signal is that
`HEAD /shorts/{video_id}` returns 200 for a Short and a 3xx redirect to
`/watch` for everything else -- already implemented, concurrently and with
rate-limit backoff, in `scripts/RecomendationAnalysis/shorts_probe.py`.

This module adds the two things that probe lacks for collector use: a durable
DB-backed tag so a video is only ever probed once across the whole pipeline,
and a duration short-circuit so videos that cannot possibly be Shorts never
cost a request.

The network probe is injected rather than imported. The analysis probe lives
under `scripts/`, and having the package import from there would invert the
dependency and break whenever the working directory changes.
"""

from __future__ import annotations

from skimmer.storage.bronze import (
    get_video_format_labels,
    upsert_video_format_labels,
)

# Shorts cannot exceed three minutes, so anything longer is definitively
# long-form and needs no request. This is only safe as a negative test -- being
# under the ceiling says nothing either way.
SHORTS_MAX_POSSIBLE_SECONDS = 180

METHOD_DURATION = "duration"
METHOD_SHORTS_URL = "shorts_url"


def _as_seconds(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_shorts(videos, probe=None, database_path=None, persist=True):
    """Return {video_id: bool | None} for each video.

    `videos` is an iterable of mappings with `video_id` and optionally
    `duration_seconds`. `probe` is called with the ids that remain unresolved
    and must return {video_id: bool | None}; when omitted, unresolved videos
    come back as None rather than being guessed.

    None means unknown, never "not a Short". Callers decide whether to fail open
    (keep it) or closed (drop it); the distinction is lost if unknown is
    silently coerced.
    """
    entries = {}
    for video in videos:
        video_id = video.get("video_id")
        if video_id:
            entries[video_id] = _as_seconds(video.get("duration_seconds"))
    if not entries:
        return {}

    resolved = {}

    cached = get_video_format_labels(entries.keys(), database_path=database_path)
    for video_id, label in cached.items():
        resolved[video_id] = label["is_short"]

    # Duration short-circuit for anything still unknown.
    new_labels = []
    for video_id, duration in entries.items():
        if video_id in resolved:
            continue
        if duration is not None and duration > SHORTS_MAX_POSSIBLE_SECONDS:
            resolved[video_id] = False
            new_labels.append(
                {"video_id": video_id, "is_short": False, "method": METHOD_DURATION}
            )

    remaining = [v for v in entries if v not in resolved]
    if remaining and probe is not None:
        for video_id, is_short in probe(remaining).items():
            resolved[video_id] = is_short
            if is_short is not None:
                new_labels.append(
                    {
                        "video_id": video_id,
                        "is_short": is_short,
                        "method": METHOD_SHORTS_URL,
                    }
                )

    if persist and new_labels:
        upsert_video_format_labels(new_labels, database_path=database_path)

    for video_id in entries:
        resolved.setdefault(video_id, None)
    return resolved


def filter_out_shorts(videos, probe=None, database_path=None, keep_unknown=True):
    """Drop videos resolved as Shorts.

    `keep_unknown` fails open, matching the existing probe's behaviour: an
    unreachable endpoint should not silently empty a selection.
    """
    videos = list(videos)
    verdicts = resolve_shorts(videos, probe=probe, database_path=database_path)
    kept = []
    for video in videos:
        verdict = verdicts.get(video.get("video_id"))
        if verdict is True:
            continue
        if verdict is None and not keep_unknown:
            continue
        kept.append(video)
    return kept


def network_probe(max_workers=8, cache=None):
    """Adapter over the analysis probe, imported lazily to keep layering intact."""

    def _probe(video_ids):
        from scripts.RecomendationAnalysis import shorts_probe

        return shorts_probe.probe_videos(
            video_ids, max_workers=max_workers, cache=cache
        )

    return _probe
