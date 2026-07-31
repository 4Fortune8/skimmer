"""Foundation utilities for recommendation lead-generation analysis.

Modules in this package avoid mandatory relative imports so they also work when
``scripts/RecomendationAnalysis`` is placed directly on ``sys.path``. Follow the
pattern ``try: from . import metrics`` with ``except ImportError: import metrics``
when adding modules that need sibling imports.
"""

try:
    from . import checkpoint, data_access, leads, metrics
except ImportError:  # pragma: no cover - supports direct sys.path imports.
    import checkpoint  # type: ignore[no-redef]
    import data_access  # type: ignore[no-redef]
    import leads  # type: ignore[no-redef]
    import metrics  # type: ignore[no-redef]

__all__ = ["checkpoint", "data_access", "leads", "metrics"]
