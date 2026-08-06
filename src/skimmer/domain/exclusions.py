"""Shared title-term exclusion rules for collection and analysis.

This is the single place that answers "is this video one we have decided we do
not want?", so the skimmer's seed selection and the leads analysis cannot drift
apart. A rule written once after reviewing leads must also stop the collector
from spending a page visit harvesting more of the same thing.

Review surfaces junk that no statistical filter can catch, because the videos
are genuine outliers by every metric the algorithms measure and are simply not
ideas this operator wants: devotional upload farms ("Jesus" plus "God Message"),
a sport they do not cover ("FIFA"), studio marketing ("Official Trailer"). The
judgement is human and stable across runs, so it lives in a file rather than in
a notebook cell.

A rule is a *set* of terms that must all appear in the same title. One term is a
plain ban; two or more express "only when these co-occur", which is what makes
the devotional case expressible without also removing every video that mentions
Jesus. Terms match on word boundaries against a normalised title, so ``fifa``
does not fire on "fifate" and casing and punctuation are irrelevant. A term
containing a space is a contiguous phrase (``official trailer``); to mean "both
words anywhere in the title", pass them as separate terms instead.

A term ending in ``*`` matches any word *starting* with the stem, so
``america*`` covers "America", "American" and "Americans" in one term. The
reaction-channel template is the case that demands it: the variation there is
derivational, not plural ("Europe" / "European" / "Europeans"), so folding
plurals would not unify it and writing one rule per spelling means three rules
that each miss the next spelling. Stemming is opt-in per term rather than the
default because the same mechanism applied everywhere would quietly broaden
every existing rule — ``god*`` reaches "Godzilla", "goddess" and "godfather" —
and there would be no way left to express an exact term. Loose stems stay safe
by co-occurrence: ``brit*`` alone would take Britney Spears with it, but
``brit*`` *and* ``america*`` in one title is the template nearly every time.

Rules carry a ``scope`` that the *analysis* uses to decide when to apply them:

``leads``   removes matches from the scored results, after the algorithms have
            run, leaving the videos in the corpus that norms and theme lift are
            measured against. Right for taste.

``corpus``  removes matches before enrichment, so they never reach the norms at
            all. Right for content distorting the measurements themselves.

Collection ignores the distinction and applies every rule, because both scopes
say "I do not want this content" and the cheapest place to act on that is before
a page is ever fetched. See :func:`is_excluded` and :func:`partition`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_FILENAME = "title_exclusions.json"
SCOPES = ("leads", "corpus")
DEFAULT_SCOPE = "leads"
DEFAULT_FIELDS = ("title",)
PATH_ENV_VAR = "SKIMMER_EXCLUSIONS_PATH"
STEM_MARKER = "*"
MIN_STEM_LENGTH = 3

_APOSTROPHES = str.maketrans("", "", "'’ʼ`")
_POSSESSIVE = re.compile(r"['’ʼ`]s(?![0-9a-z])")
_NON_WORD = re.compile(r"[^0-9a-z]+")
_WHITESPACE = re.compile(r"\s+")


def default_path(repo_root: str | Path | None = None) -> Path:
    """Return the on-disk location of the rule file.

    ``SKIMMER_EXCLUSIONS_PATH`` wins so a deployment can point at a shared file.
    Otherwise the path is derived from ``repo_root`` when a caller knows it (the
    notebook does, from its own location) and from the project root otherwise,
    which is how the rest of the collector resolves paths.
    """

    override = os.environ.get(PATH_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if repo_root is not None:
        return Path(repo_root) / "data" / DEFAULT_FILENAME
    try:
        from skimmer.config import PROJECT_ROOT
    except ImportError:  # pragma: no cover - config is always importable in-tree
        PROJECT_ROOT = Path.cwd()
    return Path(PROJECT_ROOT) / "data" / DEFAULT_FILENAME


def normalize_text(value: Any) -> str:
    """Return ``value`` lowercased and reduced to space-separated word characters.

    A trailing possessive is dropped before anything else, so "God's Message"
    normalises to ``god message``. This matters more than it looks: the titles
    this filter exists for mix "God Message Today" and "God's Message" in the
    same upload farm, and any handling that keeps the ``s`` (``gods message``) or
    splits it off (``god s message``) makes the phrase ``god message`` match one
    spelling and miss the other. Remaining apostrophes are deleted rather than
    split so "don't" folds to ``dont``, and every other non-alphanumeric run
    becomes a single space. Digits survive because "FIFA 26" carries meaning.
    """

    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN without importing pandas
        return ""
    text = _POSSESSIVE.sub("", str(value).lower()).translate(_APOSTROPHES)
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", text)).strip()


def parse_term(term: Any) -> tuple[str, bool]:
    """Return ``(normalised_term, is_stem)`` for a raw term.

    The ``*`` marker has to be read *before* normalisation, because normalising
    first deletes it as punctuation and silently turns a stem into an exact term.
    Only a trailing marker on the whole term is honoured; a ``*`` anywhere else
    falls through normalisation as a word separator, as it always has.
    """

    text = "" if term is None else str(term)
    is_stem = text.rstrip().endswith(STEM_MARKER)
    if is_stem:
        text = text.rstrip()[: -len(STEM_MARKER)]
    return normalize_text(text), is_stem


def normalize_term(term: Any) -> str:
    """Return a term in the same normal form as titles, or ``''`` if it is empty.

    The stem marker is not part of the normal form, so ``america*`` normalises to
    ``america``. Callers deciding *how* to match must use :func:`parse_term` or
    :func:`term_pattern`; this function answers only "what word is this".
    """

    return parse_term(term)[0]


def term_pattern(term: str) -> re.Pattern[str]:
    """Return the search pattern for a term, honouring a trailing ``*``.

    Accepts raw or already-normalised terms, and is the only place that turns a
    term into a regex, so a vectorised caller searches for exactly what the
    scalar path searches for; two implementations of "does this term appear"
    would be two chances to disagree.

    A stem drops the closing word boundary and consumes the rest of the word
    instead, which is the whole difference between ``american`` and
    ``american(s)``. The opening boundary is kept either way, so a stem still
    has to start a word: ``america*`` never fires inside "panamerican".
    """

    normalized, is_stem = parse_term(term)
    words = [word for word in normalized.split(" ") if word]
    if not words:
        raise ValueError(f"cannot build a pattern for an empty term: {term!r}")
    if is_stem and len(words[-1]) < MIN_STEM_LENGTH:
        raise ValueError(
            f"stem {term!r} is shorter than {MIN_STEM_LENGTH} characters; it would match most titles"
        )
    body = r"\s+".join(re.escape(word) for word in words)
    trailing = r"[0-9a-z]*" if is_stem else r"(?![0-9a-z])"
    return re.compile(r"(?<![0-9a-z])" + body + trailing)


def make_rule(
    terms: str | Iterable[str],
    *,
    scope: str = DEFAULT_SCOPE,
    fields: Iterable[str] | None = None,
    note: str | None = None,
    added: str | None = None,
) -> dict[str, Any]:
    """Return a validated rule dict.

    ``terms`` may be a single string or an iterable; every term must survive
    normalisation, because a rule whose terms all normalise away would match
    every row. A trailing ``*`` is preserved through normalisation, since it is
    part of what the term *means* rather than punctuation to be folded away, and
    is validated here so an unusable stem is refused at the point it is written
    rather than at the point it is searched.
    """

    if isinstance(terms, str):
        raw_terms: list[str] = [terms]
    else:
        raw_terms = [str(term) for term in terms]
    cleaned: list[str] = []
    for raw_term in raw_terms:
        normalized, is_stem = parse_term(raw_term)
        if not normalized:
            continue
        canonical_term = normalized + STEM_MARKER if is_stem else normalized
        term_pattern(canonical_term)  # rejects a stem too short to be selective
        cleaned.append(canonical_term)
    if not cleaned:
        raise ValueError(f"rule has no usable terms after normalisation: {raw_terms!r}")
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    resolved_fields = tuple(str(field) for field in (fields or DEFAULT_FIELDS))
    if not resolved_fields:
        raise ValueError("rule must name at least one field")
    rule: dict[str, Any] = {
        "terms": sorted(dict.fromkeys(cleaned)),
        "scope": scope,
        "fields": list(resolved_fields),
        "added": added or date.today().isoformat(),
    }
    if note:
        rule["note"] = str(note)
    return rule


def rule_id(rule: Mapping[str, Any]) -> str:
    """Return the identity of a rule: its term set, scope and fields.

    Terms are order-insensitive because the rule is a conjunction, so
    ``['jesus', 'god message']`` and ``['god message', 'jesus']`` are one rule
    and must not both be storable.
    """

    terms = "+".join(sorted(str(term) for term in rule.get("terms", [])))
    fields = ",".join(sorted(str(field) for field in rule.get("fields", DEFAULT_FIELDS)))
    return f"{rule.get('scope', DEFAULT_SCOPE)}:{fields}:{terms}"


def describe_rule(rule: Mapping[str, Any]) -> str:
    """Return a human-readable one-line form of a rule."""

    terms = " AND ".join(f"'{term}'" for term in rule.get("terms", []))
    fields = "/".join(rule.get("fields", DEFAULT_FIELDS))
    return f"{terms} [{rule.get('scope', DEFAULT_SCOPE)} on {fields}]"


def canonical(rules: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Return rules reduced to the fields that change behaviour, in a stable order.

    ``note`` and ``added`` are commentary. Including them in a checkpoint cache
    key would make re-typing a note look like a changed filter and throw away an
    analysis frame that takes minutes to rebuild, so the key is taken over this
    form. The result is still a usable rule list, not just a digest.
    """

    reduced = [
        {
            "terms": list(rule.get("terms", [])),
            "scope": str(rule.get("scope", DEFAULT_SCOPE)),
            "fields": list(rule.get("fields", DEFAULT_FIELDS)),
        }
        for rule in (rules or [])
    ]
    return sorted(reduced, key=rule_id)


