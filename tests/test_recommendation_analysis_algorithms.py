from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.RecomendationAnalysis import metrics
from scripts.RecomendationAnalysis.algorithms import (
    breakout_outliers,
    channel_relative,
    engagement,
    topics,
    velocity,
    weight_class_performance,
)


NOW = "2026-07-31T00:00:00Z"
ALGORITHMS = [
    breakout_outliers,
    weight_class_performance,
    channel_relative,
    velocity,
    engagement,
    topics,
]


def output_columns(module):
    if hasattr(module, "OUTPUT_COLUMNS"):
        return module.OUTPUT_COLUMNS
    return module._SCORE_COLUMNS


@pytest.fixture()
def enriched_frame():
    rows = []
    for i in range(40):
        rows.append(
            {
                "video_id": f"v{i}",
                "channel_id": f"c{i % 8}",
                "title": f"quiet robotics garden {i}",
                "channel_name": f"Channel {i % 8}",
                "published_at": "2026-07-01T00:00:00Z",
                "duration_seconds": 60,
                "category_id": "22",
                "views": 10_000 + i * 1_000,
                "likes": 200 + i,
                "comments": 60 + i,
                "subscribers": 1_000 + i * 100,
                "video_count": 100,
                "channel_published_at": "2024-01-01T00:00:00Z",
            }
        )
    return metrics.enrich(pd.DataFrame(rows), now=NOW, min_videos_for_baseline=2)


def call_score(module, df, **params):
    if module is velocity:
        return module.score(df, snapshots=None, **params)
    if module is topics:
        return module.score(df, leads=df.head(5), use_sklearn=False, min_lead_count=1, min_distinct_channels=1, **params)
    return module.score(df, **params)


@pytest.mark.parametrize("module", ALGORITHMS)
def test_algorithm_contracts_for_empty_sorted_unique_finite_and_no_mutation(module, enriched_frame):
    assert isinstance(module.ALGORITHM_NAME, str) and module.ALGORITHM_NAME
    assert isinstance(module.DEFAULT_PARAMS, dict)

    empty = enriched_frame.head(0).copy()
    empty_result = call_score(module, empty)
    assert list(empty_result.columns) == output_columns(module)
    assert empty_result.empty

    before = enriched_frame.copy(deep=True)
    result = call_score(
        module,
        enriched_frame,
        min_views=0,
        min_video_age_days=0,
        min_group_size=2,
        min_percentile=0.9,
        min_channel_videos=2,
        min_multiple=1,
        min_channel_median_views=0,
        min_score=0,
        min_comments=0,
        min_comment_z=-99,
        drop_suspicious=False,
    )
    pd.testing.assert_frame_equal(enriched_frame, before)
    if not result.empty:
        assert result["video_id"].is_unique
        assert result["score"].is_monotonic_decreasing
        assert result["score"].notna().all()
        assert np.isfinite(result["score"]).all()


@pytest.mark.parametrize("module", ALGORITHMS)
def test_algorithm_missing_required_column_raises_clear_error(module, enriched_frame):
    broken = enriched_frame.drop(columns=["video_id"])
    with pytest.raises((KeyError, ValueError), match="video_id|required|requires|missing"):
        call_score(module, broken)


def test_breakout_outliers_filters_by_subscriber_and_view_boundaries(enriched_frame):
    df = enriched_frame.copy()
    df.loc[0, ["video_id", "subscribers", "views", "views_per_sub", "like_rate", "comment_rate"]] = [
        "small_breakout", 10_000, 1_000_000, 100, 0.01, 0.001,
    ]
    df.loc[1, ["video_id", "subscribers", "views", "views_per_sub", "like_rate", "comment_rate"]] = [
        "huge_channel", 5_000_000, 1_000_000, 0.2, 0.01, 0.001,
    ]
    df.loc[2, ["video_id", "subscribers", "views", "views_per_sub"]] = ["nan_subs", np.nan, 2_000_000, np.nan]

    result = breakout_outliers.score(
        df,
        min_views=1_000_000,
        min_subscribers=10_000,
        max_subscribers=10_000,
        min_views_per_sub=10,
        min_video_age_days=0,
    )
    assert result["video_id"].tolist() == ["small_breakout"]


