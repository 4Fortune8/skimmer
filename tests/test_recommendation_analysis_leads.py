from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from scripts.RecomendationAnalysis import exclusions, leads, metrics
from skimmer.domain import language as domain_language


NOW = "2026-07-31T00:00:00Z"


@pytest.fixture()
def shorts_analysis_db(tmp_path):
    db_path = tmp_path / "shorts_analysis.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE bronze_youtubeapi_video_stats (
            id INTEGER PRIMARY KEY,
            video_id TEXT,
            channel_id TEXT,
            title TEXT,
            published_at TEXT,
            collected_at TEXT,
            duration_seconds TEXT,
            category_id TEXT,
            views TEXT,
            likes TEXT,
            comments TEXT,
            default_audio_language TEXT,
            default_language TEXT
        );
        CREATE TABLE bronze_youtubeapi_channel_stats (
            id INTEGER PRIMARY KEY,
            channel_id TEXT,
            channel_name TEXT,
            subscribers TEXT,
            subscribers_change TEXT,
            views TEXT,
            video_count TEXT,
            country TEXT,
            channel_published_at TEXT,
            collected_at TEXT
        );
        """
    )
    video_rows = []
    next_id = 1

    def add_video(video_id, channel_id, duration, views, collected_at="2026-07-30T00:00:00Z"):
        nonlocal next_id
        video_rows.append(
            (
                next_id,
                video_id,
                channel_id,
                f"Title {video_id}",
                "2026-07-01T00:00:00Z",
                collected_at,
                duration,
                "22",
                str(views),
                "10",
                "2",
            )
        )
        next_id += 1

    for video_id, views in [
        ("long_100", 100),
        ("long_200", 200),
        ("long_300", 300),
        ("long_400", 400),
        ("long_500", 500),
    ]:
        add_video(video_id, "baseline", "120", views, collected_at="2026-07-29T00:00:00Z")
        add_video(video_id, "baseline", "120", views + 1, collected_at="2026-07-30T00:00:00Z")
    for video_id, duration, views in [
        ("short_59", "59", 1_000_000),
        ("short_60", "60", 2_000_000),
        ("custom_180", "180", 700),
        ("custom_181", "181", 800),
        ("unknown_duration", "N/A", 900),
    ]:
        channel_id = "baseline" if video_id.startswith("short_") else "other"
        add_video(video_id, channel_id, duration, views)

    conn.executemany(
        """
        INSERT INTO bronze_youtubeapi_video_stats
        (id, video_id, channel_id, title, published_at, collected_at, duration_seconds, category_id, views, likes, comments)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        video_rows,
    )
    conn.executemany(
        """
        INSERT INTO bronze_youtubeapi_channel_stats
        (id, channel_id, channel_name, subscribers, subscribers_change, views, video_count, country, channel_published_at, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "baseline", "Baseline Channel", "10000", "0", "100000", "100", "US", "2020-01-01T00:00:00Z", "2026-07-30T00:00:00Z"),
            (2, "other", "Other Channel", "5000", "0", "50000", "20", "US", "2021-01-01T00:00:00Z", "2026-07-30T00:00:00Z"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def video_ids(frame: pd.DataFrame) -> set[str]:
    return set(frame["video_id"].astype(str))


def test_load_analysis_frame_excludes_shorts_by_default_and_can_include_them(shorts_analysis_db):
    df, _ = leads.load_analysis_frame(shorts_analysis_db, now=NOW)
    assert {"short_59", "short_60"}.isdisjoint(video_ids(df))
    assert {"long_100", "long_500", "custom_180", "custom_181", "unknown_duration"}.issubset(video_ids(df))

    unfiltered, _ = leads.load_analysis_frame(shorts_analysis_db, now=NOW, exclude_shorts=False)
    assert {"short_59", "short_60"}.issubset(video_ids(unfiltered))


def test_load_analysis_frame_honours_custom_boundary_and_drop_unknown(shorts_analysis_db):
    df, _ = leads.load_analysis_frame(
        shorts_analysis_db,
        now=NOW,
        shorts_max_duration_seconds=180,
        drop_unknown_duration=True,
    )

    assert "custom_180" not in video_ids(df)
    assert "custom_181" in video_ids(df)
    assert "unknown_duration" not in video_ids(df)


def test_load_analysis_frame_filters_before_enrichment_channel_baselines(shorts_analysis_db):
    df, _ = leads.load_analysis_frame(shorts_analysis_db, now=NOW)

    baseline = df[df["channel_id"].eq("baseline")].sort_values("video_id")
    assert baseline["video_id"].tolist() == ["long_100", "long_200", "long_300", "long_400", "long_500"]
    # Latest long-form views are 101, 201, 301, 401, 501, so the long-form-only median is 301.
    assert baseline["channel_median_views"].tolist() == [301.0] * 5
    assert baseline["channel_video_sample"].tolist() == [5] * 5

    unfiltered, _ = leads.load_analysis_frame(shorts_analysis_db, now=NOW, exclude_shorts=False)
    unfiltered_baseline = unfiltered[unfiltered["channel_id"].eq("baseline")].sort_values("video_id")
    assert unfiltered_baseline["channel_median_views"].tolist() == [401.0] * 7


def test_load_analysis_frame_restricts_snapshots_to_surviving_video_ids(shorts_analysis_db):
    df, snapshots = leads.load_analysis_frame(shorts_analysis_db, now=NOW)

    assert snapshots is not None
    assert set(snapshots["video_id"].astype(str)).issubset(video_ids(df))
    assert {"short_59", "short_60"}.isdisjoint(set(snapshots["video_id"].astype(str)))
    assert {"long_100", "long_500", "unknown_duration"}.issubset(set(snapshots["video_id"].astype(str)))

    _, no_snapshots = leads.load_analysis_frame(shorts_analysis_db, now=NOW, with_snapshots=False)
    assert no_snapshots is None


def test_corpus_scoped_exclusions_apply_before_enrichment(shorts_analysis_db):
    """A corpus rule must move the channel baseline, not just hide rows."""

    rules = [exclusions.make_rule("long 500", scope="corpus")]
    df, _ = leads.load_analysis_frame(shorts_analysis_db, now=NOW, exclusion_rules=rules)

    assert "long_500" not in video_ids(df)
    baseline = df[df["channel_id"].eq("baseline")].sort_values("video_id")
    assert len(baseline) == 4
    # Excluding one long-form video drops the channel under the 5-video minimum sample, so its
    # baseline goes from 301 to unavailable. Post-hoc row hiding could not have changed this.
    assert baseline["channel_median_views"].isna().all()

    unfiltered, _ = leads.load_analysis_frame(shorts_analysis_db, now=NOW)
    unfiltered_baseline = unfiltered[unfiltered["channel_id"].eq("baseline")]
    assert unfiltered_baseline["channel_median_views"].tolist() == [301.0] * 5


def test_corpus_scoped_exclusions_restrict_snapshots(shorts_analysis_db):
    rules = [exclusions.make_rule("long 500", scope="corpus")]
    df, snapshots = leads.load_analysis_frame(shorts_analysis_db, now=NOW, exclusion_rules=rules)

    assert snapshots is not None
    assert "long_500" not in set(snapshots["video_id"].astype(str))
    assert set(snapshots["video_id"].astype(str)).issubset(video_ids(df))


def test_lead_scoped_exclusions_do_not_touch_the_analysis_frame(shorts_analysis_db):
    """Taste filters must leave the corpus, and therefore the norms, intact."""

    rules = [exclusions.make_rule("long 500", scope="leads")]
    df, _ = leads.load_analysis_frame(shorts_analysis_db, now=NOW, exclusion_rules=rules)
    unfiltered, _ = leads.load_analysis_frame(shorts_analysis_db, now=NOW)

    assert video_ids(df) == video_ids(unfiltered)


def test_generate_leads_applies_lead_scoped_exclusions_before_normalisation(monkeypatch):
    source_df = pd.DataFrame(
        {
            "video_id": pd.Series(["v1", "v2"], dtype="string"),
            "title": ["FIFA 26 reveal", "A normal video"],
        }
    )
    seen: dict[str, pd.DataFrame] = {}

    monkeypatch.setattr(leads, "load_analysis_frame", lambda **kwargs: (source_df, None))
    monkeypatch.setattr(
        leads,
        "run_algorithms",
        lambda df, snapshots=None, params=None: {"engagement": source_df.copy()},
    )

    def fake_normalize(results):
        seen.update(results)
        return results

    monkeypatch.setattr(leads, "normalize_scores", fake_normalize)
    monkeypatch.setattr(
        leads,
        "combine",
        lambda normalized, weights=None, source_df=None: pd.DataFrame(
            {"video_id": pd.Series(["v2"], dtype="string"), "composite_score": [1.0]}
        ),
    )

    leads.generate_leads(
        db_path="sentinel.sqlite",
        now=NOW,
        top_n=None,
        verify_shorts=False,
        exclusion_rules=[exclusions.make_rule("fifa")],
    )

    assert seen["engagement"]["video_id"].tolist() == ["v2"]


def test_generate_leads_passes_shorts_kwargs_to_load_analysis_frame(monkeypatch):
    calls = []
    source_df = pd.DataFrame({"video_id": pd.Series(["v1"], dtype="string"), "views": [1]})
    snapshots = pd.DataFrame({"video_id": pd.Series(["v1"], dtype="string")})

    def fake_load_analysis_frame(**kwargs):
        calls.append(kwargs)
        return source_df, snapshots

    monkeypatch.setattr(leads, "load_analysis_frame", fake_load_analysis_frame)
    monkeypatch.setattr(leads, "run_algorithms", lambda df, snapshots=None, params=None: {})
    monkeypatch.setattr(leads, "normalize_scores", lambda results: {})
    monkeypatch.setattr(
        leads,
        "combine",
        lambda normalized, weights=None, source_df=None: pd.DataFrame({"video_id": ["v1"], "composite_score": [1.0]}),
    )

    leads.generate_leads(db_path="sentinel.sqlite", now=NOW, top_n=None)
    leads.generate_leads(
        db_path="sentinel.sqlite",
        now=NOW,
        top_n=None,
        exclude_shorts=False,
        shorts_max_duration_seconds=180,
        drop_unknown_duration=True,
    )

    assert calls[0] == {
        "db_path": "sentinel.sqlite",
        "now": NOW,
        "with_snapshots": True,
        "exclude_shorts": True,
        "shorts_max_duration_seconds": metrics.SHORTS_MAX_DURATION_SECONDS,
        "drop_unknown_duration": False,
        "english_only": False,
        "keep_unknown_language": True,
        "exclusion_rules": None,
    }
    assert calls[1] == {
        "db_path": "sentinel.sqlite",
        "now": NOW,
        "with_snapshots": True,
        "exclude_shorts": False,
        "shorts_max_duration_seconds": 180,
        "drop_unknown_duration": True,
        "english_only": False,
        "keep_unknown_language": True,
        "exclusion_rules": None,
    }


def test_generate_leads_verifies_overfetches_drops_then_truncates(monkeypatch):
    source_df = pd.DataFrame({"video_id": pd.Series([f"v{i}" for i in range(1, 8)], dtype="string")})
    combined = pd.DataFrame(
        {
            "video_id": pd.Series([f"v{i}" for i in range(1, 8)], dtype="string"),
            "composite_score": [10, 9, 8, 7, 6, 5, 4],
            "algorithms_hit": [1] * 7,
            "algorithms": ["test"] * 7,
            "reasons": ["reason"] * 7,
        }
    )
    probe_calls: list[list[str]] = []

    def fake_probe(video_ids, **kwargs):
        ids = list(video_ids)
        probe_calls.append(ids)
        return {video_id: (video_id in {"v2", "v4"}) for video_id in ids}

    monkeypatch.setattr(leads, "load_analysis_frame", lambda **kwargs: (source_df, None))
    monkeypatch.setattr(leads, "run_algorithms", lambda df, snapshots=None, params=None: {"test": pd.DataFrame({"video_id": ["v1"]})})
    monkeypatch.setattr(leads, "normalize_scores", lambda results: results)
    monkeypatch.setattr(leads, "combine", lambda normalized, weights=None, source_df=None: combined.copy())
    monkeypatch.setattr(leads.shorts_probe, "probe_videos", fake_probe)

    result = leads.generate_leads(top_n=3, verify_shorts=True, verify_shorts_over_fetch=2.0, verify_shorts_cache={})

    assert probe_calls == [["v1", "v2", "v3", "v4", "v5", "v6"]]
    assert result["video_id"].tolist() == ["v1", "v3", "v5"]
    assert len(result) == 3
    assert result.attrs["shorts_probe"] == {
        "probed": 6,
        "confirmed_shorts": 2,
        "undetermined": 0,
        "dropped": 2,
    }
    assert result.attrs["algorithm_counts"] == {"test": 1}


def test_generate_leads_verify_shorts_false_bypasses_probe(monkeypatch):
    source_df = pd.DataFrame({"video_id": pd.Series(["v1", "v2"], dtype="string")})
    combined = pd.DataFrame({"video_id": ["v1", "v2"], "composite_score": [2, 1]})
    calls: list[list[str]] = []

    monkeypatch.setattr(leads, "load_analysis_frame", lambda **kwargs: (source_df, None))
    monkeypatch.setattr(leads, "run_algorithms", lambda df, snapshots=None, params=None: {})
    monkeypatch.setattr(leads, "normalize_scores", lambda results: results)
    monkeypatch.setattr(leads, "combine", lambda normalized, weights=None, source_df=None: combined.copy())
    monkeypatch.setattr(leads.shorts_probe, "probe_videos", lambda video_ids, **kwargs: calls.append(list(video_ids)) or {})

    result = leads.generate_leads(top_n=1, verify_shorts=False)

    assert calls == []
    assert result["video_id"].tolist() == ["v1"]
    assert str(result["is_short_confirmed"].dtype) == "boolean"


@pytest.fixture()
def language_analysis_db(tmp_path):
    """Two channels with identical view profiles, one English and one Spanish."""

    db_path = tmp_path / "language_analysis.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE bronze_youtubeapi_video_stats (
            id INTEGER PRIMARY KEY,
            video_id TEXT,
            channel_id TEXT,
            title TEXT,
            published_at TEXT,
            collected_at TEXT,
            duration_seconds TEXT,
            category_id TEXT,
            views TEXT,
            likes TEXT,
            comments TEXT,
            default_audio_language TEXT,
            default_language TEXT
        );
        CREATE TABLE bronze_youtubeapi_channel_stats (
            id INTEGER PRIMARY KEY,
            channel_id TEXT,
            channel_name TEXT,
            subscribers TEXT,
            subscribers_change TEXT,
            views TEXT,
            video_count TEXT,
            country TEXT,
            channel_published_at TEXT,
            collected_at TEXT
        );
        """
    )
    rows = []
    for index, views in enumerate([100, 200, 300, 400, 500], start=1):
        rows.append((index, f"en_{index}", "english", f"English title {index}",
                     "2026-07-01T00:00:00Z", "2026-07-30T00:00:00Z", "120", "22",
                     str(views), "10", "2", "en-GB", None))
    for index, views in enumerate([1000, 2000, 3000, 4000, 5000], start=1):
        rows.append((index + 100, f"es_{index}", "spanish", f"Titulo espanol {index}",
                     "2026-07-01T00:00:00Z", "2026-07-30T00:00:00Z", "120", "22",
                     str(views), "10", "2", "es-419", None))
    # A channel YouTube never tagged, whose emoji-only title gives the detector
    # nothing to work with either: kept by default so leads are not lost.
    rows.append((300, "unknown_1", "untagged", "🔥🔥🔥", "2026-07-01T00:00:00Z",
                 "2026-07-30T00:00:00Z", "120", "22", "600", "10", "2", None, None))
    conn.executemany(
        """
        INSERT INTO bronze_youtubeapi_video_stats
        (id, video_id, channel_id, title, published_at, collected_at, duration_seconds,
         category_id, views, likes, comments, default_audio_language, default_language)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.executemany(
        """
        INSERT INTO bronze_youtubeapi_channel_stats
        (id, channel_id, channel_name, subscribers, subscribers_change, views, video_count, country, channel_published_at, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "english", "English Channel", "10000", "0", "100000", "100", "US", "2020-01-01T00:00:00Z", "2026-07-30T00:00:00Z"),
            (2, "spanish", "Canal Espanol", "10000", "0", "100000", "100", "US", "2020-01-01T00:00:00Z", "2026-07-30T00:00:00Z"),
            (3, "untagged", "Untagged Channel", "10000", "0", "100000", "100", "US", "2020-01-01T00:00:00Z", "2026-07-30T00:00:00Z"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


requires_language_model = pytest.mark.skipif(
    not domain_language.model_path().exists(),
    reason="fastText language model not downloaded; run 'make lid-model'",
)


def test_load_analysis_frame_keeps_every_language_by_default(language_analysis_db):
    df, _ = leads.load_analysis_frame(language_analysis_db, now=NOW)
    assert {"en_1", "es_1", "unknown_1"}.issubset(video_ids(df))


@requires_language_model
def test_load_analysis_frame_english_only_drops_non_english_channels(language_analysis_db):
    df, _ = leads.load_analysis_frame(language_analysis_db, now=NOW, english_only=True)

    assert {"en_1", "en_5"}.issubset(video_ids(df))
    assert not video_ids(df) & {f"es_{index}" for index in range(1, 6)}
    assert "unknown_1" in video_ids(df), "untagged channels are kept by default"
    assert set(df["channel_language"]) == {"en", "und"}


@requires_language_model
def test_english_only_can_drop_unlabelled_channels(language_analysis_db):
    df, _ = leads.load_analysis_frame(
        language_analysis_db, now=NOW, english_only=True, keep_unknown_language=False
    )
    assert "unknown_1" not in video_ids(df)


@requires_language_model
def test_english_filter_runs_before_enrichment_so_baselines_are_english(language_analysis_db):
    """The Spanish channel's larger view counts must not shift weight-class norms."""

    everything, _ = leads.load_analysis_frame(language_analysis_db, now=NOW)
    english, _ = leads.load_analysis_frame(language_analysis_db, now=NOW, english_only=True)

    assert everything["views"].median() > english["views"].median()
    assert english["views"].max() == 600, "only English and untagged videos remain"


@requires_language_model
def test_english_filter_restricts_snapshots_to_surviving_videos(language_analysis_db):
    df, snapshots = leads.load_analysis_frame(language_analysis_db, now=NOW, english_only=True)
    assert snapshots is not None
    assert set(snapshots["video_id"].astype(str)).issubset(video_ids(df))
