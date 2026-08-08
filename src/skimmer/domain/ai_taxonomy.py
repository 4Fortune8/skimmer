"""Topic classification for AI *impact* content.

The subject is how AI is changing work, creative careers, independent building,
public attitudes, and everyday practice -- not AI as a news beat. Model
releases, benchmark coverage and market/bubble commentary are deliberately
excluded: they decay in weeks, and a back catalogue of them never compounds.
See `docs/topic_targeted_discovery_plan.md`.

Design note. This differs structurally from `topic_taxonomy`, where the strong
terms are self-identifying ("ozempic" is about health no matter the context).
Here they are not: "job market", "illustrator" and "backlash" are only in scope
when AI is the causal variable. So classification is gated -- a title must
match AI_MARKER *and* a topic's strong terms. The gate is what makes broad
topic vocabulary safe to use.

Bare "AI" is matched case-sensitively. Lowercase "ai" is a common substring in
non-English titles and a given name; requiring capitals costs a little recall
on all-lowercase titles and removes a large class of false positives.
"""

from __future__ import annotations

import re

TOPICS = ("ai_labor", "ai_creative", "ai_solo_dev", "ai_sentiment", "ai_practical")

# Bump whenever term lists change; labels are stored per version so runs stay
# comparable and a re-tune never silently rewrites earlier evidence.
CLASSIFIER_VERSION = "ai-v1"

# Layer 1 is a sensor: wide net, low precision bar, tells us what exists and
# what is moving. Layer 2 is the instrument: these carry the impact argument
# and are held to a higher precision standard.
LAYER_ONE = frozenset({"ai_practical"})
LAYER_TWO = frozenset({"ai_labor", "ai_creative", "ai_solo_dev", "ai_sentiment"})


def _rx(pattern):
    return re.compile(pattern, re.IGNORECASE)


# Product names that are also ordinary words or given names are omitted on
# purpose: claude, gemini, llama, sora, grok and perplexity each match far more
# non-AI titles than AI ones. They are recoverable via the qualified forms.
AI_MARKER = _rx(
    r"\b(artificial intelligence|chat.?gpt|gpt-?[2345]|openai|anthropic|"
    r"claude (ai|[2345]|opus|sonnet|haiku)|gemini (ai|pro|[23])|llms?|"
    r"large language model|generative ai|gen.?ai|machine learning|"
    r"neural net\w*|deep learning|midjourney|dall.?e|stable diffusion|"
    r"copilot|deepseek|runway ml|elevenlabs|deepfakes?|"
    r"ai.(generated|written|made|art|model|tool|agent|assistant|video|image)|"
    r"(text|image|voice|video).to.(image|video|speech|text)|"
    r"agentic|prompt engineer\w*|vibe.cod\w*)\b"
)

# Case-sensitive: "AI" as an acronym, not "ai" as a substring.
AI_MARKER_ACRONYM = re.compile(r"\bA\.?I\.?\b")


def has_ai_marker(title):
    """Whether the title is about AI at all. Necessary for every label."""
    return bool(AI_MARKER.search(title) or AI_MARKER_ACRONYM.search(title))


# Genre noise: titles borrowing the vocabulary for something else entirely.
# Checked first and short-circuits.
EXCLUSION_PATTERN = _rx(
    r"\b(lo.?fi|ambien(ce|t)|binaural|sleep music|relaxing music|calming music|"
    r"phonk|playlist|remix|slowed|reverb|asmr|instrumental|"
    # "music for vibe coding" is a lofi track, not a story about building.
    r"music (for|to)|beats to|"
    r"full movie|full episode|official (video|audio|trailer)|"
    r"lyrics?|music video|"
    # Game AI is a different meaning of the word and is heavily represented.
    r"enemy ai|game ai|ai (bot|opponent|difficulty)|npc|"
    r"fifa|madden|nba 2k|chess engine|stockfish)\b"
)

