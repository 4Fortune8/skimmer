"""Shared paths for local development and host deployment."""

import os
from pathlib import Path


PROJECT_ROOT = Path(
    os.environ.get("SKIMMER_PROJECT_ROOT", Path.cwd())
).expanduser().resolve()
