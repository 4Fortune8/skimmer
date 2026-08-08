# Implementation Plan: Topic-Targeted Discovery and Field Measurement

Status: planned, not approved. Prerequisite for
`layer2_evidence_analysis_proposal.md` — no layer 2 production code should be
written until this plan has produced its phase 3 output.

## Purpose

Existing discovery is topic-agnostic: `select_high_views_per_subscriber_seeds`
(`bronze.py:482`) plus recommendation crawling
(`collectors/youtube_recommendations.py`) surface outliers from anywhere in the
corpus. That answers *what overperforms*, not *what overperforms in a domain we
care about*.

This plan retargets discovery at the evidence/health/behaviour/education space
in order to answer four questions, in order:

1. **Does the audience exist at viable scale?** Is this niche generous or
   brutal relative to the corpus baseline?
2. **Can a new entrant break in?** Or is the space dominated by incumbents
   whose performance is a function of existing subscriber base?
3. **Who is doing it well?** Channels with *consistent* weight-class
   overperformance, not one-hit outliers.
4. **Why is it performing?** (deferred — phase 4) Title, thumbnail and hook
   structure of the winners.

Question 1 is the live risk. A prior attempt at creator-economy/meta content
failed. The working hypothesis is that the domain was wrong rather than the
format. That hypothesis is currently untested, and this plan is the test.

## Prerequisite: per-endpoint quota accounting

Status: **implemented.** `ENDPOINT_COSTS` and `endpoint_cost()` in
`collectors/youtube_api.py`; `reserve_youtube_api_quota` and
`release_youtube_api_quota` in `storage/bronze.py`; coverage in
`tests/test_youtube_api_quota.py`. The description below is retained as the
rationale.

**This must land before any search-based discovery.**

`_request_json` (`collectors/youtube_api.py:78`) reserves quota by calling
`reserve_youtube_api_quota_unit(budget, ...)`, and that function hardcodes
`units_used = units_used + 1` (`bronze.py:1041`). One request, one unit.

That is correct today *only by coincidence*: every endpoint currently in use
costs exactly 1 unit.

| Endpoint | Cost | In use today |
| --- | --- | --- |
| `channels.list` | 1 | yes |
| `playlistItems.list` | 1 | yes |
| `videos.list` | 1 | yes |
| `search.list` | **100** | no — required by this plan |
| `captions.download` | 200 | no |
| `videos.insert` | **1600** | no — required by layer 2 publishing |

With `search.list` added and accounting unchanged, 100 searches would report
100 units consumed while actually burning the entire 10,000/day allowance. The
local budget check never trips, so the collector takes hard 403 `quotaExceeded`
errors with no graceful stop and no accurate record of what was spent.

Changes:

- Generalise to `reserve_youtube_api_quota(units, budget, ...)`, replacing the
  hardcoded `+ 1` with `+ ?` and the `units_used < ?` guard with
  `units_used + ? <= ?`. Keep the `BEGIN IMMEDIATE` transaction — the atomicity
  is already correct and worth preserving.
- Add an `ENDPOINT_COSTS` map in `youtube_api.py`; `_request_json` looks up the
  endpoint and reserves the right amount.
- Keep a thin `reserve_youtube_api_quota_unit` wrapper delegating with
  `units=1` so existing callers and `tests/` are unaffected. There are only two
  non-test call sites (`youtube_api.py:22` import, `:79` use) and the existing
  assertions at `tests/test_bronze_store.py:622-628` continue to hold.

Note the fix is smaller than it appears: `record_youtube_api_quota_usage`
(`bronze.py:993`) already accepts a `units` argument. Only the *reservation*
path was shortcut to 1, so the recording side needs no change — the asymmetry
between the two is itself the evidence that `+ 1` was a placeholder.

Gotcha: reservation happens *before* the request. A failed request has already
spent local budget but not real quota. Acceptable at 1 unit; at 100 units per
search it is worth releasing the reservation on non-quota transport errors.

## Discovery strategies, cheapest first

Deliberately ordered so the expensive method is last and informed.

### Strategy 0 — classify the existing corpus (free)

`data/skimmer.db` is already 605 MB. Before spending a single unit, label what
is already there. This may answer question 1 outright.

- Seed term lists per topic (health, nutrition, psychology/behaviour,
  education, research-methods/meta-science).
- `category_id` is already stored and used in `leads.py` — categories 26
  (Howto & Style), 27 (Education) and 28 (Science & Technology) are a cheap
  prefilter.
- Add `topicDetails` to the parts requested by `_fetch_video_records`
  (`youtube_api.py:199`). Parts are **free** — `videos.list` is 1 unit
  regardless of how many parts are requested — so this is strictly additive.
  It returns Wikipedia-backed topic categories, a far better classifier signal
  than title regex alone.
- Reuse the marker-model pattern in `scripts/train_language_markers.py` and
  `data/models/` for term-based classification rather than inventing a new one.

#### Strategy 0 result (run 2026-08-08, `terms-v1`)

