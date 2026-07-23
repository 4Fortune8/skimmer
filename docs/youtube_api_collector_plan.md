# Implementation Plan: YouTube Data API Collector

Status: planned, approved. Decision: **a channel successfully collected by the
YouTube API is fully removed from the vidiq/socialblade scraper queues** (no
supplemental scraping).

## Context

- Package layout: production code in `src/skimmer/`; collectors in
  `src/skimmer/collectors/`, storage in `src/skimmer/storage/bronze.py`,
  entry points declared in `pyproject.toml`.
- `profile_queue` is populated by `refresh_profile_queue()` from the
  `bronze_youtube_skimmed` feed (see `src/skimmer/collectors/youtube.py`).
  It already has `vidiq_failed`, `socialblade_failed`, `youtube_channel_id`
  (UC id, nullable), claim/lease columns.
- Current DB state (`data/skimmer.db`): ~2,731 queued channels; ~1,460 have
  a resolved `youtube_channel_id`; ~1,271 are handle-only.
- `.env` at repo root contains `youtubeAPI=<key>`. Nothing loads it yet;
  the project has no dotenv dependency.
- Existing collector pattern: claim batch -> collect -> insert bronze rows ->
  `mark_profile_succeeded` / `mark_profile_failed`, with
  `record_collection_attempt` / `record_collection_error` audit rows.
- Constraint gotcha: `profile_queue.assigned_source`,
  `collection_errors.source`, and `collection_attempts.source` have
  `CHECK (source IN ('vidiq','socialblade'))`. SQLite cannot alter CHECK
  constraints. Do NOT try to add `'youtube_api'` to `assigned_source`.
  Instead the API collector operates orthogonally via new columns (below),
  and audit logging for the API either rebuilds those audit tables or uses
  a separate lightweight log table (implementer's choice; a new
  `youtube_api_requests` log table is simplest).

## Tasks

### 1. Config: `.env` loader (`src/skimmer/config.py`)

- Add a minimal `.env` parser (no new dependency): read
  `PROJECT_ROOT / ".env"` if present, `KEY=VALUE` lines, ignore comments and
  blanks, do not override variables already in `os.environ`.
- Expose `def youtube_api_key() -> str` returning env var `youtubeAPI`
  (after loading `.env`); raise a clear error if missing.

### 2. Storage (`src/skimmer/storage/bronze.py`)

New `profile_queue` columns, added in `_ensure_profile_queue_columns`:

```sql
youtube_api_failed INTEGER NOT NULL DEFAULT 0   -- added via ALTER, so no CHECK
youtube_api_attempted_at TEXT                   -- last API attempt (retry backoff)
youtube_api_success_at TEXT                     -- set on successful API collection
```

New bronze tables in `_create_tables` (snapshot tables: one row per channel
or video per collection run, keyed by timestamp; `subscribers_change` /
`views_change` stay NULL at bronze and are computed later in silver from
consecutive snapshots per `channel_id`):

```sql
CREATE TABLE IF NOT EXISTS bronze_youtubeapi_channel_stats (
    id INTEGER PRIMARY KEY,
    collected_at TEXT NOT NULL,          -- UTC ISO timestamp
    channel_id TEXT NOT NULL,            -- canonical UC id
    channel_name TEXT,
    subscribers,
    subscribers_change,                  -- NULL at bronze; silver computes
    views,
    views_change,                        -- NULL at bronze; silver computes
    video_count,
    country TEXT,
    channel_published_at TEXT,
    uploads_playlist_id TEXT,
    data_digest TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_ytapi_channel_stats_channel_time
    ON bronze_youtubeapi_channel_stats(channel_id, collected_at);

CREATE TABLE IF NOT EXISTS bronze_youtubeapi_video_stats (
    id INTEGER PRIMARY KEY,
    collected_at TEXT NOT NULL,
    video_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    title TEXT,
    published_at TEXT,                   -- video age signal
    duration_seconds INTEGER,            -- parse ISO-8601 PT#M#S; length signal
    category_id TEXT,
    views,
    likes,
    comments,                            -- comment-count signal
    data_digest TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_ytapi_video_stats_video_time
    ON bronze_youtubeapi_video_stats(video_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_ytapi_video_stats_channel
    ON bronze_youtubeapi_video_stats(channel_id);
```

