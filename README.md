# Skimmer

Skimmer collects YouTube feed data and channel metrics into a local SQLite
database. Active collectors do not create CSV output. The YouTube Data API
collector is the primary source of channel and video metrics; vidIQ and
Social Blade browser scrapers act only as a fallback when the API fails for a
channel.

## Database

The default database path is `data/skimmer.db`. Set `SKIMMER_DB_PATH` to use a
different location:

```bash
export SKIMMER_DB_PATH=/var/lib/skimmer/skimmer.db
```

The collectors create the database and tables automatically. Metric tables
store only normalized fields and a deduplication digest; they do not retain raw
rendered-page JSON or ingestion timestamps.

| Table | Source |
| --- | --- |
| `bronze_youtube_skimmed` | YouTube feed items |
| `bronze_youtubeapi_channel_stats` | YouTube Data API channel snapshots (timestamped, one row per collection) |
| `bronze_youtubeapi_video_stats` | YouTube Data API video snapshots (timestamped, one row per collection) |
| `bronze_vidiq_channel_stats` | vidIQ channel statistics (fallback) |
| `bronze_vidiq_channel_profiles` | vidIQ channel profile details and content mix (fallback) |
| `bronze_socialblade_channel_stats` | Social Blade channel statistics (fallback) |
| `profile_queue` | channel collection work, tracked per source |
| `youtube_api_quota_usage` | daily YouTube Data API unit consumption |

Feed records are retained for 14 days. Once the YouTube API successfully
collects a channel, that channel is excluded from vidIQ/Social Blade and is
instead re-snapshotted by the daily API sweep. A channel only falls back to the
scrapers if the API attempt fails. A scraper-collected channel is not
re-queued for 14 days after a successful collection. A scraper source failure
is retried once by the other source; failures from both sources remain marked
for review until the API succeeds or an operator clears the review flag.

## Collectors

```bash
python -m pip install -e ".[analysis]"
skimmer-youtube
skimmer-youtube-api
skimmer-profile-manager
skimmer-vidiq
skimmer-socialblade
```

`skimmer-youtube` refreshes `profile_queue` after it stores the feed.
`skimmer-youtube-api` claims queued channels (both new and any released back
from a completed scraper lease) and calls the YouTube Data API v3 to snapshot
channel and video statistics; see the "YouTube Data API collector" section
below. `skimmer-profile-manager` can also refresh and inspect the
source-balanced queue. The two scraper profile collectors read only their
assigned queue entries, pause 15 seconds between channel requests, and mark a
queue entry complete only after a successful metric insert.

## YouTube Data API collector

Add a `.env` file at the repository root containing the API key:

```
youtubeAPI=<your YouTube Data API v3 key>
```

`skimmer-youtube-api` reads this key (or an already-exported `youtubeAPI`
environment variable takes precedence) via `skimmer.config.youtube_api_key()`.
The collector:

1. Resolves any handle-only queue entries to canonical `UC...` channel IDs
   (`channels.list?forHandle=`).
2. Snapshots channel statistics in batches of 50 IDs per request
   (`channels.list`), storing subscribers, views, video count, and the
   channel's uploads playlist ID into `bronze_youtubeapi_channel_stats`.
3. Lists each channel's uploads playlist (`playlistItems.list`) to discover
   video IDs.
4. Snapshots video statistics in batches of 50 IDs per request
   (`videos.list`), storing views, likes, comments, duration, publish date,
   and category into `bronze_youtubeapi_video_stats`, limited to videos
   published within `--video-lookback-days` (default 30).

Each request costs at least 1 quota unit; batching keeps the cost to
1 unit per 50 channels or videos. A local daily budget (default 8,000 of the
Google Cloud default 10,000-unit allocation) is enforced with an atomic SQLite
reservation in `youtube_api_quota_usage`, so concurrent runs cannot exceed it.
Adjust the budget with `YOUTUBE_API_DAILY_BUDGET` or `--budget`. The collector
never calls `search.list` (separate, low-cost daily quota bucket).

A channel that succeeds is marked `youtube_api_success_at` and excluded from
future scraper batches. A channel that fails (missing/deleted channel, quota
exhaustion, network error) is marked `youtube_api_failed` and released back to
the vidIQ/Social Blade queue.

See [RUNBOOK.md](RUNBOOK.md) for setup, execution, and inspection procedures.

## Project layout

Production code lives in `src/skimmer/`: collectors render source pages,
`storage` owns SQLite persistence and queue state, `domain` holds shared
normalization, and `services` orchestrates long-running workers. Legacy data
conversion and analysis scripts are in `scripts/`; deployment assets are in
`deploy/`; notebooks remain at the repository root until their analysis
workflow is migrated.

## Automated workflow

`workflow.py` runs two independent schedules and keeps the profile manager
running as a persistent fallback:

- YouTube feed discovery (`skimmer-youtube`) runs every 15 minutes by default
  (`SKIMMER_FEED_CYCLE_SECONDS`, default `900`).
- The YouTube Data API snapshot sweep (`skimmer-youtube-api`) runs once daily
  by default (`YOUTUBE_API_CYCLE_SECONDS`, default `86400`), starting
  immediately on service startup, then again once per interval.

The profile manager runs one worker per scraper source; each atomically leases
up to 100 channels *that the YouTube API has already failed to collect*, then
immediately claims the next batch after completing it. Each request remains
rate-limited by the collector's 15-second delay. Empty workers retry after 60
seconds; source failures back off for one hour. SQLite uses WAL mode and a
30-second busy timeout so concurrent collectors serialize writes safely.

Failures are retained in `collection_errors`, including Social Blade
Cloudflare blocks as HTTP 403 records. Do not shorten
`YOUTUBE_API_CYCLE_SECONDS` without also reducing collection scope or
increasing `YOUTUBE_API_DAILY_BUDGET`, since the API sweep is sized to fit the
default daily quota.

Social Blade workers require `profile_queue.youtube_channel_id`, the canonical
`UC...` identifier. Feed cards store this directly when YouTube exposes a
`/channel/UC...` link. The profile manager's headless YouTube resolver leases
unresolved Social Blade handles in batches of 100 and stores the canonical ID
before Social Blade can claim them.

Each profile source records identifier-level outcomes in `collection_attempts`.
vidIQ tries the original handle before the canonical ID; Social Blade tries the
canonical ID before the handle. A source is failed over only after both forms
fail.

Social Blade defaults to one channel every two minutes through
`SOCIALBLADE_CHANNEL_DELAY_SECONDS=120` and one page request per 20 seconds
through `SOCIALBLADE_PAGE_DELAY_SECONDS=20`; both limits apply to failed
identifier attempts as well as successful collection. It uses the dedicated,
persistent Firefox profile in `SOCIALBLADE_FIREFOX_PROFILE_DIR`. A Cloudflare
block releases the leased queue and starts a six-hour source-wide cooldown;
adjust it with `SOCIALBLADE_CLOUDFLARE_BACKOFF_SECONDS`.
