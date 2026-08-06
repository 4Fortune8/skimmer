"""DataFrame adapter over :mod:`skimmer.domain.language`.

The policy — which sources are trusted, in what order, and what counts as
English — lives in the domain module so the collector and the analysis code
cannot drift apart. This module only applies that policy to frames: resolve a
language per channel, attach it to every row, and filter.

Language is resolved per channel rather than per video for two reasons. The
detector needs pooled text to be accurate on title-length input, and a creator
publishing in Spanish is not an English-audience lead regardless of which of
their videos happened to surface.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

import pandas as pd

def _load_domain_language():
    """Load the shared language module from this checkout.

    ``skimmer`` is installed non-editable, so an installed copy can be older
    than the repository the notebook is running from. Analysis code should track
    the checkout it lives in, so the repository file wins and the installed
    package is only a fallback.
    """

    import importlib.util
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src" / "skimmer" / "domain" / "language.py"
    if source.exists():
        spec = importlib.util.spec_from_file_location("skimmer_domain_language", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    from skimmer.domain import language as installed  # pragma: no cover

    return installed


language = _load_domain_language()

logger = logging.getLogger(__name__)

LANGUAGE_COLUMNS = ["channel_language", "language_confidence", "language_source"]

#: Ordered by authority, not merged. ``default_audio_language`` is what a viewer
#: hears; ``default_language`` only describes the title text, which is routinely
#: English on non-English content.
AUDIO_COLUMN = "default_audio_language"
TEXT_COLUMN = "default_language"
METADATA_COLUMNS = (AUDIO_COLUMN, TEXT_COLUMN)


def resolve_channel_languages(
    df: pd.DataFrame,
    channel_column: str = "channel_id",
    title_column: str = "title",
    metadata_columns: Iterable[str] = METADATA_COLUMNS,
    use_detector: bool = True,
) -> pd.DataFrame:
    """Return one row per channel with its language, confidence, and source.

    ``language_source`` is ``metadata`` when YouTube reported a tag for at least
    one of the channel's videos and ``detected`` when the fastText model had to
    decide, which makes it easy to see how much of a run rests on inference.
    """

    if df.empty:
        return pd.DataFrame(columns=[channel_column, *LANGUAGE_COLUMNS])

    available = [column for column in metadata_columns if column in df.columns]
    if not available:
        logger.warning(
            "No language metadata columns found; every channel falls back to detection."
        )

    frame = df[[channel_column]].copy()
    frame["_title"] = df[title_column].fillna("").astype(str) if title_column in df else ""
    for column in available:
        frame[column] = df[column]

    resolved = []
    for channel_id, group in frame.groupby(channel_column, dropna=False, sort=False):
        audio_tags = group[AUDIO_COLUMN].dropna().tolist() if AUDIO_COLUMN in available else []
        text_tags = group[TEXT_COLUMN].dropna().tolist() if TEXT_COLUMN in available else []
        # Titles are always supplied: they break ties when the deciding metadata
        # field disagrees with itself.
        titles = group["_title"] if use_detector else ()
        if audio_tags or text_tags:
            tag, confidence, source = language.resolve(audio_tags, text_tags, titles)
        elif use_detector:
            tag, confidence, source = language.resolve((), (), titles)
        else:
            tag, confidence, source = language.UNKNOWN, 0.0, "unavailable"
        resolved.append(
            {
                channel_column: channel_id,
                "channel_language": tag,
                "language_confidence": confidence,
                "language_source": source,
            }
        )
    return pd.DataFrame(resolved)


def annotate(
    df: pd.DataFrame,
    channel_column: str = "channel_id",
    title_column: str = "title",
    use_detector: bool = True,
) -> pd.DataFrame:
    """Attach the channel language columns to every row of ``df``."""

    if df.empty:
        result = df.copy()
        for column in LANGUAGE_COLUMNS:
            result[column] = pd.Series(dtype="object")
        return result
    channels = resolve_channel_languages(
        df,
        channel_column=channel_column,
        title_column=title_column,
        use_detector=use_detector,
    )
    return df.merge(channels, on=channel_column, how="left")


def filter_english(
    df: pd.DataFrame,
    keep_unknown: bool = True,
    channel_column: str = "channel_id",
    title_column: str = "title",
    use_detector: bool = True,
) -> pd.DataFrame:
    """Keep rows from English channels, annotating first when needed.

    ``keep_unknown`` retains channels no source could label. That is the
    precision-first default: an unlabelled channel is usually one with very
    few titles and no metadata yet, and dropping those silently loses leads.

    Channels ruled out by their titles' script are never kept, even unlabelled.
    "No evidence" and "evidence that this is not English, but not enough to name
    the language" are different states, and only the first deserves the benefit
    of the doubt.
    """

    if df.empty:
        return df.copy()
    annotated = (
        df
        if "channel_language" in df.columns
        else annotate(
            df,
            channel_column=channel_column,
            title_column=title_column,
            use_detector=use_detector,
        )
    )
    tags = annotated["channel_language"].astype("string")
    mask = tags.map(language.is_western_english).fillna(False)
    if keep_unknown:
        unlabelled = tags.isna() | tags.eq(language.UNKNOWN)
        if "language_source" in annotated.columns:
            contradicted = (
                annotated["language_source"].astype("string").isin(language.CONTRADICTED_SOURCES)
            )
            unlabelled &= ~contradicted.fillna(False)
        mask |= unlabelled
    return annotated.loc[mask].copy()


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """Return a per-language breakdown of videos and channels for reporting."""

    if df.empty or "channel_language" not in df.columns:
        return pd.DataFrame(columns=["videos", "channels", "share"])
    summary = df.groupby(df["channel_language"].astype(str), dropna=False).agg(
        videos=("channel_language", "size"),
        channels=("channel_id", "nunique"),
    )
    summary["share"] = summary["videos"] / len(df)
    return summary.sort_values("videos", ascending=False)


def stopwords_for(languages: Any = "en") -> frozenset[str]:
    """Return the union of stopwords for one or more languages."""

    if isinstance(languages, str) or isinstance(languages, Mapping):
        languages = [languages]
    words: set[str] = set()
    for value in languages:
        words |= set(language.stopwords(value))
    return frozenset(words)
