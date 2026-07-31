"""Composite lead orchestration for recommendation analysis.

The six scoring algorithms intentionally use different score scales (percentiles,
log blends, z-score blends, and 0-100 velocity scores). This module runs them
once over a shared enriched frame, normalises each algorithm's score onto a
comparable 0-1 scale, and blends the evidence into one lead list.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

import numpy as np
import pandas as pd

try:
    from . import data_access, metrics, shorts_probe
    from .algorithms import (
        breakout_outliers,
        channel_relative,
        engagement,
        topics,
        velocity,
        weight_class_performance,
    )
except ImportError:  # pragma: no cover
    import data_access  # type: ignore[no-redef]
    import metrics  # type: ignore[no-redef]
    import shorts_probe  # type: ignore[no-redef]
    from algorithms import (  # type: ignore[no-redef]
        breakout_outliers,
        channel_relative,
        engagement,
        topics,
        velocity,
        weight_class_performance,
    )

logger = logging.getLogger(__name__)

ALGORITHMS: dict[str, ModuleType] = {
    "breakout_outliers": breakout_outliers,
    "weight_class_performance": weight_class_performance,
    "channel_relative": channel_relative,
    "velocity": velocity,
    "engagement": engagement,
    "topics": topics,
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "breakout_outliers": 1.35,
    "weight_class_performance": 1.35,
    "channel_relative": 1.05,
    "velocity": 0.85,
    "engagement": 0.60,
    "topics": 0.35,
}

IDENTITY_COLUMNS = [
    "title",
    "channel_id",
    "channel_name",
    "subscribers",
    "video_count",
    "views",
    "comments",
    "views_per_sub",
    "channel_relative_multiple",
    "comment_rate",
    "published_at",
    "video_age_days",
    "sub_class",
    "video_count_class",
    "category_id",
    "video_url",
    "channel_url",
]

BASE_OUTPUT_COLUMNS = ["video_id", "composite_score", "algorithms_hit", "algorithms", "reasons"]
OUTPUT_COLUMNS = (
    BASE_OUTPUT_COLUMNS
    + ["is_short_confirmed"]
    + [f"score_{name}" for name in ALGORITHMS]
    + [f"raw_{name}" for name in ALGORITHMS]
    + IDENTITY_COLUMNS
)


def load_analysis_frame(
    db_path: str | Path | None = None,
    now: pd.Timestamp | str | None = None,
    with_snapshots: bool = True,
    exclude_shorts: bool = True,
    shorts_max_duration_seconds: int = metrics.SHORTS_MAX_DURATION_SECONDS,
    drop_unknown_duration: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Load and enrich the latest joined frame, plus optional video history.

    The database is read once for latest joined rows and, when requested, once
    for all video snapshots so downstream algorithms can share the same frames.

    Shorts are removed before enrichment so that per-channel view baselines and
    weight-class norms describe long-form performance only; a short with a
    million views is a much weaker idea signal than a long-form video with the
    same count. Snapshots are restricted to the surviving videos so the velocity
    algorithm sees a consistent population.
    """

    with data_access.connect(db_path) as conn:
        df = data_access.load_joined(conn=conn)
        snapshots = data_access.load_video_snapshots(conn=conn) if with_snapshots else None
    if exclude_shorts:
        before = len(df)
        df = metrics.exclude_shorts(
            df,
            max_duration_seconds=shorts_max_duration_seconds,
            drop_unknown_duration=drop_unknown_duration,
        )
        logger.info("Excluded %d shorts (<=%ds); %d videos remain.", before - len(df), shorts_max_duration_seconds, len(df))
    df = metrics.enrich(df, now=now)
    df = _coerce_video_id(df)
    if snapshots is not None:
        snapshots = _coerce_video_id(snapshots)
        if exclude_shorts:
            snapshots = snapshots[snapshots["video_id"].isin(set(df["video_id"]))].copy()
    return df, snapshots