def test_breakout_outliers_enforces_each_threshold_boundary(enriched_frame):
    df = enriched_frame.copy()
    candidates = [
        ("at_all_boundaries", 10_000, 500, 50_000, 100.0),
        ("below_min_views", 10_000, 500, 49_999, 99.998),
        ("below_min_subscribers", 499, 499, 50_000, 100.2004),
        ("above_max_subscribers", 100_001, 100_001, 1_100_011, 11.0),
        ("nan_subscribers", np.nan, np.nan, 1_000_000, np.nan),
    ]
    for index, (video_id, subscribers, video_count, views, views_per_sub) in enumerate(candidates):
        df.loc[index, ["video_id", "subscribers", "video_count", "views", "views_per_sub", "likes", "comments", "like_rate", "comment_rate", "video_age_days"]] = [
            video_id, subscribers, video_count, views, views_per_sub, 1000, 100, 0.02, 0.002, 3.0,
        ]

    result = breakout_outliers.score(
        df.head(len(candidates)),
        min_views=50_000,
        min_subscribers=500,
        max_subscribers=100_000,
        min_views_per_sub=10,
        min_video_age_days=3,
    )
    assert result["video_id"].tolist() == ["at_all_boundaries"]


def test_weight_class_performance_two_stage_ranking_group_size_and_unknown_exclusion(enriched_frame):
    one = enriched_frame.head(1)
    assert weight_class_performance.score(one, min_group_size=2, min_views=0, min_percentile=0).empty

    df = enriched_frame.copy()
    df.loc[0, ["video_id", "views", "views_per_sub", "views_per_day", "comment_rate"]] = [
        "top_two_stage", 1_000_000, 500, 50_000, 0.5,
    ]
    df.loc[1, ["video_id", "sub_class"]] = ["unknown_class", "unknown"]
    result = weight_class_performance.score(df, min_group_size=2, min_percentile=0.95, min_views=0)
    assert "top_two_stage" in set(result["video_id"])
    assert "unknown_class" not in set(result["video_id"])
    assert result.loc[result["video_id"].eq("top_two_stage"), "composite_raw"].iloc[0] <= 1.0


def test_weight_class_performance_computes_two_stage_percentile_values(enriched_frame):
    df = enriched_frame.head(4).copy()
    df["sub_class"] = "1k-10k"
    df["video_count_class"] = "50-300"
    df["views"] = [10_000, 20_000, 30_000, 40_000]
    df["views_per_sub"] = [1.0, 2.0, 3.0, 4.0]
    df["views_per_day"] = [100.0, 200.0, 300.0, 400.0]
    df["comment_rate"] = [0.01, 0.02, 0.03, 0.04]
    df["video_id"] = ["rank1", "rank2", "rank3", "rank4"]

    result = weight_class_performance.score(
        df,
        min_group_size=4,
        min_percentile=0.75,
        min_views=0,
        weights={"views": 1, "views_per_sub": 1},
        min_components=2,
    )

    assert result["video_id"].tolist() == ["rank4", "rank3"]
    assert result.set_index("video_id").loc["rank4", "composite_raw"] == 1.0
    assert result.set_index("video_id").loc["rank3", "composite_raw"] == 0.75
    assert result.set_index("video_id").loc["rank4", "score"] == 1.0
    assert result.set_index("video_id").loc["rank3", "score"] == 0.75
    assert result["group_size"].tolist() == [4, 4]


