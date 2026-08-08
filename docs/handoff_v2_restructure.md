# Handoff: bottom-up assessment and V2 restructure

**Date:** 2026-08-09
**For:** Fable
**From:** the state described in `ai_impact_domain_analysis.md` and
`topic_targeted_discovery_plan.md`

## What this is

The repo has grown by accretion through several changes of purpose. It works,
it has 333 passing tests, and it holds a real asset — but the structure no
longer matches what it is being asked to do. This is a request for a
**bottom-up assessment first, restructure second**. Do not take the framing
below as settled; it is what the previous pass believed, and part of the job is
checking whether it is right.

Read `docs/ai_impact_domain_analysis.md` before starting. The strategic
conclusion there — that four rounds of corpus analysis failed because the
corpus itself is biased, not the classifiers — should shape the restructure.
**Targeted collection is the constraint to design around, not better analysis
of an untargeted corpus.**

## The asset

| table | rows |
|---|---:|
| `bronze_youtubeapi_video_stats` | 968,414 |
| `bronze_youtube_skimmed` | 501,580 |
| `profile_queue` | 60,483 |
| `bronze_youtubeapi_channel_stats` | 54,345 |
| `bronze_vidiq_channel_stats` | 11,879 |
| `collection_errors` | 12,413 |

Single SQLite file at `data/skimmer.db`. This is the thing worth protecting;
everything else is replaceable.

## Host and resource envelope

- **OrangePi 5+ (RK3588, aarch64)**, 8 cores, 31 GB RAM, idling ~40°C.
- **Scheduling is entirely in-process.** No cron, no systemd timers. Cadence
  comes from `cycle_seconds()` / `youtube_api_cycle_seconds()` in
  `src/skimmer/services/workflow.py`, which loops and shells out via
  `run_module`. Whether that should remain in-process is an open question —
  it means no work survives a crash or reboot without external supervision.
- **YouTube Data API: 10,000 units/day, free tier, fully consumed.** Costs are
  not uniform: `search.list` is 100 units, list calls are 1. Reservations go
  through `reserve_youtube_api_quota()` in `storage/bronze.py`. A previous bug
  hardcoded 1 unit per call and under-reported search by 100x; the fix is in,
  but quota allocation *policy* (how the daily budget is split across
  discovery, refresh, and targeted seeding) has never been designed.
- Selenium/Firefox collectors hold persistent profiles under
  `.interest-firefox-profile` and `.youtube-firefox-profile`, guarded by
  `fcntl.flock`. Both are gitignored and **must never be committed** — they
  contain cookies and browsing history, and the repo is public.

## Known structural problems

Verified, with locations. Confirm before acting on them.

**1. Circular dependency between the package and `scripts/`.**
- `src/skimmer/domain/shorts.py:124` imports `scripts.RecomendationAnalysis.shorts_probe`
- `scripts/RecomendationAnalysis/exclusions.py:43` imports back from `skimmer.domain`
- `scripts/RecomendationAnalysis/language_frames.py:39` likewise

The `shorts.py` side uses a lazy in-function import specifically to dodge the
cycle. That is a workaround for a design flaw, not a fix.

**2. `scripts/` is a second application, not scripts.** `scripts/RecomendationAnalysis/`
is ~4,000 lines with its own `algorithms/` package, data access layer,
checkpointing, exclusions, and six test files. The collection application lives
in `src/skimmer/`; the analysis application lives outside it and is not
installed.

**3. Duplicated ownership.** `exclusions.py` exists in both trees (450 vs 241
lines), with the `scripts/` copy doing a `try: from skimmer.domain import
exclusions as installed / except ImportError` fallback. Same shape for
`language` / `language_frames`. Neither side clearly owns the logic.

**4. `storage/bronze.py` is a 1,858-line god module** with 18 importers,
holding schema creation, quota accounting, video stats, channel stats, topic
labels, format labels, liveness, and two queues. Every feature added recently
grew it further.

**5. Two parallel topic systems that have never been reconciled.**
- `scripts/RecomendationAnalysis/algorithms/topics.py` (1,046 lines) —
  *unsupervised*: TF-IDF against a background corpus, theme extraction, burst
  windows, `niche_saturation()` with channel HHI and effective-channel counts.
- `src/skimmer/domain/topic_taxonomy.py` and `ai_taxonomy.py` — *supervised*:
  hand-tuned STRONG/WEAK term lists with a precision-first design.

