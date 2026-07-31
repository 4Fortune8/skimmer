"""Lead-generation scoring algorithms.

Every algorithm module exposes a ``score(df, **params) -> pd.DataFrame`` function
that accepts an enriched frame (see :func:`metrics.enrich`) and returns one row
per candidate video with at least the columns ``video_id``, ``score`` and a
``reason`` string describing why the video was flagged.

Sibling imports follow the package convention::

    try:
        from .. import metrics
    except ImportError:  # direct sys.path usage
        import metrics
"""