def test_channel_relative_leave_one_out_zero_median_and_min_channel_size(enriched_frame):
    rows = []
    for i, views in enumerate([1_000, 1_000, 1_000, 1_000, 100_000]):
        rows.append(
            {
                "video_id": f"loo{i}",
                "channel_id": "loo",
                "title": f"leave out {i}",
                "channel_name": "Leave Out",
                "published_at": "2026-07-01T00:00:00Z",
                "category_id": "22",
                "views": views,
                "likes": 10,
                "comments": 5,
                "subscribers": 10_000,
                "video_count": 5,
                "channel_published_at": "2020-01-01T00:00:00Z",
            }
        )
    df = metrics.enrich(pd.DataFrame(rows), now=NOW, min_videos_for_baseline=5)
    result = channel_relative.score(
        df, min_channel_videos=5, min_multiple=50, min_views=0, min_channel_median_views=0, min_video_age_days=0
    )
    assert result["video_id"].tolist() == ["loo4"]
    assert result.iloc[0]["channel_median_views"] == 1_000

    zero = df.copy()
    zero["views"] = [0, 0, 0, 0, 100]
    zero = metrics.add_channel_baselines(zero, min_videos=5)
    assert channel_relative.score(zero, min_channel_videos=5, min_multiple=1, min_views=0, min_channel_median_views=0, min_video_age_days=0).empty
    assert channel_relative.score(df.head(4), min_channel_videos=5, min_multiple=1, min_views=0, min_channel_median_views=0, min_video_age_days=0).empty


def test_channel_relative_huge_video_uses_leave_one_out_not_naive_median():
    views = [1_000, 2_000, 3_000, 4_000, 100_000]
    df = pd.DataFrame(
        {
            "video_id": [f"v{i}" for i in range(5)],
            "channel_id": ["one_channel"] * 5,
            "title": [f"video {i}" for i in range(5)],
            "channel_name": ["One Channel"] * 5,
            "published_at": ["2026-07-01T00:00:00Z"] * 5,
            "category_id": ["22"] * 5,
            "views": views,
            "likes": [10] * 5,
            "comments": [1] * 5,
            "subscribers": [10_000] * 5,
            "video_count": [5] * 5,
            "channel_published_at": ["2020-01-01T00:00:00Z"] * 5,
        }
    )
    enriched = metrics.enrich(df, now=NOW, min_videos_for_baseline=5)
    result = channel_relative.score(
        enriched,
        min_channel_videos=5,
        min_multiple=30,
        min_views=0,
        min_channel_median_views=0,
        min_video_age_days=0,
    )
    huge = result.loc[result["video_id"].eq("v4")].iloc[0]
    assert huge["channel_median_views"] == 2_500
    assert huge["channel_relative_multiple"] == 40.0
    assert huge["channel_relative_multiple"] != 100_000 / np.median(views)


def test_velocity_snapshot_edge_cases_and_publish_only(enriched_frame):
    df = enriched_frame.copy()
    df.loc[0, ["video_id", "views", "views_per_day", "video_age_days"]] = ["single", 100_000, 20_000, 10]
    df.loc[1, ["video_id", "views", "views_per_day", "video_age_days"]] = ["zero_delta", 100_000, 19_000, 10]
    df.loc[2, ["video_id", "views", "views_per_day", "video_age_days"]] = ["negative", 100_000, 18_000, 40]
    snaps = pd.DataFrame(
        [
            {"video_id": "single", "collected_at": "2026-07-20T00:00:00Z", "views": 10_000},
            {"video_id": "zero_delta", "collected_at": "2026-07-20T00:00:00Z", "views": 1_000},
            {"video_id": "zero_delta", "collected_at": "2026-07-20T00:00:00Z", "views": 5_000},
            {"video_id": "negative", "collected_at": "2026-07-20T00:00:00Z", "views": 100_000},
            {"video_id": "negative", "collected_at": "2026-07-22T00:00:00Z", "views": 90_000},
            {"video_id": "absent", "collected_at": "2026-07-22T00:00:00Z", "views": 1_000_000},
        ]
    )
    result = velocity.score(df, snapshots=snaps, min_group_size=2, min_views=0, min_score=0)
    assert {"single", "zero_delta", "negative"}.issubset(set(result["video_id"]))
    assert result["score"].notna().all()
    assert np.isfinite(result["score"]).all()
    assert "absent" not in set(result["video_id"])
    assert result.loc[result["video_id"].eq("single"), "score"].notna().all()
    assert result.loc[result["video_id"].eq("zero_delta"), "score"].notna().all()
    neg = result.loc[result["video_id"].eq("negative")].iloc[0]
    assert bool(neg["negative_delta_clipped"])
    assert neg["observed_views_per_day"] == 0
    assert neg["score"] >= 0

    publish_only = velocity.score(df, snapshots=None, min_group_size=2, min_views=0, min_score=0)
    assert not publish_only.empty
    assert publish_only["snapshot_count"].eq(0).all()
    assert publish_only["publish_velocity_score"].notna().any()


