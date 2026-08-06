"""Learn the romanized-marker vocabulary used by ``skimmer.domain.language``.

The problem this solves: a Bollywood song uploaded as "Banjaara (Full Audio) |
Ek Villain | Sidharth Malhotra" is pure ASCII with ``defaultAudioLanguage=en``
set by the uploader. YouTube's metadata says English, every language-ID model
says English, and no script check can see anything wrong. Only the vocabulary
gives it away.

Rather than hand-writing that vocabulary, this mines it from the corpus. The
channels YouTube labelled with a South Asian *audio* language are the positive
examples and English-audio channels the negatives, so the metadata that is
reliable on most channels teaches the filter to catch the ones where it is
missing or wrong. Tokens are kept when they appear on several positive channels
and essentially never on English ones.

Run after a substantial ingest to refresh the list:

    python scripts/train_language_markers.py

Writes ``src/skimmer/domain/lexicons/romanized_markers.txt``. Review the diff
before committing: this is a generated asset, but it ships in the package.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import pandas as pd  # noqa: E402

from scripts.RecomendationAnalysis import data_access  # noqa: E402
from skimmer.domain import language  # noqa: E402

#: Languages whose romanized form defeats every detector tested. Extend this if
#: another transliterated language starts leaking through.
TARGET_LANGUAGES = frozenset(
    {"hi", "ur", "pa", "bn", "ta", "te", "ml", "kn", "gu", "mr", "ne", "si", "as", "or"}
)

DEFAULT_OUTPUT = REPO_ROOT / "src" / "skimmer" / "domain" / "lexicons" / "romanized_markers.txt"
DEFAULT_MIN_LIFT = 50.0
DEFAULT_MIN_CHANNELS = 4

_TOKEN_RE = re.compile(r"[a-z][a-z']*")
_MIN_TOKEN = 3


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", str(text).lower()).encode("ascii", "ignore").decode()
    return [t.strip("'") for t in _TOKEN_RE.findall(folded) if len(t.strip("'")) >= _MIN_TOKEN]


def unanimous_audio_labels(df: pd.DataFrame) -> pd.Series:
    """Return each channel's audio language, for channels that agree with themselves."""

    tagged = df.dropna(subset=["default_audio_language"]).copy()
    tagged["primary"] = tagged["default_audio_language"].map(language.normalize_tag)
    per_channel = tagged.groupby("channel_id")["primary"].agg(lambda s: sorted(set(s.dropna())))
    unanimous = per_channel[per_channel.map(len) == 1]
    return unanimous.map(lambda values: values[0])


def learn_markers(df, labels, min_lift=DEFAULT_MIN_LIFT, min_channels=DEFAULT_MIN_CHANNELS):
    positives = set(labels[labels.isin(TARGET_LANGUAGES)].index)
    negatives = set(labels[labels == "en"].index)
    if not positives or not negatives:
        raise RuntimeError("Not enough labelled channels to learn markers.")

    positive_hits, negative_hits = Counter(), Counter()
    positive_channels = negative_channels = 0
    for channel_id, group in df.groupby("channel_id"):
        if channel_id in positives:
            target = positive_hits
            positive_channels += 1
        elif channel_id in negatives:
            target = negative_hits
            negative_channels += 1
        else:
            continue
        # Count channels containing a token, not raw frequency, so one prolific
        # uploader cannot manufacture a marker.
        seen = set()
        for title in group["title"].fillna("").astype(str):
            seen.update(tokenize(title))
        target.update(seen)

    markers = []
    for token, hits in positive_hits.items():
        if hits < min_channels:
            continue
        positive_share = hits / positive_channels
        negative_share = negative_hits.get(token, 0) / negative_channels
        lift = (positive_share + 1e-6) / (negative_share + 1e-6)
        if lift >= min_lift:
            markers.append(token)
    return sorted(markers), positive_channels, negative_channels


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-lift", type=float, default=DEFAULT_MIN_LIFT)
    parser.add_argument("--min-channels", type=int, default=DEFAULT_MIN_CHANNELS)
    args = parser.parse_args(argv)

    df = data_access.load_joined(db_path=args.db_path)
    labels = unanimous_audio_labels(df)
    markers, positives, negatives = learn_markers(
        df, labels, min_lift=args.min_lift, min_channels=args.min_channels
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(markers) + "\n", encoding="utf-8")
    print(
        f"Learned {len(markers):,} markers from {positives:,} target-language and "
        f"{negatives:,} English channels -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
