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

`workflow.py` runs YouTube feed collection once per hour and keeps the profile
manager running independently. The manager runs one worker per source; each
atomically leases up to 100 source-assigned channels, then immediately claims
the next batch after completing it. Each request remains rate-limited by the
collector's 15-second delay. Empty workers retry after 60 seconds; source
failures back off for one hour. SQLite uses WAL mode and a 30-second busy
timeout so concurrent collectors serialize writes safely.

Failures are retained in `collection_errors`, including Social Blade
Cloudflare blocks as HTTP 403 records. Set `SKIMMER_CYCLE_SECONDS` to override
the hourly YouTube interval for development.

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
identifier attempts as well as successful collection.
