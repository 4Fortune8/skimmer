"""Find videos that strongly outperform their own channel baseline."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

try:
    from .. import data_access, metrics
except ImportError:  # pragma: no cover
    import data_access
    import metrics

logger = logging.getLogger(__name__)

ALGORITHM_NAME = "channel_relative"

DEFAULT_PARAMS: dict[str, Any] = {
    "min_channel_videos": 5,
    "min_multiple": 10.0,
    "min_views": 20_000,
    "min_channel_median_views": 500.0,
    "min_video_age_days": 3.0,
    "max_video_age_days": None,
    "confidence_k": 10.0,
    "median_weight": 1.0,
    "p90_weight": 0.35,
    "views_weight": 0.15,
}

OUTPUT_COLUMNS = [
    "video_id",
    "score",
    "reason",
    "title",
    "channel_name",
    "subscribers",
    "video_count",
    "views",
    "comments",
    "channel_median_views",
    "channel_p90_views",
    "channel_video_sample",
    "channel_relative_multiple",
    "p90_multiple",
    "views_per_sub",
    "comment_rate",
    "published_at",
    "video_url",
    "channel_url",
    "sample_confidence",
    "video_age_days",
]

_REQUIRED_COLUMNS = [
    "video_id",
    "channel_id",
    "views",
    "channel_median_views",
    "channel_p90_views",
    "channel_video_sample",
    "channel_relative_multiple",
    "video_age_days",
]

_OPTIONAL_OUTPUT_COLUMNS = [
    "title",
    "channel_name",
    "subscribers",
    "video_count",
    "comments",
    "views_per_sub",
    "comment_rate",
    "published_at",
]


def score(df: pd.DataFrame, **params: Any) -> pd.DataFrame:
    """Score videos that beat their channel's usual view performance.

    The core signal is ``views / leave-one-out channel median views`` so the
    candidate itself does not inflate the channel baseline. The score log-dampens
    both the median and p90 multiples, multiplies by a sample-size confidence
    factor (``sample / (sample + confidence_k)``), and adds a mild absolute-view
    term so tiny-channel breakouts do not outrank substantially larger leads by
    relative multiple alone. If ``min_channel_videos`` differs from the default,
    channel baseline eligibility is first recomputed with
    ``metrics.add_channel_baselines``.
    """

    settings = {**DEFAULT_PARAMS, **params}
    min_channel_videos = int(settings["min_channel_videos"])
    min_multiple = float(settings["min_multiple"])
    min_views = float(settings["min_views"])
    min_channel_median_views = float(settings["min_channel_median_views"])
    min_video_age_days = float(settings["min_video_age_days"])
    max_video_age_days = settings["max_video_age_days"]
    confidence_k = float(settings["confidence_k"])
    median_weight = float(settings["median_weight"])
    p90_weight = float(settings["p90_weight"])
    views_weight = float(settings["views_weight"])

    _validate_settings(
        min_channel_videos=min_channel_videos,
        min_multiple=min_multiple,
        min_views=min_views,
        min_channel_median_views=min_channel_median_views,
        min_video_age_days=min_video_age_days,
        max_video_age_days=max_video_age_days,
        confidence_k=confidence_k,
    )
    _require_columns(df, _REQUIRED_COLUMNS)
    if df.empty:
        return _empty_result()

    result = df.copy()
    if min_channel_videos != DEFAULT_PARAMS["min_channel_videos"]:
        result = metrics.add_channel_baselines(result, min_videos=min_channel_videos)

    for column in _OPTIONAL_OUTPUT_COLUMNS:
        if column not in result.columns:
            result[column] = np.nan

    result = _add_leave_one_out_baselines(result, min_channel_videos=min_channel_videos)
    result["p90_multiple"] = metrics.safe_ratio(result["views"], result["channel_p90_views"])

    views = pd.to_numeric(result["views"], errors="coerce")
    sample = pd.to_numeric(result["channel_video_sample"], errors="coerce")
    multiple = pd.to_numeric(result["channel_relative_multiple"], errors="coerce")
    median_views = pd.to_numeric(result["channel_median_views"], errors="coerce")
    age_days = pd.to_numeric(result["video_age_days"], errors="coerce")

    qualifies = (
        (sample >= min_channel_videos)
        & (views >= min_views)
        & (median_views >= min_channel_median_views)
        & (multiple >= min_multiple)
        & (age_days >= min_video_age_days)
    )
    if max_video_age_days is not None:
        qualifies &= age_days <= float(max_video_age_days)

    result = result.loc[qualifies].copy()
    if result.empty:
        return _empty_result()

    sample = pd.to_numeric(result["channel_video_sample"], errors="coerce")
    multiple = pd.to_numeric(result["channel_relative_multiple"], errors="coerce")
    p90_multiple = pd.to_numeric(result["p90_multiple"], errors="coerce").fillna(0)
    views = pd.to_numeric(result["views"], errors="coerce")

    confidence = metrics.safe_ratio(sample, sample + confidence_k, fill=0).fillna(0)
    base = median_weight * np.log1p(multiple.clip(lower=0))
    p90_component = p90_weight * np.log1p(p90_multiple.clip(lower=0))
    views_component = views_weight * np.log1p(metrics.safe_ratio(views, min_views, fill=0).fillna(0))
    result["sample_confidence"] = confidence
    result["score"] = ((base + p90_component) * confidence + views_component).replace(
        [np.inf, -np.inf], np.nan
    )
    result["score"] = result["score"].fillna(0).astype(float)
    result["reason"] = result.apply(_reason, axis=1)
    result["video_url"] = result["video_id"].astype("string").map(data_access.video_url)
    result["channel_url"] = result["channel_id"].astype("string").map(data_access.channel_url)

    result = result.replace([np.inf, -np.inf], np.nan)
    result = result.sort_values(["score", "views"], ascending=[False, False])
    result = result.drop_duplicates("video_id", keep="first")
    return result[OUTPUT_COLUMNS].reset_index(drop=True)


def _validate_settings(
    *,
    min_channel_videos: int,
    min_multiple: float,
    min_views: float,
    min_channel_median_views: float,
    min_video_age_days: float,
    max_video_age_days: Any,
    confidence_k: float,
) -> None:
    if min_channel_videos < 2:
        raise ValueError("min_channel_videos must be at least 2.")
    if min_multiple <= 0:
        raise ValueError("min_multiple must be positive.")
    if min_views < 0:
        raise ValueError("min_views must be non-negative.")
    if min_channel_median_views < 0:
        raise ValueError("min_channel_median_views must be non-negative.")
    if min_video_age_days < 0:
        raise ValueError("min_video_age_days must be non-negative.")
    if max_video_age_days is not None and float(max_video_age_days) < min_video_age_days:
        raise ValueError("max_video_age_days must be greater than or equal to min_video_age_days.")
    if confidence_k <= 0:
        raise ValueError("confidence_k must be positive.")


def _require_columns(df: pd.DataFrame, columns: list[str]) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise KeyError(f"Missing required columns for {ALGORITHM_NAME}: {missing}")


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS).astype({"score": "float64"})


def _add_leave_one_out_baselines(df: pd.DataFrame, min_channel_videos: int) -> pd.DataFrame:
    result = df.copy()
    views = pd.to_numeric(result["views"], errors="coerce")
    channels = result["channel_id"]
    sample = views.groupby(channels).transform("count")

    median = pd.Series(np.nan, index=result.index, dtype="float64")
    p90 = pd.Series(np.nan, index=result.index, dtype="float64")
    for _, group in result.assign(_views=views).groupby("channel_id", sort=False, dropna=False):
        valid = group["_views"].dropna()
        if valid.empty:
            continue
        order = valid.sort_values(kind="mergesort")
        values = order.to_numpy(dtype="float64")
        median.loc[order.index] = _leave_one_out_quantile(values, 0.5)
        p90.loc[order.index] = _leave_one_out_quantile(values, 0.9)

    enough_sample = sample >= min_channel_videos
    result["channel_median_views"] = median.where(enough_sample, np.nan)
    result["channel_p90_views"] = p90.where(enough_sample, np.nan)
    result["channel_video_sample"] = sample.where(enough_sample, np.nan)
    result["channel_relative_multiple"] = metrics.safe_ratio(
        result["views"], result["channel_median_views"]
    )
    return result.replace([np.inf, -np.inf], np.nan)


def _leave_one_out_quantile(sorted_values: np.ndarray, quantile: float) -> np.ndarray:
    n = len(sorted_values)
    output = np.full(n, np.nan, dtype="float64")
    remaining = n - 1
    if remaining <= 0:
        return output

    position = (remaining - 1) * quantile
    lower = int(np.floor(position))
    upper = int(np.ceil(position))
    fraction = position - lower
    ranks = np.arange(n)
    lower_values = sorted_values[np.where(lower < ranks, lower, lower + 1)]
    upper_values = sorted_values[np.where(upper < ranks, upper, upper + 1)]
    output = lower_values + (upper_values - lower_values) * fraction
    return output


def _reason(row: pd.Series) -> str:
    multiple = _format_number(row["channel_relative_multiple"], suffix="x", decimals=0)
    median = _format_number(row["channel_median_views"], decimals=0)
    views = _format_number(row["views"], decimals=0)
    sample = _format_number(row["channel_video_sample"], decimals=0)
    return f"{multiple} its channel's median (median {median} -> {views} views, n={sample} videos)"


def _format_number(value: Any, *, suffix: str = "", decimals: int = 1) -> str:
    if pd.isna(value):
        return f"unknown{suffix}"
    number = float(value)
    abs_number = abs(number)
    if abs_number >= 1_000_000:
        text = f"{number / 1_000_000:.{decimals}f}M"
    elif abs_number >= 1_000:
        text = f"{number / 1_000:.{decimals}f}k"
    else:
        text = f"{number:.{decimals}f}"
    if decimals > 0:
        text = text.rstrip("0").rstrip(".")
    return f"{text}{suffix}"