def test_engagement_scale_fallback_suspicion_and_max_per_channel():
    rows = []
    for i in range(30):
        rows.append(
            {
                "video_id": f"identical{i}", "channel_id": "cap", "title": "identical comments",
                "channel_name": "Cap", "published_at": "2026-07-01T00:00:00Z", "category_id": "22",
                "views": 1_000, "likes": 20, "comments": 100, "subscribers": 500, "video_count": 10,
                "channel_published_at": "2020-01-01T00:00:00Z",
            }
        )
    for i in range(30):
        rows.append(
            {
                "video_id": f"baseline{i}", "channel_id": f"b{i}", "title": "baseline",
                "channel_name": "Base", "published_at": "2026-07-01T00:00:00Z", "category_id": "22",
                "views": 10_000, "likes": 100, "comments": 10, "subscribers": 2_000, "video_count": 10,
                "channel_published_at": "2020-01-01T00:00:00Z",
            }
        )
    rows += [
        {"video_id": "likes_ok", "channel_id": "ok", "title": "comments disabled", "channel_name": "Ok", "published_at": "2026-07-01T00:00:00Z", "category_id": "22", "views": 200_000, "likes": 20_000, "comments": np.nan, "subscribers": 2_000, "video_count": 10, "channel_published_at": "2020-01-01T00:00:00Z"},
        {"video_id": "botlike", "channel_id": "bot", "title": "empty views", "channel_name": "Bot", "published_at": "2026-07-01T00:00:00Z", "category_id": "22", "views": 5_000_000, "likes": 2, "comments": 0, "subscribers": 2_000, "video_count": 10, "channel_published_at": "2020-01-01T00:00:00Z"},
    ]
    df = metrics.enrich(pd.DataFrame(rows), now=NOW, min_videos_for_baseline=1)
    result = engagement.score(
        df,
        min_group_size=30,
        min_views=0,
        min_comments=0,
        min_comment_z=-99,
        drop_suspicious=False,
        max_per_channel=2,
    )
    identical = result[result["video_id"].str.startswith("identical")]
    assert not identical.empty
    assert set(identical["z_scale_source"]).issubset({"group_iqr", "group_std", "global_mad"})
    assert len(identical) == 2
    assert not bool(result.loc[result["video_id"].eq("likes_ok"), "suspicious_engagement"].iloc[0])
    assert bool(result.loc[result["video_id"].eq("botlike"), "suspicious_engagement"].iloc[0])


def test_engagement_zero_mad_group_falls_back_to_global_mad_regression():
    rows = []
    for i in range(30):
        rows.append(
            {
                "video_id": f"identical{i}",
                "channel_id": f"small{i}",
                "title": "identical high comments",
                "channel_name": "Small",
                "published_at": "2026-07-01T00:00:00Z",
                "category_id": "22",
                "views": 1_000,
                "likes": 20,
                "comments": 100,
                "subscribers": 500,
                "video_count": 10,
                "channel_published_at": "2020-01-01T00:00:00Z",
            }
        )
    for i in range(30):
        rows.append(
            {
                "video_id": f"baseline{i}",
                "channel_id": f"base{i}",
                "title": "ordinary comments",
                "channel_name": "Base",
                "published_at": "2026-07-01T00:00:00Z",
                "category_id": "22",
                "views": 10_000,
                "likes": 100,
                "comments": 10 + (i % 3),
                "subscribers": 2_000,
                "video_count": 10,
                "channel_published_at": "2020-01-01T00:00:00Z",
            }
        )
    df = metrics.enrich(pd.DataFrame(rows), now=NOW, min_videos_for_baseline=1)

    result = engagement.score(
        df,
        min_group_size=30,
        min_views=0,
        min_comments=0,
        min_comment_z=1.5,
        drop_suspicious=False,
        max_per_channel=None,
    )

    identical = result[result["video_id"].str.startswith("identical")]
    assert len(identical) == 30
    assert set(identical["z_scale_source"]) == {"global_mad"}
    assert (identical["comment_rate_z"] > 1.5).all()