# Out of scope by editorial decision rather than by noise: fast-decaying news
# and finance commentary. Dropping these is what makes the remainder
# longitudinal, so the exclusion is a feature, not a filter of last resort.
OUT_OF_SCOPE = _rx(
    r"\b(stock|shares?|nasdaq|s&p|earnings|ipo|valuation|market cap|"
    r"invest(ing|ment|ors?)?|portfolio|diversif\w*|trading|"
    r"bubble|crash|recession|buy now|price target|bull\w*|bear\w*|trillion|"
    r"benchmark\w*|mmlu|arc.?agi|leaderboard|"
    r"just (released|dropped|launched)|is here|first look|hands.on|"
    r"vs\.? (gpt|claude|gemini|chatgpt)|which ai is best|"
    # Model-release roundups: the fastest-decaying format there is.
    r"(gpt|claude|gemini|flux|sora) ?[0-9]+.{0,30}(new|breakthrough|update)|"
    r"weekly ai|ai news|this week in ai)\b"
)

# Monetisation content. Excluded from ai_labor because "earn $5k/month with AI"
# is not a claim about the labour market, but left available to ai_practical,
# where what people actually attempt with the tools is legitimate signal.
HUSTLE_PATTERN = _rx(
    r"\b(side hustles?|make money|making money|earn (\$|₹|money)|passive income|"
    r"\$\d+[kK]?/?(month|day|week)|lakh|dropship\w*|affiliate)\b"
)

TOPIC_EXCLUSIONS = {
    # "AI took my job" is also a gaming/meme title format, and generic career
    # advice borrows the whole vocabulary. Hustle content is handled by
    # HUSTLE_PATTERN, which is wired in for this topic only.
    "ai_labor": _rx(r"\b(in (roblox|minecraft|fortnite|gta)|speedrun|"
                    r"tycoon|simulator)\b"),
    # Art *tutorials* using AI belong to ai_practical; this topic is about the
    # career, not the technique.
    "ai_creative": _rx(r"\b(speed ?paint|drawing tutorial|how to draw)\b"),
}

# Topics for which monetisation content is out of scope.
HUSTLE_EXCLUDED = frozenset({"ai_labor", "ai_solo_dev"})

STRONG_PATTERNS = {
    # Employment effects. The AI gate carries the topicality, so the terms can
    # be ordinary labour-market vocabulary without drowning in noise.
    "ai_labor": _rx(
        r"\b(jobs?|employment|unemploy\w*|layoffs?|laid off|fired|hiring|"
        r"job market|labou?r market|workforce|white.collar|blue.collar|"
        r"wages?|salar(y|ies)|career\w*|profession|entry.level|"
        r"freelanc\w*|gig (work|economy)|outsourc\w*|offshor\w*|"
        r"replace\w* (workers?|humans?|people|staff)|"
        r"future of work|job security|redundan\w*|displac\w*)\b"
    ),
    # Creative work as a livelihood: the people, not the output.
    "ai_creative": _rx(
        r"\b(artists?|illustrat\w*|graphic design\w*|concept art\w*|"
        r"animators?|animation industry|voice actors?|voice acting|"
        r"musicians?|songwriters?|composers?|"
        r"writers?|authors?|novelists?|screenwriters?|journalis\w*|"
        r"photographers?|filmmakers?|vfx|storyboard\w*|"
        r"commissions?|art portfolio|art theft|stole\w* (art|style)|"
        r"creative (industry|work|career)|copyright|royalt\w*)\b"
    ),
    # Independent building: can one person now ship what took a team?
    "ai_solo_dev": _rx(
        r"\b(solo (dev\w*|founder|builder|entrepreneur)|"
        r"indie (dev\w*|hacker|maker|game)|one.(person|man) (team|company|startup|business)|"
        r"vibe.cod\w*|side project|micro.?saas|bootstrapp\w*|no.?code|low.?code|"
        r"built (an?|my) (app|saas|startup|game|site|tool)|"
        r"shipp?(ed|ing) (faster|in a|my)|"
        # Bare "developers"/"programmers" matched any story mentioning the
        # profession ("developers sign petition"), which is sentiment, not
        # independent building. Career context is now required.
        r"(junior|senior) (dev\w*|engineers?|programmers?)|"
        r"(software engineer\w*|developers?|programmers?) "
        r"(jobs?|careers?|hiring|market|salar\w*|obsolete|replaced)|"
        r"replac\w* (software engineer\w*|developers?|programmers?|coders?)|"
        r"coding (jobs?|career))\b"
    ),
    # Attitudes and adoption. Read the caveat in the module docs: this topic is
    # most defensible as an adoption-versus-attitude gap, not as a claim about
    # what "the public" thinks.
    "ai_sentiment": _rx(
        r"\b(backlash|anti.?ai|hate ai|ai slop|boycott|protest\w*|petition|"
        r"public opinion|survey|poll(ed|ing)?|sentiment|"
        r"trust\w* ai|distrust|scared of|afraid of|fear of|"
        # Bare "adoption" matched vendor and product-launch copy ("simplifies
        # X for mass adoption"); public-attitude context is now required.
        r"adoption (rate|surge|divide|gap)|public adoption|"
        r"cultural divide|divid(e|ed|es) (over|on|the public)|"
        r"resistance|pushback|controvers\w*|outrage|"
        r"people (hate|love|fear|think)|why (everyone|people) (hates?|loves?))\b"
    ),
    # LAYER 1 SENSOR. Precision is knowingly lower here: the job is to notice
    # what tools and practices are entering the landscape, and a tool roundup is
    # a valid signal of that even though it is not content worth making.
    "ai_practical": _rx(
        r"\b(how to use|how i use|workflow\w*|tutorial|guide|"
        r"productivity|automat(e|ing) (my|your)|use cases?|"
        r"prompts?|prompting|"
        r"accessib\w*|disabilit\w*|dyslexi\w*|blind|deaf|neurodiver\w*|"
        r"for (students|teachers|writers|beginners|small business)|"
        r"save (time|hours)|replaced my|instead of|"
        # Bare "hack" matched AI-security reporting ("AI model goes rogue,
        # hacks 3 company systems"), which is a different story entirely.
        r"ai tools?|best ai|free ai|tips|tricks|"
        r"(life|productivity|workflow) hacks?)\b"
    ),
}

