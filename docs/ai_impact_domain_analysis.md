# AI impact domain — Strategy 0 results

**Date:** 2026-08-09
**Classifier:** `skimmer.domain.ai_taxonomy`, version `ai-v1`
**Corpus:** 712,515 distinct videos (latest snapshot per video), zero API quota spent.

## What was measured

The domain is **how AI is changing work, creative careers, independent
building, public attitudes, and everyday practice** — not AI as a news beat.

Deliberately excluded by policy, not by noise filtering:

- market and bubble commentary (stock, valuation, recession, crash)
- benchmark and leaderboard coverage
- model-release news and head-to-head comparisons

These decay in weeks. Excluding them is what leaves a longitudinal remainder,
which is the whole premise: a data series appreciates as it lengthens, whereas
a release-news back catalogue is worthless within a month.

### Structure

Classification is **gated**, unlike `topic_taxonomy`. There, strong terms are
self-identifying — "ozempic" is about health regardless of context. Here they
are not: "job market", "illustrator" and "backlash" are only in scope when AI
is the causal variable. So a title must match `AI_MARKER` **and** a topic's
strong terms. The gate is what makes broad topic vocabulary safe to use.

Bare "AI" is matched case-sensitively. Lowercase "ai" is a frequent substring
in non-English titles and given names; requiring capitals costs a little recall
and removes a large false-positive class.

| layer | topics | precision bar |
|---|---|---|
| 1 (sensor) | `ai_practical` | lower — the job is noticing what enters the landscape |
| 2 (instrument) | `ai_labor`, `ai_creative`, `ai_solo_dev`, `ai_sentiment` | high — these carry the impact argument |

## Results

Corpus baseline median views/sub: **0.093**.

| topic | videos | channels | med views | med v/sub |
|---|---:|---:|---:|---:|
| `ai_practical` | 101 | 61 | 4,865 | 0.029 |
| `ai_labor` | 78 | 55 | 7,008 | 0.009 |
| `ai_sentiment` | 32 | 30 | 7,642 | 0.016 |
| `ai_creative` | 20 | 15 | 2,948 | 0.048 |
| `ai_solo_dev` | 16 | 12 | 1,834 | 0.024 |
| **all topics** | **240** | — | — | — |
| **layer 2 only** | **143** | **100** | — | — |

**Domain share: 240 / 712,515 = 0.034%.** Smaller than the
health/behaviour/education domain (281, 0.043%).

### Three findings

**1. Every topic underperforms the corpus baseline.** The best is
`ai_creative` at 0.048, roughly half the 0.093 median. `ai_labor` is 10x below
it. Whatever is driving views in this corpus, it is not AI impact content.

**2. Nobody is running this as a beat.** Layer 2 is 143 videos spread across
**100 channels — 1.4 videos per channel**. The most prolific holder is
Intellipaat, a training company, at 9. The rest are one-off segments from news
organisations (PBS, Vox, Bloomberg, CBS, Fox Business, Business Insider) and
generalists dipping in once.

This cuts both ways and the ambiguity is real:

- *Open field* — there is no incumbent to displace.
- *Failed format* — 100 channels tried it once and did not continue, which is
  what it looks like when something does not retain.

The performance data leans toward the second reading.

**3. Small channels are absent entirely.** `ai_creative`, `ai_solo_dev` and
`ai_sentiment` have **zero** videos in the 1k–10k subscriber band. In
`ai_labor`, the >1M band holds 39 of 78 videos and only 5% of them beat the
corpus median. This is the opposite of the pattern that makes a niche
enterable.

### The uncomfortable comparison

A crude earlier pass measured AI **risk/bubble** content — the slice explicitly
ruled out of scope:

| slice | videos | med v/sub | 10k–100k band: % beating corpus |
|---|---:|---:|---:|
| AI risk / bubble | 72 | **0.129** | **61%** |
| AI impact (this taxonomy) | 240 | 0.009–0.048 | 18–40% |

The one AI slice showing genuine small-channel overperformance is the one
deemed not worth making. That is worth sitting with rather than explaining
away.

## Why this result cannot be trusted as final

**The corpus cannot answer this question, and this is the fourth time it has
failed to.**

| attempt | result |
|---|---|
| health/behaviour/education (terms-v1) | 533 videos, 0.089% |
| same (terms-v2) | 281 videos, 0.043% |
| `meta_science` | **1 video in 655,615** |
| AI impact (ai-v1) | 240 videos, 0.034% |

In the health round, a channel-verification pass found that the corpus held the
mega-channels (Doctor Mike 15M, Veritasium 21.1M, SciShow 8.4M) and **missed
every mid-size specialist** — Physionic (386k), Nutrition Made Simple (377k),
Healthcare Triage (448k), and Pete Judo (115k), who makes scientific-fraud and
replication-crisis videos, precisely the format `meta_science` reported as one
video in 655,615.

The same signature appears here: the layer-2 channel list is news organisations
and generalists. The mid-size specialists who would actually own this beat are
not in the sample.

The corpus was built by topic-agnostic recommendation crawling. It is biased
toward what that crawl reaches, which is large general-interest channels. **No
amount of taxonomy refinement fixes a sampling problem.**

## Conclusion

Strategy 0 is exhausted. It has now been run four times across two domains and
returned the same unresolvable ambiguity each time, because the binding
constraint is the corpus, not the classifier.

The next action is **Strategy 1: targeted seeding** — collect from hand-picked
channels known to work the domain, and measure whether the niche is genuinely
thin or merely unsampled. Estimated cost ~100–200 units against a 10,000/day
budget.

Until that runs, the honest position is: *AI impact content is not visibly
viable in the data we hold, and we do not yet know whether that is a fact about
YouTube or a fact about our crawler.*
