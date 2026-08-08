"""Precision guards for the AI impact taxonomy.

Each false-positive case here was found by sampling real corpus titles during
tuning; they are kept so a later widening of the term lists cannot silently
reintroduce the noise class they represent.
"""

from __future__ import annotations

import pytest

from skimmer.domain.ai_taxonomy import (
    CLASSIFIER_VERSION,
    LAYER_ONE,
    LAYER_TWO,
    TOPICS,
    classify_title,
    has_ai_marker,
)


def topics_for(title, category_id=None):
    return set(classify_title(title, category_id))


class TestAiGate:
    """An AI marker is necessary; topic vocabulary alone must never label."""

    @pytest.mark.parametrize(
        "title",
        [
            "The job market is brutal for new graduates right now",
            "How illustrators price their commissions",
            "Why public opinion turned on the housing market",
            "Solo founder builds a SaaS in 30 days",
        ],
    )
    def test_topic_terms_without_ai_marker_are_unlabelled(self, title):
        assert topics_for(title) == set()

    def test_marker_plus_topic_term_labels(self):
        assert "ai_labor" in topics_for("Will AI replace your job? Careers ranked")

    def test_lowercase_ai_substring_is_not_a_marker(self):
        # "ai" appears inside ordinary words and non-English titles constantly.
        assert not has_ai_marker("Naina aur uski kahani")
        assert not has_ai_marker("Chair repair tutorial")

    def test_acronym_is_matched_case_sensitively(self):
        assert has_ai_marker("AI and the future of work")

    def test_spelled_out_marker_matches(self):
        assert has_ai_marker("artificial intelligence in the classroom")


class TestOutOfScope:
    """Market, benchmark and release-news content is excluded by policy.

    These decay in weeks. Excluding them is what leaves a longitudinal
    remainder, so the rule is deliberate rather than a noise filter.
    """

    @pytest.mark.parametrize(
        "title",
        [
            "Nvidia Isn't Enough - Here's How To Build A Diversified AI Portfolio",
            "If the AI bubble bursts will it cause a recession?",
            "Robinhood Opens to AI Agents - trading and portfolio automation",
            "GPT-5 vs Claude: which AI is best?",
            "New AI model tops the MMLU leaderboard",
        ],
    )
    def test_market_and_benchmark_titles_are_dropped(self, title):
        assert topics_for(title) == set()


class TestGenreNoise:
    @pytest.mark.parametrize(
        "title",
        [
            "music for vibe coding at 3:24 am | nooise",
            "Hardest enemy AI in any souls game",
            "AI generated lofi beats to study to",
        ],
    )
    def test_genre_titles_are_dropped(self, title):
        assert topics_for(title) == set()


class TestHustleExclusion:
    """Monetisation content is not a labour-market observation.

    It stays available to ai_practical, where what people attempt with the
    tools is legitimate layer-1 signal.
    """

    def test_side_hustle_excluded_from_labor(self):
        title = "13 EASY Claude AI Side Hustles You Can Try If You Get Laid Off"
        assert "ai_labor" not in topics_for(title)

    def test_plural_hustles_are_caught(self):
        # The singular-only pattern let every plural through.
        assert "ai_labor" not in topics_for("8 Claude AI Side Hustles That Replace a Job")

    def test_hustle_still_allowed_in_practical(self):
        title = "The Best AI Side Hustle Ideas NO ONE Is Talking About"
        assert "ai_practical" in topics_for(title)


class TestNarrowedTerms:
    def test_bare_developers_mention_is_not_solo_dev(self):
        # "developers sign petition" is sentiment, not independent building.
        assert "ai_solo_dev" not in topics_for(
            "AI progressing at alarming rate, developers sign petition urging rules"
        )

    def test_developer_career_context_is_solo_dev(self):
        assert "ai_solo_dev" in topics_for("AI is replacing developers, apparently")

    def test_security_hack_is_not_practical(self):
        assert "ai_practical" not in topics_for(
            "Another AI model goes rogue, hacks 3 company systems"
        )

    def test_workflow_hack_is_practical(self):
        assert "ai_practical" in topics_for("AI workflow hacks that save time")

    def test_bare_adoption_is_not_sentiment(self):
        assert "ai_sentiment" not in topics_for(
            "SpaceX AI Simplifies Grok Build for Mass Adoption"
        )

    def test_adoption_divide_is_sentiment(self):
        assert "ai_sentiment" in topics_for("AI Adoption Sparks Cultural Divide")


class TestMultiLabel:
    def test_a_title_may_carry_several_topics(self):
        labels = classify_title(
            "AI copyright ruling leaves illustrators without work", category_id="25"
        )
        assert "ai_creative" in labels


class TestCategoryWeighting:
    def test_noise_category_needs_corroboration(self):
        # Strong term alone in a Music category falls below MIN_CONFIDENCE.
        strong_only = classify_title("AI and redundancy", category_id="10")
        assert "ai_labor" not in strong_only

    def test_supporting_category_lifts_confidence(self):
        labels = classify_title("AI and the future of work", category_id="25")
        assert labels["ai_labor"] > 2


class TestModuleShape:
    def test_layers_partition_topics(self):
        assert LAYER_ONE | LAYER_TWO == set(TOPICS)
        assert not (LAYER_ONE & LAYER_TWO)

    def test_version_is_declared(self):
        assert CLASSIFIER_VERSION == "ai-v1"

    def test_empty_title_is_safe(self):
        assert classify_title("") == {}
        assert classify_title(None) == {}
