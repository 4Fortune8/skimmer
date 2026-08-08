import json
import re
import unicodedata

# Trailing attribution and promotional tails that differ between reposts of the
# same video: "| Channel Name", "(Official Video)", hashtag runs, emoji.
#
# Only the pipe is treated as an attribution separator. Dashes were tried and
# removed: em-dashes are used mid-title routinely ("Ozempic — Here's How"), so
# stripping after one deletes meaning rather than attribution. Dashes still
# collapse to whitespace below, which is enough to merge punctuation variants.
_TRAILING_SEGMENT = re.compile(r"\s*[|·]\s*[^|·]{1,40}$")
_HASHTAG_RUN = re.compile(r"(?:#\w+\s*)+$")
_BRACKETED = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_title(title, strip_trailing_segment=True):
    """Collapse a video title to a comparable form for duplicate detection.

    Reposts of the same video routinely differ only in emoji, punctuation, a
    trailing "| Channel Name", or a run of hashtags, so exact matching misses
    them. Normalising first catches those without the cost or false-merge risk
    of fuzzy similarity.

    `strip_trailing_segment` removes one trailing "| ..." style attribution. It
    is off for titles that legitimately end in a short segment, since the rule
    cannot tell attribution from meaning.
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", str(title))
    # Drop emoji and other symbol/pictograph characters.
    text = "".join(
        char for char in text if unicodedata.category(char) not in {"So", "Cf", "Sk"}
    )
    text = text.strip()
    text = _HASHTAG_RUN.sub("", text).strip()
    text = _BRACKETED.sub(" ", text)
    if strip_trailing_segment:
        text = _TRAILING_SEGMENT.sub("", text)
    text = text.casefold()
    text = _NON_ALNUM.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def normalize_channel_profile(
    *,
    source,
    channel_id,
    channel_name=None,
    subscribers_total=None,
    subscribers_change=None,
    subscribers_change_percentage=None,
    views_total=None,
    views_change=None,
    views_change_percentage=None,
    earnings_low=None,
    earnings_high=None,
    engagement=None,
    upload_frequency=None,
    average_length=None,
    source_url=None,
    raw_rendered_text=None,
):
    return {
        "source": source,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "subscribers_total": subscribers_total,
        "subscribers_change": subscribers_change,
        "subscribers_change_percentage": subscribers_change_percentage,
        "views_total": views_total,
        "views_change": views_change,
        "views_change_percentage": views_change_percentage,
        "earnings_low": earnings_low,
        "earnings_high": earnings_high,
        "engagement": engagement,
        "upload_frequency": upload_frequency,
        "average_length": average_length,
        "source_url": source_url,
        "raw_rendered_text": raw_rendered_text or [],
    }


def print_normalized_profile(profile):
    print(
        "Normalized profile payload:",
        json.dumps(profile, ensure_ascii=False, sort_keys=True),
    )
