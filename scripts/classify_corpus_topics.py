"""Strategy 0: label the existing corpus by topic and report domain viability.

Zero API quota -- reads only `data/skimmer.db`. See
`docs/topic_targeted_discovery_plan.md`.

Answers, as far as already-held data allows:
  Q1  Is the domain represented at viable scale, and is the room generous
      or brutal relative to the corpus baseline?
  Q2  Can a new entrant break in, or is overperformance incumbent-locked?
  Q3  Which channels do it well, by consistency rather than single peaks?

Usage:
    python scripts/classify_corpus_topics.py [--db PATH] [--write] [--limit N]
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from skimmer.domain.topic_taxonomy import TOPICS, classify_title  # noqa: E402
from skimmer.storage.bronze import (  # noqa: E402
    clear_video_topic_labels,
    insert_video_topic_labels,
)

CLASSIFIER_VERSION = "terms-v1"

# Latest snapshot per video, joined to the latest channel snapshot. Both tables
# hold repeated observations, so ROW_NUMBER collapses them before any ratio is
# computed -- otherwise heavily re-sampled videos would be counted many times.
LATEST_VIDEOS_SQL = """
WITH latest_video AS (
    SELECT video_id, channel_id, title, category_id, views, comments,
           published_at, duration_seconds,
           ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY collected_at DESC) rn
    FROM bronze_youtubeapi_video_stats
),
latest_channel AS (
    SELECT channel_id, channel_name, subscribers, video_count,
           ROW_NUMBER() OVER (PARTITION BY channel_id ORDER BY collected_at DESC) rn
    FROM bronze_youtubeapi_channel_stats
)
SELECT v.video_id, v.channel_id, v.title, v.category_id, v.views, v.comments,
       v.duration_seconds, c.channel_name, c.subscribers
