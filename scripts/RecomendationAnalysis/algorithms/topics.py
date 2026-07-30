"""Theme and niche aggregation for outlier video leads."""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

try:
    from sklearn.feature_extraction.text import TfidfVectorizer

    HAS_SKLEARN = True
except ImportError:  # pragma: no cover - depends on optional environment
    TfidfVectorizer = None
    HAS_SKLEARN = False

try:
    from .. import data_access, metrics
except ImportError:  # pragma: no cover
    import data_access  # type: ignore
    import metrics  # type: ignore

logger = logging.getLogger(__name__)

ALGORITHM_NAME = "topics"

DEFAULT_PARAMS: dict[str, Any] = {
    "ngram_range": (1, 2),
    "min_lead_count": 3,
    "min_distinct_channels": 2,
    "max_features": 5_000,
    "background_smoothing": 1.0,
    "by_category": False,
    "use_sklearn": True,
    "score_top_terms": 5,
    "saturation_weight": 0.35,
    "fallback_min_views": 50_000,
    "fallback_views_per_sub_quantile": 0.99,
    "fallback_max_leads": 2_000,
}

YOUTUBE_CATEGORY_NAMES: dict[int, str] = {
    1: "Film & Animation",
    2: "Autos & Vehicles",
    10: "Music",
    15: "Pets & Animals",
    17: "Sports",
    19: "Travel & Events",
    20: "Gaming",
    22: "People & Blogs",
    23: "Comedy",
    24: "Entertainment",
    25: "News & Politics",
    26: "Howto & Style",
    27: "Education",
    28: "Science & Technology",
    29: "Nonprofits & Activism",
}

STOPWORDS = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "did",
    "do",
    "does",
    "doing",
    "don",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "s",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "t",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "video",
    "videos",
    "short",
    "shorts",
    "official",
    "full",
    "episode",
    "episodes",
    "part",
    "new",
    "vs",
    "ft",
    "feat",
    "featuring",
    "subscribe",
    "watch",
    "youtube",
    "channel",
    "live",
    "hd",
    "hq",
    "best",
    "top",
    "latest",
    "update",
    "updates",
    "viral",
    "trending",
    "compilation",
    "clips",
    "clip",
    "highlights",
    "highlight",
    "shortsvideo",
    "ytshorts",
    "reels",
    "tik_tok",
    "tiktok",
    "reaction",
    "reacts",
    "real",
    "must",
    "see",
    "daily",
    "today",
    "de",
    "del",
    "el",
    "la",
    "las",
    "los",
    "un",
    "una",
    "que",
    "por",
    "con",
    "para",
    "su",
    "en",
    "es",
    "lo",
    "se",
    "mejor",
    "completa",
    "pelicula",
    "peliculas",
    "espanol",
    "latino",
    "estreno",
    "isn",
    "failarmy",
    "footballreels",
    "2020",
    "2021",
    "2022",
    "2023",
    "2024",
    "2025",
    "2026",
}

_THEME_COLUMNS = [
    "term",
    "ngram_size",
    "lead_count",
    "lead_share",
    "background_share",
    "lift",
    "median_views",
    "median_views_per_sub",
    "distinct_channels",
    "example_titles",
    "example_video_ids",
]
_CATEGORY_THEME_COLUMNS = ["category_id", "category_name", *_THEME_COLUMNS]
_SATURATION_COLUMNS = [
    "category_id",
    "category_name",
    "distinct_channels",
    "outlier_channels",
    "total_videos",
    "outlier_videos",
    "outlier_rate",
    "median_outlier_subscribers",
    "saturation_score",
]
_SCORE_COLUMNS = [
    "video_id",
    "score",
    "reason",
    "title",
    "channel_name",
    "subscribers",
    "views",
    "matched_terms",
    "theme_lift",
    "category_name",
    "video_url",
    "channel_url",
]
_TOKEN_RE = re.compile(r"[^\w\s\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def extract_themes(
    leads: pd.DataFrame,
    background: pd.DataFrame | None = None,
    **params: Any,
) -> pd.DataFrame:
    """Return recurring title terms and phrases among outlier videos.

    Titles are normalized with lowercase whitespace tokenisation, punctuation and emoji removal,
    English/Youtube stopword filtering, and configurable n-grams. CJK text is handled without
    crashing, but language-specific CJK tokenisation is out of scope; contiguous CJK text remains
    a whitespace-delimited token. When a background corpus is supplied, document frequencies are
    computed only for candidate terms observed in leads, making the 258k-row corpus pass cheap.
    """

    options = _params(params)
    by_category = bool(options["by_category"])
    columns = _CATEGORY_THEME_COLUMNS if by_category else _THEME_COLUMNS
    lead_frame = _dedupe_videos(leads)
    if lead_frame.empty:
        return pd.DataFrame(columns=columns)

    if by_category:
        frames = []
        for category_id, group in lead_frame.groupby(_category_series(lead_frame), dropna=False):
            bg_group = None
            if background is not None and not background.empty:
                bg_categories = _category_series(background)
                category_mask = bg_categories.isna() if pd.isna(category_id) else bg_categories.eq(category_id)
                bg_group = background.loc[category_mask].copy()
            themes = _extract_theme_group(group, bg_group, options)
            if not themes.empty:
                themes.insert(0, "category_name", _category_name(category_id))
                themes.insert(0, "category_id", category_id if pd.notna(category_id) else np.nan)
                frames.append(themes)
        if not frames:
            return pd.DataFrame(columns=columns)
        return pd.concat(frames, ignore_index=True)[columns]

    return _extract_theme_group(lead_frame, background, options)[columns]


