# Implementation Plan: Area-of-Interest Recommendation Crawl

Status: planned, not approved. Implements strategy 2 of
`docs/topic_targeted_discovery_plan.md`. Zero YouTube Data API quota at the
discovery step (see "Quota interaction" for the downstream cost, which is not
zero).

## What does not change

The existing feed skimmer works and is explicitly out of scope:

- `collectors/youtube.py` — untouched.
- `collectors/youtube_recommendations.py` — `HighViewsPerSubscriberSeedSelector`,
  `extract_recommended_records` and `collect_recommendation_recovery` keep their
  current behaviour. The recommendation *recovery* path inside the feed run
  (triggered when the home feed comes up short) stays exactly as it is.
- Its Chrome profile — see "Profile isolation", which is the main risk in this
  plan.

Everything below is additive: a second selector, a second job, new tables, and
one new step in the orchestrator.

## Two scans

**Scan A — feed skimmer.** Unchanged. Runs on `SKIMMER_FEED_CYCLE_SECONDS`
(default 15 min). Topic-agnostic, broad, populates `bronze_youtube_skimmed`.

**Scan B — interest crawl.** New. Runs *after* scan A completes, on its own
much slower cycle. Picks ~10 on-topic videos already collected, opens each,
harvests the recommendation rail, classifies what comes back, and promotes
on-topic results into an interest queue.

Sequencing matters for a mechanical reason beyond tidiness: both drive
Selenium/Firefox, and the RK3588 host cannot afford two concurrent browsers.
`workflow.run_module` is already sequential and returns a success bool, so scan
B slots in as another `run_module` call gated on scan A's return.

Scan B must *not* run every feed cycle. Proposed
`SKIMMER_INTEREST_CYCLE_SECONDS`, default 6 h, tracked the same way
`youtube_api_cycle_seconds` already gates the daily API job.

## Component 1: `InterestSeedSelector`

New selector in a new module (`collectors/interest_seeds.py`), implementing the
same informal interface as the existing selector — a `name` attribute and
`select(database_path=None) -> [DiscoverySeed]` — so `discovery_seed_history`
records it under its own `selector` value and the two scans stay separable in
analysis.

Selection criteria differ from the existing selector, deliberately:

| Criterion | Rationale |
| --- | --- |
| Has a `video_topic_labels` row at the current classifier version | On-topic by construction |
| `confidence >= 3` | Strong term plus corroboration; see the precision notes in the strategy 0 result |
| `views >= 10_000` | **Not** a quality filter. Low-view videos have thin co-view data, so their recommendation rail is generic or empty. This is about rail quality, not video quality |
| Channel not seeded in `seed_cooldown_days` | Prevents collapse into one cluster |
| Max 1 seed per channel per run | Same |
| Prefer channels with no prior interest-crawl visit | Pushes the frontier outward instead of re-walking |

The existing exclusion rules (`RecomendationAnalysis/exclusions.py`, loaded via
`exclusions.load()`) should apply here too — they encode "the operator does not
want more of this", which is orthogonal to topic.

Seed count is 10 per run, matching the request. That is a parameter
(`SKIMMER_INTEREST_SEED_LIMIT`), not a constant.

## Component 2: the crawl job

New module `collectors/interest_crawl.py`, entry point
`skimmer-interest-crawl`.

Per run:

1. Select seeds via `InterestSeedSelector`.
2. For each seed, open the watch page and harvest the rail with the existing
   `extract_recommended_records(driver)`. Reuse it as-is; it already handles
   both YouTube layouts and that is exactly the brittle part not worth
   duplicating.
3. Classify every returned title with `domain.topic_taxonomy.classify_title`.
4. Write **all** results to `interest_crawl_results` — on-topic and off-topic
   alike. The off-topic rows are not waste; they are the drift measurement, and
   discarding them would make yield rate uncomputable.
5. Promote on-topic results into `interest_queue`.
6. Record one `discovery_seed_history` row per seed, with
   `discovered_channels` populated as the existing path does.

Depth is capped. A result promoted to the queue may be selected as a seed on a
later run, which is what makes this a frontier crawl rather than a one-shot
sweep, but `depth` increments each hop and seeds above
`SKIMMER_INTEREST_MAX_DEPTH` (default 3) are never selected. Without a cap a
focused crawler walks off into general entertainment and never returns.

## Component 3: schema

Append-only, `observed_at`-style timestamps, consistent with the bronze
convention.

