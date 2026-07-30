"""Lead scoring by performance within subscriber and upload-count peer groups."""

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

ALGORITHM_NAME = "weight_class_performance"

DEFAULT_PARAMS: dict[str, Any] = {
    "group_keys": ("sub_class", "video_count_class"),
    "weights": {
        "views": 0.5,
        "views_per_sub": 2.5,
        "comment_rate": 0.5,
        "views_per_day": 0.7,
    },
    "min_components": 2,
    "min_group_size": 30,
    "include_unknown_classes": False,
    "min_percentile": 0.99,
    "min_views": 10_000,
}

PERCENTILE_COLUMNS = {
    "views": "pct_views",
    "views_per_day": "pct_views_per_day",
    "comment_rate": "pct_comment_rate",
    "views_per_sub": "pct_views_per_sub",
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
    "comment_rate",
    "sub_class",
    "video_count_class",
    "group_size",
    "composite_raw",
    "pct_views",
    "pct_views_per_day",
    "pct_comment_rate",
    "pct_views_per_sub",
    "published_at",
    "video_url",
    "channel_url",
]


def score(df: pd.DataFrame, **params: Any) -> pd.DataFrame:
    """Return videos outperforming their subscriber/upload-count peer group.

    First, each signal is percentile-ranked within ``group_keys`` and combined
    into ``composite_raw`` as a weighted mean over available components. Then
    ``composite_raw`` is percentile-ranked within the same peer group to produce
    ``score``, so ``score >= 0.99`` means top 1% of that weight class. Unknown
    classes are dropped by default.
    """

    settings = {**DEFAULT_PARAMS, **params}
    group_keys = tuple(settings["group_keys"])
    weights = _normalized_weights(settings["weights"])
    min_components = int(settings["min_components"])
    min_group_size = int(settings["min_group_size"])
    min_percentile = float(settings["min_percentile"])
    min_views = float(settings["min_views"])
    include_unknown_classes = bool(settings["include_unknown_classes"])

    _validate_params(group_keys, weights, min_components, min_group_size, min_percentile)
    if df.empty:
        return _empty_result()

    required = set(group_keys) | set(PERCENTILE_COLUMNS) | {
        "video_id",
        "channel_id",
        "title",
        "channel_name",
        "subscribers",
        "video_count",
        "comments",
        "published_at",
    }
    _require_columns(df, required)

    result = df.copy()
    for column in PERCENTILE_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["views"] = pd.to_numeric(result["views"], errors="coerce")

    if not include_unknown_classes:
        known_mask = pd.Series(True, index=result.index)
        for key in group_keys:
            known_mask &= result[key].astype("string").ne("unknown")
        result = result.loc[known_mask].copy()

    if result.empty:
        return _empty_result()

    grouped = result.groupby(list(group_keys), observed=True, dropna=False)
    result["group_size"] = grouped["video_id"].transform("size")
    for source, percentile_column in PERCENTILE_COLUMNS.items():
        result[percentile_column] = grouped[source].rank(pct=True)

    component_columns = [PERCENTILE_COLUMNS[source] for source in weights]
    weighted_values = pd.DataFrame(index=result.index)
    weighted_present = pd.DataFrame(index=result.index)
    for source, percentile_column in zip(weights, component_columns, strict=True):
        values = pd.to_numeric(result[percentile_column], errors="coerce")
        weight = weights[source]
        weighted_values[percentile_column] = values * weight
        weighted_present[percentile_column] = values.notna().astype(float) * weight

    present_components = result[component_columns].notna().sum(axis=1)
    weight_sum = weighted_present.sum(axis=1)
    result["composite_raw"] = weighted_values.sum(axis=1).div(weight_sum).where(weight_sum > 0)
    result["composite_raw"] = result["composite_raw"].where(present_components >= min_components)
    result["score"] = result.groupby(list(group_keys), observed=True, dropna=False)[
        "composite_raw"
    ].rank(pct=True)

    qualified = result.loc[
        (result["group_size"] >= min_group_size)
        & (result["score"] >= min_percentile)
        & (result["views"] >= min_views)
        & np.isfinite(result["score"])
    ].copy()

    if qualified.empty:
        return _empty_result()

    qualified["video_url"] = qualified["video_id"].map(data_access.video_url)
    qualified["channel_url"] = qualified["channel_id"].map(data_access.channel_url)
    qualified["reason"] = qualified.apply(lambda row: _reason(row, group_keys), axis=1)

    qualified = qualified.sort_values(
        ["score", "views_per_sub", "composite_raw", "views"],
        ascending=[False, False, False, False],
    )
    qualified = qualified.drop_duplicates("video_id", keep="first")
    logger.debug("Selected %d weight-class performance leads.", len(qualified))
    return qualified.loc[:, OUTPUT_COLUMNS].reset_index(drop=True)


def _normalized_weights(weights: dict[str, float]) -> dict[str, float]:
    normalized = {}
    for key, value in weights.items():
        source = key.removeprefix("pct_")
        if source not in PERCENTILE_COLUMNS:
            raise ValueError(f"Unknown weight component: {key!r}")
        numeric_weight = float(value)
        if not np.isfinite(numeric_weight) or numeric_weight <= 0:
            raise ValueError(f"Weight for {key!r} must be a positive finite number.")
        normalized[source] = numeric_weight
    if not normalized:
        raise ValueError("weights must include at least one percentile component.")
    return normalized


def _validate_params(
    group_keys: tuple[str, ...],
    weights: dict[str, float],
    min_components: int,
    min_group_size: int,
    min_percentile: float,
) -> None:
    if not group_keys:
        raise ValueError("group_keys must include at least one column.")
    if min_components < 1 or min_components > len(weights):
        raise ValueError("min_components must be between 1 and the number of weighted components.")
    if min_group_size < 1:
        raise ValueError("min_group_size must be at least 1.")
    if not 0 <= min_percentile <= 1:
        raise ValueError("min_percentile must be between 0 and 1.")


def _require_columns(df: pd.DataFrame, required: set[str]) -> None:
    missing = sorted(required.difference(df.columns))
    if missing:
        raise KeyError(f"weight_class_performance requires columns: {', '.join(missing)}")


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def _reason(row: pd.Series, group_keys: tuple[str, ...]) -> str:
    top_percent = max((1 - float(row["score"])) * 100, 0)
    return (
        f"top {top_percent:.1f}% of {_group_label(row, group_keys)} peer group "
        f"(n={int(row['group_size']):,})"
    )


def _group_label(row: pd.Series, group_keys: tuple[str, ...]) -> str:
    labels = []
    for key in group_keys:
        value = row[key]
        if key == "sub_class":
            labels.append(f"{value} subs")
        elif key == "video_count_class":
            labels.append(f"{value} videos")
        else:
            labels.append(f"{key}={value}")
    return " / ".join(labels)