def niche_saturation(df: pd.DataFrame, leads: pd.DataFrame, **params: Any) -> pd.DataFrame:
    """Summarise category-level supply versus outlier concentration.

    ``saturation_score`` is ``outlier_rate / log1p(distinct_channels)``. Higher values indicate
    categories where a large share of videos are outliers while comparatively few channels compete,
    a practical signal for underserved niches where a new entrant may have room to win.
    """

    _ = _params(params)
    if df.empty:
        return pd.DataFrame(columns=_SATURATION_COLUMNS)

    corpus = df.copy()
    lead_frame = _attach_background_columns(_dedupe_videos(leads), corpus)
    corpus_category = _category_series(corpus)
    total = (
        corpus.assign(category_id=corpus_category)
        .groupby("category_id", dropna=False)
        .agg(
            distinct_channels=("channel_id", "nunique"),
            total_videos=("video_id", "nunique"),
        )
    )

    if lead_frame.empty:
        outliers = pd.DataFrame(
            columns=["outlier_channels", "outlier_videos", "median_outlier_subscribers"]
        )
    else:
        lead_category = _category_series(lead_frame)
        outliers = (
            lead_frame.assign(category_id=lead_category)
            .groupby("category_id", dropna=False)
            .agg(
                outlier_channels=("channel_id", "nunique"),
                outlier_videos=("video_id", "nunique"),
                median_outlier_subscribers=("subscribers", "median"),
            )
        )

    result = total.join(outliers, how="outer").reset_index()
    result["distinct_channels"] = result["distinct_channels"].fillna(0).astype(int)
    result["total_videos"] = result["total_videos"].fillna(0).astype(int)
    result["outlier_channels"] = result["outlier_channels"].fillna(0).astype(int)
    result["outlier_videos"] = result["outlier_videos"].fillna(0).astype(int)
    result["outlier_rate"] = metrics.safe_ratio(result["outlier_videos"], result["total_videos"], fill=0.0)
    denominator = np.log1p(result["distinct_channels"].clip(lower=1))
    result["saturation_score"] = metrics.safe_ratio(result["outlier_rate"], denominator, fill=0.0)
    result["category_name"] = result["category_id"].map(_category_name)
    result = result.replace([np.inf, -np.inf], 0)
    result = result.sort_values(
        ["saturation_score", "outlier_videos", "outlier_rate"], ascending=[False, False, False]
    )
    return result[_SATURATION_COLUMNS].reset_index(drop=True)