def run_algorithms(
    df: pd.DataFrame,
    snapshots: pd.DataFrame | None = None,
    algorithms: Mapping[str, ModuleType] | None = None,
    params: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, pd.DataFrame]:
    """Run scoring algorithms, isolating individual algorithm failures.

    ``velocity`` receives the shared ``snapshots`` frame. ``topics`` receives the
    union of the other algorithms' successful leads so it scores recurring themes
    across already-identified candidates.
    """

    selected = dict(ALGORITHMS if algorithms is None else algorithms)
    overrides = params or {}
    results: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}

    non_topic = [(name, module) for name, module in selected.items() if name != "topics"]
    for name, module in non_topic:
        result = _run_one_algorithm(name, module, df, snapshots=snapshots, params=overrides.get(name, {}))
        if result.attrs.get("error"):
            failures[name] = str(result.attrs["error"])
        results[name] = result

    if "topics" in selected:
        topic_params = dict(overrides.get("topics", {}))
        max_topic_leads = topic_params.pop("max_input_leads", 5_000)
        union_leads = _topic_input_leads(results, max_leads=max_topic_leads)
        result = _run_one_algorithm(
            "topics",
            selected["topics"],
            df,
            leads=union_leads,
            params=topic_params,
        )
        if result.attrs.get("error"):
            failures["topics"] = str(result.attrs["error"])
        results["topics"] = result

    for result in results.values():
        result.attrs["algorithm_failures"] = failures
    return results


def normalize_scores(results: Mapping[str, pd.DataFrame], method: str = "rank") -> dict[str, pd.DataFrame]:
    """Return result frames with ``normalized_score`` in the inclusive 0-1 range.

    Rank percentile is the default because algorithm raw scores have unrelated
    scales and heavy tails; within-algorithm ranks preserve ordering without a
    single extreme value flattening the rest. ``minmax`` is available for callers
    that want magnitude-sensitive normalisation. Single-row and constant-score
    frames normalise to 1.0.
    """

    if method not in {"rank", "minmax"}:
        raise ValueError("method must be 'rank' or 'minmax'.")

    normalized: dict[str, pd.DataFrame] = {}
    for name, frame in results.items():
        result = _standardize_result(frame)
        if result.empty:
            result["normalized_score"] = pd.Series(dtype="float64")
            result.attrs.update(frame.attrs)
            normalized[name] = result
            continue

        scores = pd.to_numeric(result["score"], errors="coerce").replace([np.inf, -np.inf], np.nan)
        if method == "rank":
            valid = scores.notna()
            norm = pd.Series(0.0, index=result.index, dtype="float64")
            if valid.any():
                norm.loc[valid] = scores.loc[valid].rank(method="max", pct=True).astype(float)
        else:
            norm = _minmax(scores)
        result["normalized_score"] = norm.fillna(0.0).clip(0.0, 1.0)
        result = _dedupe_algorithm_result(result)
        result.attrs.update(frame.attrs)
        normalized[name] = result
    return normalized


