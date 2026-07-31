from __future__ import annotations

import os

import pandas as pd
import pytest

from scripts.RecomendationAnalysis import shorts_probe


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, True),
        (303, False),
        (301, False),
        (302, False),
        (404, None),
        (500, None),
    ],
)
def test_probe_video_status_mapping(monkeypatch, status, expected):
    monkeypatch.setattr(shorts_probe, "_head_status", lambda *args, **kwargs: status)

    assert shorts_probe.probe_video("abc123") is expected


@pytest.mark.parametrize("exc", [TimeoutError("slow"), OSError("connection failed")])
def test_probe_video_network_errors_return_none(monkeypatch, exc):
    def fake_head_status(*args, **kwargs):
        raise exc

    monkeypatch.setattr(shorts_probe, "_head_status", fake_head_status)

    assert shorts_probe.probe_video("abc123") is None


def test_probe_videos_complete_dict_and_dedupes(monkeypatch):
    calls: list[str] = []

    def fake_probe(video_id, *, timeout=shorts_probe.DEFAULT_TIMEOUT, session=None):
        calls.append(video_id)
        if video_id == "bad":
            raise OSError("boom")
        return video_id == "short"

    monkeypatch.setattr(shorts_probe, "probe_video", fake_probe)

    result = shorts_probe.probe_videos(
        ["short", "long", "short", "bad"],
        max_workers=4,
        cache={},
        request_delay=0,
        request_jitter=0,
        rate_limit_backoff=0,
    )

    assert result == {"short": True, "long": False, "bad": None}
    assert sorted(calls) == ["bad", "long", "short"]


def test_probe_videos_empty_input_returns_empty_dict():
    assert shorts_probe.probe_videos([], cache={}) == {}


def test_probe_videos_uses_cache_without_re_requesting(monkeypatch):
    calls: list[str] = []

    def fake_probe(video_id, *, timeout=shorts_probe.DEFAULT_TIMEOUT, session=None):
        calls.append(video_id)
        return False

    monkeypatch.setattr(shorts_probe, "probe_video", fake_probe)
    cache = {"cached": True}

    result = shorts_probe.probe_videos(
        ["cached", "fresh"],
        cache=cache,
        request_delay=0,
        request_jitter=0,
        rate_limit_backoff=0,
    )

    assert result == {"cached": True, "fresh": False}
    assert calls == ["fresh"]
    assert cache["fresh"] is False


def test_probe_videos_tolerates_corrupt_cache_file(monkeypatch, tmp_path):
    calls: list[str] = []
    cache_path = tmp_path / "shorts-cache.json"
    cache_path.write_text("{not-json", encoding="utf-8")

    def fake_probe(video_id, *, timeout=shorts_probe.DEFAULT_TIMEOUT, session=None):
        calls.append(video_id)
        return True

    monkeypatch.setattr(shorts_probe, "probe_video", fake_probe)

    result = shorts_probe.probe_videos(
        ["fresh"],
        cache=cache_path,
        request_delay=0,
        request_jitter=0,
        rate_limit_backoff=0,
    )

    assert result == {"fresh": True}
    assert calls == ["fresh"]


def test_verify_leads_fail_open_and_does_not_mutate(monkeypatch):
    frame = pd.DataFrame({"video_id": ["short", "long", "unknown"], "rank": [1, 2, 3]})
    original = frame.copy(deep=True)

    monkeypatch.setattr(
        shorts_probe,
        "probe_videos",
        lambda video_ids, **kwargs: {"short": True, "long": False, "unknown": None},
    )

    result = shorts_probe.verify_leads(frame, cache={})

    assert result["video_id"].tolist() == ["long", "unknown"]
    assert str(result["is_short_confirmed"].dtype) == "boolean"
    assert result["is_short_confirmed"].tolist() == [False, pd.NA]
    pd.testing.assert_frame_equal(frame, original)


def test_verify_leads_can_keep_confirmed_shorts(monkeypatch):
    frame = pd.DataFrame({"video_id": ["short", "long", "unknown"]})
    monkeypatch.setattr(
        shorts_probe,
        "probe_videos",
        lambda video_ids, **kwargs: {"short": True, "long": False, "unknown": None},
    )

    result = shorts_probe.verify_leads(frame, drop_confirmed_shorts=False, cache={})

    assert result["video_id"].tolist() == ["short", "long", "unknown"]
    assert result["is_short_confirmed"].tolist() == [True, False, pd.NA]


def test_verify_leads_empty_frame_adds_nullable_boolean_column():
    result = shorts_probe.verify_leads(pd.DataFrame(columns=["video_id"]), cache={})

    assert result.empty
    assert str(result["is_short_confirmed"].dtype) == "boolean"


@pytest.mark.skipif(not os.environ.get("SKIMMER_NETWORK_TESTS"), reason="set SKIMMER_NETWORK_TESTS=1 to hit YouTube")
def test_probe_video_real_youtube_fixtures():
    assert shorts_probe.probe_video("FlwKjLuvw2U") is True
    assert shorts_probe.probe_video("iU2DCVXf-Go") is True
    assert shorts_probe.probe_video("MQ6WtM35zxM") is False
    assert shorts_probe.probe_video("mp1YJD21DGY") is False
    assert shorts_probe.probe_video("kXVHNfpZUUY") is False
