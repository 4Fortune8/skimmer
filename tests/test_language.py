from __future__ import annotations

import pandas as pd
import pytest

from scripts.RecomendationAnalysis import language_frames
from skimmer.domain import language


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en", "en"),
        ("en-GB", "en"),
        ("en_US", "en"),
        ("EN", "en"),
        ("es-419", "es"),
        ("hi", "hi"),
        # "no linguistic content" and friends are not evidence of a language.
        ("zxx", None),
        ("und", None),
        ("mul", None),
        ("", None),
        (None, None),
        (float("nan"), None),
    ],
)
def test_normalize_tag_reduces_to_primary_subtag(raw, expected):
    assert language.normalize_tag(raw) == expected


def test_is_english_accepts_regional_variants():
    assert language.is_english("en-GB")
    assert language.is_english("EN")
    assert not language.is_english("es-419")
    assert not language.is_english("zxx")


def test_resolve_prefers_metadata_over_detection():
    """Metadata must win: it is the only source that gets romanized titles right."""

    tag, _, source = language.resolve(
        ["hi", "hi", "hi"],
        texts=["Kabze - Bintu Pabra I Shiva Choudhary I Pellet Drum Productions"],
    )
    assert tag == "hi"
    assert source == language.SOURCE_AUDIO


def test_audio_language_outranks_title_language():
    """A Bollywood upload is Hindi audio under a transliterated English title.

    Pooling the two fields makes that a 50/50 tie, and a tie broken by a detector
    that reads romanized Hindi as English lets the whole category through.
    """

    tag, _, source = language.resolve(
        ["hi", "hi", "hi"],
        ["en", "en", "en"],
        ["Yeh Dil Deewana | Shah Rukh Khan | Sonu Nigam | Pardes"],
    )
    assert tag == "hi"
    assert source == language.SOURCE_AUDIO


def test_title_language_is_used_only_when_no_audio_language_exists():
    tag, _, source = language.resolve([], ["es", "es"], ["Some title"])
    assert tag == "es"
    assert source == language.SOURCE_TEXT


def test_any_non_english_tag_disqualifies_english():
    """Mixed {en, hi} channels are not clean English-audience sources."""

    tag, _, source = language.resolve(["en", "en", "en", "hi"], texts=["An English title"])
    assert tag == "hi"
    assert source == language.SOURCE_AUDIO

    # Even when only the weaker text field carries the contradiction.
    tag, _, source = language.resolve([], ["en", "en", "hi"], ["An English title"])
    assert tag == "hi"
    assert source == language.SOURCE_TEXT


def test_english_is_affirmed_only_when_nothing_contradicts_it():
    tag, confidence, source = language.resolve(["en", "en-GB", "en-US"], ["en"])
    assert tag == "en"
    assert confidence == 1.0
    assert source == language.SOURCE_AUDIO


def test_a_foreign_text_tag_overrides_an_all_english_audio_field():
    tag, _, source = language.resolve(["en", "en"], ["hi"], ["An English title"])
    assert tag == "hi"
    assert source == language.SOURCE_TEXT


def test_resolve_ignores_unusable_metadata_tags():
    """A channel tagged only 'zxx' has no usable metadata and must fall through."""

    tag, _, source = language.resolve(["zxx", None, ""], [], [])
    assert source == language.SOURCE_DETECTED
    assert tag == language.UNKNOWN


def test_resolve_returns_unknown_for_empty_input():
    assert language.resolve([], [], [])[0] == language.UNKNOWN


def test_non_english_label_is_deterministic_regardless_of_input_order():
    assert language.resolve(["es", "de"], [], [])[0] == language.resolve(["de", "es"], [], [])[0]


def test_stopwords_are_empty_for_unsupported_language():
    assert language.stopwords("zxx") == frozenset()
    assert "the" in language.stopwords("en")


def _frame(rows):
    return pd.DataFrame(
        rows,
        columns=["channel_id", "title", "default_audio_language", "default_language"],
    )


