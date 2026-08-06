"""Tests for persistent title-term exclusion rules."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.RecomendationAnalysis import exclusions  # noqa: E402


@pytest.fixture
def frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"video_id": "a", "title": "Jesus Says: A God Message For You Today", "channel_name": "Divine Words"},
            {"video_id": "b", "title": "Who was Jesus, historically?", "channel_name": "History Lab"},
            {"video_id": "c", "title": "God's Message of hope", "channel_name": "Divine Words"},
            {"video_id": "d", "title": "FIFA 26 gameplay reveal", "channel_name": "Pitch Talk"},
            {"video_id": "e", "title": "The Fifations are a made up band", "channel_name": "Music Lab"},
            {"video_id": "f", "title": "DUNE PART THREE | Official Trailer", "channel_name": "Studio"},
            {"video_id": "g", "title": "A British guy tries American snacks", "channel_name": "Snack Time"},
            {"video_id": "h", "title": "A British guy tries Japanese snacks", "channel_name": "Snack Time"},
            {"video_id": "i", "title": None, "channel_name": "Broken Metadata"},
        ]
    )


@pytest.fixture
def stem_frame() -> pd.DataFrame:
    """Real reaction-template spellings, which vary derivationally, not by plural."""

    return pd.DataFrame(
        [
            {"video_id": "p", "title": "British Family Reacts to 15 Things Only Americans Do"},
            {"video_id": "q", "title": "Europeans Couldn't Believe How America Celebrates the 4th of July"},
            {"video_id": "r", "title": "British Family Reacts to Europeans WEREN'T READY for July 4th in America!"},
            {"video_id": "s", "title": "The PanAmerican Highway, end to end"},
            {"video_id": "t", "title": "I tried a Japanese convenience store breakfast"},
        ]
    )


def _ids(df: pd.DataFrame) -> list[str]:
    return sorted(df["video_id"].tolist())


def test_normalize_text_folds_case_and_punctuation():
    assert exclusions.normalize_text("DUNE PART THREE | Official Trailer") == "dune part three official trailer"
    assert exclusions.normalize_text("FIFA 26") == "fifa 26"
    assert exclusions.normalize_text(None) == ""
    assert exclusions.normalize_text(float("nan")) == ""


def test_possessives_fold_to_the_bare_noun():
    """The upload farms mix "God Message Today" and "God's Message" in one corpus.

    Keeping the ``s`` (``gods message``) or splitting it off (``god s message``)
    would make the phrase rule match one spelling and miss the other.
    """

    assert exclusions.normalize_text("God's Message") == "god message"
    assert exclusions.normalize_text("Gods Message") == "gods message"


def test_remaining_apostrophes_are_deleted_not_split():
    assert exclusions.normalize_text("Don't Look Up") == "dont look up"


def test_phrase_rule_matches_both_spellings_of_a_possessive():
    frame = pd.DataFrame(
        [
            {"video_id": "a", "title": "God Message Today ~ Gods Message Now"},
            {"video_id": "b", "title": "URGENT! Jesus Says: I Need to Speak | God's Message"},
        ]
    )
    rule = exclusions.make_rule("god message")
    assert _ids(frame[exclusions.rule_mask(frame, rule)]) == ["a", "b"]


def test_single_term_rule_is_a_plain_ban(frame):
    rule = exclusions.make_rule("FIFA")
    assert _ids(frame[exclusions.rule_mask(frame, rule)]) == ["d"]


def test_terms_match_on_word_boundaries(frame):
    """'fifa' must not fire on 'Fifations'."""

    rule = exclusions.make_rule("fifa")
    matched = frame[exclusions.rule_mask(frame, rule)]
    assert "e" not in matched["video_id"].tolist()


def test_multi_term_rule_requires_all_terms(frame):
    """The devotional case: 'Jesus' alone is legitimate, the pair is not."""

    rule = exclusions.make_rule(["Jesus", "God Message"])
    assert _ids(frame[exclusions.rule_mask(frame, rule)]) == ["a"]


def test_word_pair_anywhere_in_title(frame):
    rule = exclusions.make_rule(["British", "American"])
    assert _ids(frame[exclusions.rule_mask(frame, rule)]) == ["g"]


def test_multi_word_term_is_a_contiguous_phrase(frame):
    """'official trailer' as one term must not match the words scattered apart."""

    scattered = pd.DataFrame([{"title": "Official statement about the trailer", "video_id": "z"}])
    assert not exclusions.rule_mask(scattered, exclusions.make_rule("official trailer")).any()
    assert _ids(frame[exclusions.rule_mask(frame, exclusions.make_rule("official trailer"))]) == ["f"]


def test_missing_titles_never_match(frame):
    rule = exclusions.make_rule("jesus")
    assert "i" not in frame[exclusions.rule_mask(frame, rule)]["video_id"].tolist()


def test_rules_over_multiple_fields_pool_the_text(frame):
    rule = exclusions.make_rule(["divine words", "hope"], fields=["title", "channel_name"])
    assert _ids(frame[exclusions.rule_mask(frame, rule)]) == ["c"]


def test_mask_is_the_union_of_rules(frame):
    rules = [exclusions.make_rule("fifa"), exclusions.make_rule("official trailer")]
    assert _ids(frame[exclusions.mask(frame, rules)]) == ["d", "f"]


def test_apply_removes_matches_and_leaves_the_rest(frame):
    kept = exclusions.apply(frame, [exclusions.make_rule("fifa")])
    assert "d" not in kept["video_id"].tolist()
    assert len(kept) == len(frame) - 1


def test_apply_with_no_rules_is_a_noop(frame):
    assert exclusions.apply(frame, []).equals(frame)
    assert exclusions.apply(frame, None).equals(frame)


def test_empty_frame_is_handled():
    empty = pd.DataFrame(columns=["video_id", "title"])
    assert exclusions.apply(empty, [exclusions.make_rule("fifa")]).empty
    assert not exclusions.mask(empty, [exclusions.make_rule("fifa")]).any()


def test_stem_term_matches_the_whole_word_family(stem_frame):
    """The reaction template varies derivationally, so one stem must span it."""

    europe = exclusions.make_rule(["america*", "europe*"])
    assert _ids(stem_frame[exclusions.rule_mask(stem_frame, europe)]) == ["q", "r"]

    # "Americans" against a bare 'american' term is the miss that started this.
    british = exclusions.make_rule(["america*", "brit*"])
    assert _ids(stem_frame[exclusions.rule_mask(stem_frame, british)]) == ["p", "r"]

    # Neither rule reaches the two titles that are not the template.
    assert _ids(stem_frame[exclusions.mask(stem_frame, [europe, british])]) == ["p", "q", "r"]


def test_stem_term_replaces_three_exact_rules(stem_frame):
    """What ['europeans','americans'], ['european','american'] and ['european','america'] did."""

    exact_rules = [
        exclusions.make_rule(["europeans", "americans"]),
        exclusions.make_rule(["european", "american"]),
        exclusions.make_rule(["european", "america"]),
    ]
    stemmed = [exclusions.make_rule(["america*", "europe*"])]
    exact_hits = set(stem_frame.loc[exclusions.mask(stem_frame, exact_rules), "video_id"])
    stem_hits = set(stem_frame.loc[exclusions.mask(stem_frame, stemmed), "video_id"])
    assert exact_hits < stem_hits
    # "Europeans ... in America" mixes a plural with a bare noun, so no single
    # exact rule covers it; this is the title that motivated stemming.
    assert "r" in stem_hits and "r" not in exact_hits


def test_stem_still_has_to_start_a_word(stem_frame):
    """A stem is a prefix of a word, not a substring of one."""

    rule = exclusions.make_rule("america*")
    matched = _ids(stem_frame[exclusions.rule_mask(stem_frame, rule)])
    assert "s" not in matched  # "PanAmerican" — the stem does not start the word
    assert "p" in matched


def test_stemming_is_opt_in_so_exact_terms_stay_exact(stem_frame):
    """Without the marker nothing broadens; this is why god* cannot reach Godzilla."""

    assert not exclusions.rule_mask(stem_frame, exclusions.make_rule("american")).any()
    godzilla = pd.DataFrame([{"video_id": "z", "title": "Godzilla vs the world"}])
    assert not exclusions.rule_mask(godzilla, exclusions.make_rule("god")).any()
    assert exclusions.rule_mask(godzilla, exclusions.make_rule("god*")).all()


def test_stem_marker_survives_normalisation_into_the_saved_rule():
    """Normalising first would delete the '*' as punctuation and silently un-stem the rule."""

    rule = exclusions.make_rule(["America*", "  brit* "])
    assert rule["terms"] == ["america*", "brit*"]
    assert exclusions.normalize_term("america*") == "america"
    assert exclusions.parse_term("america*") == ("america", True)
    assert exclusions.parse_term("america") == ("america", False)


def test_stem_applies_to_the_last_word_of_a_phrase():
    frame = pd.DataFrame(
        [
            {"video_id": "a", "title": "DUNE | Official Trailers, all of them"},
            {"video_id": "b", "title": "An official statement about trailers"},
        ]
    )
    rule = exclusions.make_rule("official trailer*")
    assert _ids(frame[exclusions.rule_mask(frame, rule)]) == ["a"]


def test_make_rule_rejects_a_stem_too_short_to_be_selective():
    with pytest.raises(ValueError):
        exclusions.make_rule("a*")
    with pytest.raises(ValueError):
        exclusions.make_rule(["america*", "b*"])


def test_load_skips_an_unusable_stem_without_dropping_the_file(tmp_path):
    path = tmp_path / "title_exclusions.json"
    path.write_text(
        json.dumps({"version": 1, "rules": [{"terms": ["a*"]}, {"terms": ["america*"]}]}),
        encoding="utf-8",
    )
    assert [rule["terms"] for rule in exclusions.load(path)] == [["america*"]]


def test_make_rule_normalizes_and_dedupes_terms():
    rule = exclusions.make_rule(["FIFA", "fifa!", "Official  Trailer"])
    assert rule["terms"] == ["fifa", "official trailer"]
    assert rule["scope"] == "leads"
    assert rule["fields"] == ["title"]


def test_make_rule_rejects_terms_that_normalize_away():
    with pytest.raises(ValueError):
        exclusions.make_rule(["!!!", "  "])


def test_make_rule_rejects_unknown_scope():
    with pytest.raises(ValueError):
        exclusions.make_rule("fifa", scope="everywhere")


def test_rule_id_is_order_insensitive():
    left = exclusions.make_rule(["jesus", "god message"])
    right = exclusions.make_rule(["god message", "jesus"])
    assert exclusions.rule_id(left) == exclusions.rule_id(right)


def test_rule_id_separates_scopes():
    assert exclusions.rule_id(exclusions.make_rule("fifa")) != exclusions.rule_id(
        exclusions.make_rule("fifa", scope="corpus")
    )


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "title_exclusions.json"
    rules = [
        exclusions.make_rule(["Jesus", "God Message"], note="devotional upload farms"),
        exclusions.make_rule("FIFA", scope="corpus"),
    ]
    exclusions.save(rules, path)
    loaded = exclusions.load(path)
    assert [exclusions.rule_id(rule) for rule in loaded] == [exclusions.rule_id(rule) for rule in rules]
    assert loaded[0]["note"] == "devotional upload farms"


def test_load_missing_file_returns_empty(tmp_path):
    assert exclusions.load(tmp_path / "absent.json") == []


def test_load_tolerates_a_malformed_file_without_destroying_it(tmp_path):
    """A hand-edit typo must not block a run, and must not be silently rewritten."""

    path = tmp_path / "title_exclusions.json"
    path.write_text("{not json", encoding="utf-8")
    assert exclusions.load(path) == []
    assert path.read_text(encoding="utf-8") == "{not json"


def test_load_skips_invalid_entries_but_keeps_valid_ones(tmp_path):
    path = tmp_path / "title_exclusions.json"
    path.write_text(
        json.dumps({"version": 1, "rules": [{"terms": []}, "fifa", {"terms": ["official trailer"]}]}),
        encoding="utf-8",
    )
    loaded = exclusions.load(path)
    assert [rule["terms"] for rule in loaded] == [["fifa"], ["official trailer"]]


def test_load_accepts_a_bare_list(tmp_path):
    path = tmp_path / "title_exclusions.json"
    path.write_text(json.dumps([{"terms": ["fifa"]}]), encoding="utf-8")
    assert [rule["terms"] for rule in exclusions.load(path)] == [["fifa"]]


def test_load_dedupes_identical_rules(tmp_path):
    path = tmp_path / "title_exclusions.json"
    path.write_text(
        json.dumps({"rules": [{"terms": ["a", "b"]}, {"terms": ["b", "a"]}]}),
        encoding="utf-8",
    )
    assert len(exclusions.load(path)) == 1


def test_add_is_idempotent():
    rules, added = exclusions.add("FIFA", rules=[])
    assert added and len(rules) == 1
    rules, added_again = exclusions.add("fifa", rules=rules)
    assert not added_again and len(rules) == 1


def test_remove_by_identity():
    rules, _ = exclusions.add("FIFA", rules=[])
    rules, removed = exclusions.remove("fifa", rules=rules)
    assert removed and rules == []


def test_remove_reports_when_nothing_matched():
    rules, removed = exclusions.remove("fifa", rules=[])
    assert not removed and rules == []


def test_for_scope_partitions_rules():
    rules = [exclusions.make_rule("fifa"), exclusions.make_rule("jesus", scope="corpus")]
    assert [rule["terms"] for rule in exclusions.for_scope(rules, "leads")] == [["fifa"]]
    assert [rule["terms"] for rule in exclusions.for_scope(rules, "corpus")] == [["jesus"]]
    assert exclusions.for_scope(None, "leads") == []


def test_canonical_drops_commentary_so_cache_keys_are_stable():
    """Editing a note must not invalidate an expensive checkpoint."""

    first = exclusions.make_rule("fifa", note="not my niche", added="2026-01-01")
    second = exclusions.make_rule("fifa", note="still not my niche", added="2026-08-06")
    assert exclusions.canonical([first]) == exclusions.canonical([second])


def test_canonical_is_order_insensitive_across_rules():
    left = [exclusions.make_rule("fifa"), exclusions.make_rule("official trailer")]
    right = [exclusions.make_rule("official trailer"), exclusions.make_rule("fifa")]
    assert exclusions.canonical(left) == exclusions.canonical(right)


def test_canonical_output_is_still_a_usable_rule_list(frame):
    rules = exclusions.canonical([exclusions.make_rule("fifa", note="x")])
    assert _ids(frame[exclusions.mask(frame, rules)]) == ["d"]


def test_canonical_distinguishes_scope_and_terms():
    assert exclusions.canonical([exclusions.make_rule("fifa")]) != exclusions.canonical(
        [exclusions.make_rule("fifa", scope="corpus")]
    )
    assert exclusions.canonical(None) == []


def test_apply_to_results_filters_every_frame_and_keeps_attrs(frame):
    results = {"engagement": frame.copy(), "velocity": frame.copy()}
    results["engagement"].attrs["algorithm_failures"] = {"velocity": "boom"}
    filtered = exclusions.apply_to_results(results, [exclusions.make_rule("fifa")])
    assert all("d" not in value["video_id"].tolist() for value in filtered.values())
    assert filtered["engagement"].attrs["algorithm_failures"] == {"velocity": "boom"}


def test_summarize_reports_hits_and_examples(frame):
    rules = [exclusions.make_rule("fifa"), exclusions.make_rule("no such term here")]
    summary = exclusions.summarize(frame, rules)
    by_terms = summary.set_index("terms")
    assert by_terms.loc["fifa", "matched_rows"] == 1
    assert "FIFA 26" in by_terms.loc["fifa", "examples"]
    assert by_terms.loc["no such term here", "matched_rows"] == 0


def test_summarize_with_no_rules_is_empty(frame):
    assert exclusions.summarize(frame, []).empty
