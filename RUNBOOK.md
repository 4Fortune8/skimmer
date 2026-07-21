# Skimmer Runbook

## Prerequisites

Install the project’s collector dependencies in the Python environment:

```bash
python -m pip install -e ".[analysis]"
```

The YouTube, vidIQ, and Social Blade collectors render pages with Firefox and
geckodriver. `skimmer-youtube` automatically uses a configured
`GECKODRIVER_PATH` or `FIREFOX_BINARY_PATH`; otherwise it uses an installed
browser/driver or downloads them into `.drivers/`. Configure those variables
for the profile collectors when their default `.drivers` paths are unsuitable.

## Configure storage

Set the SQLite location before running a collector when the default
`data/skimmer.db` is not appropriate:

```bash
export SKIMMER_DB_PATH=/var/lib/skimmer/skimmer.db
```

Create the parent directory with permissions for the account that runs the
collectors. Do not place the database in a directory served publicly.

## Run collection

1. Collect YouTube feed data:

   ```bash
   skimmer-youtube
   ```

2. Refresh and inspect the profile queue. This is normally run automatically
   at the end of the YouTube collector:

   ```bash
   skimmer-profile-manager
   ```

3. Collect the source-assigned profile metrics. Each collector waits 15 seconds
   between channels. A failure automatically moves the channel to the other
   source; a second failure marks it for review rather than re-queueing it:

   ```bash
   skimmer-vidiq
   skimmer-socialblade
   ```

All collector output is written to SQLite. Profile metrics are deduplicated by
their normalized values. YouTube feed records are retained for 14 days.

Social Blade routes canonical YouTube channel IDs (`UC...`) as
`/youtube/channel/<id>`. Run Social Blade headed under Xvfb
(`SOCIALBLADE_HEADLESS=false`) because its headless browser requests may be
blocked by Cloudflare.

## Run at startup

Run the looping workflow manually with:

```bash
YOUTUBE_HEADLESS=false VIDIQ_HEADLESS=true SOCIALBLADE_HEADLESS=false \
  xvfb-run -a -s "-screen 0 1920x1080x24" skimmer-workflow
```

It runs YouTube collection once per hour. The profile manager runs independently
with one worker each for vidIQ and Social Blade. Each worker atomically claims
100 source-assigned profiles, processes them with the collector's existing
15-second request delay, then immediately claims another batch.

Before Social Blade collects a profile, the manager's headless YouTube resolver
stores its canonical `UC...` value in `profile_queue.youtube_channel_id`. It
resolves 100 handles per batch and waits five seconds between lookups; adjust
that delay with `SKIMMER_CHANNEL_ID_RESOLUTION_DELAY_SECONDS`.

To start it automatically on this host:

```bash
sudo cp deploy/systemd/skimmer-workflow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now skimmer-workflow.service
```

Use `journalctl -u skimmer-workflow.service -f` to follow the workflow logs.

## Inspect data

Use the SQLite CLI to inspect recent rows:

```bash
sqlite3 "$SKIMMER_DB_PATH" \
  "SELECT observed_at, channel_id, video_name FROM bronze_youtube_skimmed ORDER BY id DESC LIMIT 10;"
```

When using the default location, replace `"$SKIMMER_DB_PATH"` with
`data/skimmer.db`.

Inspect queued work and channels requiring manual review:

```bash
sqlite3 "$SKIMMER_DB_PATH" "
SELECT assigned_source, digested, needs_review, COUNT(*)
FROM profile_queue
GROUP BY assigned_source, digested, needs_review;
"
```

Inspect unresolved Social Blade channel IDs:

```bash
sqlite3 "$SKIMMER_DB_PATH" "
SELECT channel_id, youtube_channel_id, youtube_channel_id_attempted_at
FROM profile_queue
WHERE assigned_source = 'socialblade' AND youtube_channel_id IS NULL
ORDER BY latest_video_at DESC
LIMIT 100;
"
```

## Troubleshooting

If Firefox cannot start, configure both `FIREFOX_BINARY_PATH` and
`GECKODRIVER_PATH` to executable, compatible binaries. For non-interactive
hosts where YouTube must still run in headed mode, use
`xvfb-run -a -s "-screen 0 1920x1080x24"` with `YOUTUBE_HEADLESS=false`.

If a collector has no work, run `skimmer-profile-manager` and inspect
`profile_queue`; completed channels remain digested until they are eligible
again after seven days or a newly seen video.

If Social Blade reports a Cloudflare block, the collector exits without marking
the queued channel as failed. The manager releases the queue and pauses
Social Blade for six hours before retrying; configure that cooldown with
`SOCIALBLADE_CLOUDFLARE_BACKOFF_SECONDS`. The collector uses a dedicated,
persistent headed Firefox profile set by `SOCIALBLADE_FIREFOX_PROFILE_DIR`;
keep that directory private to the service user and do not run concurrent
Social Blade collectors against it.

Profile workers wait 60 seconds when their source has no queued profiles. A
source-level failure waits one hour before retrying; set
`SKIMMER_EMPTY_QUEUE_SECONDS` or `SKIMMER_SOURCE_ERROR_BACKOFF_SECONDS` to tune
those delays. Inspect collection errors, including Social Blade Cloudflare 403
blocks, with:

```bash
sqlite3 "$SKIMMER_DB_PATH" "
SELECT occurred_at, source, status_code, error_type, channel_id, message
FROM collection_errors
ORDER BY id DESC
LIMIT 100;
"
```

Social Blade waits two minutes between channel starts and 20 seconds between
browser page requests, including identifier fallback attempts. Set
`SOCIALBLADE_CHANNEL_DELAY_SECONDS` or `SOCIALBLADE_PAGE_DELAY_SECONDS` only if
a different rate is required.

Each collector tries its preferred identifier first: vidIQ uses the handle, then
the canonical channel ID; Social Blade uses the canonical channel ID, then the
handle. A source fails only after both attempts fail. Review which identifier
works for each source with:

```bash
sqlite3 "$SKIMMER_DB_PATH" "
SELECT occurred_at, source, identifier_kind, outcome, failure_type, channel_key
FROM collection_attempts
ORDER BY id DESC
LIMIT 100;
"
```

The service pins only the hourly YouTube process to CPU 0 through
`SKIMMER_YOUTUBE_CPU=0`. Remove or change that setting if CPU 0 is not a
lower-performance core on the host.
