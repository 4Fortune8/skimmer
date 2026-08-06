"""DataFrame adapter over :mod:`skimmer.domain.exclusions`.

The policy — what a rule is, how terms are normalised and matched, what the
scopes mean, and where the rule file lives — belongs to the domain module so the
collector's seed selection and this analysis cannot drift apart. A rule written
after reviewing leads has to mean the same thing to the skimmer, or the notebook
would keep hiding videos the collector keeps going out to gather more of.

This module only applies that policy to frames. It matches vectorised rather
than row by row, because the background corpus is a quarter of a million titles
and a per-row Python call over it is seconds per rule; the regex it searches for
comes from the domain module, so the two paths cannot disagree about what a term
means.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd


def _load_domain_exclusions():
    """Load the shared exclusions module from this checkout.

    ``skimmer`` is installed non-editable, so an installed copy can be older
    than the repository the notebook is running from. Analysis code should track
    the checkout it lives in, so the repository file wins and the installed
    package is only a fallback.
    """

    import importlib.util
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src" / "skimmer" / "domain" / "exclusions.py"
    if source.exists():
        spec = importlib.util.spec_from_file_location("skimmer_domain_exclusions", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    from skimmer.domain import exclusions as installed  # pragma: no cover

    return installed


domain = _load_domain_exclusions()
logger = logging.getLogger(__name__)

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]

SCHEMA_VERSION = domain.SCHEMA_VERSION
DEFAULT_FILENAME = domain.DEFAULT_FILENAME
SCOPES = domain.SCOPES
DEFAULT_SCOPE = domain.DEFAULT_SCOPE
DEFAULT_FIELDS = domain.DEFAULT_FIELDS

STEM_MARKER = domain.STEM_MARKER
MIN_STEM_LENGTH = domain.MIN_STEM_LENGTH

normalize_text = domain.normalize_text
normalize_term = domain.normalize_term
parse_term = domain.parse_term
term_pattern = domain.term_pattern
make_rule = domain.make_rule
rule_id = domain.rule_id
describe_rule = domain.describe_rule
canonical = domain.canonical
for_scope = domain.for_scope
first_match = domain.first_match
is_excluded = domain.is_excluded
partition = domain.partition


def default_path(repo_root: Any = None) -> Any:
    """Return the rule file for *this checkout* unless told otherwise.

    The notebook locates its repository root explicitly rather than relying on
    the working directory, so the analysis default is anchored to this file's
    location. The domain default falls back to the project root, which is how
    the collector resolves paths.
    """

    return domain.default_path(REPO_ROOT if repo_root is None else repo_root)


def load(path: Any = None) -> list[dict[str, Any]]:
    """Return the saved rules, or an empty list when the file does not exist."""

    return domain.load(path, repo_root=REPO_ROOT)


def save(rules: Iterable[Mapping[str, Any]], path: Any = None) -> Any:
    """Write ``rules`` to disk and return the path written."""

    return domain.save(rules, path, repo_root=REPO_ROOT)


def add(terms: Any, *, rules: Iterable[Mapping[str, Any]] | None = None, path: Any = None, **kwargs: Any):
    """Add one rule, returning ``(rules, added)``.

    Wrapped rather than re-exported so that omitting ``rules`` reads the same
    file this module's :func:`load` writes; the domain default resolves from the
    working directory, which is not where the notebook's rules live.
    """

    current = load(path) if rules is None else rules
    return domain.add(terms, rules=current, **kwargs)


def remove(terms: Any, *, rules: Iterable[Mapping[str, Any]] | None = None, path: Any = None, **kwargs: Any):
    """Remove one rule by identity, returning ``(rules, removed)``."""

    current = load(path) if rules is None else rules
    return domain.remove(terms, rules=current, **kwargs)


def rule_mask(df: pd.DataFrame, rule: Mapping[str, Any]) -> pd.Series:
    """Return a boolean mask of rows matching a single rule.

    Every term must be found, but not necessarily in the same field: a rule over
    ``title`` and ``channel_name`` matches when each term appears in either. The
    per-field normalised text is concatenated once and searched, so cost is
    linear in rows regardless of term count.
    """

    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    fields = [str(field) for field in rule.get("fields", DEFAULT_FIELDS)]
    present = [field for field in fields if field in df.columns]
    if not present:
        logger.warning("Exclusion rule %s names no column present in the frame.", describe_rule(rule))
        return pd.Series(False, index=df.index, dtype=bool)

    haystack = None
    for field in present:
        normalized = df[field].map(normalize_text)
        haystack = normalized if haystack is None else haystack.str.cat(normalized, sep=" ")

    mask_series = pd.Series(True, index=df.index, dtype=bool)
    for term in rule.get("terms", []):
        if not normalize_term(term):
            continue
        # The raw term, not the normalised one: normalisation drops the trailing
        # ``*`` that tells the pattern builder to match the whole word family.
        mask_series &= haystack.str.contains(term_pattern(term), regex=True, na=False)
        if not mask_series.any():
            break
    return mask_series


def mask(df: pd.DataFrame, rules: Iterable[Mapping[str, Any]] | None) -> pd.Series:
    """Return a boolean mask of rows matched by *any* rule (True means exclude)."""

    combined = pd.Series(False, index=df.index, dtype=bool)
    if df.empty:
        return combined
    for rule in rules or []:
        combined |= rule_mask(df, rule)
    return combined


def apply(
    df: pd.DataFrame,
    rules: Iterable[Mapping[str, Any]] | None,
    *,
    label: str = "frame",
) -> pd.DataFrame:
    """Return ``df`` without the rows matched by ``rules``."""

    rule_list = list(rules or [])
    if df.empty or not rule_list:
        return df
    excluded = mask(df, rule_list)
    removed = int(excluded.sum())
    if removed:
        logger.info("Exclusion rules removed %d of %d rows from %s.", removed, len(df), label)
    return df.loc[~excluded].copy()


def apply_to_results(
    results: Mapping[str, pd.DataFrame],
    rules: Iterable[Mapping[str, Any]] | None,
) -> dict[str, pd.DataFrame]:
    """Return algorithm result frames with matched rows removed.

    ``attrs`` are preserved per frame because the pipeline carries algorithm
    failure information there, and a filtered frame that silently dropped it
    would make failures invisible downstream.
    """

    rule_list = list(rules or [])
    filtered: dict[str, pd.DataFrame] = {}
    for name, frame in results.items():
        if rule_list and not frame.empty:
            kept = apply(frame, rule_list, label=f"{name} results")
        else:
            kept = frame
        kept.attrs.update(frame.attrs)
        filtered[name] = kept
    return filtered


def summarize(
    df: pd.DataFrame,
    rules: Iterable[Mapping[str, Any]] | None,
    *,
    examples: int = 2,
) -> pd.DataFrame:
    """Return per-rule hit counts against ``df``, with example titles.

    A rule matching zero rows is usually a typo or an over-specific phrase, and a
    rule matching an implausible share of the corpus is usually a term that is
    too generic; both are visible here before the rule is trusted.
    """

    columns = ["rule", "scope", "terms", "matched_rows", "matched_share", "examples"]
    rule_list = list(rules or [])
    if not rule_list:
        return pd.DataFrame(columns=columns)

    total = max(len(df), 1)
    rows = []
    for rule in rule_list:
        matched = rule_mask(df, rule) if not df.empty else pd.Series(dtype=bool)
        count = int(matched.sum()) if len(matched) else 0
        sample = []
        if count and "title" in df.columns:
            sample = df.loc[matched, "title"].astype(str).head(examples).tolist()
        rows.append(
            {
                "rule": describe_rule(rule),
                "scope": rule.get("scope", DEFAULT_SCOPE),
                "terms": " AND ".join(rule.get("terms", [])),
                "matched_rows": count,
                "matched_share": count / total,
                "examples": " | ".join(sample),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("matched_rows", ascending=False).reset_index(drop=True)
