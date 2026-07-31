"""Score small-channel videos whose view count breaks far beyond audience size.

The algorithm targets the strongest lead-gen signal: a video earning many more
views than the channel has subscribers. The score keeps views-per-subscriber as
the primary signal while multiplying by a tunable log dampener for absolute
views, so a tiny channel with only modest views does not outrank a materially
large breakout.
"""

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

ALGORITHM_NAME = "breakout_outliers"

DEFAULT_PARAMS: dict[str, Any] = {
    "min_views_per_sub": 10.0,
    "max_subscribers": 100_000,
    "min_subscribers": 500,
    "min_views": 50_000,
    "max_video_age_days": None,
    "min_video_age_days": 3.0,
    "require_engagement": True,
    "min_like_rate": 0.001,
    "min_comment_rate": 0.0001,
    "views_log_weight": 0.5,
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
    "views_per_sub",
    "views_per_day",
    "comment_rate",
    "published_at",
    "video_url",
    "channel_url",
]

REQUIRED_COLUMNS = [
    "video_id",
    "channel_id",
    "title",
    "channel_name",
    "subscribers",
    "video_count",
    "views",
    "comments",
    "views_per_sub",
    "views_per_day",
    "like_rate",
    "comment_rate",
    "published_at",
    "video_age_days",
]


def score(df: pd.DataFrame, **params: Any) -> pd.DataFrame:
    """Return high-confidence small-channel breakout candidates.

    Engagement filtering rejects rows where both comment and like rates are
    missing or effectively zero, which helps avoid bot-like pseudo-view spikes.
    A missing comment rate is still accepted when the like rate is healthy
    because legitimate videos can have comments disabled.
    """

    _validate_columns(df)
    if df.empty:
        return _empty_result()

    options = DEFAULT_PARAMS | params
    views_log_weight = float(options["views_log_weight"])
    if views_log_weight < 0:
        raise ValueError("views_log_weight must be non-negative.")

    result = df.copy()
    _coerce_numeric(
        result,
        [
            "subscribers",
            "video_count",
            "views",
            "comments",
            "views_per_sub",
            "views_per_day",
            "like_rate",
            "comment_rate",
            "video_age_days",
        ],
    )
    result["views_per_sub"] = result["views_per_sub"].replace([np.inf, -np.inf], np.nan)
    result["views_per_sub"] = result["views_per_sub"].where(
        result["views_per_sub"].notna(),
        metrics.safe_ratio(result["views"], result["subscribers"]),
    )

    mask = (
        result["views_per_sub"].ge(float(options["min_views_per_sub"]))
        & result["subscribers"].ge(float(options["min_subscribers"]))
        & result["subscribers"].le(float(options["max_subscribers"]))
        & result["views"].ge(float(options["min_views"]))
        & result["video_age_days"].ge(float(options["min_video_age_days"]))
    )
    max_age = options["max_video_age_days"]
    if max_age is not None:
        mask &= result["video_age_days"].le(float(max_age))
    if bool(options["require_engagement"]):
        mask &= _has_engagement(
            result,
            min_like_rate=float(options["min_like_rate"]),
            min_comment_rate=float(options["min_comment_rate"]),
        )

    result = result.loc[mask].copy()
    if result.empty:
        return _empty_result()

    views_per_sub = result["views_per_sub"].replace([np.inf, -np.inf], np.nan)
    views = result["views"].replace([np.inf, -np.inf], np.nan)
    result["score"] = np.log1p(views_per_sub) * np.power(
        np.log1p(views),
        views_log_weight,
    )
    result["score"] = result["score"].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    result = result[result["score"].gt(0)].copy()
    if result.empty:
        return _empty_result()

    result["video_url"] = result["video_id"].astype("string").map(data_access.video_url)
    result["channel_url"] = result["channel_id"].astype("string").map(data_access.channel_url)
    result["reason"] = [
        _format_reason(views_per_sub, subscribers, views)
        for views_per_sub, subscribers, views in zip(
            result["views_per_sub"], result["subscribers"], result["views"], strict=False
        )
    ]

    result = (
        result.sort_values(["score", "views"], ascending=[False, False])
        .drop_duplicates("video_id", keep="first")
        .reset_index(drop=True)
    )
    logger.debug("Found %d breakout outlier candidates.", len(result))
    return result[OUTPUT_COLUMNS]


def _validate_columns(df: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise KeyError(f"Missing required columns for {ALGORITHM_NAME}: {missing}")


def _coerce_numeric(df: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")


def _has_engagement(
    df: pd.DataFrame,
    min_like_rate: float,
    min_comment_rate: float,
) -> pd.Series:
    like_ok = df["like_rate"].fillna(0).gt(min_like_rate)
    comment_ok = df["comment_rate"].fillna(0).gt(min_comment_rate)
    return like_ok | comment_ok


def _format_reason(views_per_sub: float, subscribers: float, views: float) -> str:
    return (
        f"{views_per_sub:.1f}x views/sub on a {_compact_number(subscribers)}-sub channel "
        f"({_compact_number(views)} views)"
    )


def _compact_number(value: float) -> str:
    if not np.isfinite(value):
        return "unknown"
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{value:.0f}"


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS).astype({"score": "float64"})
