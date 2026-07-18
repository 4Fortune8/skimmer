# Skimmer

Skimmer collects YouTube feed data and channel metrics into a local SQLite
database. Active collectors do not create CSV output.

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
| `bronze_vidiq_channel_stats` | vidIQ channel statistics |
| `bronze_socialblade_channel_stats` | Social Blade channel statistics |
| `bronze_socialblade_daily_channel_metrics` | Social Blade daily metrics |
| `profile_queue` | source-assigned profile collection work |

Feed records are retained for 14 days. The queue marks a profile for collection
when it has not succeeded in seven days, or when the feed contains a video less
than 14 days old. A source failure is retried once by the other source; failures
from both sources remain marked for review.

## Collectors

```bash
python youtubeSkimmer.py
python buildProfileManager.py
python buildIDProfile.py
python buildIDProfile-old.py
```

`youtubeSkimmer.py` refreshes `profile_queue` after it stores the feed.
`buildProfileManager.py` can also refresh and inspect the source-balanced
queue. The two profile collectors read only their assigned queue entries,
pause 15 seconds between channel requests, and mark a queue entry complete
only after a successful metric insert.

See [RUNBOOK.md](RUNBOOK.md) for setup, execution, and inspection procedures.

## Automated workflow

`workflow.py` runs the full collection cycle in order: YouTube feed, queue
refresh, vidIQ profiles, then Social Blade profiles. It waits 30 minutes after
the cycle completes before starting again. Set `SKIMMER_CYCLE_SECONDS` to
override that interval for development.