def test_channel_language_resolves_from_metadata_without_a_model():
    """Metadata-only frames must not need the fastText model at all."""

    frame = _frame(
        [
            ("c1", "Some English Title", "en-GB", None),
            ("c1", "Another English Title", None, "en"),
            ("c2", "Kabze - Bintu Pabra I Shiva Choudhary", "hi", None),
            ("c2", "Another romanized title", None, None),
        ]
    )
    resolved = language_frames.resolve_channel_languages(frame, use_detector=False)
    languages = dict(zip(resolved["channel_id"], resolved["channel_language"]))
    assert languages == {"c1": "en", "c2": "hi"}
    assert set(resolved["language_source"]) == {language.SOURCE_AUDIO}


def test_filter_english_keeps_english_channels_and_drops_others():
    frame = _frame(
        [
            ("c1", "English title one", "en", None),
            ("c1", "English title two", "en", None),
            ("c2", "Titulo en espanol", "es", None),
            ("c3", "Untagged title", None, None),
        ]
    )
    annotated = language_frames.annotate(frame, use_detector=False)
    kept = language_frames.filter_english(annotated)
    assert set(kept["channel_id"]) == {"c1", "c3"}, "unknown channels are kept by default"

    strict = language_frames.filter_english(annotated, keep_unknown=False)
    assert set(strict["channel_id"]) == {"c1"}

def test_model_path_prefers_an_existing_model_over_a_layout_guess(tmp_path, monkeypatch):
    """An installed copy lives in site-packages, where no repo 'data/' exists."""

    monkeypatch.delenv("SKIMMER_LID_MODEL_PATH", raising=False)
    repo_model = tmp_path / "repo" / "data" / "models" / language.MODEL_FILENAME
    cache_model = tmp_path / "cache" / language.MODEL_FILENAME
    monkeypatch.setattr(language, "_REPO_MODEL_PATH", repo_model)
    monkeypatch.setattr(language, "_CACHE_MODEL_PATH", cache_model)

    # Nothing downloaded yet, and no repo layout: fall back to the user cache.
    assert language.model_path() == cache_model

    cache_model.parent.mkdir(parents=True)
    cache_model.write_bytes(b"model")
    assert language.model_path() == cache_model

    # A checkout that already holds the model keeps using it.
    repo_model.parent.mkdir(parents=True)
    repo_model.write_bytes(b"model")
    assert language.model_path() == repo_model


def test_model_path_honours_the_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIMMER_LID_MODEL_PATH", str(tmp_path / "custom.ftz"))
    assert language.model_path() == tmp_path / "custom.ftz"


def test_missing_model_names_the_command_that_fixes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIMMER_LID_MODEL_PATH", str(tmp_path / "absent.ftz"))
    language.load_detector.cache_clear()
    try:
        with pytest.raises(language.LanguageModelMissing, match="make lid-model"):
            language.load_detector()
    finally:
        language.load_detector.cache_clear()


def test_normalize_tag_handles_pandas_missing_values():
    """A 'string' dtype column holds pd.NA, which refuses to compare with itself."""

    assert language.normalize_tag(pd.NA) is None
    assert language.normalize_tag(pd.NaT) is None
    assert not language.is_english(pd.NA)


@pytest.mark.parametrize("dtype", ["string", "object"])
def test_filter_english_survives_either_column_dtype(dtype):
    """Parquet round-trips restore 'string' dtype, so both paths must work."""

    frame = pd.DataFrame(
        {
            "channel_id": ["a", "b", "c"],
            "title": ["one", "two", "three"],
            "channel_language": pd.array(["en", "es", None], dtype="string").astype(dtype),
            "language_confidence": [1.0, 1.0, 0.0],
            "language_source": ["metadata"] * 3,
        }
    )
    kept = language_frames.filter_english(frame)
    assert kept["channel_id"].tolist() == ["a", "c"]
    assert language_frames.filter_english(frame, keep_unknown=False)["channel_id"].tolist() == ["a"]


def test_annotate_reads_string_dtype_metadata_columns():
    frame = pd.DataFrame(
        {
            "channel_id": ["a", "a", "b"],
            "title": ["x", "y", "z"],
            "default_audio_language": pd.array(["en-GB", None, "es"], dtype="string"),
            "default_language": pd.array([None, None, None], dtype="string"),
        }
    )
    assert language_frames.annotate(frame, use_detector=False)["channel_language"].tolist() == [
        "en",
        "en",
        "es",
    ]


