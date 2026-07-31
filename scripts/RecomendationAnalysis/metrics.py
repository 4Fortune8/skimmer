"""Derived metric helpers for YouTube recommendation analysis."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

SUB_WEIGHT_CLASSES = ["<1k", "1k-10k", "10k-50k", "50k-200k", "200k-1M", "1M+"]
VIDEO_COUNT_CLASSES = ["<50", "50-300", "300-1000", "1000+"]

SHORTS_MAX_DURATION_SECONDS = 60


def add_duration_flags(
    df: pd.DataFrame,
    max_duration_seconds: int = SHORTS_MAX_DURATION_SECONDS,
) -> pd.DataFrame:
    """Return a copy with an ``is_short`` flag derived from ``duration_seconds``.

    Videos at or below ``max_duration_seconds`` are treated as shorts. Rows with
    an unknown duration keep ``pd.NA`` so callers can decide how to treat them.
    """

    result = df.copy()
    duration = pd.to_numeric(result["duration_seconds"], errors="coerce")
    result["is_short"] = (duration <= max_duration_seconds).astype("boolean").where(duration.notna())
    return result


def exclude_shorts(
    df: pd.DataFrame,
    max_duration_seconds: int = SHORTS_MAX_DURATION_SECONDS,
    drop_unknown_duration: bool = False,
) -> pd.DataFrame:
    """Drop shorts so view counts are only compared across long-form videos.

    A short reaching a million views is a far weaker idea signal than a long-form
    video doing the same, so shorts are removed before any metric is derived.
    Filtering here rather than inside each algorithm also keeps per-channel
    baselines free of shorts. Rows with an unknown duration are kept unless
    ``drop_unknown_duration`` is set.
    """

    flagged = add_duration_flags(df, max_duration_seconds=max_duration_seconds)
    is_short = flagged["is_short"]
    keep = is_short.isna() if not drop_unknown_duration else pd.Series(False, index=flagged.index)
    keep = keep | (is_short == False)  # noqa: E712 - pd.NA-safe comparison
    return flagged.loc[keep.fillna(False)].drop(columns="is_short").copy()


def safe_ratio(numerator: Any, denominator: Any, fill: float = np.nan) -> Any:
    """Divide elementwise while replacing zero, null, and infinite results."""

    num = _numeric(numerator)
    den = _numeric(denominator)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = num / den
    valid = (den != 0) & pd.notna(den) & np.isfinite(result)
    if np.isscalar(result):
        return result if bool(valid) else fill
    if isinstance(result, pd.Series):
        return result.where(valid, fill)
    return np.where(valid, result, fill)


def add_video_metrics(df: pd.DataFrame, now: pd.Timestamp | str | None = None) -> pd.DataFrame:
    """Return a copy with video age, rate, and engagement metrics added."""

    result = df.copy()
    current_time = _utc_now(now)
    published_at = pd.to_datetime(result["published_at"], utc=True, errors="coerce", format="mixed")
    age_days = (current_time - published_at).dt.total_seconds() / 86_400
    result["video_age_days"] = age_days.clip(lower=0.5)
    result["views_per_day"] = safe_ratio(result["views"], result["video_age_days"])
    result["views_per_sub"] = safe_ratio(result["views"], result["subscribers"])
    result["like_rate"] = safe_ratio(result["likes"], result["views"])
    result["comment_rate"] = safe_ratio(result["comments"], result["views"])
    result["engagement_rate"] = safe_ratio(result["likes"] + result["comments"], result["views"])
    return _replace_infinities(result)


def add_channel_metrics(df: pd.DataFrame, now: pd.Timestamp | str | None = None) -> pd.DataFrame:
    """Return a copy with channel age and upload cadence metrics added."""

    result = df.copy()
    current_time = _utc_now(now)
    published_at = pd.to_datetime(
        result["channel_published_at"], utc=True, errors="coerce", format="mixed"
    )
    result["channel_age_days"] = (current_time - published_at).dt.total_seconds() / 86_400
    result["uploads_per_month"] = safe_ratio(result["video_count"], result["channel_age_days"] / 30.44)
    return _replace_infinities(result)


def add_channel_baselines(df: pd.DataFrame, min_videos: int = 5) -> pd.DataFrame:
    """Return a copy with per-channel view baselines and relative multiples."""

    result = df.copy()
    views = pd.to_numeric(result["views"], errors="coerce")
    grouped = views.groupby(result["channel_id"])
    sample = grouped.transform("count")
    median = grouped.transform("median")
    p90 = grouped.transform(lambda values: values.quantile(0.9))
    enough_sample = sample >= min_videos
    result["channel_median_views"] = median.where(enough_sample, np.nan)
    result["channel_p90_views"] = p90.where(enough_sample, np.nan)
    result["channel_video_sample"] = sample.where(enough_sample, np.nan)
    result["channel_relative_multiple"] = safe_ratio(result["views"], result["channel_median_views"])
    return _replace_infinities(result)


def add_weight_classes(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with ordered subscriber and video-count class labels."""

    result = df.copy()
    sub_labels = SUB_WEIGHT_CLASSES + ["unknown"]
    video_labels = VIDEO_COUNT_CLASSES + ["unknown"]
    result["sub_class"] = _categorize(
        result["subscribers"],
        bins=[-np.inf, 1_000, 10_000, 50_000, 200_000, 1_000_000, np.inf],
        labels=SUB_WEIGHT_CLASSES,
        categories=sub_labels,
    )
    result["video_count_class"] = _categorize(
        result["video_count"],
        bins=[-np.inf, 50, 300, 1_000, np.inf],
        labels=VIDEO_COUNT_CLASSES,
        categories=video_labels,
    )
    return result


def enrich(
    df: pd.DataFrame,
    now: pd.Timestamp | str | None = None,
    min_videos_for_baseline: int = 5,
) -> pd.DataFrame:
    """Run all enrichment steps in dependency order."""

    result = add_video_metrics(df, now=now)
    result = add_channel_metrics(result, now=now)
    result = add_channel_baselines(result, min_videos=min_videos_for_baseline)
    return add_weight_classes(result)


def _numeric(value: Any) -> Any:
    if isinstance(value, pd.Series):
        return pd.to_numeric(value, errors="coerce")
    if isinstance(value, pd.Index):
        return pd.to_numeric(value, errors="coerce")
    if np.isscalar(value):
        return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return pd.to_numeric(value, errors="coerce")


def _utc_now(now: pd.Timestamp | str | None) -> pd.Timestamp:
    if now is None:
        return pd.Timestamp.utcnow()
    timestamp = pd.Timestamp(now)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _categorize(
    values: pd.Series,
    bins: list[float],
    labels: list[str],
    categories: list[str],
) -> pd.Categorical:
    numeric = pd.to_numeric(values, errors="coerce")
    classified = pd.cut(numeric, bins=bins, labels=labels, right=False)
    series = pd.Series(classified, index=values.index).astype("string").fillna("unknown")
    return pd.Categorical(series, categories=categories, ordered=True)


def _replace_infinities(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace([np.inf, -np.inf], np.nan)