```sql
CREATE TABLE interest_crawl_results (
    id INTEGER PRIMARY KEY,
    observed_at TEXT NOT NULL,
    seed_video_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    channel_id TEXT,
    title TEXT,
    rail_position INTEGER,
    matched_topic TEXT,          -- NULL when off-topic
    confidence INTEGER,
    depth INTEGER NOT NULL,
    classifier_version TEXT NOT NULL
);

CREATE TABLE interest_queue (
    channel_key TEXT PRIMARY KEY,
    channel_id TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    best_confidence INTEGER NOT NULL,
    hit_count INTEGER NOT NULL,      -- times reached from distinct seeds
    discovered_via TEXT,             -- seed video id
    depth INTEGER NOT NULL,
    status TEXT NOT NULL,            -- pending | promoted | rejected
    promoted_at TEXT
);
```

`hit_count` is the ranking signal that matters. A channel reached from one seed
is weak evidence; a channel reached independently from five different on-topic
seeds is a genuine cluster member, and that is the "area of interest" the
request is asking for. Rank the queue by `hit_count` first, `best_confidence`
second.

**Gotcha carried forward:** do not attempt to add a source value to
`profile_queue.assigned_source`, `collection_errors.source` or
`collection_attempts.source`. All three have
`CHECK (source IN ('vidiq','socialblade'))` and SQLite cannot alter CHECK
constraints. Promotion from `interest_queue` into the existing collection path
must go through the same orthogonal-column approach the API collector already
uses.

## Profile isolation — the main risk

YouTube's recommendation rail is personalised. Which profile scan B drives
changes what it returns, and this is a design decision, not an implementation
detail:

- **Logged-out / fresh profile per run.** Reproducible and unbiased, but the
  rail regresses toward general popularity — which is precisely the
  entertainment mass that strategy 0 showed drowns this domain. Low yield
  expected.
- **Persistent warmed profile.** Watch history accumulates on-topic, the rail
  tunes toward the niche, and yield rises substantially. The cost is that the
  profile becomes a confounder: results reflect "what YouTube recommends *to
  this profile*", not "what YouTube associates with this topic" in general.

Recommendation: **a dedicated persistent profile for scan B.** The niche is
thin enough that an unwarmed rail will likely return almost nothing, and the
confounder is acceptable because the goal here is finding channels, not
measuring YouTube's global topology. Log the profile identity alongside results
so the confound stays visible in analysis.

**Isolation is free, as it turns out.** `youtube.py:create_driver()` sets no
profile at all, so Selenium launches Firefox with a fresh anonymous temporary
profile on every run — scan A is already fully logged-out and unwarmed, and
there is no persistent profile to accidentally share. Scan B therefore *adds*
profile persistence rather than modifying anything, which is the safest
possible shape for this change.

Follow the pattern already established in `socialblade.py:create_driver()`:
a directory under `PROJECT_ROOT` overridable by env var, `mkdir(mode=0o700)`,
`options.add_argument("-profile")`, plus the `fcntl.flock` non-blocking lock
from `socialblade_profile_lock()`. Firefox refuses to share a profile between
concurrent processes, so the lock is required, not decorative.

Caveat on warming strength: a logged-out profile personalises through cookies
only, which is real but weak. A logged-in profile would tune far harder. That
involves storing credentials and is deliberately out of scope here — noted so
the ceiling on logged-out warming is not mistaken for a bug.

### Warming implementation notes (`collectors/interest_profile.py`)

Three things were found by testing rather than reasoning, and all three would
have silently degraded the crawl:

- **Firefox blocks autoplay.** A plain `driver.get()` on a watch page leaves
  the player at `paused: true, currentTime: 0` — a page view with no watch
  time. Since watch time is what drives personalisation, warming without
  `media.autoplay.default = 0` plus a muted `video.play()` is a no-op that
  looks like it worked. `warm_profile` now reports elapsed playback per video
  and warns when any video failed to play.
- **Duration no longer identifies Shorts.** Eligibility extends to three
  minutes, so videos of 78 s and 179 s tagged `#shorts` clear a 60 s cutoff.
  Shorts are served from a different recommendation surface than the watch-page
  rail, so warming on them tunes the wrong thing. Selection filters on the tag
  as well as duration. *This also affects `EXCLUDE_SHORTS` /
  `SHORTS_MAX_DURATION_SECONDS` in the leads notebook, which uses the 60 s
  cutoff alone.*
- **Warming must be balanced across topics and deduped by channel and title.**
  Health dominates the labelled pool, and reposted titles recur; unbalanced
  warming tunes the profile into one topic or one channel rather than the
  domain.

## Drift and yield

The metric to watch is **yield rate**: on-topic results ÷ total results, per
seed and per depth.

Expected shape is decay with depth. Two failure signatures:

- **Immediate collapse** (yield near zero at depth 1). YouTube does not cluster
  this content — the rail from an on-topic video leads straight back to general
  entertainment. That is a finding, not a bug, and it is a strong negative
  signal for the domain: it means the audience does not exist as a navigable
  cluster, which is worse news than the niche merely being small.