def test_channel_language_uses_audio_over_title_metadata():
    """The end-to-end shape of the Bollywood case, through the frame adapter."""

    frame = pd.DataFrame(
        [
            ("c1", "Yeh Dil Deewana | Shah Rukh Khan | Sonu Nigam", "hi", "en"),
            ("c1", "Toh Phir Aao | Emraan Hashmi Song | Pritam", "hi", "en"),
            ("c2", "Why America Cannot Get Enough of Haaland", "en", "en"),
        ],
        columns=["channel_id", "title", "default_audio_language", "default_language"],
    )
    kept = language_frames.filter_english(
        language_frames.annotate(frame, use_detector=False)
    )
    assert set(kept["channel_id"]) == {"c2"}


def test_channel_language_falls_back_to_title_language(recwarn):
    frame = pd.DataFrame(
        [("c1", "Algun titulo", None, "es"), ("c1", "Otro titulo", None, "es")],
        columns=["channel_id", "title", "default_audio_language", "default_language"],
    )
    resolved = language_frames.resolve_channel_languages(frame, use_detector=False)
    assert resolved["channel_language"].tolist() == ["es"]


def test_region_subtag_separates_western_english():
    """en-IN is English, but it is Indian-market English."""

    assert language.is_english("en-IN")
    assert not language.is_western_english("en-IN")
    assert language.is_western_english("en-GB")
    assert language.is_western_english("en")
    assert language.region_of("en-IN") == "IN"
    assert language.region_of("en") is None


def test_non_western_english_tag_disqualifies_a_channel():
    tag, _, source = language.resolve(["en-IN", "en-IN"], texts=["Some title"])
    assert source == language.SOURCE_REGION
    assert not language.is_western_english(tag)


def test_foreign_script_overrides_an_english_metadata_claim():
    """A Devanagari title is objective evidence a typed 'en' cannot outrank."""

    titles = ["कोई जाए तो ले आए hindi song trending"] * 3 + ["a normal english title"]
    tag, _, source = language.resolve(["en"] * 4, texts=titles)
    assert source == language.SOURCE_SCRIPT
    assert not language.is_western_english(tag)


def test_emoji_and_maths_do_not_count_as_foreign_script():
    assert not language.carries_foreign_script("FIFA Drops Hammer on Argentina 😱🇺🇸💔")
    assert not language.carries_foreign_script("🔥🔥🔥")
    assert language.foreign_script_chars("Why America Cannot Get Enough of Haaland") == 0
    assert language.carries_foreign_script("ATARASHII GAKKO! - 新しい学校のリーダーズ")


def test_romanized_markers_catch_what_metadata_and_script_cannot():
    """The Bollywood case: ASCII titles, uploader-set defaultAudioLanguage=en."""

    titles = [
        "Banjaara (Full Audio) | Ek Villain | Sidharth Malhotra | Shraddha Kapoor",
        "Le Main Saiyaan Aa Gayi (Lofi) | Mohabbatein | Shah Rukh Khan",
        "Tum Hi Ho | Aashiqui 2 | Arijit Singh | Mithoon",
    ]
    assert language.foreign_script_chars(" ".join(titles)) == 0, "no script signal exists here"
    tag, _, source = language.resolve(["en"] * 3, texts=titles)
    assert source == language.SOURCE_MARKERS
    assert not language.is_western_english(tag)


def test_markers_do_not_fire_on_english_titles():
    titles = [
        "Why America Cannot Get Enough of Erling Haaland",
        "The Angry Birds Movie but there is zero birds",
        "How Did Ancient Women Handle Their First Period?",
    ]
    tag, _, source = language.resolve(["en"] * 3, texts=titles)
    assert tag == "en"
    assert source == language.SOURCE_AUDIO


def test_contradicted_channels_are_never_kept_as_unknown():
    """'No evidence' and 'evidence against' must not be treated the same."""

    frame = pd.DataFrame(
        [
            ("c1", "Banjaara | Ek Villain | Shraddha Kapoor | Arijit Singh", "en", "en"),
            ("c1", "Tum Hi Ho | Aashiqui 2 | Arijit Singh | Mithoon", "en", "en"),
            ("c2", "🔥🔥🔥", None, None),
        ],
        columns=["channel_id", "title", "default_audio_language", "default_language"],
    )
    annotated = language_frames.annotate(frame)
    kept = language_frames.filter_english(annotated, keep_unknown=True)
    assert set(kept["channel_id"]) == {"c2"}, "c1 is contradicted, c2 merely has no evidence"