def test_engagement_tiny_residue_scale_is_rejected_by_relative_tolerance():
    rows = []
    for i in range(30):
        rows.append(
            {
                "video_id": f"tiny_residue{i}",
                "channel_id": f"small{i}",
                "title": "tiny residue comments",
                "channel_name": "Small",
                "published_at": "2026-07-01T00:00:00Z",
                "category_id": "22",
                "views": 1.0,
                "likes": 0.02,
                "comments": 0.1 + (1e-17 if i % 2 else 0.0),
                "subscribers": 500,
                "video_count": 10,
                "channel_published_at": "2020-01-01T00:00:00Z",
            }
        )
    for i in range(30):
        rows.append(
            {
                "video_id": f"baseline{i}",
                "channel_id": f"base{i}",
                "title": "ordinary comments",
                "channel_name": "Base",
                "published_at": "2026-07-01T00:00:00Z",
                "category_id": "22",
                "views": 10_000,
                "likes": 100,
                "comments": 10 + (i % 3),
                "subscribers": 2_000,
                "video_count": 10,
                "channel_published_at": "2020-01-01T00:00:00Z",
            }
        )
    df = metrics.enrich(pd.DataFrame(rows), now=NOW, min_videos_for_baseline=1)

    result = engagement.score(
        df,
        min_group_size=30,
        min_views=0,
        min_comments=0,
        min_comment_z=1.5,
        drop_suspicious=False,
        max_per_channel=None,
    )

    tiny = result[result["video_id"].str.startswith("tiny_residue")]
    assert len(tiny) == 30
    assert set(tiny["z_scale_source"]) == {"global_mad"}
    assert (tiny["comment_rate_z"] > 1.5).all()
    assert tiny["comment_rate_z"].max() < 10_000


def test_engagement_healthy_group_uses_group_mad_before_fallbacks():
    rows = []
    for i in range(30):
        rows.append(
            {
                "video_id": f"healthy{i}",
                "channel_id": f"small{i}",
                "title": "healthy varied comments",
                "channel_name": "Small",
                "published_at": "2026-07-01T00:00:00Z",
                "category_id": "22",
                "views": 1_000,
                "likes": 20,
                "comments": 10 + i,
                "subscribers": 500,
                "video_count": 10,
                "channel_published_at": "2020-01-01T00:00:00Z",
            }
        )
    for i in range(30):
        rows.append(
            {
                "video_id": f"baseline{i}",
                "channel_id": f"base{i}",
                "title": "ordinary comments",
                "channel_name": "Base",
                "published_at": "2026-07-01T00:00:00Z",
                "category_id": "22",
                "views": 10_000,
                "likes": 100,
                "comments": 10 + (i % 3),
                "subscribers": 2_000,
                "video_count": 10,
                "channel_published_at": "2020-01-01T00:00:00Z",
            }
        )
    df = metrics.enrich(pd.DataFrame(rows), now=NOW, min_videos_for_baseline=1)

    result = engagement.score(
        df,
        min_group_size=30,
        min_views=0,
        min_comments=0,
        min_comment_z=-99,
        drop_suspicious=False,
        max_per_channel=None,
    )

    healthy = result[result["video_id"].str.startswith("healthy")]
    assert len(healthy) == 30
    assert set(healthy["z_scale_source"]) == {"group_mad"}
    assert healthy["comment_rate_z"].max() > 0
    assert "global_mad" not in set(healthy["z_scale_source"])