def combine(
    results: Mapping[str, pd.DataFrame],
    weights: Mapping[str, float] | None = None,
    *,
    source_df: pd.DataFrame | None = None,
    corroboration_bonus: float = 0.12,
    normalize_method: str = "rank",
) -> pd.DataFrame:
    """Outer-join algorithm results into a composite lead frame.

    Missing algorithm hits contribute zero. A corroboration multiplier of
    ``1 + corroboration_bonus * (algorithms_hit - 1)`` rewards independent
    agreement without letting many weak signals dominate the weighted score.
    """

    if corroboration_bonus < 0:
        raise ValueError("corroboration_bonus must be non-negative.")
    selected_weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    prepared = _ensure_normalized(results, normalize_method)
    names = [name for name in ALGORITHMS if name in prepared] + [
        name for name in prepared if name not in ALGORITHMS
    ]
    frames = [_component_frame(name, prepared[name]) for name in names]
    if not frames:
        output = _empty_combined(source_df)
        output.attrs["algorithm_failures"] = _collect_failures(results)
        return output

    combined = frames[0]
    for frame in frames[1:]:
        combined = combined.merge(frame, on="video_id", how="outer", validate="one_to_one")
    if combined.empty:
        output = _empty_combined(source_df)
        output.attrs["algorithm_failures"] = _collect_failures(results)
        return output

    for name in names:
        score_col = f"score_{name}"
        raw_col = f"raw_{name}"
        hit_col = f"_hit_{name}"
        reason_col = f"_reason_{name}"
        if score_col not in combined:
            combined[score_col] = 0.0
        if raw_col not in combined:
            combined[raw_col] = np.nan
        if hit_col not in combined:
            combined[hit_col] = False
        if reason_col not in combined:
            combined[reason_col] = ""
        combined[score_col] = pd.to_numeric(combined[score_col], errors="coerce").fillna(0.0).clip(0.0, 1.0)
        combined[raw_col] = pd.to_numeric(combined[raw_col], errors="coerce")
        combined[hit_col] = combined[hit_col].where(combined[hit_col].notna(), False).astype(bool)
        combined[reason_col] = combined[reason_col].fillna("").astype("string")

    score_cols = [f"score_{name}" for name in names]
    raw_cols = [f"raw_{name}" for name in names]
    hit_cols = [f"_hit_{name}" for name in names]
    reason_cols = [f"_reason_{name}" for name in names]
    combined["algorithms_hit"] = combined[hit_cols].sum(axis=1).astype(int)
    combined["algorithms"] = [
        ",".join(name for name in names if bool(row[f"_hit_{name}"]))
        for _, row in combined.iterrows()
    ]
    combined["reasons"] = [
        " | ".join(str(row[column]) for column in reason_cols if str(row[column]).strip())
        for _, row in combined.iterrows()
    ]

    weighted_sum = pd.Series(0.0, index=combined.index, dtype="float64")
    for name in names:
        weighted_sum += combined[f"score_{name}"] * float(selected_weights.get(name, 1.0))
    multiplier = 1.0 + corroboration_bonus * (combined["algorithms_hit"].clip(lower=1) - 1)
    combined["composite_score"] = (weighted_sum * multiplier).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    identity = _identity_frame(source_df, fallback_results=prepared)
    if not identity.empty:
        combined = combined.merge(identity, on="video_id", how="left", validate="many_to_one")

    for column in IDENTITY_COLUMNS:
        if column not in combined:
            combined[column] = np.nan
    if "is_short_confirmed" not in combined:
        combined["is_short_confirmed"] = pd.Series(pd.NA, index=combined.index, dtype="boolean")
    else:
        combined["is_short_confirmed"] = combined["is_short_confirmed"].astype("boolean")
    for name in ALGORITHMS:
        score_col = f"score_{name}"
        raw_col = f"raw_{name}"
        if score_col not in combined:
            combined[score_col] = 0.0
        if raw_col not in combined:
            combined[raw_col] = np.nan

    combined = combined[combined["algorithms_hit"].gt(0)].copy()
    if combined.empty:
        output = _empty_combined(source_df)
        output.attrs["algorithm_failures"] = _collect_failures(results)
        return output
    combined["video_id"] = combined["video_id"].astype("string")
    combined = combined.replace([np.inf, -np.inf], np.nan)
    combined["composite_score"] = pd.to_numeric(combined["composite_score"], errors="coerce").fillna(0.0)
    combined = combined.sort_values(
        ["composite_score", "algorithms_hit", "views"], ascending=[False, False, False]
    ).drop_duplicates("video_id", keep="first")
    output = combined[OUTPUT_COLUMNS].reset_index(drop=True)
    output.attrs["algorithm_failures"] = _collect_failures(results)
    return output