Digest note: `_insert_metric`-style dedupe must include the snapshot DATE
(day granularity of `collected_at`) in the digest so repeated runs the same
day dedupe but daily snapshots persist. Either extend `_insert_metric` with
an extra-digest-fields parameter or write dedicated insert helpers:
`insert_youtubeapi_channel_stats(record)`, `insert_youtubeapi_video_stats(records)`.

Quota accounting table (protects the 10k/day budget across multiple runs;
YouTube quota resets midnight Pacific — store the Pacific date):

```sql
CREATE TABLE IF NOT EXISTS youtube_api_quota_usage (
    quota_date TEXT PRIMARY KEY,         -- date in America/Los_Angeles
    units_used INTEGER NOT NULL DEFAULT 0
);
```

New queue functions (mirror the existing claim/mark style):

- `claim_youtube_api_batch(worker_id, limit)`: leases rows where
  `digested = 0 AND needs_review = 0 AND youtube_api_success_at IS NULL AND
  (claim expired-or-null) AND (youtube_api_attempted_at older than backoff
  or NULL)`. Not filtered by `assigned_source` — the API is tried for every
  channel regardless of scraper assignment. Returns
  `(channel_key, channel_id/handle, youtube_channel_id)` tuples.
- `mark_youtube_api_succeeded(channel_key)`: sets
  `youtube_api_success_at = now`, `digested = 1`, `last_success_at = now`,
  clears claim columns.
- `mark_youtube_api_failed(channel_key)`: sets `youtube_api_failed = 1`,
  `youtube_api_attempted_at = now`, clears claim, leaves the channel
  eligible for the existing vidiq/socialblade flow (unchanged fallback).
- Reuse `store_youtube_channel_id()` when Phase A resolves a handle.

Scraper exclusion (the approved decision): add to the WHERE clauses of
`claim_profile_batch` and `get_profile_queue`:

```sql
AND youtube_api_success_at IS NULL
```

Re-collection over time: `refresh_profile_queue` currently re-opens
(`digested = 0`) channels with new videos or stale success. Keep that logic,
but ALSO clear `youtube_api_success_at` back to NULL is WRONG — instead,
treat `youtube_api_success_at` as "the API owns this channel". The API
collector re-snapshots owned channels on schedule regardless of queue state
(Phase B below queries all rows with `youtube_api_success_at IS NOT NULL`
plus newly claimed ones), so scrapers never see them again.

### 3. Collector (`src/skimmer/collectors/youtube_api.py`)

Plain HTTPS via `urllib.request` (or `requests` if preferred — but avoid
adding the heavy google-api-python-client). Base:
`https://www.googleapis.com/youtube/v3`. Key from `config.youtube_api_key()`.
NEVER call `search.list` (separate 100/day bucket, wasteful).

Quota manager: before each request, check
`youtube_api_quota_usage` for today's Pacific date; refuse to exceed a
configurable per-day cap (env `YOUTUBE_API_DAILY_BUDGET`, default 8000 of
the 10,000 quota, leaving headroom). Every request (even failed) costs >=1
unit — record after each call. All list endpoints used cost 1 unit/request.

Four phases per run, in order, each bounded by remaining budget:

- **Phase A — handle resolution** (`channels.list?forHandle=@handle&part=id`,
  1 unit per channel, unbatchable): for claimed rows lacking
  `youtube_channel_id`. Cap per run (env, default 500/day) since the
  ~1,271-handle backlog is one-time. On resolution call
  `store_youtube_channel_id`; on HTTP 404/empty items treat as failure.
- **Phase B — channel snapshots**
  (`channels.list?id=<50 ids>&part=snippet,statistics,contentDetails`,
  1 unit per 50 channels): for all claimed channels with UC ids PLUS all
  previously API-owned channels (`youtube_api_success_at IS NOT NULL`) —
  the daily time series. Map: `statistics.subscriberCount -> subscribers`,
  `statistics.viewCount -> views`, `statistics.videoCount -> video_count`,
  `snippet.title -> channel_name`, `snippet.country`, `snippet.publishedAt`,
  `contentDetails.relatedPlaylists.uploads -> uploads_playlist_id`.
  Respect `statistics.hiddenSubscriberCount` (store NULL subscribers).
  A channel id missing from the response = deleted/terminated -> failure.
