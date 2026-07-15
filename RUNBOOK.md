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

2. Collect vidIQ snapshots for YouTube channels without an existing vidIQ
   bronze record:

   ```bash
   python buildIDProfil.py
   ```

3. Collect Social Blade snapshots for YouTube channels without an existing
   Social Blade bronze record:

   ```bash
   python buildIDProfile-old.py
   ```

All collector output is written to SQLite. No CSV output is produced.

Social Blade routes canonical YouTube channel IDs (`UC...`) as
`/youtube/channel/<id>` and handles as `/youtube/handle/<handle>`.
Run `SOCIALBLADE_HEADLESS=true python buildIDProfile-old.py` when a visible
browser is not required.

## Inspect data

Use the SQLite CLI to inspect recent rows:

```bash
sqlite3 "$SKIMMER_DB_PATH" \
  "SELECT create_dt, channel_id, video_name FROM bronze_youtube_skimmed ORDER BY id DESC LIMIT 10;"
```

When using the default location, replace `"$SKIMMER_DB_PATH"` with
`data/skimmer.db`.

Check that every bronze row has a creation timestamp:

```bash
sqlite3 "$SKIMMER_DB_PATH" "
SELECT 'youtube' AS source, COUNT(*) AS missing_create_dt
FROM bronze_youtube_skimmed WHERE create_dt IS NULL
UNION ALL
SELECT 'vidiq', COUNT(*) FROM bronze_vidiq_channel_stats WHERE create_dt IS NULL
UNION ALL
SELECT 'socialblade', COUNT(*) FROM bronze_socialblade_channel_stats WHERE create_dt IS NULL;
"
```

## Troubleshooting

If Firefox cannot start, configure both `FIREFOX_BINARY_PATH` and
`GECKODRIVER_PATH` to executable, compatible binaries. Set
`YOUTUBE_HEADLESS=true` for non-interactive environments.

If a collector has no work, confirm `bronze_youtube_skimmed` contains channel
IDs and that the same IDs are not already present in the target bronze table.
