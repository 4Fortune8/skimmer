from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.RecomendationAnalysis import metrics


NOW = "2026-07-31T00:00:00Z"


def test_add_duration_flags_marks_shorts_unknowns_and_does_not_mutate():
    df = pd.DataFrame(
        {
            "video_id": ["below", "at_boundary", "above", "missing", "junk"],
            "duration_seconds": [59, 60, 61, None, "N/A"],
        }
    )
    before = df.copy(deep=True)

    out = metrics.add_duration_flags(df)

    pd.testing.assert_frame_equal(df, before)
    assert str(out["is_short"].dtype) == "boolean"
    assert out["is_short"].tolist()[:3] == [True, True, False]
    assert pd.isna(out.loc[3, "is_short"])
    assert pd.isna(out.loc[4, "is_short"])


def test_exclude_shorts_boundary_keeps_long_and_unknown_by_default():
    df = pd.DataFrame(
        {
            "video_id": ["v59", "v60", "v61", "unknown", "long"],
            "duration_seconds": [59, 60, 61, None, 3600],
            "title": ["a", "b", "c", "d", "e"],
        }
    )

    out = metrics.exclude_shorts(df)

    assert out["video_id"].tolist() == ["v61", "unknown", "long"]
    assert list(out.columns) == list(df.columns)


def test_exclude_shorts_can_drop_unknown_duration():
    df = pd.DataFrame(
        {
            "video_id": ["v59", "v60", "v61", "unknown", "long"],
            "duration_seconds": [59, 60, 61, None, 3600],
        }
    )

    out = metrics.exclude_shorts(df, drop_unknown_duration=True)

    assert out["video_id"].tolist() == ["v61", "long"]
    assert list(out.columns) == list(df.columns)


def test_exclude_shorts_honours_custom_duration_boundary():
    df = pd.DataFrame(
        {
            "video_id": ["v179", "v180", "v181", "unknown"],
            "duration_seconds": [179, 180, 181, None],
        }
    )

    out = metrics.exclude_shorts(df, max_duration_seconds=180)

    assert out["video_id"].tolist() == ["v181", "unknown"]
    assert list(out.columns) == list(df.columns)


def test_exclude_shorts_empty_frame_preserves_columns():
    df = pd.DataFrame({"video_id": pd.Series(dtype="string"), "duration_seconds": pd.Series(dtype="float64")})

    out = metrics.exclude_shorts(df)

    assert out.empty
    assert list(out.columns) == list(df.columns)


def test_exclude_shorts_all_shorts_returns_empty_without_helper_column():
    df = pd.DataFrame({"video_id": ["v0", "v60"], "duration_seconds": [0, 60]})

    out = metrics.exclude_shorts(df)

    assert out.empty
    assert list(out.columns) == list(df.columns)


def test_exclude_shorts_treats_non_numeric_duration_as_unknown_not_zero():
    df = pd.DataFrame({"video_id": ["junk", "short", "long"], "duration_seconds": ["N/A", "0", "61"]})

    out = metrics.exclude_shorts(df)

    assert out["video_id"].tolist() == ["junk", "long"]
    assert list(out.columns) == list(df.columns)


def test_safe_ratio_handles_scalars_series_bad_denominators_and_fill():
    assert metrics.safe_ratio(10, 2) == 5
    assert metrics.safe_ratio(-10, 2) == -5
    assert metrics.safe_ratio(10, 0, fill=-1) == -1
    assert metrics.safe_ratio(10, np.nan, fill=-2) == -2

    result = metrics.safe_ratio(pd.Series([10, 10, -9]), pd.Series([2, 0, 3]), fill=-99)
    assert result.tolist() == [5, -99, -3]
    assert not np.isinf(result).any()


def test_add_video_metrics_computes_rates_clips_age_and_avoids_inf():
    df = pd.DataFrame(
        {
            "video_id": ["v1", "fresh", "nosubs", "zeroview"],
            "published_at": [
                "2026-07-21T00:00:00Z",
                "2026-07-30T23:59:00Z",
                "2026-07-21T00:00:00Z",
                "2026-07-21T00:00:00Z",
            ],
            "views": [1000, 100, 500, 0],
            "likes": [50, 5, 10, 0],
            "comments": [10, 1, 1, 0],
            "subscribers": [100, 50, np.nan, 10],
        }
    )
    out = metrics.add_video_metrics(df, now=NOW)
    row = out.loc[out["video_id"].eq("v1")].iloc[0]
    assert row["video_age_days"] == 10
    assert row["views_per_day"] == 100
    assert row["views_per_sub"] == 10
    assert row["like_rate"] == 0.05
    assert row["comment_rate"] == 0.01
    assert row["engagement_rate"] == 0.06
    assert out.loc[out["video_id"].eq("fresh"), "video_age_days"].iloc[0] == 0.5
    assert np.isnan(out.loc[out["video_id"].eq("nosubs"), "views_per_sub"].iloc[0])
    zero = out.loc[out["video_id"].eq("zeroview")].iloc[0]
    assert np.isnan(zero["like_rate"])
    assert np.isnan(zero["comment_rate"])
    assert not np.isinf(out.select_dtypes(include=[float, int]).to_numpy()).any()


