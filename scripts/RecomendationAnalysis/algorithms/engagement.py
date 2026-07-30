"""Engagement-resonance lead scoring for discussion-heavy topics."""

from __future__ import annotations

import logging
from typing import Any, Iterable

import numpy as np
import pandas as pd

try:
    from .. import data_access, metrics
except ImportError:  # pragma: no cover
    import data_access
    import metrics

logger = logging.getLogger(__name__)

ALGORITHM_NAME = "engagement"

DEFAULT_PARAMS: dict[str, Any] = {
    "grouping_keys": ("sub_class",),
    "min_group_size": 30,
    "premium_min_views_per_sub": 5.0,
    "premium_min_comment_z": 2.0,
    "premium_bonus": 2.0,
    "bot_min_views": 100_000,
    "bot_max_engagement_rate": 0.0005,
    "bot_max_like_rate": 0.0005,
    "bot_max_comment_rate": 0.0001,
    "drop_suspicious": True,
    "suspicious_penalty": 8.0,
    "min_views": 10_000,
    "min_comment_z": 1.5,
    "min_comments": 50,
    "comment_z_weight": 3.0,
    "like_z_weight": 0.5,
    "views_per_sub_weight": 1.0,
    "max_per_channel": 3,
    "score_clip_min": -25.0,
    "score_clip_max": None,
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
    "likes",
    "comments",
    "comment_rate",
    "like_rate",
    "engagement_rate",
    "comment_rate_z",
    "like_rate_z",
    "z_scale_source",
    "views_per_sub",
    "premium_lead",
    "suspicious_engagement",
    "sub_class",
    "published_at",
    "video_url",
    "channel_url",
]

_REQUIRED_COLUMNS = {
    "video_id",
    "channel_id",
    "title",
    "channel_name",
    "subscribers",
    "video_count",
    "views",
    "likes",
    "comments",
    "comment_rate",
    "like_rate",
    "engagement_rate",
    "views_per_sub",
    "sub_class",
    "published_at",
}


def score(df: pd.DataFrame, **params: Any) -> pd.DataFrame:
    """Return videos whose comment activity suggests unusually resonant topics.

    Comment-rate z-score is dominant because replies indicate debate, questions, or strong
    opinions; like-rate z-score is a small corroborating signal. A log1p views-per-subscriber
    term rewards reach without letting huge channels swamp the rate signal, and videos with
    both high views per subscriber and high comment z-score receive a premium bonus. The raw
    blend is log-compressed so extreme comment rates remain sortable without saturating.
    Robust z-scores are computed within subscriber class by default using median and MAD.
    Degenerate zero-MAD groups fall back to meaningfully non-zero group IQR/std, then
    out-of-group global median and MAD, then zero. Global fallback deliberately uses the
    global center as well as the global scale, so a group with one identical high comment
    rate is scored as exceptional relative to the rest of the corpus rather than flattened
    against its own median.

    The bot detector flags high-view rows only when engagement is implausibly low and both
    like_rate and comment_rate are near zero. Comment-disabled rows with NaN comments can be
    legitimate when like_rate is healthy, so NaN comment_rate is not treated as evidence of
    suspicious engagement.
    """

    options = {**DEFAULT_PARAMS, **params}
    grouping_keys = _as_tuple(options["grouping_keys"])
    if df.empty:
        return _empty_result()
    _validate_columns(df, grouping_keys)

    result = df.copy()
    for column in ("views", "likes", "comments", "comment_rate", "like_rate", "engagement_rate", "views_per_sub"):
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result["comment_rate"] = metrics.safe_ratio(result["comments"], result["views"])
    result["like_rate"] = metrics.safe_ratio(result["likes"], result["views"])
    result["engagement_rate"] = metrics.safe_ratio(result["likes"] + result["comments"], result["views"])
    result["views_per_sub"] = metrics.safe_ratio(result["views"], result["subscribers"])

    comment_norm = _robust_group_z(
        result, "comment_rate", grouping_keys, int(options["min_group_size"])
    )
    result["comment_rate_z"] = comment_norm["z"]
    result["z_scale_source"] = comment_norm["source"]
    result["_comment_rate_norm"] = comment_norm["center"]
    like_norm = _robust_group_z(
        result, "like_rate", grouping_keys, int(options["min_group_size"])
    )
    result["like_rate_z"] = like_norm["z"]

    result["premium_lead"] = (
        result["views_per_sub"].fillna(0) >= float(options["premium_min_views_per_sub"])
    ) & (result["comment_rate_z"] >= float(options["premium_min_comment_z"]))
    result["suspicious_engagement"] = _suspicious_engagement(result, options)

    views_term = np.log1p(result["views_per_sub"].clip(lower=0).fillna(0))
    raw_score = (
        float(options["comment_z_weight"]) * result["comment_rate_z"]
        + float(options["like_z_weight"]) * result["like_rate_z"]
        + float(options["views_per_sub_weight"]) * views_term
        + result["premium_lead"].astype(float) * float(options["premium_bonus"])
    )
    if not bool(options["drop_suspicious"]):
        raw_score = raw_score - result["suspicious_engagement"].astype(float) * float(
            options["suspicious_penalty"]
        )
    compressed_score = np.sign(raw_score) * np.log1p(raw_score.abs())
    result["score"] = compressed_score.replace([np.inf, -np.inf], np.nan).fillna(0).clip(
        lower=float(options["score_clip_min"])
    )
    if options["score_clip_max"] is not None:
        result["score"] = result["score"].clip(upper=float(options["score_clip_max"]))

    qualifies = (
        (result["views"].fillna(0) >= float(options["min_views"]))
        & (result["comment_rate_z"] >= float(options["min_comment_z"]))
        & (result["comments"].fillna(0) >= float(options["min_comments"]))
    )
    if bool(options["drop_suspicious"]):
        qualifies &= ~result["suspicious_engagement"]

    result = result.loc[qualifies].copy()
    if result.empty:
        return _empty_result()

    result["video_url"] = result["video_id"].astype("string").map(data_access.video_url)
    result["channel_url"] = result["channel_id"].astype("string").map(data_access.channel_url)
    result["reason"] = result.apply(_reason, axis=1)

    result = result.sort_values(["score", "video_id"], ascending=[False, True]).drop_duplicates(
        "video_id", keep="first"
    )
    max_per_channel = options["max_per_channel"]
    if max_per_channel is not None:
        result = result.groupby("channel_id", observed=True, sort=False).head(int(max_per_channel))
        result = result.sort_values(["score", "video_id"], ascending=[False, True])
    result["score"] = result["score"].astype(float)
    return result[OUTPUT_COLUMNS].reset_index(drop=True)