These are not competitors; they are the two layers described below. They have
never been named as such or wired together.

**6. `ai_taxonomy.py` duplicates the scoring engine of `topic_taxonomy.py`.**
Deliberate, to avoid destabilising a tested module under time pressure. It
should be unified into one classifier that takes a spec.

**7. A single channel uploaded an identical title 20+ times** and alone
accounted for a third of two topics before dedup was applied in the analysis
harness. **Nothing in the pipeline itself deduplicates repeated uploads.** Any
metric computed over raw rows is exposed to this.

## The intended architecture

Two layers, which the code already half-implements under the wrong names.

**Layer 1 — landscape capture.** Broad, lightly filtered collection. A sensor:
what exists, what is entering, what is moving. `ai_practical` and
`algorithms/topics.py` belong here. Precision bar is deliberately low.

**Layer 2 — topic peer analysis.** Distilled from layer 1. Takes a topic layer 1
surfaced and measures how **that topic performs across every subdomain it
touches, not just within AI**. "Voice cloning" gets profiled in music,
audiobooks, accessibility, fraud coverage, and voice-acting labour. The topic is
the unit of analysis; AI is one of its habitats.

Note that `algorithms/topics.py` already has `"by_category": False` in
`DEFAULT_PARAMS` and a `_CATEGORY_THEME_COLUMNS` path — per-category
decomposition exists and is switched off. Cross-domain profiling may be closer
to an extension than a new build. Verify this.

### Proposed layout — a proposal, not a spec

```
skimmer/
  storage/   <- owned by neither layer; bronze.py split by concern
  collect/   <- LAYER 1. Sensor. No knowledge of analysis.
  analyze/   <- LAYER 2. Reads storage. Never writes collection.
```

Dependency rule: `analyze → storage ← collect`, nothing sideways, enforced by a
test. This kills the cycle by construction.

**A sister repo was considered and rejected.** It would duplicate the
collectors, quota accounting, and bronze layer — the components most recently
debugged — and "siloed but able to access each other" across repo boundaries
degenerates into either a shared package (which is one repo with release
friction) or two processes writing one SQLite file. If layer 2 later needs to
ship separately, extracting a clean internal boundary is easy; splitting early
and merging back is not. Revisit if you disagree.

## What to assess, bottom-up

The requested scope, in the owner's words, is an assessment of:

1. **How skimming is catalogued** — the bronze schema, snapshot semantics, and
   whether repeated observations are handled coherently. Note that every
   analysis query currently opens with a `ROW_NUMBER() OVER (PARTITION BY ...)`
   to collapse snapshots, which suggests the storage model is fighting its
   consumers.
2. **Data flow structure** — bronze → labels → queues → analysis. There is no
   silver/gold layer; analysis reads bronze directly and recomputes
   collapsing every time.
3. **Scoring algorithm inefficiencies** — `algorithms/` holds breakout
   outliers, channel-relative, engagement, velocity, weight-class performance,
   topics. Assess correctness and cost, not just speed. The views-per-sub
   metric has a known small-denominator failure below ~1k subscribers.
4. **Task scheduling and compute usage on the host** — in-process loop vs.
   external supervision; what happens on crash or reboot; whether the 8 cores
   are used at all.
5. **Resource allocation** — the 10,000 unit/day budget has no allocation
   policy across discovery, refresh, and targeted seeding.
6. **Running both layers in tandem** — sequencing, shared storage, contention
   on a single SQLite writer.
7. **Whether this is approaching a meaningful product** — the honest state is
   that four analysis rounds have not yet established that any candidate domain
   is viable. Treat that as an open question the architecture must serve, not
   one it can assume away.

## Constraints

- The repo `4Fortune8/skimmer` is **public**. A `.env` containing a YouTube API
  key was committed in `04d473a` and remains in history; the key has been
  rotated. Never commit `.env` or the Firefox profile directories.
- Fish shell, not bash. Command grouping with `{ ...; } && ...` fails silently.
- The package must stay installed editable (`pip install -e . --no-deps`). A
  stale non-editable copy in site-packages previously shadowed the real source,
  meaning a whole module of exclusion rules had never run in production.
- 333 tests pass, 1 skipped. Keep them green or change them deliberately.

## Suggested first move

Assess before restructuring, and report findings before making large changes.
The highest-value early questions are probably: is the two-layer framing
correct, is the storage model the real source of the analysis complexity, and
does the quota budget support targeted seeding at a useful cadence.