def test_add_channel_metrics_handles_missing_publish_date():
    df = pd.DataFrame(
        {
            "channel_published_at": ["2026-06-30T00:00:00Z", np.nan],
            "video_count": [31, 10],
        }
    )
    out = metrics.add_channel_metrics(df, now=NOW)
    assert out.loc[0, "channel_age_days"] == 31
    assert round(out.loc[0, "uploads_per_month"], 3) == round(31 / (31 / 30.44), 3)
    assert np.isnan(out.loc[1, "channel_age_days"])
    assert np.isnan(out.loc[1, "uploads_per_month"])


def test_add_channel_baselines_min_boundary_relative_and_zero_median():
    df = pd.DataFrame(
        {
            "channel_id": ["few", "few", "exact", "exact", "exact", "zero", "zero", "zero"],
            "views": [10, 20, 100, 200, 300, 0, 0, 100],
        }
    )
    out = metrics.add_channel_baselines(df, min_videos=3)
    assert out.loc[out["channel_id"].eq("few"), "channel_median_views"].isna().all()
    assert out.loc[out["channel_id"].eq("few"), "channel_video_sample"].isna().all()
    exact = out.loc[out["channel_id"].eq("exact")]
    assert exact["channel_video_sample"].tolist() == [3, 3, 3]
    assert exact["channel_median_views"].tolist() == [200, 200, 200]
    assert exact.loc[exact["views"].eq(300), "channel_relative_multiple"].iloc[0] == 1.5
    zero = out.loc[out["channel_id"].eq("zero") & out["views"].eq(100)].iloc[0]
    assert np.isnan(zero["channel_relative_multiple"])


def test_add_weight_classes_boundary_values_and_ordered_categories():
    df = pd.DataFrame(
        {
            "subscribers": [
                999, 1000, 1001, 9999, 10000, 49999,
                50000, 199999, 200000, 999999, 1000000, np.nan,
            ],
            "video_count": [49, 50, 299, 300, 999, 1000, np.nan, 49, 50, 299, 300, 999],
        }
    )
    out = metrics.add_weight_classes(df)
    expected_subscribers = {
        999: "<1k",
        1000: "1k-10k",
        1001: "1k-10k",
        9999: "1k-10k",
        10000: "10k-50k",
        49999: "10k-50k",
        50000: "50k-200k",
        199999: "50k-200k",
        200000: "200k-1M",
        999999: "200k-1M",
        1000000: "1M+",
    }
    for value, expected in expected_subscribers.items():
        actual = out.loc[out["subscribers"].eq(value), "sub_class"].astype(str).iloc[0]
        assert actual == expected, f"subscribers={value}"
    assert out.loc[out["subscribers"].isna(), "sub_class"].astype(str).iloc[0] == "unknown"

    expected_video_counts = {
        49: "<50",
        50: "50-300",
        299: "50-300",
        300: "300-1000",
        999: "300-1000",
        1000: "1000+",
    }
    for value, expected in expected_video_counts.items():
        actual = out.loc[out["video_count"].eq(value), "video_count_class"].astype(str).iloc[0]
        assert actual == expected, f"video_count={value}"
    assert out.loc[out["video_count"].isna(), "video_count_class"].astype(str).iloc[0] == "unknown"
    assert out["sub_class"].cat.ordered
    assert list(out["sub_class"].cat.categories) == metrics.SUB_WEIGHT_CLASSES + ["unknown"]
    assert out["video_count_class"].cat.ordered
    assert list(out["video_count_class"].cat.categories) == metrics.VIDEO_COUNT_CLASSES + ["unknown"]


def test_enrich_adds_expected_columns_does_not_mutate_and_has_no_infinities():
    df = pd.DataFrame(
        {
            "video_id": ["a", "b", "c", "d", "e"],
            "channel_id": ["c1"] * 5,
            "published_at": ["2026-07-21T00:00:00Z"] * 5,
            "views": [100, 200, 300, 400, 500],
            "likes": [10, 20, 30, 40, 50],
            "comments": [1, 2, 3, 4, 5],
            "subscribers": [100] * 5,
            "video_count": [10] * 5,
            "channel_published_at": ["2025-07-31T00:00:00Z"] * 5,
        }
    )
    original_columns = set(df.columns)
    out = metrics.enrich(df, now=NOW, min_videos_for_baseline=5)
    assert set(df.columns) == original_columns
    expected = {
        "video_age_days", "views_per_day", "views_per_sub", "like_rate", "comment_rate",
        "engagement_rate", "channel_age_days", "uploads_per_month", "channel_median_views",
        "channel_p90_views", "channel_video_sample", "channel_relative_multiple",
        "sub_class", "video_count_class",
    }
    assert expected.issubset(out.columns)
    assert not np.isinf(out.select_dtypes(include=[float, int]).to_numpy()).any()