def test_topics_theme_extraction_saturation_and_fallback_path(enriched_frame):
    empty_themes = topics.extract_themes(pd.DataFrame(), use_sklearn=False)
    assert empty_themes.empty
    assert list(empty_themes.columns) == topics._THEME_COLUMNS

    empty_saturation = topics.niche_saturation(pd.DataFrame(), pd.DataFrame(), use_sklearn=False)
    assert empty_saturation.empty
    assert list(empty_saturation.columns) == topics._SATURATION_COLUMNS

    empty_score = topics.score(enriched_frame, leads=pd.DataFrame(), use_sklearn=False)
    assert empty_score.empty
    assert list(empty_score.columns) == output_columns(topics)

    leads = pd.DataFrame(
        [
            {"video_id": "l1", "channel_id": "a", "title": "robot garden harvest", "views": 100_000, "subscribers": 1_000, "category_id": np.nan},
            {"video_id": "l2", "channel_id": "b", "title": "robot garden tools", "views": 80_000, "subscribers": 1_000, "category_id": "22"},
            {"video_id": "emoji", "channel_id": "c", "title": "😀 !!! the and of", "views": 60_000, "subscribers": 1_000, "category_id": "22"},
        ]
    )
    themes = topics.extract_themes(
        leads,
        background=pd.DataFrame({"title": ["unrelated cooking"], "video_id": ["b1"]}),
        min_lead_count=2,
        min_distinct_channels=2,
        use_sklearn=False,
    )
    assert "robot" in set(themes["term"])
    robot = themes.loc[themes["term"].eq("robot")].iloc[0]
    assert robot["lead_count"] == 2
    assert np.isfinite(robot["lift"])

    stopword_only = topics.extract_themes(
        pd.DataFrame(
            [
                {"video_id": "s1", "channel_id": "a", "title": "😀 !!! the and of"},
                {"video_id": "s2", "channel_id": "b", "title": "??? video official"},
            ]
        ),
        min_lead_count=1,
        min_distinct_channels=1,
        use_sklearn=False,
    )
    assert stopword_only.empty

    assert topics.extract_themes(leads.head(1), min_lead_count=2, use_sklearn=False).empty
    single = topics.extract_themes(leads.head(1), min_lead_count=1, min_distinct_channels=1, use_sklearn=False)
    assert not single.empty
    assert single.loc[single["term"].eq("robot"), "lead_count"].iloc[0] == 1

    saturation = topics.niche_saturation(enriched_frame, leads, use_sklearn=False)
    assert "Unknown" in set(saturation["category_name"])
    assert np.isfinite(saturation["saturation_score"]).all()

    scored = topics.score(enriched_frame, leads=leads, min_lead_count=1, min_distinct_channels=1, use_sklearn=False)
    assert list(scored.columns) == output_columns(topics)
    assert scored["score"].notna().all()


def test_topics_uses_stdlib_fallback_when_sklearn_absent_and_ranks_terms():
    assert topics.HAS_SKLEARN is False
    leads = pd.DataFrame(
        [
            {"video_id": "a", "channel_id": "c1", "title": "ceramic frog pond", "views": 100_000, "subscribers": 1_000, "category_id": "22"},
            {"video_id": "b", "channel_id": "c2", "title": "stone frog garden", "views": 90_000, "subscribers": 1_000, "category_id": "22"},
            {"video_id": "c", "channel_id": "c3", "title": "glass frog patio", "views": 80_000, "subscribers": 1_000, "category_id": "22"},
            {"video_id": "d", "channel_id": "c4", "title": "wooden bird patio", "views": 70_000, "subscribers": 1_000, "category_id": "22"},
        ]
    )
    background = pd.DataFrame(
        {
            "video_id": ["bg1", "bg2", "bg3"],
            "title": ["unrelated news", "plain cooking", "garden tools"],
        }
    )

    themes = topics.extract_themes(
        leads,
        background=background,
        min_lead_count=2,
        min_distinct_channels=2,
        use_sklearn=True,
    )

    assert themes.iloc[0]["term"] == "frog"
    assert themes.iloc[0]["lead_count"] == 3
    assert themes.iloc[0]["distinct_channels"] == 3
    assert np.isfinite(themes["lift"]).all()
    assert (themes["lift"] > 0).all()