def generate_leads(
    db_path: str | Path | None = None,
    top_n: int | None = 100,
    weights: Mapping[str, float] | None = None,
    params: Mapping[str, Mapping[str, Any]] | None = None,
    filters: Mapping[str, Any] | None = None,
    now: pd.Timestamp | str | None = None,
    export_path: str | Path | None = None,
    exclude_shorts: bool = True,
    shorts_max_duration_seconds: int = metrics.SHORTS_MAX_DURATION_SECONDS,
    drop_unknown_duration: bool = False,
    verify_shorts: bool = True,
    verify_shorts_max_workers: int = shorts_probe.DEFAULT_MAX_WORKERS,
    verify_shorts_timeout: float = shorts_probe.DEFAULT_TIMEOUT,
    verify_shorts_cache: Any = None,
    verify_shorts_over_fetch: float = 2.0,
) -> pd.DataFrame:
    """Run the full composite lead pipeline and optionally export a CSV.

    When ``verify_shorts`` is enabled, Shorts URL verification runs after all
    scoring/filtering and only on the final candidate list. For finite
    ``top_n`` calls, the candidate list is over-fetched by
    ``verify_shorts_over_fetch`` before confirmed Shorts are dropped, then the
    surviving rows are truncated back to ``top_n``.
    """

    df, snapshots = load_analysis_frame(
        db_path=db_path,
        now=now,
        with_snapshots=True,
        exclude_shorts=exclude_shorts,
        shorts_max_duration_seconds=shorts_max_duration_seconds,
        drop_unknown_duration=drop_unknown_duration,
    )
    results = run_algorithms(df, snapshots=snapshots, params=params)
    normalized = normalize_scores(results)
    leads = combine(normalized, weights=weights, source_df=df)
    leads = _apply_filters(leads, filters)
    if verify_shorts:
        leads = _verify_shorts_for_final_candidates(
            leads,
            top_n=top_n,
            max_workers=verify_shorts_max_workers,
            timeout=verify_shorts_timeout,
            cache=verify_shorts_cache,
            over_fetch=verify_shorts_over_fetch,
        )
    elif top_n is not None:
        leads = leads.head(int(top_n)).copy()
    if not verify_shorts and "is_short_confirmed" not in leads.columns:
        leads["is_short_confirmed"] = pd.Series(pd.NA, index=leads.index, dtype="boolean")
    leads = leads.reset_index(drop=True)
    leads.attrs["algorithm_counts"] = {name: len(frame) for name, frame in results.items()}
    leads.attrs["algorithm_failures"] = _collect_failures(results)
    if export_path is not None:
        path = _default_export_path() if str(export_path).lower() in {"", "true", "default"} else Path(export_path)
        leads.attrs["export_path"] = str(export_leads(leads, path))
    return leads


def export_leads(df: pd.DataFrame, path: str | Path) -> Path:
    """Write leads to CSV and return the resolved output path."""

    output_path = Path(path)
    if output_path.is_dir() or str(path).endswith("/"):
        output_path = output_path / _default_export_path().name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    logger.info("Wrote %d leads to %s", len(df), output_path)
    return output_path