def score(df: pd.DataFrame, leads: pd.DataFrame | None = None, **params: Any) -> pd.DataFrame:
    """Score lead videos by recurring theme lift boosted by category undersaturation.

    If ``leads`` is omitted, a fallback lead set is derived from ``df`` using videos above the
    configured high ``views_per_sub`` quantile, minimum views, and maximum lead count. This keeps
    the function usable before sibling outlier algorithms are available while avoiding database IO.
    """

    options = _params(params)
    if df.empty:
        return pd.DataFrame(columns=_SCORE_COLUMNS)

    if leads is None:
        lead_frame = _fallback_leads(df, options)
        logger.info("Derived %d fallback topic leads from high views_per_sub.", len(lead_frame))
    else:
        lead_frame = _dedupe_videos(leads)
    lead_frame = _attach_background_columns(lead_frame, df)
    if lead_frame.empty:
        return pd.DataFrame(columns=_SCORE_COLUMNS)

    theme_params = dict(options)
    theme_params["by_category"] = False
    themes = extract_themes(lead_frame, background=df, **theme_params)
    saturation = niche_saturation(df, lead_frame, **options)
    saturation_map = saturation.set_index("category_id")["saturation_score"].to_dict()
    max_saturation = max([float(value) for value in saturation_map.values()] or [0.0])

    term_stats = {
        str(row.term): {
            "lift": float(row.lift) if pd.notna(row.lift) else 0.0,
            "lead_count": int(row.lead_count),
        }
        for row in themes.itertuples(index=False)
    }
    theme_terms = set(term_stats)

    rows: list[dict[str, Any]] = []
    for row in lead_frame.itertuples(index=False):
        row_dict = row._asdict()
        title = row_dict.get("title", "")
        title_terms = _terms_in_text(title, options["ngram_range"])
        matched = sorted(title_terms & theme_terms, key=lambda term: (-term_stats[term]["lift"], term))
        top_matched = matched[: int(options["score_top_terms"])]
        strengths = [
            math.log1p(term_stats[term]["lift"]) * math.log1p(term_stats[term]["lead_count"])
            for term in top_matched
        ]
        theme_strength = float(sum(strengths))
        theme_lift = float(np.mean([term_stats[term]["lift"] for term in top_matched])) if top_matched else 0.0
        category_id = _normalize_category_value(row_dict.get("category_id"))
        saturation_score = float(saturation_map.get(category_id, 0.0))
        saturation_boost = 1.0
        if max_saturation > 0:
            saturation_boost += float(options["saturation_weight"]) * saturation_score / max_saturation
        final_score = theme_strength * saturation_boost
        final_score = final_score if np.isfinite(final_score) else 0.0
        rows.append(
            {
                "video_id": row_dict.get("video_id"),
                "score": float(final_score),
                "reason": _score_reason(top_matched, theme_lift, saturation_score),
                "title": title,
                "channel_name": row_dict.get("channel_name", np.nan),
                "subscribers": row_dict.get("subscribers", np.nan),
                "views": row_dict.get("views", np.nan),
                "matched_terms": " | ".join(top_matched),
                "theme_lift": theme_lift,
                "category_name": _category_name(category_id),
                "video_url": data_access.video_url(str(row_dict.get("video_id", ""))),
                "channel_url": data_access.channel_url(str(row_dict.get("channel_id", ""))),
            }
        )

    result = pd.DataFrame(rows, columns=_SCORE_COLUMNS)
    result["score"] = pd.to_numeric(result["score"], errors="coerce").fillna(0.0)
    result["score"] = result["score"].replace([np.inf, -np.inf], 0.0)
    return result.sort_values(["score", "views"], ascending=[False, False]).reset_index(drop=True)