def for_scope(rules: Iterable[Mapping[str, Any]] | None, scope: str) -> list[dict[str, Any]]:
    """Return only the rules belonging to ``scope``."""

    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    return [dict(rule) for rule in (rules or []) if str(rule.get("scope", DEFAULT_SCOPE)) == scope]


def load(path: str | Path | None = None, *, repo_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Return the saved rules, or an empty list when the file does not exist.

    A malformed file is reported and treated as empty rather than raised, so a
    hand-edit typo cannot block a collection run or an entire notebook; the file
    is left on disk untouched so the typo can be fixed rather than silently
    overwritten.
    """

    file_path = Path(path) if path is not None else default_path(repo_root)
    if not file_path.exists():
        return []
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read exclusions from %s (%s); treating as empty.", file_path, exc)
        return []
    if isinstance(payload, Mapping):
        raw_rules = payload.get("rules", [])
    elif isinstance(payload, Sequence):
        raw_rules = payload
    else:
        logger.warning("Unexpected exclusions payload in %s; treating as empty.", file_path)
        return []

    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_rules:
        if isinstance(entry, str):
            entry = {"terms": [entry]}
        if not isinstance(entry, Mapping):
            logger.warning("Skipping non-object exclusion entry: %r", entry)
            continue
        try:
            rule = make_rule(
                entry.get("terms", []),
                scope=str(entry.get("scope", DEFAULT_SCOPE)),
                fields=entry.get("fields") or DEFAULT_FIELDS,
                note=entry.get("note"),
                added=entry.get("added"),
            )
        except ValueError as exc:
            logger.warning("Skipping invalid exclusion entry %r: %s", entry, exc)
            continue
        identity = rule_id(rule)
        if identity in seen:
            continue
        seen.add(identity)
        rules.append(rule)
    return rules


def save(
    rules: Iterable[Mapping[str, Any]],
    path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
) -> Path:
    """Write ``rules`` to disk and return the path written."""

    file_path = Path(path) if path is not None else default_path(repo_root)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "updated": date.today().isoformat(),
        "rules": [dict(rule) for rule in rules],
    }
    file_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return file_path


def add(
    terms: str | Iterable[str],
    *,
    scope: str = DEFAULT_SCOPE,
    fields: Iterable[str] | None = None,
    note: str | None = None,
    path: str | Path | None = None,
    rules: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Add one rule, returning ``(rules, added)``.

    ``added`` is False when an identical rule already exists, which makes the
    notebook cell that calls this safe to re-run.
    """

    current = [dict(rule) for rule in (rules if rules is not None else load(path))]
    rule = make_rule(terms, scope=scope, fields=fields, note=note)
    identity = rule_id(rule)
    if any(rule_id(existing) == identity for existing in current):
        return current, False
    current.append(rule)
    return current, True


def remove(
    terms: str | Iterable[str],
    *,
    scope: str = DEFAULT_SCOPE,
    fields: Iterable[str] | None = None,
    path: str | Path | None = None,
    rules: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Remove one rule by identity, returning ``(rules, removed)``."""

    current = [dict(rule) for rule in (rules if rules is not None else load(path))]
    identity = rule_id(make_rule(terms, scope=scope, fields=fields))
    remaining = [rule for rule in current if rule_id(rule) != identity]
    return remaining, len(remaining) != len(current)


def _record_text(record: Any, fields: Iterable[str]) -> str:
    """Return the normalised text of ``record`` across ``fields``.

    Accepts mappings and attribute objects alike so the same matcher serves the
    database rows the selector reads and the ``DiscoverySeed`` objects it
    returns.
    """

    values = []
    for field in fields:
        if isinstance(record, Mapping):
            value = record.get(field)
        else:
            value = getattr(record, field, None)
        normalized = normalize_text(value)
        if normalized:
            values.append(normalized)
    return " ".join(values)


def rule_matches(rule: Mapping[str, Any], record: Any) -> bool:
    """Return True when every term of ``rule`` is present in ``record``."""

    text = _record_text(record, rule.get("fields", DEFAULT_FIELDS))
    if not text:
        return False
    for term in rule.get("terms", []):
        if not normalize_term(term):
            continue
        if not term_pattern(term).search(text):
            return False
    return True


def first_match(record: Any, rules: Iterable[Mapping[str, Any]] | None) -> dict[str, Any] | None:
    """Return the first rule matching ``record``, or None.

    Returning the rule rather than a boolean lets callers log *why* a candidate
    was skipped, which is the difference between a debuggable collection run and
    one that silently visits fewer pages than expected.
    """

    for rule in rules or []:
        if rule_matches(rule, record):
            return dict(rule)
    return None


def is_excluded(record: Any, rules: Iterable[Mapping[str, Any]] | None) -> bool:
    """Return True when any rule matches ``record``."""

    return first_match(record, rules) is not None


def partition(
    records: Iterable[Any],
    rules: Iterable[Mapping[str, Any]] | None,
) -> tuple[list[Any], list[tuple[Any, dict[str, Any]]]]:
    """Split ``records`` into ``(kept, [(record, matched_rule), ...])``."""

    rule_list = list(rules or [])
    kept: list[Any] = []
    dropped: list[tuple[Any, dict[str, Any]]] = []
    for record in records:
        matched = first_match(record, rule_list) if rule_list else None
        if matched is None:
            kept.append(record)
        else:
            dropped.append((record, matched))
    return kept, dropped
