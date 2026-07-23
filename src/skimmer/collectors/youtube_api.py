"""Collect YouTube Data API channel and video snapshots."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from skimmer.config import youtube_api_key
from skimmer.storage.bronze import (
    claim_youtube_api_batch,
    get_youtube_api_quota_usage,
    insert_youtubeapi_channel_stats,
    insert_youtubeapi_video_stats,
    mark_youtube_api_failed,
    mark_youtube_api_succeeded,
    release_youtube_api_batch,
    reserve_youtube_api_quota_unit,
    store_youtube_channel_id,
)

API_ROOT = "https://www.googleapis.com/youtube/v3"
DEFAULT_DAILY_BUDGET = 8000
DEFAULT_BATCH_SIZE = 5000
DEFAULT_VIDEO_LOOKBACK_DAYS = 30
REQUEST_TIMEOUT_SECONDS = 30


class QuotaExceeded(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _timestamp(value=None):
    return (value or _now()).replace(microsecond=0).isoformat()


def parse_int_env(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1.")
    return parsed


def _chunked(values, size):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _parse_duration_seconds(value):
    if not value:
        return None
    match = re.fullmatch(
        r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value,
    )
    if not match:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def _request_json(endpoint, params, database_path=None, budget=None):
    if budget is not None and not reserve_youtube_api_quota_unit(
        budget, database_path=database_path
    ):
        raise QuotaExceeded(f"YouTube API budget exhausted at {budget} units.")

    query = dict(params)
    query["key"] = youtube_api_key()
    url = f"{API_ROOT}/{endpoint}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8")
            data = json.loads(payload)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            data = {}
        reason = _extract_error_reason(data)
        if exc.code == 403 and reason == "quotaExceeded":
            raise QuotaExceeded("YouTube API quota exceeded.") from exc
        raise RuntimeError(
            f"YouTube API request failed for {endpoint}: HTTP {exc.code} {reason or 'unknown_error'}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"YouTube API request failed for {endpoint}: {exc.reason}") from exc
    return data


def _extract_error_reason(payload):
    errors = payload.get("error", {}).get("errors", [])
    if errors:
        return errors[0].get("reason")
    return None


def _resolve_handle(handle, database_path=None, budget=None):
    payload = _request_json(
        "channels",
        {"part": "id", "forHandle": handle.lstrip("@"), "maxResults": 1},
        database_path=database_path,
        budget=budget,
    )
    items = payload.get("items") or []
    if not items:
        return None
    return items[0].get("id")


def _fetch_channels(channel_ids, database_path=None, budget=None):
    records = {}
    for group in _chunked(channel_ids, 50):
        try:
            payload = _request_json(
                "channels",
                {
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(group),
                    "maxResults": 50,
                },
                database_path=database_path,
                budget=budget,
            )
        except QuotaExceeded:
            raise
        except Exception as exc:
            print(f"YouTube API channel batch failed, skipping: {exc}", file=sys.stderr)
            continue
        for item in payload.get("items") or []:
            channel_id = item.get("id")
            if not channel_id:
                continue
            snippet = item.get("snippet") or {}
            statistics = item.get("statistics") or {}
            content_details = item.get("contentDetails") or {}
            uploads_playlist_id = (
                content_details.get("relatedPlaylists") or {}
            ).get("uploads")
            subscribers = None
            if not statistics.get("hiddenSubscriberCount"):
                subscribers_value = statistics.get("subscriberCount")
                if subscribers_value is not None:
                    subscribers = int(subscribers_value)
            records[channel_id] = {
                "collected_at": _timestamp(),
                "channel_id": channel_id,
                "channel_name": snippet.get("title"),
                "subscribers": subscribers,
                "subscribers_change": None,
                "views": int(statistics["viewCount"]) if statistics.get("viewCount") else None,
                "views_change": None,
                "video_count": int(statistics["videoCount"]) if statistics.get("videoCount") else None,
                "country": snippet.get("country"),
                "channel_published_at": snippet.get("publishedAt"),
                "uploads_playlist_id": uploads_playlist_id,
            }
    return records


def _fetch_upload_video_ids(playlist_id, database_path=None, budget=None):
    payload = _request_json(
        "playlistItems",
        {
            "part": "contentDetails,snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
        },
        database_path=database_path,
        budget=budget,
    )
    video_ids = []
    for item in payload.get("items") or []:
        content_details = item.get("contentDetails") or {}
        video_id = content_details.get("videoId")
        if video_id:
            video_ids.append(video_id)
    return video_ids


def _fetch_video_records(video_ids, database_path=None, budget=None):
    records = []
    for group in _chunked(video_ids, 50):
        payload = _request_json(
            "videos",
            {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(group),
                "maxResults": 50,
            },
            database_path=database_path,
            budget=budget,
        )
        for item in payload.get("items") or []:
            snippet = item.get("snippet") or {}
            statistics = item.get("statistics") or {}
            content_details = item.get("contentDetails") or {}
            records.append(
                {
                    "collected_at": _timestamp(),
                    "video_id": item.get("id"),
                    "channel_id": snippet.get("channelId"),
                    "title": snippet.get("title"),
                    "published_at": snippet.get("publishedAt"),
                    "duration_seconds": _parse_duration_seconds(content_details.get("duration")),
                    "category_id": snippet.get("categoryId"),
                    "views": int(statistics["viewCount"]) if statistics.get("viewCount") else None,
                    "likes": int(statistics["likeCount"]) if statistics.get("likeCount") else None,
                    "comments": int(statistics["commentCount"]) if statistics.get("commentCount") else None,
                }
            )
    return [record for record in records if record.get("video_id") and record.get("channel_id")]


def collect_youtube_api(limit, budget, database_path=None, worker_id=None, video_lookback_days=DEFAULT_VIDEO_LOOKBACK_DAYS):
    if worker_id is None:
        worker_id = f"youtube-api-{os.getpid()}"
    claimed = claim_youtube_api_batch(worker_id, limit=limit, database_path=database_path)
    if not claimed:
        return {"claimed": 0, "channels": 0, "videos": 0, "quota_used": get_youtube_api_quota_usage(database_path=database_path)}

    failed_channels = []
    resolved_handles = 0
    channel_rows = 0
    video_rows = 0
    try:
        resolved_rows = []
        for channel_key, channel_id, youtube_channel_id in claimed:
            canonical_id = youtube_channel_id or None
            if canonical_id is None and channel_id and channel_id.startswith("UC"):
                canonical_id = channel_id
                store_youtube_channel_id(channel_key, canonical_id, database_path)
                resolved_handles += 1
            elif canonical_id is None:
                try:
                    canonical_id = _resolve_handle(
                        channel_id, database_path=database_path, budget=budget
                    )
                except QuotaExceeded:
                    raise
                except Exception as exc:
                    print(
                        f"YouTube API handle resolution failed for {channel_id}, "
                        f"marking failed: {exc}",
                        file=sys.stderr,
                    )
                    canonical_id = None
                if canonical_id:
                    store_youtube_channel_id(channel_key, canonical_id, database_path)
                    resolved_handles += 1
            if not canonical_id:
                mark_youtube_api_failed(channel_key, database_path)
                failed_channels.append(channel_key)
                continue
            resolved_rows.append((channel_key, channel_id, canonical_id))

        channel_records = _fetch_channels(
            [row[2] for row in resolved_rows],
            database_path=database_path,
            budget=budget,
        )

        for channel_key, original_id, canonical_id in resolved_rows:
            channel_record = channel_records.get(canonical_id)
            if channel_record is None:
                mark_youtube_api_failed(channel_key, database_path)
                failed_channels.append(channel_key)
                continue
            if original_id != canonical_id:
                store_youtube_channel_id(channel_key, canonical_id, database_path)
            insert_youtubeapi_channel_stats(channel_record, database_path)
            channel_rows += 1
            mark_youtube_api_succeeded(channel_key, database_path)

            playlist_id = channel_record.get("uploads_playlist_id")
            if not playlist_id:
                continue
            try:
                upload_video_ids = _fetch_upload_video_ids(
                    playlist_id, database_path=database_path, budget=budget
                )
                if not upload_video_ids:
                    continue
                video_records = _fetch_video_records(
                    upload_video_ids, database_path=database_path, budget=budget
                )
                lookback_cutoff = _now() - timedelta(days=video_lookback_days)
                recent_rows = []
                for record in video_records:
                    published_at = record.get("published_at")
                    if published_at:
                        try:
                            published_dt = datetime.fromisoformat(
                                published_at.replace("Z", "+00:00")
                            )
                        except ValueError:
                            published_dt = None
                        if published_dt is not None and published_dt < lookback_cutoff:
                            continue
                    recent_rows.append(record)
                video_rows += insert_youtubeapi_video_stats(recent_rows, database_path)
            except QuotaExceeded:
                raise
            except Exception:
                continue
    except QuotaExceeded:
        release_youtube_api_batch(worker_id, database_path)
        raise
    except Exception:
        release_youtube_api_batch(worker_id, database_path)
        raise

    release_youtube_api_batch(worker_id, database_path)
    return {
        "claimed": len(claimed),
        "channels": channel_rows,
        "videos": video_rows,
        "resolved_handles": resolved_handles,
        "failed_channels": len(failed_channels),
        "quota_used": get_youtube_api_quota_usage(database_path=database_path),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect YouTube API channel/video snapshots.")
    parser.add_argument("--limit", type=int, default=parse_int_env("SKIMMER_BATCH_SIZE", DEFAULT_BATCH_SIZE))
    parser.add_argument("--budget", type=int, default=parse_int_env("YOUTUBE_API_DAILY_BUDGET", DEFAULT_DAILY_BUDGET))
    parser.add_argument("--db-path", default=os.environ.get("SKIMMER_DB_PATH"))
    parser.add_argument(
        "--video-lookback-days",
        type=int,
        default=parse_int_env("YOUTUBE_API_VIDEO_LOOKBACK_DAYS", DEFAULT_VIDEO_LOOKBACK_DAYS),
    )
    args = parser.parse_args(argv)

    try:
        summary = collect_youtube_api(
            limit=args.limit,
            budget=args.budget,
            database_path=args.db_path,
            video_lookback_days=args.video_lookback_days,
        )
    except QuotaExceeded as exc:
        print(str(exc))
        return 0

    print(
        "YouTube API collection complete: "
        f"claimed={summary['claimed']} channels={summary['channels']} "
        f"videos={summary['videos']} resolved_handles={summary['resolved_handles']} "
        f"failed_channels={summary['failed_channels']} quota_used={summary['quota_used']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