def _extract_theme_group(
    leads: pd.DataFrame,
    background: pd.DataFrame | None,
    options: dict[str, Any],
) -> pd.DataFrame:
    columns = _THEME_COLUMNS
    if leads.empty:
        return pd.DataFrame(columns=columns)

    if HAS_SKLEARN and bool(options["use_sklearn"]):
        try:
            term_docs = _lead_doc_terms_sklearn(leads["title"], options)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falling back to stdlib title tokenisation after sklearn failure: %s", exc)
            term_docs = _lead_doc_terms_fallback(leads["title"], options)
    else:
        term_docs = _lead_doc_terms_fallback(leads["title"], options)

    min_lead_count = int(options["min_lead_count"])
    lead_counts = term_docs.sum(axis=0) if not term_docs.empty else pd.Series(dtype="int64")
    lead_counts = lead_counts[lead_counts >= min_lead_count].sort_values(ascending=False)
    if lead_counts.empty:
        return pd.DataFrame(columns=columns)
    if options.get("max_features"):
        lead_counts = lead_counts.head(int(options["max_features"]))
    candidate_terms = set(lead_counts.index.astype(str))

    background_counts, background_docs = _background_doc_counts(background, candidate_terms, options)
    total_leads = max(len(leads), 1)
    smoothing = float(options["background_smoothing"])

    rows = []
    lead_term_sets = term_docs.apply(lambda row: set(row.index[row.astype(bool)]), axis=1)
    leads_reset = leads.reset_index(drop=True)
    for term, lead_count in lead_counts.items():
        mask = lead_term_sets.map(lambda terms, value=term: value in terms)
        examples = leads_reset.loc[mask, ["video_id", "title"]].head(3)
        term_leads = leads_reset.loc[mask]
        lead_share = float(lead_count) / total_leads
        background_count = int(background_counts.get(term, 0))
        if background_docs > 0:
            background_share = (background_count + smoothing) / (background_docs + (2.0 * smoothing))
            outlier_share = (float(lead_count) + smoothing) / (total_leads + (2.0 * smoothing))
            lift = outlier_share / background_share if background_share > 0 else outlier_share
        else:
            background_share = np.nan
            lift = lead_share
        rows.append(
            {
                "term": term,
                "ngram_size": len(term.split()),
                "lead_count": int(lead_count),
                "lead_share": lead_share,
                "background_share": background_share,
                "lift": float(lift) if np.isfinite(lift) else 0.0,
                "median_views": pd.to_numeric(term_leads.get("views"), errors="coerce").median(),
                "median_views_per_sub": _views_per_sub(term_leads).median(),
                "distinct_channels": int(term_leads.get("channel_id", pd.Series(dtype="object")).nunique()),
                "example_titles": " | ".join(examples["title"].fillna("").astype(str).tolist()),
                "example_video_ids": " | ".join(examples["video_id"].fillna("").astype(str).tolist()),
            }
        )

    result = pd.DataFrame(rows, columns=columns)
    if result.empty:
        return result
    result = result[result["distinct_channels"].ge(int(options["min_distinct_channels"]))]
    if result.empty:
        return pd.DataFrame(columns=columns)
    composite = (
        result["lift"].fillna(0)
        * np.log1p(result["lead_count"].fillna(0))
        * np.log1p(result["distinct_channels"].fillna(0))
    )
    result = result.assign(_composite=composite)
    return result.sort_values(
        ["_composite", "lift", "lead_count", "distinct_channels"],
        ascending=[False, False, False, False],
    ).drop(columns="_composite").reset_index(drop=True)


def _lead_doc_terms_sklearn(titles: pd.Series, options: dict[str, Any]) -> pd.DataFrame:
    if TfidfVectorizer is None:
        return _lead_doc_terms_fallback(titles, options)
    vectorizer = TfidfVectorizer(
        analyzer=lambda text: list(_generate_ngrams(_tokenize(text), options["ngram_range"])),
        lowercase=False,
        use_idf=True,
        norm=None,
        binary=False,
    )
    matrix = vectorizer.fit_transform(titles.fillna("").astype(str).tolist())
    terms = vectorizer.get_feature_names_out()
    binary = (matrix > 0).astype("int8")
    return pd.DataFrame.sparse.from_spmatrix(binary, columns=terms).astype(bool)


def _lead_doc_terms_fallback(titles: pd.Series, options: dict[str, Any]) -> pd.DataFrame:
    doc_terms = [_terms_in_text(title, options["ngram_range"]) for title in titles.fillna("").astype(str)]
    all_terms = sorted(set().union(*doc_terms)) if doc_terms else []
    if not all_terms:
        return pd.DataFrame(index=range(len(doc_terms)))
    rows = [{term: term in terms for term in all_terms} for terms in doc_terms]
    return pd.DataFrame(rows, columns=all_terms).fillna(False)


def _background_doc_counts(
    background: pd.DataFrame | None,
    candidate_terms: set[str],
    options: dict[str, Any],
) -> tuple[Counter[str], int]:
    if background is None or background.empty or not candidate_terms:
        return Counter(), 0
    titles = background["title"].fillna("").astype(str)
    counts: Counter[str] = Counter()
    for terms in titles.map(lambda title: _terms_in_text(title, options["ngram_range"])):
        counts.update(terms & candidate_terms)
    return counts, int(len(titles))


def _tokenize(text: Any) -> list[str]:
    normalized = unicodedata.normalize("NFKC", "" if pd.isna(text) else str(text)).lower()
    normalized = _TOKEN_RE.sub(" ", normalized.replace("_", " "))
    normalized = _SPACE_RE.sub(" ", normalized).strip()
    tokens = []
    for token in normalized.split(" "):
        token = token.strip("_")
        if (
            not token
            or token in STOPWORDS
            or len(token) <= 1
            or token.isnumeric()
            or any(char.isdigit() for char in token)
        ):
            continue
        tokens.append(token)
    return tokens