# Domain-adjacent but overloaded. Raises confidence, never creates a label.
WEAK_PATTERNS = {
    "ai_labor": _rx(
        r"\b(work\w*|economy|income|money|skills?|resume|interview|"
        r"industry|company|companies|employer|boss|manager)\b"
    ),
    "ai_creative": _rx(
        r"\b(art|creative|design|music|writing|film|drawing|painting|"
        r"studio|client|craft|style)\b"
    ),
    "ai_solo_dev": _rx(
        r"\b(code|coding|app|saas|startup|build\w*|ship\w*|launch\w*|"
        r"github|repo|stack|deploy\w*|engineer\w*)\b"
    ),
    "ai_sentiment": _rx(
        r"\b(people|public|users?|community|reaction|opinion|debate|"
        r"ethics|ethical|consent|regulation)\b"
    ),
    "ai_practical": _rx(
        r"\b(easy|simple|fast|free|beginner|step|learn\w*|help\w*|"
        r"better|actually|real|daily)\b"
    ),
}

# Categories where the vocabulary tends to be literal: Education, Science &
# Tech, Howto & Style, Nonprofits & Activism, News & Politics.
SUPPORTING_CATEGORIES = frozenset({"27", "28", "26", "29", "25"})
# Categories where it tends to be noise: Music, Gaming, Sports, Film, Autos,
# Comedy, Pets, Travel.
NOISE_CATEGORIES = frozenset({"10", "20", "17", "1", "2", "23", "15", "19"})

STRONG_WEIGHT = 2
WEAK_WEIGHT = 1
CATEGORY_BONUS = 1
CATEGORY_PENALTY = 1
MIN_CONFIDENCE = 2


def classify_title(title, category_id=None):
    """Return {topic: confidence} for a video title.

    An AI marker and a strong topic term are both necessary; weak terms and
    category only adjust confidence. Confidence below MIN_CONFIDENCE is
    dropped, so a strong hit in a noise category needs weak-term corroboration
    to survive.
    """
    if not title:
        return {}
    if EXCLUSION_PATTERN.search(title):
        return {}
    if OUT_OF_SCOPE.search(title):
        return {}
    if not has_ai_marker(title):
        return {}

    category = str(category_id) if category_id is not None else None
    is_hustle = bool(HUSTLE_PATTERN.search(title))
    labels = {}
    for topic in TOPICS:
        if not STRONG_PATTERNS[topic].search(title):
            continue
        if is_hustle and topic in HUSTLE_EXCLUDED:
            continue
        topic_exclusion = TOPIC_EXCLUSIONS.get(topic)
        if topic_exclusion is not None and topic_exclusion.search(title):
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
