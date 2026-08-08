# Proposal: Layer 2 — Evidence Auditing as an Automated Content Pipeline

Status: proposal, not approved. Supersedes nothing. Depends on
`topic_targeted_discovery_plan.md` for the market-validation phase, which
must complete before any production code in this document is written.

## Summary

Layer 1 (this repo, `skimmer`) finds YouTube videos that overperformed their
weight class. It answers *what ideas have proven demand*.

Layer 2 is a separate repo (`forge`) that produces original video content in a
single repeatable format:

> Compare what was promised, pre-registered, or claimed against what the
> data actually shows.

Applied across health, behaviour/social science, and education. One method,
many topics, continuous supply of episodes.

## Why this format

Three constraints drove the choice:

1. **No rights exposure.** All source data is public scientific/government
   record. Nothing is reused footage, so YouTube's reused-content and
   inauthentic-content policies do not apply, and there is no Content ID
   surface. Layer 2 can be monetised normally.
2. **Automation ratio.** Collection, joining, anomaly detection, charting,
   scripting, assembly and render are mechanical. The non-automatable step
   is judging whether an anomaly is a *finding*. Target is 45–60 min of
   human time per video, against 2–3 weeks for artisanal production.
3. **Moat.** In gaming/entertainment domains the moat is data *access*. In
   evidence domains the data is free and abundant — the moat is *rigor*.
   The competition is largely conclusion-first content, so published
   methodology is an immediate and durable differentiator.

The architecture also transfers. `scripts/RecomendationAnalysis/` is already an
"entity, peer group, performance metric, outlier" engine. The scoring
algorithms are domain-agnostic; only the entity type changes.

## Definitions

- **Anomaly** — a statistical fact produced by the pipeline. Cheap, automatable,
  generated in bulk.
- **Finding** — an anomaly that contradicts a belief the audience actually holds
  *and* changes what they would do. Not automatable. This judgement is the
  product.

Conflating these is the primary failure mode. A pipeline optimised to emit
anomalies will produce fluent, confident, meaningless claims at volume.

## Division of labour

| Automated | Human |
| --- | --- |
| Collect, normalise, dedupe | Pick the domain |
| Join registries to publications | Pick the belief under test |
| Detect and rank discrepancies | **Judge anomaly vs finding** |
| Render charts | Write the hook (first 15s) |
| Draft script scaffold | Title + thumbnail |
| TTS, assemble, render, upload | Final QC pass |

Title, thumbnail and hook stay manual permanently — they determine CTR and
retention, which determine everything else.

## Finding engines

Each is a video generator, not a video. All sources are free and bulk
downloadable; none require the scraping budget.

1. **Outcome switching.** ClinicalTrials.gov publishes pre-registered primary
   outcomes; publications report outcomes. Divergence means the trial changed
   its target after seeing data. Documented, widespread, not systematised into
   content anywhere. Structurally an outlier-detection problem on joined
   records — closest fit to existing skills.
2. **Zombie citations.** Retraction Watch (public via Crossref) joined to
   citation graphs: retracted papers still being cited, sometimes still
   load-bearing in guidance.
3. **The n=12 problem.** Sample sizes and effect sizes behind widely repeated
   health and pop-psych claims. High belief density, trivially verifiable.
4. **Replication tracking.** OSF and replication databases against citation
   counts — unreplicated findings still cited in textbooks and talks.
5. **Education ROI.** College Scorecard program-level earnings and debt against
   the "major doesn't matter" and credential-value beliefs. High-intent
   audience making expensive decisions.

## Data sources

| Source | Access | Auth | Notes |
| --- | --- | --- | --- |
| ClinicalTrials.gov | API + bulk XML | none | Registered outcomes |
| Crossref | API | none | Includes Retraction Watch |
| OpenAlex | API + bulk snapshots | none | Citation graph |
| PubMed / MEDLINE | FTP baseline files | none | Abstracts, metadata |
| College Scorecard | bulk CSV | key (free) | Program-level outcomes |
| OSF | API | none | Preregistrations, replications |

## Rigor infrastructure

Not optional, and not a matter of willpower. The named risk is audience
capture: these domains attract viewers who want conclusions, not methods, and
selecting findings by what the audience cheers for reproduces exactly the
content this is meant to replace, with better charts.

