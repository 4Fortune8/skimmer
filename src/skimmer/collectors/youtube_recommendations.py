"""Discover channels from recommendations around high-value seed videos."""

import os
import time
from dataclasses import dataclass

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from skimmer.storage.bronze import (
    new_profile_channel_keys,
    record_discovery_seed,
    select_high_views_per_subscriber_seeds,
)


def _positive_int_env(name, default):
    value = os.environ.get(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be at least one.")
    return parsed


@dataclass(frozen=True)
class DiscoverySeed:
    channel_id: str
    channel_name: str | None
    subscribers: int
    channel_views: int | None
    video_id: str
    title: str | None
    published_at: str
    video_views: int
    score: float


class HighViewsPerSubscriberSelector:
    """Rank recent videos by views per subscriber for channels below a cap."""

    name = "high_views_per_subscriber"

    def __init__(
        self,
        limit=5,
        minimum_subscribers=100,
        maximum_subscribers=1_000_000,
        minimum_video_views=10_000,
        video_lookback_days=90,
        seed_cooldown_days=7,
    ):
        self.limit = limit
        self.minimum_subscribers = minimum_subscribers
        self.maximum_subscribers = maximum_subscribers
        self.minimum_video_views = minimum_video_views
        self.video_lookback_days = video_lookback_days
        self.seed_cooldown_days = seed_cooldown_days

    @classmethod
    def from_environment(cls):
        return cls(
            limit=_positive_int_env("YOUTUBE_DISCOVERY_SEED_LIMIT", 5),
            minimum_subscribers=_positive_int_env(
                "YOUTUBE_DISCOVERY_MIN_SUBSCRIBERS", 100
            ),
            maximum_subscribers=_positive_int_env(
                "YOUTUBE_DISCOVERY_MAX_SUBSCRIBERS", 1_000_000
            ),
            minimum_video_views=_positive_int_env(
                "YOUTUBE_DISCOVERY_MIN_VIDEO_VIEWS", 10_000
            ),
            video_lookback_days=_positive_int_env(
                "YOUTUBE_DISCOVERY_VIDEO_LOOKBACK_DAYS", 90
            ),
            seed_cooldown_days=_positive_int_env(
                "YOUTUBE_DISCOVERY_SEED_COOLDOWN_DAYS", 7
            ),
        )

    def select(self, database_path=None):
        records = select_high_views_per_subscriber_seeds(
            limit=self.limit,
            minimum_subscribers=self.minimum_subscribers,
            maximum_subscribers=self.maximum_subscribers,
            minimum_video_views=self.minimum_video_views,
            video_lookback_days=self.video_lookback_days,
            seed_cooldown_days=self.seed_cooldown_days,
            database_path=database_path,
        )
        return [DiscoverySeed(**record) for record in records]


def extract_recommended_records(driver):
    """Extract recommendation records from legacy and current YouTube layouts."""
    return driver.execute_script(
        """
        const legacyRecords = Array.from(
            document.querySelectorAll('ytd-compact-video-renderer')
        )
            .map((item) => {
                const video = item.querySelector(
                    'a#video-title[href*="/watch"], a#thumbnail[href*="/watch"]'
                );
                const channel = item.querySelector(
                    '#channel-name a[href*="/@"], #channel-name a[href*="/channel/"], '
                    + '#channel-name a[href*="/c/"], #channel-name a[href*="/user/"], '
                    + 'ytd-channel-name a[href*="/@"], '
                    + 'ytd-channel-name a[href*="/channel/"]'
                );
                const metadata = Array.from(
                    item.querySelectorAll('#metadata-line .inline-metadata-item')
                )
                    .map((element) => element.textContent.trim())
                    .filter(Boolean);

                if (!video || !channel || metadata.length < 2) {
                    return null;
                }

                const channelPath = new URL(channel.href).pathname;
                const channelId = channelPath.split('/').filter(Boolean).pop();
                return {
                    video_name: video.textContent.trim(),
                    video_id: new URL(video.href).searchParams.get('v'),
                    channel_display_name: channel.textContent.trim(),
                    views: metadata[0],
                    age: metadata[1],
                    channel_id: channelId,
                    youtube_channel_id: channelPath.startsWith('/channel/')
                        ? channelId
                        : null,
                };
            })
            .filter((record) => record && record.channel_id);

        if (legacyRecords.length) {
            return legacyRecords;
        }

        const records = [];
        const seen = new Set();
        const visited = new Set();
        const findFirst = (value, key) => {
            if (!value || typeof value !== 'object') {
                return null;
            }
            if (Object.prototype.hasOwnProperty.call(value, key)) {
                return value[key];
            }
            for (const child of Object.values(value)) {
                const found = findFirst(child, key);
                if (found !== null) {
                    return found;
                }
            }
            return null;
        };
        const textContent = (part) => part?.text?.content || null;
        const visit = (value) => {
            if (!value || typeof value !== 'object' || visited.has(value)) {
                return;
            }
            visited.add(value);
            const lockup = value.lockupViewModel;
            if (lockup) {
                const metadata = lockup.metadata?.lockupMetadataViewModel;
                const rows = metadata?.metadata?.contentMetadataViewModel
                    ?.metadataRows || [];
                const videoId = findFirst(lockup, 'videoId');
                const channelId = findFirst(lockup, 'browseId');
                const title = metadata?.title?.content || null;
                const channelName = textContent(rows[0]?.metadataParts?.[0]);
                const views = textContent(rows[1]?.metadataParts?.[0]);
                const age = textContent(rows[1]?.metadataParts?.[1]);
                if (videoId && channelId && title && channelName && views && age
                    && !seen.has(videoId)) {
                    seen.add(videoId);
                    records.push({
                        video_name: title,
                        video_id: videoId,
                        channel_display_name: channelName,
                        views,
                        age,
                        channel_id: channelId,
                        youtube_channel_id: channelId.startsWith('UC')
                            ? channelId
                            : null,
                    });
                }
            }
            for (const child of Object.values(value)) {
                visit(child);
            }
        };
        visit(window.ytInitialData);
        return records;
        """
    )


class RecommendedVideosSurface:
    """Collect a bounded set of recommendations from a seed watch page."""

    def __init__(self, scroll_steps=8, scroll_delay_seconds=1):
        self.scroll_steps = scroll_steps
        self.scroll_delay_seconds = scroll_delay_seconds

    @classmethod
    def from_environment(cls):
        return cls(
            scroll_steps=_positive_int_env(
                "YOUTUBE_RECOMMENDATION_SCROLL_STEPS", 8
            ),
            scroll_delay_seconds=_positive_int_env(
                "YOUTUBE_RECOMMENDATION_SCROLL_DELAY_SECONDS", 1
            ),
        )

    def collect(self, driver, seed):
        driver.get(f"https://www.youtube.com/watch?v={seed.video_id}")
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "ytd-compact-video-renderer, yt-lockup-view-model",
                )
            )
        )
        for _ in range(self.scroll_steps):
            driver.execute_script(
                "window.scrollTo(0, document.documentElement.scrollHeight);"
            )
            time.sleep(self.scroll_delay_seconds)
        return extract_recommended_records(driver)


