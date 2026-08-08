"""Topic classification for the evidence/health/behaviour/education domain.

Strategy 0 of `docs/topic_targeted_discovery_plan.md`: label the corpus already
in `data/skimmer.db` at zero API quota, to establish whether the domain is
represented at viable scale before spending any units on targeted discovery.

Design note. Naive term matching on titles has roughly 20-30% precision here,
because the corpus is dominated by music and entertainment and the domain
vocabulary is heavily overloaded:

    "study"     -> lofi study playlists, Bible study
    "school"    -> "old school" music mixes, back-to-school hauls
    "anxiety"   -> relaxing music for anxiety
    "evidence"  -> political news
    "diet"      -> Diet Coke
    "scientists"-> "Scientists CONFIRMED This Is Our Strongest Evidence of Alien Life!"

So classification is deliberately precision-first: a STRONG term is necessary,
WEAK terms and category only modulate confidence. Recall is knowingly
sacrificed. A count that is too low but trustworthy supports the go/no-go
decision; a count inflated by lofi playlists does not.
"""

from __future__ import annotations

import re

TOPICS = ("health", "behavior", "education", "meta_science")


def _rx(pattern):
    return re.compile(pattern, re.IGNORECASE)


# Titles borrowing domain vocabulary for an unrelated genre. Checked first and
# short-circuits: no label is assigned regardless of term matches.
EXCLUSION_PATTERN = _rx(
    r"\b(lo.?fi|ambien(ce|t)|binaural|sleep music|relaxing music|calming music|"
    r"jazz|piano|phonk|study with me|pomodoro|brown noise|white noise|"
    r"bible study|old school|back.to.school|school haul|study music|"
    r"deep focus|focus music|instrumental|playlist|remix|slowed|reverb|asmr)\b"
)

# Unambiguous domain vocabulary. Necessary for a label.
STRONG_PATTERNS = {
    "health": _rx(
        r"\b(ozempic|semaglutide|statins?|creatine|microbiome|gut health|"
        r"intermittent fasting|blood sugar|cholesterol|insulin resistance|"
        r"type 2 diabetes|obesity|metabolic|longevity|supplements?|multivitamin|"
        r"saturated fat|processed food|seed oils?|testosterone|calorie deficit|"
        r"blood pressure|antidepressant\w*|vaccin\w*)\b"
    ),
    "behavior": _rx(
        r"\b(psycholog(y|ist|ical)|neuroscien(ce|tist)|dopamine|cognitive bias|"
        r"confirmation bias|behaviou?ral econom\w*|attachment style|"
        r"procrastinat\w*|willpower|placebo effect|iq test|personality test|"
        r"myers.?briggs|big five|cognitive behaviou?ral)\b"
    ),
    "education": _rx(
        r"\b(student loans?|student debt|tuition|college degree|university degree|"
        r"higher education|standardi[sz]ed test\w*|common core|charter school\w*|"
        r"homeschool\w*|teacher shortage|school funding|college admissions?|"
        r"is college worth|credential\w*|bootcamp|college dropout)\b"
    ),
    "meta_science": _rx(
        r"\b(meta.?analys\w*|systematic review|peer.?review\w*|replication crisis|"
        r"clinical trial|randomi[sz]ed controlled|double.?blind|p.?hack\w*|"
        r"sample size|statistical significan\w*|retract(ed|ion)|preprint|"
        r"pre.?registered|effect size|confidence interval|junk science|"
        r"bad science|study found|new study)\b"
    ),
}

# Domain-adjacent but overloaded. Raises confidence, never creates a label.
WEAK_PATTERNS = {
    "health": _rx(
        r"\b(diet|nutrition|calorie|protein|vitamin|cancer|inflammation|fasting|"
        r"keto|health|doctor|medical|disease|weight loss)\b"
    ),
    "behavior": _rx(
        r"\b(habits?|motivation|anxiety|depression|therapy|adhd|autis\w*|"
        r"bias(es)?|brain|mental health|mindset)\b"
    ),
    "education": _rx(
        r"\b(college|university|school|teacher|classroom|curriculum|exam|gpa|"
        r"literacy|degree|tutor\w*)\b"
    ),
    "meta_science": _rx(
        r"\b(stud(y|ies)|research|scientists?|evidence|data|statistic\w*|"
        r"debunk\w*|myth|experiment)\b"
    ),
}

# Categories where domain vocabulary tends to be literal.
SUPPORTING_CATEGORIES = frozenset({"27", "28", "26", "29"})
# Categories where it tends to be noise (Music, Gaming, Sports, Film, Autos,
# Comedy, Pets, Travel).
NOISE_CATEGORIES = frozenset({"10", "20", "17", "1", "2", "23", "15", "19"})

STRONG_WEIGHT = 2
WEAK_WEIGHT = 1
CATEGORY_BONUS = 1
CATEGORY_PENALTY = 1
MIN_CONFIDENCE = 2


def classify_title(title, category_id=None):
    """Return {topic: confidence} for a video title.

    A STRONG match is necessary; weak terms and category adjust confidence.
    Confidence below MIN_CONFIDENCE is dropped, so a strong hit in a noise
    category needs weak-term corroboration to survive.
    """
    if not title:
        return {}
    if EXCLUSION_PATTERN.search(title):
        return {}

    category = str(category_id) if category_id is not None else None
    labels = {}
    for topic in TOPICS:
        if not STRONG_PATTERNS[topic].search(title):
            continue
        confidence = STRONG_WEIGHT
        if WEAK_PATTERNS[topic].search(title):
            confidence += WEAK_WEIGHT
        if category in SUPPORTING_CATEGORIES:
            confidence += CATEGORY_BONUS
        elif category in NOISE_CATEGORIES:
            confidence -= CATEGORY_PENALTY
        if confidence >= MIN_CONFIDENCE:
            labels[topic] = confidence
    return labels
