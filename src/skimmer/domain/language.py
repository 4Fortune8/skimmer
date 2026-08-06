"""Shared language identification for collected YouTube content.

This is the single place that answers "what language is this?", so the leads
filter, the topics algorithm, and anything added later agree on one answer.

Two sources feed it, in priority order:

1. **YouTube's own metadata.** ``snippet.defaultAudioLanguage`` and
   ``snippet.defaultLanguage`` arrive free with the ``part=snippet`` call the
   collector already makes, and they are present on roughly four out of five
   videos. This is the only source that gets romanized non-English right: a
   title like "Kabze - Bintu Pabra I Shiva Choudhary I Pellet Drum Productions"
   is pure ASCII with no English words, and *every* statistical detector tested
   (langdetect, lingua, py3langid, fastText lid.176, GlotLID) calls it English
   with high confidence, while YouTube reports ``hi``.

2. **fastText lid.176** for whatever metadata does not cover. It is a standard
   176-language model, ~1MB, and classifies ~25k titles/second. GlotLID was
   evaluated as an alternative because it carries romanized labels
   (``hin_Latn``); it was rejected because its 2000-language granularity
   shreds ordinary English into Nigerian Pidgin, Scots, and Jamaican Creole,
   mislabelling plain English channels at a far higher rate than it rescues
   romanized ones.

Detection is deliberately exposed per text *and* per group. A single video
title carries five to ten usable tokens, which is too thin to classify
reliably; pooling a channel's titles is what makes the detector accurate, and
channel is also the right granularity for the product question, since a creator
publishing in Spanish is not an English-audience lead regardless of which
individual video surfaced.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

#: Tag used when no source can name the language.
UNKNOWN = "und"

#: ``zxx`` is YouTube's "no linguistic content" tag, common on music uploads.
#: It is not evidence of a language, so it is treated as missing and the
#: detector is allowed to decide from the title text instead.
NO_CONTENT_TAGS = frozenset({"zxx", "und", "mul", ""})

DEFAULT_MODEL_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz"
MODEL_FILENAME = "lid.176.ftz"

#: Where the model is looked for, in order. The repository path only resolves
#: when running from a checkout; an installed copy of this package sits in
#: site-packages, where no ``data/`` directory exists, so a user cache is the
#: fallback that works for both.
_REPO_MODEL_PATH = Path(__file__).resolve().parents[3] / "data" / "models" / MODEL_FILENAME
_CACHE_MODEL_PATH = Path.home() / ".cache" / "skimmer" / MODEL_FILENAME

_TAG_RE = re.compile(r"^[a-z]{2,3}")


class LanguageModelMissing(RuntimeError):
    """Raised when detection is requested but the fastText model is not present."""


def _is_missing(value):
    """Return True for None, NaN, and pandas' NA, without importing pandas.

    ``pd.NA`` refuses to answer ``bool(NA != NA)`` and raises instead, which is
    why the comparison is guarded: a value that cannot say whether it equals
    itself is not a usable language tag either way.
    """

    if value is None:
        return True
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return True


def normalize_tag(tag):
    """Reduce a BCP-47 tag to its primary language subtag.

    ``en-GB``, ``en_US``, and ``EN`` all become ``en``; ``es-419`` becomes
    ``es``. Tags carrying no linguistic content return ``None`` so callers fall
    through to detection rather than trusting them.
    """

    if _is_missing(tag):  # str(nan) would otherwise parse as the tag "nan"
        return None
    text = str(tag).strip().lower().replace("_", "-")
    if text in NO_CONTENT_TAGS:
        return None
    match = _TAG_RE.match(text)
    if not match:
        return None
    primary = match.group(0)
    return None if primary in NO_CONTENT_TAGS else primary


def region_of(tag):
    """Return the uppercase region subtag of a BCP-47 tag, or None."""

    if _is_missing(tag):
        return None
    parts = str(tag).strip().lower().replace("_", "-").split("-")
    for part in parts[1:]:
        if len(part) == 2 and part.isalpha():
            return part.upper()
    return None


def is_english(tag):
    """Return True when a tag names English, accepting regional variants."""

    return normalize_tag(tag) == "en"


def is_western_english(tag):
    """Return True for English from a western-audience region.

    ``en-IN`` is English, but it is Indian-market English, and a creator serving
    that market is not a lead for a western audience. Tags with no region are
    accepted, since most English content is tagged bare ``en``.
    """

    if not is_english(tag):
        return False
    region = region_of(tag)
    return region is None or region in WESTERN_ENGLISH_REGIONS


def model_path():
    """Return the fastText model location, preferring one that already exists.

    ``SKIMMER_LID_MODEL_PATH`` wins outright. Otherwise an existing file is
    returned from either the repository checkout or the user cache, so a model
    downloaded once is found whether the caller imported this module from
    ``src/`` or from an installed copy in site-packages.
    """

    override = os.environ.get("SKIMMER_LID_MODEL_PATH")
    if override:
        return Path(override).expanduser()
    for candidate in (_REPO_MODEL_PATH, _CACHE_MODEL_PATH):
        if candidate.exists():
            return candidate
    return _REPO_MODEL_PATH if _REPO_MODEL_PATH.parent.parent.is_dir() else _CACHE_MODEL_PATH


def download_model(destination=None, url=DEFAULT_MODEL_URL):
    """Fetch the fastText language model. Called by ``make lid-model``.

    Downloads to the user cache by default, because that path resolves the same
    way whether the caller imported this module from a checkout or from an
    installed copy. An existing model elsewhere is left alone.

    Detection never downloads implicitly: analysis runs should not make
    surprise network requests, so a missing model raises instead.
    """

    if destination:
        target = Path(destination)
    else:
        existing = model_path()
        target = existing if existing.exists() else _CACHE_MODEL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading language model from %s to %s", url, target)
    with urllib.request.urlopen(url, timeout=120) as response:
        payload = response.read()
    target.write_bytes(payload)
    return target


@functools.lru_cache(maxsize=1)
def load_detector():
    """Load and cache the fastText model, raising a fixable error when absent."""

    path = model_path()
    if not path.exists():
        raise LanguageModelMissing(
            f"Language model not found at {path}. Run 'make lid-model' to download it, "
            "or set SKIMMER_LID_MODEL_PATH to an existing lid.176 model."
        )
    try:
        import fasttext
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LanguageModelMissing(
            "fasttext is not installed. Install the analysis extra: pip install -e '.[analysis]'"
        ) from exc
    return fasttext.load_model(str(path))


def _predict(model, text, k=1):
    # fasttext-wheel 0.9.2's Python predict() wrapper calls np.array(copy=False),
    # which numpy 2 rejects, so the C++ model is called directly.
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return []
    return [
        (label.replace("__label__", ""), float(probability))
        for probability, label in model.f.predict(cleaned, k, 0.0, "strict")
    ]


def detect(text, minimum_confidence=0.0):
    """Return ``(tag, confidence)`` for one piece of text, or ``(UNKNOWN, 0.0)``.

    Short inputs are genuinely ambiguous, so callers that can pool related text
    (a channel's titles) should do so via :func:`detect_group` instead.
    """

    predictions = _predict(load_detector(), text)
    if not predictions:
        return UNKNOWN, 0.0
    tag, confidence = predictions[0]
    if confidence < minimum_confidence:
        return UNKNOWN, confidence
    return normalize_tag(tag) or UNKNOWN, confidence


def detect_group(texts, character_budget=5000, minimum_confidence=0.0):
    """Classify a group of related texts as one document.

    Pooling is what lifts accuracy on title-length input: the corpus averages
    ~22 titles per channel, which is ample evidence where one title is not.
    """

    pooled = " ".join(str(text) for text in texts if text)[:character_budget]
    return detect(pooled, minimum_confidence=minimum_confidence)


#: Reported in ``language_source`` so callers can see how strong the evidence is.
#: ``audio`` is decisive, ``text`` only describes the title's script, and
#: ``detected`` is a guess from title text alone.
SOURCE_AUDIO = "audio"
SOURCE_TEXT = "text"
SOURCE_REGION = "region"
SOURCE_SCRIPT = "script"
SOURCE_MARKERS = "markers"
SOURCE_DETECTED = "detected"

#: Sources that positively rule English out. A channel carrying one of these is
#: never kept as "unlabelled": having no evidence and having evidence against
#: are different states, and only the first deserves the benefit of the doubt.
CONTRADICTED_SOURCES = frozenset({SOURCE_REGION, SOURCE_SCRIPT, SOURCE_MARKERS})

#: Regions whose English is the western-audience English being targeted. YouTube
#: reports the region in the tag itself (``en-IN``), which is thrown away by
#: reducing a tag to its primary subtag, so it is checked before that happens.
WESTERN_ENGLISH_REGIONS = frozenset({"US", "GB", "CA", "AU", "NZ", "IE", "ZA"})

#: A title needs this many characters from a non-Latin writing system before it
#: counts as carrying one, so a stray Greek letter in a maths title or a single
#: CJK character in an English one does not trigger it.
FOREIGN_SCRIPT_MIN_CHARS = 3

#: Share of a channel's titles that must carry a foreign script before the
#: channel stops counting as English.
FOREIGN_SCRIPT_MIN_SHARE = 0.10


#: Writing systems recognised by name. An allowlist rather than a denylist,
#: because emoji and symbols carry names whose first word looks just like a
#: script would ("FACE SCREAMING IN FEAR", "HEAVY BLACK HEART") and would
#: otherwise be counted as foreign text.
_NON_LATIN_SCRIPTS = frozenset(
    {
        "DEVANAGARI", "ARABIC", "CJK", "HIRAGANA", "KATAKANA", "HANGUL", "CYRILLIC",
        "GREEK", "HEBREW", "THAI", "BENGALI", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM",
        "GUJARATI", "GURMUKHI", "ORIYA", "SINHALA", "MYANMAR", "KHMER", "LAO", "TIBETAN",
        "GEORGIAN", "ARMENIAN", "ETHIOPIC", "MONGOLIAN", "SYRIAC", "THAANA", "YI",
        "CHEROKEE", "VAI", "COPTIC", "GLAGOLITIC", "RUNIC", "OGHAM", "IDEOGRAPHIC",
    }
)


def script_of(char):
    """Return the writing system a character belongs to, or None if it has none.

    Read from the first word of the Unicode character name ("DEVANAGARI DANDA",
    "CJK UNIFIED IDEOGRAPH-4E00"), and accepted only if that word names a script
    this module knows about.
    """

    name = unicodedata.name(char, "")
    if not name:
        return None
    script = name.split(" ", 1)[0].split("-", 1)[0]
    if script == "LATIN" or script in _NON_LATIN_SCRIPTS:
        return script
    return None


def foreign_script_chars(text):
    """Count characters written in a script other than Latin."""

    total = 0
    for char in unicodedata.normalize("NFKD", str(text)):
        script = script_of(char)
        if script and script != "LATIN":
            total += 1
    return total


def carries_foreign_script(text, minimum=FOREIGN_SCRIPT_MIN_CHARS):
    """Return True when a title visibly uses a non-Latin writing system."""

    return foreign_script_chars(text) >= minimum


def foreign_script_share(texts, minimum=FOREIGN_SCRIPT_MIN_CHARS):
    """Return the share of a group's texts that carry a non-Latin script."""

    texts = [text for text in texts if str(text).strip()]
    if not texts:
        return 0.0
    return sum(1 for text in texts if carries_foreign_script(text, minimum)) / len(texts)


MARKERS_PATH = Path(__file__).resolve().parent / "lexicons" / "romanized_markers.txt"

#: Share of a channel's title tokens that must be romanized markers before the
#: channel stops counting as English. Chosen on held-out channels: it recalls
#: ~75% of South Asian channels at ~1% nominal false positives, and inspection
#: showed most of those "false positives" were Indian-market channels that
#: YouTube had simply mislabelled as English audio.
MARKER_MIN_SHARE = 0.04

_MARKER_TOKEN_RE = re.compile(r"[a-z][a-z']*")
_MIN_MARKER_TOKEN = 3


@functools.lru_cache(maxsize=1)
def romanized_markers():
    """Load the learned romanized-marker vocabulary.

    These tokens are *learned*, not hand-written: ``scripts/train_language_markers.py``
    mines them from channels YouTube labelled with a South Asian audio language,
    keeping tokens that essentially never appear on English-labelled channels.
    Metadata thereby teaches the filter to catch the cases where metadata is
    missing or wrong, which is the whole failure mode here -- a romanized Hindi
    song uploaded with ``defaultAudioLanguage=en``.
    """

    if not MARKERS_PATH.exists():  # pragma: no cover - packaging guard
        logger.warning("Romanized marker lexicon missing at %s; that tier is disabled.", MARKERS_PATH)
        return frozenset()
    return frozenset(MARKERS_PATH.read_text(encoding="utf-8").split())


def fold(text):
    """Return lowercase ASCII text with accents and styled unicode folded away.

    NFKD matters beyond accents here: YouTube titles are full of mathematical
    and fullwidth alphabets ("𝙥𝙡𝙖𝙮𝙡𝙞𝙨𝙩"), which are ordinary words that would
    otherwise tokenise to nothing.
    """

    return unicodedata.normalize("NFKD", str(text).lower()).encode("ascii", "ignore").decode("ascii")


def _marker_tokens(text):
    folded = fold(text)
    return [
        token
        for token in (value.strip("'") for value in _MARKER_TOKEN_RE.findall(folded))
        if len(token) >= _MIN_MARKER_TOKEN
    ]


def romanized_marker_share(texts):
    """Return the share of a group's title tokens that are romanized markers."""

    markers = romanized_markers()
    if not markers:
        return 0.0
    tokens = [token for text in texts for token in _marker_tokens(text)]
    if not tokens:
        return 0.0
    return sum(1 for token in tokens if token in markers) / len(tokens)


def resolve(audio_tags, text_tags=(), texts=(), character_budget=5000):
    """Decide one language for a group: audio metadata, then text, then detection.

    The two YouTube fields are deliberately *not* pooled. ``defaultAudioLanguage``
    describes the spoken audio and ``defaultLanguage`` describes the language the
    title and description are written in, and they disagree constantly in exactly
    the population that matters here: a Bollywood upload has Hindi audio under a
    transliterated English title, giving ``hi`` audio and ``en`` text. Counting
    both as equal votes makes that a tie, and a tie broken by a detector that
    cannot read romanized Hindi resolves to English every time.

    Audio therefore outranks text, because "what will a viewer hear" is the
    question an audience filter is actually asking, while a transliterated title
    is Latin-scripted by definition and says almost nothing.

    A channel is English only if nothing credible contradicts it: any non-English
    tag in either field disqualifies it, rather than being outvoted. That is a
    deliberate trade of recall for precision. A channel posting mostly in English
    with occasional Hindi uploads is not a clean source of English-audience
    ideas, and the alternative -- majority voting -- is what let mixed ``{en, hi}``
    music channels through.

    Titles written in a non-Latin script override an English metadata claim.
    Tags are typed by a human and are wrong often enough to matter, but a title
    containing Devanagari or Arabic characters is objective evidence that no
    ``defaultAudioLanguage`` of ``en`` can talk us out of. This is the mirror of
    the romanized problem and the one case where reading the title beats asking
    YouTube.
    """

    raw = [(value, SOURCE_AUDIO) for value in audio_tags] + [(value, SOURCE_TEXT) for value in text_tags]
    audio = [tag for tag in (normalize_tag(value) for value in audio_tags) if tag]
    text = [tag for tag in (normalize_tag(value) for value in text_tags) if tag]

    for tags, source in ((audio, SOURCE_AUDIO), (text, SOURCE_TEXT)):
        foreign = [tag for tag in tags if tag != "en"]
        if foreign:
            counts = Counter(foreign)
            # Count desc then alphabetical, so the label never depends on row order.
            tag = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            return tag, len(foreign) / len(tags), source

    # English, but from a region that is not the audience being targeted.
    non_western = [
        str(value).strip().lower().replace("_", "-")
        for value, _ in raw
        if is_english(value) and not is_western_english(value)
    ]
    if non_western:
        counts = Counter(non_western)
        tag = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        return tag, len(non_western) / max(len(audio) + len(text), 1), SOURCE_REGION

    script_share = foreign_script_share(texts)
    if script_share > FOREIGN_SCRIPT_MIN_SHARE:
        # Name the language with the detector, which is reliable on non-Latin
        # scripts. If it is unavailable or insists on English despite the script,
        # report UNKNOWN: the evidence still rules English out, and callers must
        # not read that as a channel merely lacking evidence.
        try:
            detected, _ = detect_group(texts, character_budget=character_budget)
        except LanguageModelMissing:
            detected = UNKNOWN
        return (UNKNOWN if detected == "en" else detected), script_share, SOURCE_SCRIPT

    # Last tier, and the only one that reaches romanized content: titles written
    # in Latin script whose vocabulary is not English. Nothing above can see it,
    # because the tags claim English and no detector reads transliterated Hindi.
    marker_share = romanized_marker_share(texts)
    if marker_share > MARKER_MIN_SHARE:
        return UNKNOWN, marker_share, SOURCE_MARKERS

    if audio:
        return "en", 1.0, SOURCE_AUDIO
    if text:
        return "en", 1.0, SOURCE_TEXT
    tag, confidence = detect_group(texts, character_budget=character_budget)
    return tag, confidence, SOURCE_DETECTED


@functools.lru_cache(maxsize=64)
def stopwords(language="en"):
    """Return stopwords for a language, empty when the language is unsupported.

    Backed by ``stopwordsiso`` (58 languages) so tokenizers do not each carry a
    hand-maintained English list.
    """

    tag = normalize_tag(language)
    if not tag:
        return frozenset()
    try:
        import stopwordsiso
    except ImportError:  # pragma: no cover - optional at collection time
        logger.warning("stopwordsiso is not installed; stopword filtering is disabled.")
        return frozenset()
    if not stopwordsiso.has_lang(tag):
        return frozenset()
    return frozenset(stopwordsiso.stopwords(tag))