- **No decay** (yield flat and high across depths). Almost certainly the
  classifier matching its own vocabulary in a tight content farm cluster.
  Sample the results by hand before believing it.

Report yield per run. It is the primary output of this scan, ahead of the
channel list itself.

## Quota interaction

Discovery is free — Selenium only, no API calls. Enrichment is not.

Every promoted channel eventually needs `channels.list` + `playlistItems.list` +
`videos.list` to become usable, roughly 3-6 units each. Ten seeds × ~20 rail
items × even a 20 % yield is ~40 new channels per run; at a 6 h cycle that is
~160 channels/day, or ~500-1000 units/day of downstream API cost against a
10,000 budget already described as maxed.

So `interest_queue` needs an explicit promotion budget
(`SKIMMER_INTEREST_PROMOTION_LIMIT`, default 25/day), draining highest
`hit_count` first. Without it this plan quietly starves the existing collector —
the same class of failure the endpoint cost table was written to prevent.

## Build order

1. ~~Schema + `interest_queue` / `interest_crawl_results` accessors in
   `bronze.py`, with tests. No crawling.~~ **Done.** Accessors:
   `insert_interest_crawl_results`, `refresh_interest_queue`,
   `get_interest_queue`, `mark_interest_queue_status`, `interest_crawl_yield`.
   Tests in `tests/test_interest_queue.py`.

   Design change from the sketch above: `hit_count` and `seed_count` are
   **derived**, not incrementally maintained. `refresh_interest_queue` rebuilds
   the queue from `interest_crawl_results` the way `refresh_profile_queue`
   rebuilds from the feed, so the counters cannot drift from the evidence and
   re-running a crawl cannot double-count. `status` and `promoted_at` survive
   the rebuild, so an already-promoted channel does not revert to pending when
   a later run sees it again.
2. `InterestSeedSelector`, tested against the existing DB. Verify the 10 seeds
   it picks are actually on-topic by hand before running any browser.
3. `interest_crawl.py` with a dry-run mode that selects seeds and reports what
   it *would* open.
4. Live crawl, one run, seeds capped at 3. Hand-check every returned rail.
5. Yield reporting.
6. Promotion into the collection path, with the budget cap.
7. `workflow.py` integration on `SKIMMER_INTEREST_CYCLE_SECONDS`.

Step 4 is the real gate. If a hand-checked rail from a strong on-topic seed
returns nothing on-topic, stop and report — steps 5-7 are pointless and the
strategy 2 result is already in.

## Open decisions

1. **Profile warming** — recommendation above is a persistent dedicated
   profile. Needs a decision before step 4, since it changes what step 4
   measures.
2. ~~**Seed pool floor.**~~ **Measured 2026-08-08.** See below; the pool is
   thin enough to change the plan.

## Measured seed pool (`terms-v1`)

| threshold | videos | channels |
| --- | ---: | ---: |
| `confidence >= 3` | 206 | 119 |
| `confidence >= 3`, views >= 1k | 106 | 65 |
| `confidence >= 3`, views >= 10k | **70** | **42** |
| `confidence >= 3`, views >= 50k | 31 | 23 |

Per topic at `confidence >= 3`: health 80 (49 channels), behavior 50 (29),
education 30 (24), meta_science **12 (12)**.

Two consequences.

**The initial pool is ~4 runs deep.** At the planned thresholds, 42 distinct
channels with a 1-seed-per-channel-per-run rule and 10 seeds per run exhausts
the cooldown pool in roughly four runs — about a day at a 6 h cycle. The crawl
does not stall permanently, because promoted results become future seeds, but
it only *sustains* itself if each run replaces what it consumes.

**Sustainability threshold.** Ten seeds × ~20 rail items = ~200 results per
run, of which the run must yield more than 10 new qualifying seed candidates.
So the crawl is self-sustaining iff:

> yield rate > ~5 %

Below that it decays toward the initial pool and stops. This is the same yield
metric defined above, now with a specific number attached, and it makes step 4
a genuine go/no-go rather than a vibe check: measure yield on three seeds, and
if it lands under 5 % the frontier will not sustain and strategy 1 must supply
seeds instead.

**meta_science has effectively no seed pool** — 12 videos across 12 channels,
one run's worth. The topic ranked strongest in the layer 2 proposal cannot be
crawled from what is currently held, and depends entirely on strategy 1.

Adjustment to the plan: drop the initial view floor to 1k (65 channels, ~6
runs of runway) for the first live runs, and lengthen the cycle to 24 h rather
than 6 h so cooldown has time to expire while yield is still unknown.