FROM latest_video v
LEFT JOIN latest_channel c ON c.channel_id = v.channel_id AND c.rn = 1
WHERE v.rn = 1 AND v.title IS NOT NULL
"""


def load_videos(connection, limit=None):
    sql = LATEST_VIDEOS_SQL + (f" LIMIT {int(limit)}" if limit else "")
    return connection.execute(sql).fetchall()


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def views_per_sub(views, subscribers):
    views = _number(views)
    subscribers = _number(subscribers)
    if views is None or not subscribers or subscribers < 1:
        return None
    return views / subscribers


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def summarise(rows, label):
    """Distribution summary for one slice of the corpus."""
    views = [v for v in (_number(r["views"]) for r in rows) if v is not None]
    vps = [
        x
        for x in (views_per_sub(r["views"], r["subscribers"]) for r in rows)
        if x is not None
    ]
    subs = [
        s for s in (_number(r["subscribers"]) for r in rows) if s is not None and s > 0
    ]
    return {
        "label": label,
        "videos": len(rows),
        "channels": len({r["channel_id"] for r in rows if r["channel_id"]}),
        "median_views": statistics.median(views) if views else None,
        "p90_views": _percentile(views, 0.90),
        "median_vps": statistics.median(vps) if vps else None,
        "p90_vps": _percentile(vps, 0.90),
        "median_subs": statistics.median(subs) if subs else None,
    }


def _fmt(value):
    if value is None:
        return "-"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.2f}"


def print_summary_table(summaries):
    header = (
        f"{'slice':<16}{'videos':>9}{'chans':>8}{'med views':>12}"
        f"{'p90 views':>12}{'med v/sub':>12}{'p90 v/sub':>12}{'med subs':>11}"
    )
    print(header)
    print("-" * len(header))
    for s in summaries:
        print(
            f"{s['label']:<16}{s['videos']:>9}{s['channels']:>8}"
            f"{_fmt(s['median_views']):>12}{_fmt(s['p90_views']):>12}"
            f"{_fmt(s['median_vps']):>12}{_fmt(s['p90_vps']):>12}"
            f"{_fmt(s['median_subs']):>11}"
        )


def print_entrant_analysis(rows, corpus_median_vps):
    """Q2: is domain overperformance available to small channels?

    The <1k band is reported but must not be read as opportunity: views-per-sub
    has a small denominator there, so a 27-subscriber channel with 84 views
    scores 3.11 and outranks everything. `weight_class_performance` in the main
    pipeline avoids this by comparing within subscriber classes. Treat 1k as
    the first band where the ratio means anything.
    """
    bands = [
        ("<1k (noisy)", 0, 1_000),
        ("1k-10k", 1_000, 10_000),
        ("10k-100k", 10_000, 100_000),
        ("100k-1M", 100_000, 1_000_000),
        (">1M", 1_000_000, float("inf")),
    ]
    print(f"\n{'sub band':<12}{'videos':>9}{'med v/sub':>12}{'% beating corpus med':>24}")
    print("-" * 57)
    for name, low, high in bands:
        band = [
            r
            for r in rows
            if (_number(r["subscribers"]) or 0) >= low
            and (_number(r["subscribers"]) or 0) < high
        ]
        vps = [
            x
            for x in (views_per_sub(r["views"], r["subscribers"]) for r in band)
            if x is not None
        ]
        if not vps:
            print(f"{name:<12}{len(band):>9}{'-':>12}{'-':>24}")
            continue
        beating = sum(1 for x in vps if x > corpus_median_vps) / len(vps) * 100
        print(
            f"{name:<12}{len(band):>9}{_fmt(statistics.median(vps)):>12}{beating:>23.1f}%"
        )


def print_top_channels(rows, min_videos=3, limit=15, min_subs=1_000):
    """Q3: rank by consistency of per-video reach, not by a single peak.

    `min_subs` excludes the small-denominator noise described in
    print_entrant_analysis; without it the ranking is entirely sub-100
    subscriber channels with two-digit view counts.
    """
    by_channel = {}
    for r in rows:
        if not r["channel_id"]:
            continue
        if (_number(r["subscribers"]) or 0) < min_subs:
            continue
        value = views_per_sub(r["views"], r["subscribers"])
        if value is None:
            continue
        by_channel.setdefault(r["channel_id"], {"name": r["channel_name"], "vps": [], "subs": r["subscribers"]})
        by_channel[r["channel_id"]]["vps"].append(value)

    ranked = [
        (cid, d["name"], d["subs"], len(d["vps"]), statistics.median(d["vps"]))
        for cid, d in by_channel.items()
        if len(d["vps"]) >= min_videos
    ]
    ranked.sort(key=lambda x: x[4], reverse=True)

    print(
        f"\nchannels with >= {min_videos} labelled videos and >= {_fmt(float(min_subs))} "
        f"subs, ranked by median views/sub ({len(ranked)} qualify)"
    )
    print(f"{'channel':<40}{'subs':>10}{'vids':>7}{'med v/sub':>12}")
    print("-" * 69)
    for _cid, name, subs, count, med in ranked[:limit]:
        display = (name or "?")[:38]
        print(f"{display:<40}{_fmt(_number(subs)):>10}{count:>7}{_fmt(med):>12}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO_ROOT / "data" / "skimmer.db"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--write", action="store_true", help="Persist labels to video_topic_labels."
    )
    args = parser.parse_args(argv)

    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    rows = load_videos(connection, args.limit)
    print(f"corpus: {len(rows)} distinct videos\n")

    labels = {}
    records = []
    for row in rows:
        assigned = classify_title(row["title"], row["category_id"])
        for topic, confidence in assigned.items():
            labels.setdefault(topic, []).append(row)
            records.append(
                {"video_id": row["video_id"], "topic": topic, "confidence": confidence}
            )

    domain_ids = {r["video_id"] for topic_rows in labels.values() for r in topic_rows}
    domain_rows = [r for r in rows if r["video_id"] in domain_ids]

    print("=== Q1: scale and room quality ===")
    summaries = [summarise(rows, "corpus")]
    summaries.append(summarise(domain_rows, "domain (any)"))
    for topic in TOPICS:
        summaries.append(summarise(labels.get(topic, []), topic))
    print_summary_table(summaries)

    share = 100 * len(domain_rows) / len(rows) if rows else 0
    print(f"\ndomain share of corpus: {len(domain_rows)}/{len(rows)} = {share:.3f}%")

    corpus_median_vps = summaries[0]["median_vps"]
    if corpus_median_vps:
        print("\n=== Q2: is overperformance incumbent-locked? ===")
        print_entrant_analysis(domain_rows, corpus_median_vps)

    print("\n=== Q3: who does it well ===")
    print_top_channels(domain_rows)

    if args.write:
        removed = clear_video_topic_labels(CLASSIFIER_VERSION, database_path=args.db)
        written = insert_video_topic_labels(
            records, CLASSIFIER_VERSION, database_path=args.db
        )
        print(f"\nlabels: replaced {removed}, wrote {written} ({CLASSIFIER_VERSION})")
    else:
        print(f"\n{len(records)} labels computed (dry run; pass --write to persist)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