Implemented in `src/skimmer/domain/topic_taxonomy.py` and
`scripts/classify_corpus_topics.py`. Labels persisted to `video_topic_labels`.

| slice | videos | channels | med views | med v/sub |
| --- | ---: | ---: | ---: | ---: |
| corpus | 597,478 | 28,048 | 4.5k | 0.09 |
| domain (any) | 533 | 302 | 2.7k | 0.03 |
| health | 267 | 149 | 3.5k | 0.01 |
| behavior | 156 | 96 | 1.4k | 0.12 |
| education | 95 | 62 | 5.8k | 0.05 |
| meta_science | 15 | 15 | 822 | 0.00 |

**Domain share: 0.089 %.** The domain slice's median views-per-sub (0.03) is
roughly a third of the corpus baseline (0.09) — on this evidence the room is
harder than average, not more generous. `meta_science`, the finding engine
ranked strongest in the layer 2 proposal, has 15 videos.

Two methodology notes:

- Naive term matching scored ~20-30 % precision. The corpus is dominated by
  music and entertainment and domain vocabulary is overloaded (`study` → lofi
  playlists and Bible study, `school` → "old school" mixes, `anxiety` →
  relaxing music, `diet` → Diet Coke). The classifier is therefore
  precision-first: a strong term is necessary, weak terms and category only
  modulate confidence. Recall is knowingly low.
- The `<1k` subscriber band shows median v/sub 1.23 with 92 % "beating" corpus
  median. This is a small-denominator artifact, not entrant opportunity — a
  27-subscriber channel with 84 views scores 3.11. `weight_class_performance`
  avoids this by comparing within subscriber classes; the strategy 0 script
  applies a 1k floor to channel ranking for the same reason.

#### Strategy 0 re-run (`terms-v2`)

v1 warming surfaced political and entertainment content, so the terms were
re-tuned against per-term productivity measurements rather than intuition.
Labels for both versions are retained, so the runs stay comparable.

| slice | v1 | v2 |
| --- | ---: | ---: |
| domain (any) | 533 | **281** |
| health | 267 | 179 |
| behavior | 156 | 36 |
| education | 95 | 65 |
| meta_science | 15 | **1** |
| domain share | 0.089 % | **0.043 %** |
| seed pool (conf >= 3) | 206 videos / 119 channels | 145 / 77 |

**Terms removed, with the evidence:**

| term | matches | why |
| --- | ---: | --- |
| `vaccin\w*` | 66 | ~85 % political (Fauci, RFK, Aaron Rodgers). Vaccines are a politics topic on YouTube, not a health one |
| `testosterone` | 36 | masculinity culture-war content |
| `psycholog(y\|ical)` | 140 | the "Psychology of X" entertainment format: celebrities, footballers, killers |
| `dopamine` | 25 | music playlists ("Dopamine Reset 40Hz") and detox hustle content |
| `bootcamp` | 21 | military and fitness bootcamps |
| `tuition` (bare) | 39 | Indian TV drama, lofi tracks; now needs a cost or institution qualifier |
| `new study` | 12 | local-news and political headlines |
| `big five` | 2 | safari animals and 1960s bands |

`obesity` was measured and **kept**: its high News-category share is genuine
pharma and policy coverage, so the category proxy alone would have pruned it
wrongly.

**Terms that are fruitful:** `ozempic`, `blood sugar`, `blood pressure`,
`cholesterol`, `creatine`, `supplements`, `seed oils`, `insulin resistance`
(health); `college admissions` (0 % noise), `homeschool`, `higher education`,
`school funding` (education); `clinical psychologist`, `willpower`,
`attachment style`, `procrastinat\w*` (behaviour).

A design correction fell out of this: exclusions are now split into global
genre markers and per-topic ones. `psychology of` is noise for behaviour only,
and applying it globally discarded the education label from titles covering
both.

**Seed supply is concentrated in one topic.** Channels with 2+ labelled videos:

| topic | channels |
| --- | ---: |
| health | 29 |
| education | 12 |
| behavior | 4 |
| meta_science | 0 |

Repeat operators worth seeding from: Thomas DeLauer (4.03M), Ben Azadi (1.34M),
Leonid Kim MD (840k), Dr. John Meyers (125k), Bazgha Khalid MD (56k, mean
confidence 4.0). Several high-count channels are unusable as seeds despite
their counts -- "The Insight Room" (12 labels, ~0 subs, ~1k peak views) and
"Great Minds Advising" (8 labels, ~0 subs) will return thin or empty rails.

**The domain, as this corpus sees it, is really just health** -- specifically
doctor-explainer content. Education is thin, behaviour marginal, and
meta-science absent. That materially narrows what layer 2 could be about, and
it is the strongest signal yet that the finding engines ranked highest in the
layer 2 proposal have no audience to land in.

**This result does not settle Q1.** Discovery was seeded from high
views-per-sub outliers and recommendation crawling, both topic-agnostic, so the
corpus reflects what that seeding surfaced. A 0.089 % domain share is evidence
about *the sampling*, and only weak evidence about the niche's true size on
the platform. What it does establish: there is not enough domain data already
held to justify building layer 2, and strategies 1-2 are required before the
go/no-go gate can be called either way.