def _generate_ngrams(tokens: list[str], ngram_range: tuple[int, int]) -> Iterable[str]:
    min_n, max_n = ngram_range
    for size in range(max(1, min_n), max(max_n, min_n) + 1):
        if len(tokens) < size:
            continue
        for index in range(0, len(tokens) - size + 1):
            yield " ".join(tokens[index : index + size])


def _terms_in_text(text: Any, ngram_range: tuple[int, int]) -> set[str]:
    return set(_generate_ngrams(_tokenize(text), ngram_range))


def _params(params: dict[str, Any]) -> dict[str, Any]:
    options = {**DEFAULT_PARAMS, **params}
    ngram_range = tuple(options["ngram_range"])
    if len(ngram_range) != 2:
        raise ValueError("ngram_range must contain exactly two integers")
    options["ngram_range"] = (int(ngram_range[0]), int(ngram_range[1]))
    return options


def _dedupe_videos(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    result = df.copy()
    if "video_id" in result.columns:
        result = result.dropna(subset=["video_id"]).drop_duplicates("video_id", keep="first")
    return result.reset_index(drop=True)


def _attach_background_columns(leads: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    if leads.empty:
        return leads.copy()
    result = leads.copy()
    if "video_id" not in result.columns or "video_id" not in df.columns:
        return result
    needed = [
        "video_id",
        "title",
        "channel_id",
        "channel_name",
        "subscribers",
        "views",
        "category_id",
        "views_per_sub",
    ]
    available = [column for column in needed if column in df.columns]
    lookup = df[available].drop_duplicates("video_id", keep="first")
    overlap = [column for column in lookup.columns if column in result.columns and column != "video_id"]
    merged = result.merge(lookup, on="video_id", how="left", suffixes=("", "_background"))
    for column in overlap:
        background_column = f"{column}_background"
        if background_column in merged.columns:
            merged[column] = merged[column].combine_first(merged[background_column])
            merged = merged.drop(columns=background_column)
    return merged


def _fallback_leads(df: pd.DataFrame, options: dict[str, Any]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    result = df.copy()
    if "views_per_sub" in result.columns:
        views_per_sub = pd.to_numeric(result["views_per_sub"], errors="coerce")
    else:
        views_per_sub = metrics.safe_ratio(result.get("views"), result.get("subscribers"), fill=np.nan)
    views = pd.to_numeric(result.get("views"), errors="coerce")
    eligible = result.loc[views.ge(float(options["fallback_min_views"])) & views_per_sub.notna()].copy()
    if eligible.empty:
        return eligible
    threshold = views_per_sub.loc[eligible.index].quantile(float(options["fallback_views_per_sub_quantile"]))
    eligible = eligible.loc[views_per_sub.loc[eligible.index].ge(threshold)].copy()
    eligible["score"] = views_per_sub.loc[eligible.index]
    return eligible.sort_values("score", ascending=False).head(int(options["fallback_max_leads"]))


def _views_per_sub(df: pd.DataFrame) -> pd.Series:
    if "views_per_sub" in df.columns:
        return pd.to_numeric(df["views_per_sub"], errors="coerce")
    if "views" in df.columns and "subscribers" in df.columns:
        return metrics.safe_ratio(df["views"], df["subscribers"], fill=np.nan)
    return pd.Series(np.nan, index=df.index, dtype="float64")


def _category_series(df: pd.DataFrame) -> pd.Series:
    if "category_id" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return df["category_id"].map(_normalize_category_value)


def _normalize_category_value(value: Any) -> Any:
    if pd.isna(value):
        return np.nan
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return value


def _category_name(category_id: Any) -> str:
    category_id = _normalize_category_value(category_id)
    if pd.isna(category_id):
        return "Unknown"
    return YOUTUBE_CATEGORY_NAMES.get(category_id, f"Unknown ({category_id})")


def _score_reason(matched_terms: list[str], theme_lift: float, saturation_score: float) -> str:
    if not matched_terms:
        return "No recurring high-lift theme met the minimum lead count."
    terms = ", ".join(matched_terms[:3])
    return f"Matches hot theme(s): {terms}; avg lift {theme_lift:.1f}x; niche saturation {saturation_score:.4f}."