def _validate_columns(df: pd.DataFrame, grouping_keys: tuple[str, ...]) -> None:
    missing = sorted((_REQUIRED_COLUMNS | set(grouping_keys)) - set(df.columns))
    if missing:
        raise KeyError(f"engagement score requires columns: {', '.join(missing)}")


def _as_tuple(value: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _robust_group_z(
    df: pd.DataFrame,
    column: str,
    grouping_keys: tuple[str, ...],
    min_group_size: int,
) -> pd.DataFrame:
    values = pd.to_numeric(df[column], errors="coerce")
    global_median, global_mad = _median_mad(values)
    if not _valid_scale(global_mad, reference=global_median):
        return pd.DataFrame(
            {
                "z": pd.Series(0.0, index=df.index),
                "source": pd.Series("degenerate", index=df.index),
                "center": pd.Series(global_median, index=df.index),
            }
        )

    output = pd.DataFrame(
        {
            "z": pd.Series(0.0, index=df.index),
            "source": pd.Series("degenerate", index=df.index),
            "center": pd.Series(global_median, index=df.index),
        }
    )
    grouped = df.assign(_value=values).groupby(list(grouping_keys), observed=True)
    for _, group_df in grouped:
        group = group_df["_value"]
        fallback_median, fallback_mad = _fallback_global_stats(
            values, group.index, global_median, global_mad
        )
        group_result = _group_z_frame(group, min_group_size, fallback_median, fallback_mad)
        output.loc[group.index, ["z", "source", "center"]] = group_result[["z", "source", "center"]]

    output["z"] = output["z"].replace([np.inf, -np.inf], np.nan).fillna(0).astype(float)
    output["source"] = output["source"].fillna("degenerate").astype("string")
    output["center"] = (
        output["center"].replace([np.inf, -np.inf], np.nan).fillna(global_median).astype(float)
    )
    return output


def _robust_group_center(
    df: pd.DataFrame,
    column: str,
    grouping_keys: tuple[str, ...],
    min_group_size: int,
) -> pd.Series:
    values = pd.to_numeric(df[column], errors="coerce")
    global_median, _ = _median_mad(values)
    grouped = df.assign(_value=values).groupby(list(grouping_keys), observed=True)["_value"]
    centers = grouped.transform(lambda group: _group_center(group, min_group_size, global_median))
    return centers.replace([np.inf, -np.inf], np.nan).fillna(global_median).astype(float)


def _group_z_frame(
    group: pd.Series,
    min_group_size: int,
    fallback_median: float,
    fallback_mad: float,
) -> pd.DataFrame:
    valid = group.dropna()
    if len(valid) < min_group_size:
        if _valid_scale(fallback_mad, reference=fallback_median):
            return _z_frame(group, fallback_median, fallback_mad, "global_mad")
        return _z_frame(group, 0.0, 1.0, "degenerate", zero=True)
    median, mad = _median_mad(valid)
    if _valid_scale(mad, reference=median, global_scale=fallback_mad):
        return _z_frame(group, median, mad, "group_mad")

    iqr_scale = _iqr_scale(valid)
    if _valid_scale(iqr_scale, reference=median, global_scale=fallback_mad):
        return _z_frame(group, median, iqr_scale, "group_iqr")

    std = float(valid.std(ddof=0))
    if _valid_scale(std, reference=median, global_scale=fallback_mad):
        return _z_frame(group, median, std, "group_std")

    if _valid_scale(fallback_mad, reference=fallback_median):
        return _z_frame(group, fallback_median, fallback_mad, "global_mad")
    return _z_frame(group, 0.0, 1.0, "degenerate", zero=True)


def _z_frame(
    group: pd.Series,
    center: float,
    scale: float,
    source: str,
    zero: bool = False,
) -> pd.DataFrame:
    z = pd.Series(0.0, index=group.index) if zero else (group - center) / scale
    return pd.DataFrame({"z": z, "source": source, "center": center}, index=group.index)


def _group_center(group: pd.Series, min_group_size: int, global_median: float) -> pd.Series:
    valid = group.dropna()
    if len(valid) < min_group_size:
        center = global_median
    else:
        center = float(valid.median())
    return pd.Series(center, index=group.index)


def _median_mad(values: pd.Series) -> tuple[float, float]:
    valid = pd.to_numeric(values, errors="coerce").dropna()
    if valid.empty:
        return 0.0, 0.0
    median = float(valid.median())
    mad = float(1.4826 * (valid - median).abs().median())
    return median, mad


def _fallback_global_stats(
    values: pd.Series,
    group_index: pd.Index,
    global_median: float,
    global_mad: float,
) -> tuple[float, float]:
    outside = values.drop(index=group_index, errors="ignore")
    outside_median, outside_mad = _median_mad(outside)
    if _valid_scale(outside_mad, reference=outside_median):
        return outside_median, outside_mad
    return global_median, global_mad


def _iqr_scale(values: pd.Series) -> float:
    q1 = float(values.quantile(0.25))
    q3 = float(values.quantile(0.75))
    return (q3 - q1) / 1.349


def _valid_scale(
    scale: float,
    reference: float = 0.0,
    global_scale: float | None = None,
    relative_tolerance: float = 1e-9,
) -> bool:
    if not np.isfinite(scale):
        return False
    relative_reference = max(abs(reference), abs(global_scale or 0.0))
    threshold = max(1e-12, relative_tolerance * relative_reference)
    return bool(scale > threshold)


def _suspicious_engagement(df: pd.DataFrame, options: dict[str, Any]) -> pd.Series:
    high_views = df["views"].fillna(0) >= float(options["bot_min_views"])
    low_engagement = df["engagement_rate"].notna() & (
        df["engagement_rate"] < float(options["bot_max_engagement_rate"])
    )
    low_likes = df["like_rate"].notna() & (df["like_rate"] <= float(options["bot_max_like_rate"])
    )
    low_comments = df["comment_rate"].notna() & (
        df["comment_rate"] <= float(options["bot_max_comment_rate"])
    )
    return (high_views & low_engagement & low_likes & low_comments).fillna(False)


def _reason(row: pd.Series) -> str:
    norm = float(row["_comment_rate_norm"]) if pd.notna(row["_comment_rate_norm"]) else 0.0
    if norm > 0 and pd.notna(row["comment_rate"]):
        multiplier = float(row["comment_rate"]) / norm
    else:
        multiplier = max(0.0, 1.0 + float(row["comment_rate_z"]))
    comment_pct = 100 * float(row["comment_rate"]) if pd.notna(row["comment_rate"]) else 0.0
    comments = _compact_number(row["comments"])
    prefix = "premium lead; " if bool(row["premium_lead"]) else ""
    return (
        f"{prefix}comment rate {multiplier:.1f}x class norm "
        f"({comment_pct:.1f}% of viewers commented, {comments} comments)"
    )


def _compact_number(value: Any) -> str:
    number = float(value) if pd.notna(value) else 0.0
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}k"
    return f"{number:.0f}"


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="float64" if column == "score" else "object") for column in OUTPUT_COLUMNS})