def _run_one_algorithm(
    name: str,
    module: ModuleType,
    df: pd.DataFrame,
    *,
    snapshots: pd.DataFrame | None = None,
    leads: pd.DataFrame | None = None,
    params: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    try:
        kwargs = dict(params or {})
        if name == "velocity":
            result = module.score(df, snapshots=snapshots, **kwargs)
        elif name == "topics":
            result = module.score(df, leads=leads, **kwargs)
        else:
            result = module.score(df, **kwargs)
        result = _standardize_result(result)
        logger.info("%s produced %d leads.", name, len(result))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("Algorithm %s failed; continuing with empty result.", name)
        empty = pd.DataFrame(columns=["video_id", "score", "reason"])
        empty.attrs["error"] = str(exc)
        return empty


def _standardize_result(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ("video_id", "score", "reason"):
        if column not in result:
            result[column] = pd.Series(dtype="object")
    result = _coerce_video_id(result)
    result["score"] = pd.to_numeric(result["score"], errors="coerce")
    result["reason"] = result["reason"].fillna("").astype("string")
    result.attrs.update(frame.attrs)
    return result


def _coerce_video_id(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    if "video_id" in result.columns:
        result["video_id"] = result["video_id"].astype("string")
    return result


def _dedupe_algorithm_result(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    result["_score_sort"] = pd.to_numeric(result["score"], errors="coerce").fillna(-np.inf)
    result = result.sort_values(["video_id", "_score_sort"], ascending=[True, False])
    result = result.drop_duplicates("video_id", keep="first").drop(columns="_score_sort")
    return result.reset_index(drop=True)


def _verify_shorts_for_final_candidates(
    leads: pd.DataFrame,
    *,
    top_n: int | None,
    max_workers: int,
    timeout: float,
    cache: Any,
    over_fetch: float,
) -> pd.DataFrame:
    if leads.empty:
        result = leads.copy()
        result["is_short_confirmed"] = pd.Series(pd.NA, index=result.index, dtype="boolean")
        result.attrs["shorts_probe"] = {"probed": 0, "confirmed_shorts": 0, "undetermined": 0, "dropped": 0}
        return result

    candidate_count = len(leads) if top_n is None else min(len(leads), _shorts_probe_limit(int(top_n), over_fetch))
    candidates = leads.head(candidate_count).copy()
    statuses = shorts_probe.probe_videos(
        candidates["video_id"].dropna().astype("string").tolist(),
        max_workers=max_workers,
        timeout=timeout,
        cache=cache,
    )
    status_values = pd.Series(candidates["video_id"].astype("string").map(statuses), index=candidates.index, dtype="boolean")
    candidates["is_short_confirmed"] = status_values
    confirmed = int(status_values.fillna(False).sum())
    undetermined = int(status_values.isna().sum())
    verified = candidates.loc[~candidates["is_short_confirmed"].fillna(False)].copy()
    if top_n is not None:
        verified = verified.head(int(top_n)).copy()
    diagnostics = {
        "probed": len(statuses),
        "confirmed_shorts": confirmed,
        "undetermined": undetermined,
        "dropped": confirmed,
    }
    verified.attrs.update(leads.attrs)
    verified.attrs["shorts_probe"] = diagnostics
    return verified.reset_index(drop=True)


def _shorts_probe_limit(top_n: int, over_fetch: float) -> int:
    if top_n <= 0:
        return 0
    multiplier = max(1.0, float(over_fetch))
    return max(top_n, int(math.ceil(top_n * multiplier)))


def _minmax(scores: pd.Series) -> pd.Series:
    valid = scores.notna()
    norm = pd.Series(0.0, index=scores.index, dtype="float64")
    if not valid.any():
        return norm
    minimum = float(scores.loc[valid].min())
    maximum = float(scores.loc[valid].max())
    if not np.isfinite(minimum) or not np.isfinite(maximum) or maximum == minimum:
        norm.loc[valid] = 1.0
        return norm
    norm.loc[valid] = (scores.loc[valid] - minimum) / (maximum - minimum)
    return norm


def _ensure_normalized(
    results: Mapping[str, pd.DataFrame], normalize_method: str
) -> dict[str, pd.DataFrame]:
    if all("normalized_score" in frame.columns for frame in results.values()):
        return {name: _standardize_result(frame) for name, frame in results.items()}
    return normalize_scores(results, method=normalize_method)


def _component_frame(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    result = _dedupe_algorithm_result(_standardize_result(frame))
    columns = ["video_id", f"score_{name}", f"raw_{name}", f"_hit_{name}", f"_reason_{name}"]
    if result.empty:
        return pd.DataFrame(columns=columns).astype({"video_id": "string"})
    component = pd.DataFrame(
        {
            "video_id": result["video_id"].astype("string"),
            f"score_{name}": pd.to_numeric(result.get("normalized_score"), errors="coerce")
            .fillna(0.0)
            .clip(0.0, 1.0),
            f"raw_{name}": pd.to_numeric(result["score"], errors="coerce"),
            f"_hit_{name}": True,
            f"_reason_{name}": result["reason"].fillna("").astype("string"),
        }
    )
    return component


def _union_leads(results: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in results.values() if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=["video_id"])
    union = pd.concat(frames, ignore_index=True, sort=False)
    union = _coerce_video_id(union)
    if "score" in union.columns:
        union["_score_sort"] = pd.to_numeric(union["score"], errors="coerce").fillna(-np.inf)
        union = union.sort_values(["video_id", "_score_sort"], ascending=[True, False])
        union = union.drop(columns="_score_sort")
    return union.drop_duplicates("video_id", keep="first").reset_index(drop=True)


def _topic_input_leads(
    results: Mapping[str, pd.DataFrame],
    max_leads: int | None = 5_000,
) -> pd.DataFrame:
    """Return the cross-algorithm lead set passed into topic aggregation.

    The full union is used by default for ordinary/small runs. On the real
    18k+ lead union, the current topic implementation can exceed small-machine
    memory limits while building title-term matrices, so the orchestrator keeps
    a balanced high-confidence subset unless callers pass
    ``params={"topics": {"max_input_leads": None}}``.
    """

    union = _union_leads(results)
    if max_leads is None or len(union) <= int(max_leads):
        return union

    limit = int(max_leads)
    names = [name for name in results if name != "topics" and not results[name].empty]
    if not names:
        return union.head(limit).copy()

    quota = max(1, limit // len(names))
    sampled = []
    for name in names:
        frame = _standardize_result(results[name])
        frame["_score_sort"] = pd.to_numeric(frame["score"], errors="coerce").fillna(-np.inf)
        sampled.append(frame.sort_values("_score_sort", ascending=False).drop(columns="_score_sort").head(quota))
    selected = _union_leads({str(index): frame for index, frame in enumerate(sampled)})
    if len(selected) < limit:
        selected = pd.concat([selected, union], ignore_index=True, sort=False)
        selected = _coerce_video_id(selected).drop_duplicates("video_id", keep="first")
    return selected.head(limit).reset_index(drop=True)


def _identity_frame(
    source_df: pd.DataFrame | None,
    *,
    fallback_results: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    if source_df is not None and not source_df.empty and "video_id" in source_df.columns:
        identity = source_df.copy()
    else:
        frames = [frame for frame in fallback_results.values() if not frame.empty]
        if not frames:
            return pd.DataFrame(columns=["video_id", *IDENTITY_COLUMNS])
        identity = pd.concat(frames, ignore_index=True, sort=False)
    identity = _coerce_video_id(identity)
    for column in IDENTITY_COLUMNS:
        if column not in identity:
            identity[column] = np.nan
    identity["video_url"] = identity.get("video_url", pd.Series(index=identity.index, dtype="object"))
    identity["channel_url"] = identity.get("channel_url", pd.Series(index=identity.index, dtype="object"))
    if "channel_id" in identity.columns:
        identity["video_url"] = identity["video_url"].where(
            identity["video_url"].notna(), identity["video_id"].astype("string").map(data_access.video_url)
        )
        identity["channel_url"] = identity["channel_url"].where(
            identity["channel_url"].notna(), identity["channel_id"].astype("string").map(data_access.channel_url)
        )
    sort_col = "collected_at" if "collected_at" in identity.columns else "published_at"
    if sort_col in identity.columns:
        identity = identity.sort_values(["video_id", sort_col], ascending=[True, False])
    return identity[["video_id", *IDENTITY_COLUMNS]].drop_duplicates("video_id", keep="first")


def _empty_combined(source_df: pd.DataFrame | None = None) -> pd.DataFrame:
    output = pd.DataFrame(columns=OUTPUT_COLUMNS)
    if source_df is not None and "sub_class" in source_df.columns:
        for column in ("sub_class", "video_count_class"):
            if column in source_df.columns and pd.api.types.is_categorical_dtype(source_df[column]):
                output[column] = pd.Categorical([], categories=source_df[column].cat.categories, ordered=True)
    return output


def _apply_filters(df: pd.DataFrame, filters: Mapping[str, Any] | None) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    options = {"max_per_channel": 3, **(filters or {})}
    result = df.copy()
    mask = pd.Series(True, index=result.index)
    if options.get("max_subscribers") is not None:
        mask &= pd.to_numeric(result["subscribers"], errors="coerce").le(float(options["max_subscribers"]))
    if options.get("min_subscribers") is not None:
        mask &= pd.to_numeric(result["subscribers"], errors="coerce").ge(float(options["min_subscribers"]))
    if options.get("min_views") is not None:
        mask &= pd.to_numeric(result["views"], errors="coerce").ge(float(options["min_views"]))
    if options.get("max_video_age_days") is not None:
        mask &= pd.to_numeric(result["video_age_days"], errors="coerce").le(float(options["max_video_age_days"]))
    if options.get("sub_class") is not None:
        allowed = _as_list(options["sub_class"])
        mask &= result["sub_class"].astype("string").isin([str(value) for value in allowed])
    if options.get("category_id") is not None:
        allowed = _as_list(options["category_id"])
        mask &= result["category_id"].astype("string").isin([str(value) for value in allowed])
    result = result.loc[mask].copy()
    max_per_channel = options.get("max_per_channel")
    if max_per_channel is not None and "channel_id" in result.columns:
        result = result.groupby("channel_id", dropna=False, sort=False).head(int(max_per_channel))
    return result.reset_index(drop=True)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set, pd.Index, pd.Series)):
        return list(value)
    return [value]


def _default_export_path() -> Path:
    timestamp = pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M%S")
    return Path(__file__).resolve().parents[2] / "data" / "output" / f"video_idea_leads_{timestamp}.csv"


def _collect_failures(results: Mapping[str, pd.DataFrame]) -> dict[str, str]:
    failures: dict[str, str] = {}
    for name, frame in results.items():
        if frame.attrs.get("error"):
            failures[name] = str(frame.attrs["error"])
        failures.update({key: str(value) for key, value in frame.attrs.get("algorithm_failures", {}).items()})
    return failures