Structural defences, all of which are repo features:

- **Pre-registration.** The question and its falsification condition are
  committed, timestamped, *before* the query runs. Published in the video.
- **Null results ship.** Videos where the belief held up. They underperform.
  They are the proof the process is real and the reason the positive findings
  are believable.
- **Code and data public.** Being checkable is the product, independent of
  whether anyone checks.

This infrastructure is also the precondition for attaching personal identity to
the channel downstream, which is a stated goal.

## Pipeline

```
forge/
  ingest/     # append-only collectors, snapshot per run
  detect/     # discrepancy + anomaly detection, ranked candidates
  spec/       # candidate -> angle, claims, structural target
  script/     # spec -> narration + on-screen beat sheet (JSON)
  assets/     # claims -> matplotlib frame sequences
  voice/      # TTS -> per-beat audio
  assemble/   # beat sheet + assets + audio -> ffmpeg filter_complex
  gate/       # human checkpoint
  publish/    # upload + metadata
  feedback/   # own-video performance -> back into layer 1
```

`feedback/` is the differentiator. Most creators' feedback loop is n=1 and
unusable: post, wait, look at views, guess — with no baseline to separate a bad
idea from algorithmic noise. Scoring own videos against the weight-class
baselines already computed in `leads.py` yields "beat expectation for a channel
of this size" after roughly one video instead of thirty.

## Platform stack

Target hardware is the RK3588 host this repo runs on.

- **TTS** — Kokoro-82M. Voice quality is a material retention factor.
- **Charts** — matplotlib frame sequences into ffmpeg. Reuses existing skills;
  Manim and Remotion both add more friction than value on ARM.
- **Assembly** — ffmpeg `concat` + `filter_complex` driven from beat-sheet JSON.
  Not MoviePy (slow and memory-hungry on ARM).
- **Encode** — `libx264 -preset slow` for finals. `h264_v4l2m2m` is present and
  faster but visibly worse; encode time is irrelevant at this volume.
- **Transcripts** — `yt-dlp --write-auto-sub --skip-download`, zero API quota.
  Not currently installed.

## Quota interaction with layer 1

`videos.insert` costs **1600 units** — 16 % of the daily 10k budget per upload.
Layer 2 publishing must route through the same
`reserve_youtube_api_quota_unit` accounting so an upload cannot silently
starve layer 1 collection. See the cost-table prerequisite in
`topic_targeted_discovery_plan.md`; that fix is shared.

Otherwise layer 2 is strictly read-only against `data/skimmer.db` and consumes
no discovery quota.

## Phasing

- **Phase 1 (videos 1–5)** — single-claim audits against current state.
  Shallow findings; establishes format.
- **Phase 2 (6–15)** — longitudinal. Collection started at day zero means
  registry-vs-publication *rates and trends* nobody else recorded. The moat
  forms here.
- **Phase 3 (15+)** — prediction and validation on camera. Researcher
  engagement. Citability.
- **Phase 4** — the dataset becomes the product (searchable evidence-quality
  index). Content has already done the selling, which avoids the cold-sales
  problem.

## Build order, with a hard stop

1. Append-only collectors for ClinicalTrials.gov, Crossref, OpenAlex.
2. Discrepancy detection producing ranked candidates.
3. Spec generator.

**Stop.** Hand-produce one video from a generated spec before writing any
render, TTS or assembly code.

The likeliest failure is that specs generate angles that sound plausible and
contain no real insight. That costs a day to discover at step 3 and three weeks
to discover after building the full pipeline. Steps 4–9 are earned only by a
spec that produced something that performed.

## Open questions

- Which finding engine leads. Deferred to the market-validation output.
- Whether the audience for this content exists at viable scale on YouTube.
  This is the entire purpose of `topic_targeted_discovery_plan.md` and is
  currently **unvalidated**. A prior attempt at creator-economy/meta content
  failed; the working hypothesis is that the domain was wrong rather than the
  format, and that hypothesis is untested.

## Immediate action

Start the append-only collectors now, before the domain decision. Historical
data cannot be backfilled: deciding in three months and collecting from then
puts phase 1 three months out, whereas dumb collectors running this week mean
whichever engine is chosen is already at phase 2. Collector quality does not
matter — raw JSON, snapshot per run, cron'd. The moat is time-in-market on
collection, not code.
