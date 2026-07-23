"""Shared paths for local development and host deployment."""

import os
from pathlib import Path


PROJECT_ROOT = Path(
    os.environ.get("SKIMMER_PROJECT_ROOT", Path.cwd())
).expanduser().resolve()


def _load_dotenv():
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip("'\"")
        os.environ[key] = value


_load_dotenv()


def youtube_api_key():
    value = os.environ.get("youtubeAPI")
    if not value:
        raise RuntimeError(
            "Missing youtubeAPI environment variable. Add it to .env or export it."
        )
    return value