def collect_recommendation_recovery(
    driver,
    selector,
    surface,
    database_path=None,
):
    """Run one bounded seed-selection and recommendation discovery phase."""
    seeds = selector.select(database_path)
    print(f"Recommendation recovery selected {len(seeds)} seed videos.")
    records = []
    discovered_keys = set()
    for seed in seeds:
        collection_failed = False
        try:
            seed_records = surface.collect(driver, seed)
        except (TimeoutException, WebDriverException) as exc:
            print(
                f"Recommendation recovery failed for {seed.video_id} "
                f"({exc.__class__.__name__}); it will remain eligible for retry."
            )
            seed_records = []
            collection_failed = True
        source_file = f"https://www.youtube.com/watch?v={seed.video_id}"
        for record in seed_records:
            record["source_file"] = source_file
        new_keys = new_profile_channel_keys(seed_records, database_path)
        newly_discovered = new_keys - discovered_keys
        discovered_keys.update(new_keys)
        if not collection_failed:
            record_discovery_seed(
                selector.name,
                seed.video_id,
                seed.channel_id,
                seed.score,
                len(newly_discovered),
                database_path,
            )
        records.extend(seed_records)
        print(
            f"Seed {seed.video_id}: {len(seed_records)} recommendations, "
            f"{len(newly_discovered)} new channels."
        )
    return records
