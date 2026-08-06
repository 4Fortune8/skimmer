"""Backfill YouTube language tags onto already-collected videos.

The language fields ride along free with ``part=snippet``, so videos collected
from now on carry them. Everything collected before does not, and re-fetching
all 497k stored videos would cost ~10k quota units.

This backfills by sampling instead. Language is decided per channel, so a
handful of videos per channel is enough to label the channel authoritatively:
~5 videos across ~23k channels is roughly 2.3k units, which fits in a day's
spare quota. Channels whose sampled videos come back without any language tag
are simply left to the detector.
"""

import argparse
import os

from skimmer.storage.bronze import (
    get_youtube_api_quota_usage,
    update_youtubeapi_video_languages,
    videos_missing_language,
)

from skimmer.collectors.youtube_api import (
    DEFAULT_DAILY_BUDGET,
    QuotaExceeded,
    _chunked,
    _request_json,
    parse_int_env,
)

DEFAULT_VIDEOS_PER_CHANNEL = 5


def _fetch_languages(video_ids, database_path=None, budget=None):
    records = []
    for group in _chunked(video_ids, 50):
        payload = _request_json(
            "videos",
            {"part": "snippet", "id": ",".join(group), "maxResults": 50},
            database_path=database_path,
            budget=budget,
        )
        for item in payload.get("items") or []:
            snippet = item.get("snippet") or {}
            records.append(
                {
                    "video_id": item.get("id"),
                    "default_audio_language": snippet.get("defaultAudioLanguage"),
                    "default_language": snippet.get("defaultLanguage"),
                }
            )
    return records


def backfill_video_languages(
    videos_per_channel=DEFAULT_VIDEOS_PER_CHANNEL,
    budget=DEFAULT_DAILY_BUDGET,
    database_path=None,
    limit=None,
):
    """Sample videos lacking language tags and store what YouTube reports."""

    video_ids = videos_missing_language(
        videos_per_channel=videos_per_channel,
        limit=limit,
        database_path=database_path,
    )
    requested = len(video_ids)
    updated = 0
    tagged = 0
    quota_exhausted = False
    try:
        for group in _chunked(video_ids, 50):
            records = _fetch_languages(group, database_path=database_path, budget=budget)
            tagged += sum(
                1
                for record in records
                if record.get("default_audio_language") or record.get("default_language")
            )
            updated += update_youtubeapi_video_languages(records, database_path=database_path)
    except QuotaExceeded:
        # Partial progress is still progress: what was written stays written and
        # the next run picks up the remaining channels.
        quota_exhausted = True
    return {
        "requested": requested,
        "tagged": tagged,
        "rows_updated": updated,
        "quota_exhausted": quota_exhausted,
        "quota_used": get_youtube_api_quota_usage(database_path=database_path),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backfill YouTube video language tags.")
    parser.add_argument(
        "--videos-per-channel",
        type=int,
        default=parse_int_env("SKIMMER_LANGUAGE_SAMPLE", DEFAULT_VIDEOS_PER_CHANNEL),
        help="How many untagged videos to sample per channel.",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=parse_int_env("YOUTUBE_API_DAILY_BUDGET", DEFAULT_DAILY_BUDGET),
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap total videos requested.")
    parser.add_argument("--db-path", default=os.environ.get("SKIMMER_DB_PATH"))
    args = parser.parse_args(argv)

    summary = backfill_video_languages(
        videos_per_channel=args.videos_per_channel,
        budget=args.budget,
        database_path=args.db_path,
        limit=args.limit,
    )
    print(
        "Language backfill complete: "
        f"requested={summary['requested']} tagged={summary['tagged']} "
        f"rows_updated={summary['rows_updated']} quota_used={summary['quota_used']}"
        + (" (quota exhausted, rerun to continue)" if summary["quota_exhausted"] else "")
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