- **Phase C — upload discovery**
  (`playlistItems.list?playlistId=<uploads>&part=contentDetails,snippet&maxResults=50`,
  1 unit per channel per page): first page only per channel per run.
  Collect video ids newer than a lookback window (env, default 90 days) or
  not yet present in `bronze_youtubeapi_video_stats`. This is the
  scale bottleneck (unbatchable) — schedule tiers if channel count grows
  (see budget section).
- **Phase D — video snapshots**
  (`videos.list?id=<50 ids>&part=snippet,statistics,contentDetails`,
  1 unit per 50 videos): Phase C's new ids PLUS re-snapshot of tracked
  videos: daily while `published_at` < 30 days old, weekly afterward
  (query distinct video ids from `bronze_youtubeapi_video_stats` with the
  age/cadence rule). Map: `snippet.title`, `snippet.publishedAt`,
  `snippet.categoryId`, `contentDetails.duration` (ISO-8601 -> seconds),
  `statistics.viewCount/likeCount/commentCount` (each may be absent when
  hidden -> NULL).

Success/failure semantics: a claimed channel counts as succeeded when its
Phase B row is stored (video collection is best-effort per run). Call
`mark_youtube_api_succeeded` / `mark_youtube_api_failed` accordingly.
Errors: HTTP 403 `quotaExceeded` -> stop the run gracefully; 400/404 per
channel -> per-channel failure; network errors -> release claims
(add `release_youtube_api_batch(worker_id)` mirroring
`release_profile_batch`).

Category names: fetch `videoCategories.list?regionCode=US` once and cache
in a small table or module-level dict; only `categoryId` is stored at bronze.

`main()` entry point: argparse for `--limit`, `--budget`, `--db-path`;
prints a per-phase summary (channels resolved / snapshotted / videos
stored / units used).

### 4. Wiring

- `pyproject.toml`: add
  `skimmer-youtube-api = "skimmer.collectors.youtube_api:main"`.
- Optional: systemd unit in `deploy/systemd/` alongside
  `skimmer-workflow.service`, daily timer. Ordering: after the youtube.py
  feed run, BEFORE vidiq/socialblade collectors, so the API grabs channels
  first and scrapers only get the remainder.

### 5. Tests (`tests/`)

Follow `tests/test_bronze_store.py` conventions (tmp db path):

- New columns appear on fresh AND migrated databases.
- `claim_youtube_api_batch` leases, backoff, and expiry.
- `mark_youtube_api_succeeded` removes the channel from
  `claim_profile_batch('vidiq'|'socialblade', ...)` results.
- `mark_youtube_api_failed` leaves the scraper path intact.
- Channel/video snapshot inserts: same-day duplicate deduped, next-day
  snapshot stored.
- Collector unit tests with mocked HTTP: batching (50/request), quota
  accounting including failed requests, duration parsing, hidden
  subscriber/like counts, quotaExceeded shutdown.

## Quota budget and scaling ceilings (free tier: 10,000 units/day)

| Phase | Cost @ 2,731 channels | Scaling behavior |
|---|---:|---|
| A handle resolution | ~1,271 units one-time (cap 500/day) | 1 unit/handle, unbatchable |
| B channel snapshots | 55/day | 50 channels/unit -> 500k channels/day ceiling |
| C upload discovery | 2,731/day | 1 unit/channel — THE bottleneck |
| D video snapshots | ~2,200/day (~110k tracked videos) | 50 videos/unit |
| Total | ~5,000/day | 50% headroom at current scale |

Growth path: daily-everything caps around ~9,500 channels. Beyond that,
tier Phase C by `latest_video_at` (active channels daily, dormant every
3–7 days) -> ~30–60k channels on free quota. Phase B is effectively
unlimited. If more is ever needed: multiple GCP projects or paid quota
extension request.

## Future silver layer (out of scope now, informs bronze design)

- `silver_channel_growth`: per `channel_id`, window over
  `bronze_youtubeapi_channel_stats` ordered by `collected_at` ->
  subscribers_change / views_change over 7/30-day spans.
- `silver_video_performance`: per video — view velocity (views / age),
  engagement (comments/views, likes/views), and outlier signals:
  video views ÷ channel median recent views, views ÷ subscribers —
  to surface videos performing exceptionally for their channel size,
  driving topic selection.
