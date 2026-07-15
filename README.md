# Skimmer

Skimmer collects YouTube feed data and channel-statistics snapshots into a local
SQLite database. Active collectors do not create CSV output.

## Database

The default database path is `data/skimmer.db`. Set `SKIMMER_DB_PATH` to use a
different location:

```bash
export SKIMMER_DB_PATH=/var/lib/skimmer/skimmer.db
```

The collectors create the database and tables automatically. All bronze tables
retain raw source values and include a UTC ISO-8601 `create_dt` column:

| Table | Source |
| --- | --- |
| `bronze_youtube_skimmed` | YouTube feed items |
| `bronze_vidiq_channel_stats` | vidIQ channel statistics |
| `bronze_socialblade_channel_stats` | Social Blade channel statistics |

`raw_record_json` preserves the complete record delivered by each collector.
`ingested_at` remains available for compatibility with databases created before
`create_dt` was added.

## Collectors

```bash
python youtubeSkimmer.py
python buildIDProfil.py
python buildIDProfile-old.py
```

`buildIDProfil.py` and `buildIDProfile-old.py` read channel IDs from
`bronze_youtube_skimmed` and only request channels that do not already have a
snapshot in their respective bronze table.

See [RUNBOOK.md](RUNBOOK.md) for setup, execution, and inspection procedures.
