"""Parquet checkpoint cache for the expensive recommendation-analysis steps.

Loading and enriching the joined frame and running the six scoring algorithms
takes minutes on the full corpus, and the notebook loses those results whenever
the kernel restarts. This module persists them as Parquet next to a small JSON
manifest so a re-run can reload instead of recompute.

Cache entries are keyed by a fingerprint of:

- the resolved database path plus a content fingerprint of analysis-relevant tables (with file-stat fallback);
- the analysis parameters (shorts filter settings, ``now``, algorithm params),
  so tuning a knob never silently reuses a stale frame;
- the source code of this package, so editing an algorithm invalidates the
  results it produced.

Failures are non-fatal by design: an unreadable or unwritable cache logs a
warning and falls back to recomputation, because a checkpoint is an optimisation
and must never change the analysis outcome.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

try:
    from . import data_access
except ImportError:  # pragma: no cover
    import data_access  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "leads"
MANIFEST_NAME = "manifest.json"

# Tables ``data_access`` reads for the analysis frame. The content fingerprint counts
# only these so that unrelated collector writes (quota logs, attempt logs, other
# collectors) do not invalidate an otherwise valid checkpoint.
SOURCE_TABLES = (
    "bronze_youtubeapi_video_stats",
    "bronze_youtubeapi_channel_stats",
    "bronze_vidiq_channel_profiles",
    "bronze_vidiq_channel_stats",
    "profile_queue",
)
DB_FINGERPRINT_MODES = ("content", "mtime", "path")

_PACKAGE_DIR = Path(__file__).resolve().parent
_SOURCE_FINGERPRINT: str | None = None


class CheckpointError(RuntimeError):
    """Raised for cache problems that callers may choose to ignore."""


def resolve_cache_dir(cache_dir: str | Path | None = None) -> Path:
    """Return the checkpoint directory, defaulting to ``data/cache/leads``."""

    return Path(cache_dir).expanduser() if cache_dir else DEFAULT_CACHE_DIR


def database_fingerprint(db_path: str | Path | None = None, mode: str = "content") -> dict[str, Any]:
    """Return a change-detecting fingerprint for the analysis database.

    ``content`` (default) uses per-table row counts and max ids for the tables the
    analysis actually reads. It is preferred over file mtime because collectors
    write to this database continuously, and a file-stat key would miss on almost
    every run even when no analysis-relevant row changed. ``mtime`` uses size and
    modification time, and ``path`` ignores data changes entirely.
    """

    if mode not in DB_FINGERPRINT_MODES:
        raise ValueError(f"mode must be one of {DB_FINGERPRINT_MODES}.")
    path = data_access.resolve_db_path(db_path).resolve()
    fingerprint: dict[str, Any] = {"path": str(path), "mode": mode}
    if mode == "path":
        return fingerprint
    if mode == "content":
        tables = _table_fingerprint(path)
        if tables is not None:
            fingerprint["tables"] = tables
            return fingerprint
        logger.warning("Falling back to file-stat fingerprint for %s.", path)
        fingerprint["mode"] = "mtime"
    stat = path.stat()
    fingerprint["size"] = stat.st_size
    fingerprint["mtime_ns"] = stat.st_mtime_ns
    return fingerprint


def source_fingerprint() -> str:
    """Return a hash of the package sources so code edits invalidate entries."""

    global _SOURCE_FINGERPRINT
    if _SOURCE_FINGERPRINT is None:
        digest = hashlib.sha256()
        for path in sorted(_PACKAGE_DIR.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            digest.update(path.relative_to(_PACKAGE_DIR).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
        _SOURCE_FINGERPRINT = digest.hexdigest()
    return _SOURCE_FINGERPRINT


def make_key(
    kind: str,
    db_path: str | Path | None = None,
    params: Mapping[str, Any] | None = None,
    *,
    include_source: bool = True,
    extra: Mapping[str, Any] | None = None,
    db_fingerprint_mode: str = "content",
) -> str:
    """Return a stable ``<kind>-<hash>`` cache key for the given inputs."""

    payload = {
        "version": CACHE_VERSION,
        "kind": kind,
        "database": database_fingerprint(db_path, mode=db_fingerprint_mode),
        "params": _canonical(params or {}),
        "extra": _canonical(extra or {}),
    }
    if include_source:
        payload["source"] = source_fingerprint()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"{kind}-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:20]}"


def read_frames(key: str, cache_dir: str | Path | None = None) -> dict[str, pd.DataFrame | None] | None:
    """Return cached frames for ``key`` or ``None`` when unavailable."""

    entry_dir = resolve_cache_dir(cache_dir) / key
    manifest_path = entry_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable checkpoint manifest %s: %s", manifest_path, exc)
        return None
    if manifest.get("version") != CACHE_VERSION or manifest.get("key") != key:
        logger.warning("Ignoring checkpoint %s written by a different cache version.", key)
        return None

    stored = manifest.get("frames") or {}
    # Manifests are written with sorted keys, so the explicit order list restores the
    # original insertion order that callers rely on for display.
    order = [name for name in (manifest.get("frame_order") or []) if name in stored]
    order += [name for name in stored if name not in order]

    frames: dict[str, pd.DataFrame | None] = {}
    for name in order:
        info = stored[name]
        if info.get("is_none"):
            frames[name] = None
            continue
        frame_path = entry_dir / info["file"]
        try:
            frame = pd.read_parquet(frame_path)
        except (OSError, ValueError, ImportError) as exc:
            logger.warning("Ignoring unreadable checkpoint frame %s: %s", frame_path, exc)
            return None
        frame.attrs.update(info.get("attrs") or {})
        frames[name] = frame
    return frames


def write_frames(
    key: str,
    frames: Mapping[str, pd.DataFrame | None],
    cache_dir: str | Path | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Path | None:
    """Persist ``frames`` under ``key``; return the entry dir or ``None`` on failure."""

    entry_dir = resolve_cache_dir(cache_dir) / key
    staging_dir = entry_dir.with_name(f"{entry_dir.name}.tmp")
    try:
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "version": CACHE_VERSION,
            "key": key,
            "created_at": pd.Timestamp.utcnow().isoformat(),
            "metadata": _canonical(metadata or {}),
            "frame_order": [str(name) for name in frames],
            "frames": {},
        }
        for name, frame in frames.items():
            if frame is None:
                manifest["frames"][name] = {"is_none": True}
                continue
            file_name = f"{_safe_name(name)}.parquet"
            _write_parquet(frame, staging_dir / file_name)
            manifest["frames"][name] = {
                "file": file_name,
                "rows": int(len(frame)),
                "attrs": _canonical(dict(frame.attrs)),
            }
        (staging_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        shutil.rmtree(entry_dir, ignore_errors=True)
        staging_dir.replace(entry_dir)
    except Exception as exc:  # noqa: BLE001 - caching must never break the pipeline
        logger.warning("Could not write checkpoint %s: %s", key, exc)
        shutil.rmtree(staging_dir, ignore_errors=True)
        return None
    logger.info("Wrote checkpoint %s (%d frames) to %s", key, len(frames), entry_dir)
    return entry_dir


def cached_frames(
    key: str,
    compute: Callable[[], Mapping[str, pd.DataFrame | None]],
    cache_dir: str | Path | None = None,
    *,
    use_cache: bool = True,
    refresh: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[dict[str, pd.DataFrame | None], bool]:
    """Return ``(frames, from_cache)``, computing and storing them on a miss."""

    if use_cache and not refresh:
        cached = read_frames(key, cache_dir=cache_dir)
        if cached is not None:
            logger.info("Loaded checkpoint %s from cache.", key)
            return cached, True
    frames = dict(compute())
    if use_cache:
        write_frames(key, frames, cache_dir=cache_dir, metadata=metadata)
    return frames, False


def load_analysis_frame_cached(
    db_path: str | Path | None = None,
    cache_dir: str | Path | None = None,
    *,
    use_cache: bool = True,
    refresh: bool = False,
    db_fingerprint_mode: str = "content",
    loader: Callable[..., tuple[pd.DataFrame, pd.DataFrame | None]] | None = None,
    **frame_kwargs: Any,
) -> tuple[pd.DataFrame, pd.DataFrame | None, bool]:
    """Cached wrapper around ``leads.load_analysis_frame``.

    Returns ``(df, snapshots, from_cache)``. ``frame_kwargs`` are forwarded to
    the loader and are part of the cache key, so changing the shorts filter or
    ``now`` produces a distinct entry instead of reusing an incompatible frame.
    """

    if loader is None:
        loader = _default_loader()
    key = make_key("analysis_frame", db_path, frame_kwargs, db_fingerprint_mode=db_fingerprint_mode)

    def compute() -> dict[str, pd.DataFrame | None]:
        df, snapshots = loader(db_path=db_path, **frame_kwargs)
        return {"full_df": df, "snapshots": snapshots}

    frames, from_cache = cached_frames(
        key,
        compute,
        cache_dir=cache_dir,
        use_cache=use_cache,
        refresh=refresh,
        metadata={"frame_kwargs": frame_kwargs},
    )
    df = frames.get("full_df")
    if df is None:
        raise CheckpointError(f"Checkpoint {key} is missing the 'full_df' frame.")
    return df, frames.get("snapshots"), from_cache


def run_algorithms_cached(
    df: pd.DataFrame,
    snapshots: pd.DataFrame | None = None,
    db_path: str | Path | None = None,
    cache_dir: str | Path | None = None,
    *,
    params: Mapping[str, Mapping[str, Any]] | None = None,
    algorithms: Mapping[str, ModuleType] | None = None,
    frame_kwargs: Mapping[str, Any] | None = None,
    use_cache: bool = True,
    refresh: bool = False,
    db_fingerprint_mode: str = "content",
    runner: Callable[..., Mapping[str, pd.DataFrame]] | None = None,
) -> tuple[dict[str, pd.DataFrame], bool]:
    """Cached wrapper around ``leads.run_algorithms``.

    Returns ``(results, from_cache)``. The key covers the algorithm parameters,
    the selected algorithm names, and the frame parameters that produced ``df``,
    so cached scores always match the frame they were computed from.
    """

    if runner is None:
        runner = _default_runner()
    names = _algorithm_names(algorithms)
    key = make_key(
        "algorithm_results",
        db_path,
        params or {},
        extra={"algorithms": names, "frame_kwargs": dict(frame_kwargs or {}), "rows": int(len(df))},
        db_fingerprint_mode=db_fingerprint_mode,
    )

    def compute() -> dict[str, pd.DataFrame | None]:
        kwargs: dict[str, Any] = {"snapshots": snapshots, "params": params}
        if algorithms is not None:
            kwargs["algorithms"] = algorithms
        return dict(runner(df, **kwargs))

    frames, from_cache = cached_frames(
        key,
        compute,
        cache_dir=cache_dir,
        use_cache=use_cache,
        refresh=refresh,
        metadata={"algorithms": names, "frame_kwargs": dict(frame_kwargs or {})},
    )
    results = {name: frame for name, frame in frames.items() if frame is not None}
    return results, from_cache


def clear_cache(cache_dir: str | Path | None = None, kind: str | None = None) -> int:
    """Delete cached entries, optionally only those of one ``kind``."""

    root = resolve_cache_dir(cache_dir)
    if not root.is_dir():
        return 0
    removed = 0
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if kind is not None and not entry.name.startswith(f"{kind}-"):
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1
    return removed


def describe_cache(cache_dir: str | Path | None = None) -> pd.DataFrame:
    """Return one row per cached entry for notebook inspection."""

    root = resolve_cache_dir(cache_dir)
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            manifest_path = entry / MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            frames = manifest.get("frames") or {}
            rows.append(
                {
                    "key": manifest.get("key", entry.name),
                    "created_at": manifest.get("created_at"),
                    "frames": len(frames),
                    "rows": sum(int(info.get("rows") or 0) for info in frames.values()),
                    "size_mb": round(
                        sum(path.stat().st_size for path in entry.rglob("*") if path.is_file()) / 1_048_576, 2
                    ),
                }
            )
    return pd.DataFrame(rows, columns=["key", "created_at", "frames", "rows", "size_mb"])


def _table_fingerprint(path: Path) -> dict[str, list[int]] | None:
    """Return ``{table: [row_count, max_id]}`` for the analysis source tables."""

    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts: dict[str, list[int]] = {}
            for table in SOURCE_TABLES:
                if table not in existing:
                    continue
                has_id = any(row[1] == "id" for row in conn.execute(f'PRAGMA table_info("{table}")'))
                column = "MAX(id)" if has_id else "0"
                rows, max_id = conn.execute(f'SELECT COUNT(*), COALESCE({column}, 0) FROM "{table}"').fetchone()
                counts[table] = [int(rows), int(max_id)]
    except sqlite3.Error as exc:
        logger.warning("Could not fingerprint database tables in %s: %s", path, exc)
        return None
    return counts or None


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    prepared = frame.reset_index(drop=True)
    prepared.columns = [str(column) for column in prepared.columns]
    try:
        prepared.to_parquet(path, index=False)
    except (ValueError, TypeError, OverflowError, ImportError, ArithmeticError):
        # Mixed-type object columns cannot be typed by Arrow; stringify them and retry
        # rather than losing the whole checkpoint over one messy column.
        fallback = prepared.copy()
        for column in fallback.columns:
            if fallback[column].dtype == object:
                fallback[column] = fallback[column].astype("string")
        fallback.to_parquet(path, index=False)


def _algorithm_names(algorithms: Mapping[str, ModuleType] | None) -> list[str]:
    if algorithms is not None:
        return sorted(algorithms)
    try:
        from . import leads as _leads  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        import leads as _leads  # type: ignore[no-redef]  # noqa: PLC0415
    return sorted(_leads.ALGORITHMS)


def _default_loader() -> Callable[..., tuple[pd.DataFrame, pd.DataFrame | None]]:
    try:
        from . import leads as _leads  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        import leads as _leads  # type: ignore[no-redef]  # noqa: PLC0415
    return _leads.load_analysis_frame


def _default_runner() -> Callable[..., Mapping[str, pd.DataFrame]]:
    try:
        from . import leads as _leads  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        import leads as _leads  # type: ignore[no-redef]  # noqa: PLC0415
    return _leads.run_algorithms


def _safe_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(name))
    return cleaned or "frame"


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (str, bytes)):
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    if isinstance(value, Sequence):
        return [_canonical(item) for item in value]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)
