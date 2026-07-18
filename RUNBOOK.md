# Skimmer Runbook

## Prerequisites

Install the project’s collector dependencies in the Python environment:

```bash
python -m pip install selenium scrapy beautifulsoup4
```

The YouTube, vidIQ, and Social Blade collectors render pages with Firefox and
geckodriver. `youtubeSkimmer.py` automatically uses a configured
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
   python youtubeSkimmer.py
   ```

2. Refresh and inspect the profile queue. This is normally run automatically
   at the end of the YouTube collector:

   ```bash
   python buildProfileManager.py
   ```

3. Collect the source-assigned profile metrics. Each collector waits 15 seconds
   between channels. A failure automatically moves the channel to the other
   source; a second failure marks it for review rather than re-queueing it:

   ```bash
   python buildIDProfile.py
   python buildIDProfile-old.py
   ```

All collector output is written to SQLite. Profile metrics are deduplicated by
their normalized values. YouTube feed records are retained for 14 days.

Social Blade routes canonical YouTube channel IDs (`UC...`) as
`/youtube/channel/<id>` and handles as `/youtube/handle/<handle>`.
Run `SOCIALBLADE_HEADLESS=true python buildIDProfile-old.py` when a visible
browser is not required.

## Run at startup

Run the looping workflow manually with:

```bash
YOUTUBE_HEADLESS=true VIDIQ_HEADLESS=true SOCIALBLADE_HEADLESS=true \
  python workflow.py
```

It runs YouTube collection, refreshes the profile queue, collects the vidIQ and
Social Blade assignments, then waits 30 minutes before repeating. If YouTube
collection fails, the dependent profile steps are skipped for that cycle.

To start it automatically on this host:

```bash
sudo cp systemd/skimmer-workflow.service /etc/systemd/system/
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

## Troubleshooting

If Firefox cannot start, configure both `FIREFOX_BINARY_PATH` and
`GECKODRIVER_PATH` to executable, compatible binaries. Set
`YOUTUBE_HEADLESS=true` for non-interactive environments.

If a collector has no work, run `python buildProfileManager.py` and inspect
`profile_queue`; completed channels remain digested until they are eligible
again after seven days or a newly seen video.
