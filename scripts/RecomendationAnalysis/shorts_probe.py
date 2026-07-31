"""Network verification for YouTube Shorts watch URLs.

The duration heuristic removes most Shorts before scoring. This module is a
fail-open final-list verifier for the 61-180 second band that can slip through:
``HEAD /shorts/{video_id}`` returns 200 for Shorts and redirects for non-Shorts.

Probe results are cached in memory and, by default, in
``data/output/shorts_probe_cache.json`` so repeated notebook runs do not hit the
public YouTube endpoint again.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Mapping, MutableMapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib import error, request

import pandas as pd

SHORTS_URL_TEMPLATE = "https://www.youtube.com/shorts/{video_id}"
DEFAULT_TIMEOUT: float = 8.0
DEFAULT_MAX_WORKERS: int = 8
DEFAULT_REQUEST_DELAY: float = 0.05
DEFAULT_REQUEST_JITTER: float = 0.05
DEFAULT_RATE_LIMIT_BACKOFF: float = 1.0
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "output" / "shorts_probe_cache.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

logger = logging.getLogger(__name__)
_MEMORY_CACHE: dict[str, bool | None] = {}


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def probe_video(
    video_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    session: Any = None,
) -> bool | None:
    """Return whether ``video_id`` resolves as a Short, or ``None`` on failure."""

    video_id = str(video_id).strip()
    if not video_id:
        return None
    url = SHORTS_URL_TEMPLATE.format(video_id=video_id)
    try:
        status = _head_status(url, timeout=timeout, session=session)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Shorts probe failed for %s: %s", video_id, exc)
        return None
    if status == 200:
        return True
    if 300 <= status < 400:
        return False
    if status == 429 and DEFAULT_RATE_LIMIT_BACKOFF > 0:
        time.sleep(DEFAULT_RATE_LIMIT_BACKOFF)
    return None


def probe_videos(
    video_ids: Any,
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout: float = DEFAULT_TIMEOUT,
    cache: str | Path | MutableMapping[str, bool | None] | None = None,
    progress: bool = False,
    request_delay: float = DEFAULT_REQUEST_DELAY,
    request_jitter: float = DEFAULT_REQUEST_JITTER,
    rate_limit_backoff: float = DEFAULT_RATE_LIMIT_BACKOFF,
) -> dict[str, bool | None]:
    """Probe many IDs concurrently and return a complete ``video_id -> result`` map.

    ``cache`` may be a mapping for in-process tests/callers, a JSON path for
    on-disk caching, or ``None`` to use ``data/output/shorts_probe_cache.json``.
    Corrupt or missing cache files are ignored.
    """

    ids = _unique_video_ids(video_ids)
    if not ids:
        return {}

    disk_path, cache_data = _load_cache(cache)
    results: dict[str, bool | None] = {}
    missing: list[str] = []
    for video_id in ids:
        if video_id in cache_data:
            results[video_id] = _coerce_cached_value(cache_data[video_id])
        else:
            missing.append(video_id)

    if missing:
        workers = max(1, min(int(max_workers), DEFAULT_MAX_WORKERS, len(missing)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _probe_with_delay,
                    video_id,
                    timeout=timeout,
                    request_delay=request_delay,
                    request_jitter=request_jitter,
                    rate_limit_backoff=rate_limit_backoff,
                ): video_id
                for video_id in missing
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                video_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Shorts probe worker failed for %s: %s", video_id, exc)
                    result = None
                results[video_id] = result
                cache_data[video_id] = result
                if progress and completed % 25 == 0:
                    logger.info("Probed %d/%d YouTube Shorts URLs.", completed, len(missing))
        _save_cache(disk_path, cache_data)

    undetermined = sum(value is None for value in results.values())
    if undetermined:
        logger.warning("%d of %d YouTube Shorts probes were undetermined.", undetermined, len(results))
    return {video_id: results.get(video_id) for video_id in ids}


def verify_leads(
    df: pd.DataFrame,
    *,
    video_id_column: str = "video_id",
    drop_confirmed_shorts: bool = True,
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout: float = DEFAULT_TIMEOUT,
    cache: str | Path | MutableMapping[str, bool | None] | None = None,
) -> pd.DataFrame:
    """Add ``is_short_confirmed`` and optionally drop rows confirmed as Shorts.

    Undetermined probes remain ``<NA>`` and are kept, so network problems cannot
    silently remove a valid lead.
    """

    result = df.copy()
    if "is_short_confirmed" not in result.columns:
        result["is_short_confirmed"] = pd.Series(pd.NA, index=result.index, dtype="boolean")
    else:
        result["is_short_confirmed"] = result["is_short_confirmed"].astype("boolean")
    if result.empty or video_id_column not in result.columns:
        return result

    probe_results = probe_videos(
        result[video_id_column].dropna().astype("string").tolist(),
        max_workers=max_workers,
        timeout=timeout,
        cache=cache,
    )
    mapped = result[video_id_column].astype("string").map(probe_results)
    result["is_short_confirmed"] = pd.Series(mapped, index=result.index, dtype="boolean")
    undetermined = int(result["is_short_confirmed"].isna().sum())
    if undetermined:
        logger.warning("%d lead Shorts verifications were undetermined; keeping those rows.", undetermined)
    if drop_confirmed_shorts:
        result = result.loc[~result["is_short_confirmed"].fillna(False)].copy()
    return result.reset_index(drop=True)


def _head_status(url: str, *, timeout: float, session: Any = None) -> int:
    if session is not None:
        response = session.head(url, timeout=timeout, allow_redirects=False, headers={"User-Agent": USER_AGENT})
        return int(response.status_code)
    opener = request.build_opener(_NoRedirectHandler)
    req = request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=timeout) as response:
            return int(response.status)
    except error.HTTPError as exc:
        return int(exc.code)


def _probe_with_delay(
    video_id: str,
    *,
    timeout: float,
    request_delay: float,
    request_jitter: float,
    rate_limit_backoff: float,
) -> bool | None:
    pause = max(0.0, request_delay) + random.uniform(0.0, max(0.0, request_jitter))
    if pause:
        time.sleep(pause)
    result = probe_video(video_id, timeout=timeout)
    if result is None and rate_limit_backoff > 0:
        time.sleep(rate_limit_backoff)
    return result


def _unique_video_ids(video_ids: Any) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for value in list(video_ids):
        if pd.isna(value):
            continue
        video_id = str(value).strip()
        if video_id and video_id not in seen:
            seen.add(video_id)
            ids.append(video_id)
    return ids


def _load_cache(
    cache: str | Path | MutableMapping[str, bool | None] | None,
) -> tuple[Path | None, MutableMapping[str, bool | None]]:
    if isinstance(cache, MutableMapping):
        return None, cache
    disk_path = DEFAULT_CACHE_PATH if cache is None else Path(cache)
    data: MutableMapping[str, bool | None] = _MEMORY_CACHE if cache is None else {}
    try:
        if disk_path.exists():
            raw = json.loads(disk_path.read_text(encoding="utf-8"))
            if isinstance(raw, Mapping):
                data.update({str(key): _coerce_cached_value(value) for key, value in raw.items()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ignoring unreadable Shorts probe cache %s: %s", disk_path, exc)
    return disk_path, data


def _save_cache(path: Path | None, data: Mapping[str, bool | None]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {key: value for key, value in sorted(data.items()) if value is not None}
        path.write_text(json.dumps(serializable, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write Shorts probe cache %s: %s", path, exc)


def _coerce_cached_value(value: Any) -> bool | None:
    if value is True or value is False or value is None:
        return value
    return None
