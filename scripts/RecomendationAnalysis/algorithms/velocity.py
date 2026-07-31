"""Lead scoring based on publish velocity and observed re-acceleration."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

try:
    from .. import data_access
    from .. import metrics
except ImportError:  # pragma: no cover
    import data_access
    import metrics

logger = logging.getLogger(__name__)

ALGORITHM_NAME = "velocity"

DEFAULT_PARAMS: dict[str, Any] = {
    "min_views": 10_000,
    "max_video_age_days": 90.0,
    "evergreen_min_age_days": 30.0,
    "min_group_size": 30,
    "min_interval_days": 0.5,
    "publish_weight": 0.4,
    "acceleration_weight": 0.6,
    "min_score": 80.0,
    "use_video_count_class": True,
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
    "views_per_day",
    "observed_views_per_day",
    "lifetime_views_per_day",
    "acceleration_ratio",
    "snapshot_count",
    "observation_span_days",
    "video_age_days",
    "sub_class",
    "published_at",
    "video_url",
    "channel_url",
    "publish_velocity_score",
    "publish_percentile",
    "acceleration_score",
    "negative_delta_clipped",
]

DF_REQUIRED_COLUMNS = {
    "video_id",
    "channel_id",
    "title",
    "channel_name",
    "subscribers",
    "video_count",
    "views",
    "comments",
    "views_per_day",
    "video_age_days",
    "sub_class",
    "published_at",
}

SNAPSHOT_REQUIRED_COLUMNS = {"video_id", "collected_at", "views"}


def score(df: pd.DataFrame, snapshots: pd.DataFrame | None = None, **params: Any) -> pd.DataFrame:
    """Return velocity-qualified video leads sorted by descending score."""

    config = {**DEFAULT_PARAMS, **params}
    _require_columns(df, DF_REQUIRED_COLUMNS, "df")
    if df.empty:
        return _empty_output()

    result = df.copy()
    result = _add_publish_velocity(
        result,
        max_video_age_days=float(config["max_video_age_days"]),
        min_group_size=int(config["min_group_size"]),
        use_video_count_class=bool(config["use_video_count_class"]),
    )
    result["views"] = pd.to_numeric(result["views"], errors="coerce")
    result["comments"] = pd.to_numeric(result["comments"], errors="coerce")
    result["video_age_days"] = pd.to_numeric(result["video_age_days"], errors="coerce").clip(lower=0.5)
    result["lifetime_views_per_day"] = metrics.safe_ratio(result["views"], result["video_age_days"])

    if snapshots is None:
        logger.info("No snapshots supplied; scoring velocity with publish-rate signal only.")
        history = pd.DataFrame(columns=["video_id"])
    elif snapshots.empty:
        logger.info("Empty snapshots supplied; no velocity candidates can be scored.")
        return _empty_output()
    else:
        _require_columns(snapshots, SNAPSHOT_REQUIRED_COLUMNS, "snapshots")
        history = _observed_velocity(
            snapshots,
            candidate_video_ids=result["video_id"],
            min_interval_days=float(config["min_interval_days"]),
        )
        if history.empty:
            logger.info("Snapshots supplied but none overlapped df with usable video ids.")

    if not history.empty:
        result = result.merge(history, on="video_id", how="left", validate="one_to_one")
    else:
        for column in (
            "observed_views_per_day",
            "snapshot_count",
            "observation_span_days",
            "negative_delta_clipped",
        ):
            result[column] = np.nan

    result["snapshot_count"] = pd.to_numeric(result["snapshot_count"], errors="coerce").fillna(0).astype(int)
    result["observation_span_days"] = pd.to_numeric(
        result["observation_span_days"], errors="coerce"
    ).fillna(0.0)
    result["negative_delta_clipped"] = pd.Series(
        np.where(result["negative_delta_clipped"].isna(), False, result["negative_delta_clipped"]),
        index=result.index,
    ).astype(bool)
    result["observed_views_per_day"] = pd.to_numeric(result["observed_views_per_day"], errors="coerce")
    result["acceleration_ratio"] = metrics.safe_ratio(
        result["observed_views_per_day"], result["lifetime_views_per_day"]
    )
    result["acceleration_score"] = _acceleration_score(
        result["acceleration_ratio"],
        result["video_age_days"],
        evergreen_min_age_days=float(config["evergreen_min_age_days"]),
    )
    result["score"] = _blend_scores(
        result["publish_velocity_score"],
        result["acceleration_score"],
        publish_weight=float(config["publish_weight"]),
        acceleration_weight=float(config["acceleration_weight"]),
    )

    candidates = result[
        (result["views"] >= float(config["min_views"]))
        & (result["score"] >= float(config["min_score"]))
        & np.isfinite(result["score"])
    ].copy()
    if candidates.empty:
        return _empty_output()

    candidates["reason"] = [_reason(row) for _, row in candidates.iterrows()]
    candidates["video_url"] = candidates["video_id"].astype("string").map(data_access.video_url)
    candidates["channel_url"] = candidates["channel_id"].astype("string").map(data_access.channel_url)
    candidates["score"] = candidates["score"].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    candidates = candidates.sort_values("score", ascending=False).drop_duplicates("video_id", keep="first")
    return candidates[OUTPUT_COLUMNS].reset_index(drop=True)


def _add_publish_velocity(
    df: pd.DataFrame,
    max_video_age_days: float,
    min_group_size: int,
    use_video_count_class: bool,
) -> pd.DataFrame:
    """Add robust within-peer publish velocity scores."""

    result = df.copy()
    result["views_per_day"] = pd.to_numeric(result["views_per_day"], errors="coerce")
    result["video_age_days"] = pd.to_numeric(result["video_age_days"], errors="coerce")

    group_columns = ["sub_class"]
    if use_video_count_class and "video_count_class" in result.columns:
        group_columns.append("video_count_class")

    global_values = result["views_per_day"].replace([np.inf, -np.inf], np.nan).dropna()
    global_median = float(global_values.median()) if not global_values.empty else 0.0
    global_mad = _mad(global_values)
    if not np.isfinite(global_mad) or global_mad <= 0:
        global_mad = 1.0
    global_percentile = result["views_per_day"].rank(pct=True).fillna(0.0)

    grouped = result.groupby(group_columns, observed=True)["views_per_day"]
    count = grouped.transform("count")
    median = grouped.transform("median")
    mad = grouped.transform(_mad)
    percentile = grouped.rank(pct=True)
    group_label = result[group_columns].astype("string").agg("/".join, axis=1)

    if len(group_columns) > 1:
        sub_grouped = result.groupby("sub_class", observed=True)["views_per_day"]
        sub_count = sub_grouped.transform("count")
        sub_median = sub_grouped.transform("median")
        sub_mad = sub_grouped.transform(_mad)
        sub_percentile = sub_grouped.rank(pct=True)
        use_sub = (count < min_group_size) & (sub_count >= min_group_size)
        median = median.where(~use_sub, sub_median)
        mad = mad.where(~use_sub, sub_mad)
        percentile = percentile.where(~use_sub, sub_percentile)
        group_label = group_label.where(~use_sub, result["sub_class"].astype("string"))
        count = count.where(~use_sub, sub_count)

    use_global = (count < min_group_size) | (mad <= 0) | ~np.isfinite(mad)
    median = median.where(~use_global, global_median)
    mad = mad.where(~use_global, global_mad)
    percentile = percentile.where(~use_global, global_percentile)
    group_label = group_label.where(~use_global, "global")

    robust_scale = (1.4826 * mad).replace(0, global_mad)
    z_score = metrics.safe_ratio(result["views_per_day"] - median, robust_scale, fill=0.0)
    positive_z = pd.Series(z_score).clip(lower=0)
    result["publish_velocity_score"] = 100.0 * positive_z / (positive_z + 25.0)
    result["publish_velocity_score"] = result["publish_velocity_score"].where(
        result["video_age_days"] <= max_video_age_days, np.nan
    )
    result["publish_percentile"] = percentile.clip(0, 1).fillna(0.0)
    result["publish_group"] = group_label
    return result


def _observed_velocity(
    snapshots: pd.DataFrame,
    candidate_video_ids: pd.Series,
    min_interval_days: float,
) -> pd.DataFrame:
    """Calculate recent observed velocity from multi-snapshot history."""

    ids = set(candidate_video_ids.dropna().astype(str))
    snap = snapshots.loc[:, ["video_id", "collected_at", "views"]].copy()
    snap["video_id"] = snap["video_id"].astype("string")
    snap = snap[snap["video_id"].astype(str).isin(ids)]
    if snap.empty:
        return pd.DataFrame(columns=["video_id"])

    snap["collected_at"] = pd.to_datetime(snap["collected_at"], utc=True, errors="coerce", format="mixed")
    snap["views"] = pd.to_numeric(snap["views"], errors="coerce")
    snap = snap.dropna(subset=["video_id", "collected_at", "views"])
    if snap.empty:
        return pd.DataFrame(columns=["video_id"])

    grouped = snap.groupby("video_id", observed=True)
    summary = grouped.agg(
        snapshot_count=("views", "size"),
        first_collected_at=("collected_at", "min"),
        last_collected_at=("collected_at", "max"),
    )
    summary["observation_span_days"] = (
        summary["last_collected_at"] - summary["first_collected_at"]
    ).dt.total_seconds() / 86_400

    snap = snap.sort_values(["video_id", "collected_at"])
    grouped = snap.groupby("video_id", observed=True)
    snap["delta_views"] = grouped["views"].diff()
    snap["delta_days"] = grouped["collected_at"].diff().dt.total_seconds() / 86_400
    negative = snap["delta_views"] < 0
    negative_by_video = negative.groupby(snap["video_id"], observed=True).any()
    valid_interval = snap["delta_days"].ge(min_interval_days) & snap["delta_views"].notna()
    intervals = snap[valid_interval].copy()
    if intervals.empty:
        summary["observed_views_per_day"] = np.nan
    else:
        intervals["observed_views_per_day"] = intervals["delta_views"].clip(lower=0) / intervals["delta_days"]
        latest = intervals.drop_duplicates("video_id", keep="last").set_index("video_id")
        summary["observed_views_per_day"] = latest["observed_views_per_day"]

    negative_aligned = negative_by_video.reindex(summary.index)
    summary["negative_delta_clipped"] = pd.Series(
        np.where(negative_aligned.isna(), False, negative_aligned),
        index=summary.index,
    )
    return summary.reset_index()[
        [
            "video_id",
            "observed_views_per_day",
            "snapshot_count",
            "observation_span_days",
            "negative_delta_clipped",
        ]
    ]


def _acceleration_score(
    acceleration_ratio: pd.Series,
    video_age_days: pd.Series,
    evergreen_min_age_days: float,
) -> pd.Series:
    ratio = pd.to_numeric(acceleration_ratio, errors="coerce")
    age = pd.to_numeric(video_age_days, errors="coerce")
    log_gain = np.log2(ratio.clip(lower=1.0))
    score_value = 100.0 * (1.0 - np.exp(-log_gain))
    return pd.Series(score_value, index=ratio.index).where(age >= evergreen_min_age_days, np.nan).fillna(0.0)


def _blend_scores(
    publish_score: pd.Series,
    acceleration_score: pd.Series,
    publish_weight: float,
    acceleration_weight: float,
) -> pd.Series:
    publish = pd.to_numeric(publish_score, errors="coerce")
    acceleration = pd.to_numeric(acceleration_score, errors="coerce")
    publish_available = publish.notna()
    acceleration_available = acceleration.notna() & (acceleration > 0)
    numerator = publish.fillna(0.0) * publish_weight + acceleration.fillna(0.0) * acceleration_weight
    denominator = (
        publish_available.astype(float) * publish_weight
        + acceleration_available.astype(float) * acceleration_weight
    )
    blended = metrics.safe_ratio(numerator, denominator, fill=np.nan)
    return pd.Series(blended).replace([np.inf, -np.inf], np.nan)


def _reason(row: pd.Series) -> str:
    ratio = row.get("acceleration_ratio")
    if pd.notna(ratio) and ratio > 1 and row.get("acceleration_score", 0) > 0:
        snapshots = int(row.get("snapshot_count", 0))
        span = row.get("observation_span_days", 0.0)
        return f"accelerating: {ratio:.1f}x its lifetime rate across {snapshots} snapshots over {span:.0f}d"

    percentile = row.get("publish_percentile", 0.0)
    top_pct = max(1, int(np.ceil((1.0 - float(percentile)) * 100)))
    group = row.get("publish_group") or row.get("sub_class") or "peer"
    return f"top {top_pct}% views/day in {group} class"


def _mad(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if numeric.empty:
        return np.nan
    median = numeric.median()
    return float((numeric - median).abs().median())


def _require_columns(df: pd.DataFrame, columns: set[str], name: str) -> None:
    missing = sorted(columns.difference(df.columns))
    if missing:
        raise KeyError(f"{name} is missing required columns: {missing}")


def _empty_output() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)