### Strategy 1 — seed channel expansion (cheap)

Hand-pick 30–50 known channels in the space. Per channel: `channels.list` (1) +
`playlistItems.list` (1 per 50 uploads) + `videos.list` (1 per 50 videos) ≈ 3–6
units. A full sweep of 50 channels costs roughly **200 units** — 2 % of daily
budget.

This is the primary method and should carry most of the volume. The existing
`collect_youtube_api` flow already does exactly this shape of work; it needs a
topic-seeded queue rather than the `profile_queue` population path.

### Strategy 2 — recommendation crawl from topic seeds (free)

`collectors/youtube_recommendations.py` is Selenium-based and costs **zero API
quota**. Currently seeded from high views-per-sub channels; reseed it from
strategy 0/1 topic videos.

This surfaces what YouTube itself associates with the topic, which is a
different and more useful signal than keyword matching — it reflects actual
routing behaviour. Given the stated scraping appetite, this should carry the
exploratory volume.

### Strategy 3 — `search.list` (expensive, last)

100 units per call, ≤50 results. Allocating 3,000 units/day (30 % of budget)
buys 30 searches ≈ 1,500 videos/day. Use only for gaps strategies 0–2 leave,
with query terms derived from what those strategies revealed rather than
guessed up front.

Hard-cap search spend separately from the global budget so it cannot starve the
existing collection pipeline.

## Schema additions

Follow the existing bronze conventions — append-only, `observed_at` timestamps,
no destructive updates.

- `topic_seed_terms` — `topic`, `term`, `weight`, `source`, `added_at`.
- `video_topic_labels` — `video_id`, `topic`, `method`
  (`category` | `topic_details` | `terms` | `recommendation`), `confidence`,
  `labeled_at`. Append-only so relabelling is a new row and classifier drift
  stays auditable.
- `topic_discovery_queue` — mirrors `profile_queue` claim/lease semantics for
  topic-seeded channels.

Gotcha, carried from `youtube_api_collector_plan.md`: `profile_queue`,
`collection_errors` and `collection_attempts` all have
`CHECK (source IN ('vidiq','socialblade'))`, and SQLite cannot alter CHECK
constraints. Do not attempt to add a topic source to those tables. Use new
tables, as the API collector already does.

## Analysis outputs

All of this reuses `scripts/RecomendationAnalysis/` with the frame restricted to
topic-labelled videos. The six algorithms are already entity-agnostic; only the
input frame changes.

**Question 1 — is the niche viable?**
Compare topic-subset distributions against corpus baselines: median
views-per-sub, median channel-relative multiple, comment rate. A niche whose
median views-per-sub sits below corpus median is a hard room, and that is a
finding worth having *before* building layer 2.

**Question 2 — can a new entrant break in?**
Run `breakout_outliers` and `weight_class_performance` restricted to the topic
frame. The diagnostic is the subscriber distribution of the top scorers: if
overperformance is concentrated above some subscriber threshold, the space is
incumbent-locked and layer 2 has no entry path regardless of content quality.

**Question 3 — who is doing it well?**
Aggregate to channel level. Rank by *consistency* of weight-class
overperformance — median score across a channel's videos, plus a count of how
many clear threshold — rather than by any single peak. One-hit outliers are
noise for this purpose; the target is repeatable operators.

Carry through the replicability signals discussed for lead quality: channel
age × upload cadence, description credits, whether an institution backs the
channel. A finding that the winners are all institutionally funded is as
decision-relevant as a finding that they are all solo operators.

**Question 4 — why? (phase 4, deferred)**
Transcripts via `yt-dlp --write-auto-sub --skip-download` at zero API quota.
Extract hook length, time-to-first-claim, cut cadence. Feeds the structural
spec generator in layer 2. `yt-dlp` is not currently installed.

## Phasing

1. ~~Quota cost table + `reserve_youtube_api_quota` generalisation. No new
   collection. Land with tests.~~ **Done.**
2. ~~Strategy 0 — classify existing corpus. Report question 1 from data already
   held. Zero quota.~~ **Done** — see the strategy 0 result above. Outcome:
   0.089 % domain share, underperforming the corpus baseline, but confounded by
   topic-agnostic seeding. Not sufficient to call the gate.
3. Strategies 1 and 2 — seed channels and recommendation reseeding. Report
   questions 1–3 properly. **This is the go/no-go gate for layer 2.**
4. Strategy 3 — targeted search for identified gaps.
5. Question 4 — transcript structural analysis.

## Decision gate

Phase 3 output determines whether `layer2_evidence_analysis_proposal.md`
proceeds, gets redirected to a different domain, or is dropped. Committing to
layer 2 production before that output exists repeats the error that produced
the earlier failed attempt: choosing a domain by intuition and discovering the
audience economics afterwards.

Note the asymmetry — a negative result here is cheap and useful. It costs a few
thousand quota units and redirects the domain choice before any production
pipeline exists.
