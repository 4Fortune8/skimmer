import os
import re
import time
from pathlib import Path

from bronze_store import (
    claim_profile_batch,
    insert_socialblade_channel_stats,
    mark_profile_failed,
    mark_profile_succeeded,
    profile_identifier_candidates,
    record_collection_attempt,
    record_collection_error,
    release_profile_batch,
)
from profile_normalization import normalize_channel_profile, print_normalized_profile
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.wait import WebDriverWait


def convert_compact_number(value):
    if value is None:
        return None

    match = re.search(r"([\d,.]+)\s*([KMB]?)", value.upper())
    if not match:
        return value

    number = float(match.group(1).replace(",", ""))
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[
        match.group(2)
    ]
    return int(number * multiplier)


def value_after_label(lines, label):
    try:
        return lines[lines.index(label) + 1]
    except ValueError:
        return None


def value_before_label(lines, label):
    try:
        index = lines.index(label)
    except ValueError:
        return None
    if index == 0:
        return None

    value = lines[index - 1]
    if re.fullmatch(r"[-+]?[\d,.]+\s*[KMB]?", value.upper()):
        return value
    return None


def socialblade_url(channel_id):
    if channel_id.startswith("UC"):
        return f"https://socialblade.com/youtube/channel/{channel_id}"
    return f"https://socialblade.com/youtube/handle/{channel_id.lstrip('@')}"


def nullable_value(value):
    return None if value == "--" else value


def is_cloudflare_blocked(driver):
    page_text = driver.find_element(By.TAG_NAME, "body").text
    return (
        "attention required" in driver.title.lower()
        or "cloudflare" in page_text.lower()
    )


def parse_daily_metrics(channel_id, rows):
    records = []
    for row in rows:
        if len(row) < 8:
            continue

        date_match = re.search(r"\d{4}-\d{2}-\d{2}", row[0])
        if date_match is None:
            continue

        earnings = row[7].split(" - ")
        records.append(
            {
                "channel_id": channel_id,
                "subscribers_change": convert_compact_number(nullable_value(row[1])),
                "subscribers_total": convert_compact_number(nullable_value(row[2])),
                "views_change": convert_compact_number(nullable_value(row[3])),
                "views_total": convert_compact_number(nullable_value(row[4])),
                "videos_change": convert_compact_number(nullable_value(row[5])),
                "videos_total": convert_compact_number(nullable_value(row[6])),
                "earnings_low": convert_compact_number(nullable_value(earnings[0])),
                "earnings_high": convert_compact_number(
                    nullable_value(earnings[1]) if len(earnings) == 2 else None
                ),
                "raw_row": row,
            }
        )
    return records


def create_driver():
    options = Options()
    options.set_preference("media.volume_scale", "0.0")
    firefox_path = os.environ.get(
        "FIREFOX_BINARY_PATH",
        Path(__file__).resolve().parent / ".drivers" / "firefox" / "firefox",
    )
    geckodriver_path = os.environ.get(
        "GECKODRIVER_PATH",
        Path(__file__).resolve().parent / ".drivers" / "geckodriver",
    )
    options.binary_location = str(firefox_path)
    if os.environ.get("SOCIALBLADE_HEADLESS", "").lower() in {"1", "true", "yes"}:
        options.add_argument("-headless")
    return webdriver.Firefox(service=Service(str(geckodriver_path)), options=options)


def collect_socialblade_stats():
    channel_limit = int(
        os.environ.get("SKIMMER_BATCH_SIZE", os.environ.get("SKIMMER_CHANNEL_LIMIT", "100"))
    )
    if channel_limit < 1:
        channel_limit = 100
    worker_id = os.environ.get("SKIMMER_WORKER_ID", f"socialblade-{os.getpid()}")
    channel_ids = claim_profile_batch("socialblade", worker_id, channel_limit)
    print(f"Social Blade collection queue size: {len(channel_ids)}")
    if not channel_ids:
        print("No unprocessed channels found for Social Blade collector.")
        return None

    channel_delay_seconds = int(
        os.environ.get("SOCIALBLADE_CHANNEL_DELAY_SECONDS", "120")
    )
    page_delay_seconds = int(
        os.environ.get("SOCIALBLADE_PAGE_DELAY_SECONDS", "20")
    )
    if channel_delay_seconds < 1 or page_delay_seconds < 1:
        raise ValueError("Social Blade rate limits must be at least one second.")
    driver = create_driver()
    driver.set_page_load_timeout(60)
    last_channel_at = None
    last_request_at = None
    try:
        for channel_id in channel_ids:
            profile_stored = False
            for attempt_index, (identifier, identifier_kind) in enumerate(
                profile_identifier_candidates(
                "socialblade", channel_id
                )
            ):
                if attempt_index == 0 and last_channel_at is not None:
                    elapsed = time.monotonic() - last_channel_at
                    if elapsed < channel_delay_seconds:
                        time.sleep(channel_delay_seconds - elapsed)
                if last_request_at is not None:
                    elapsed = time.monotonic() - last_request_at
                    if elapsed < page_delay_seconds:
                        time.sleep(page_delay_seconds - elapsed)
                source_url = socialblade_url(identifier)
                try:
                    last_request_at = time.monotonic()
                    if attempt_index == 0:
                        last_channel_at = last_request_at
                    driver.get(source_url)
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located(
                            (By.XPATH, "//*[normalize-space()='Subscribers']")
                        )
                    )
                    lines = [
                        line.strip()
                        for line in driver.find_element(By.TAG_NAME, "body").text.splitlines()
                        if line.strip()
                    ]
                except TimeoutException:
                    if is_cloudflare_blocked(driver):
                        record_collection_error(
                            "socialblade",
                            "cloudflare_block",
                            "Cloudflare blocked the headed browser session.",
                            channel_id,
                            source_url,
                            403,
                        )
                        release_profile_batch("socialblade", worker_id)
                        print(
                            "Social Blade is blocked by Cloudflare; "
                            "leaving the collection queue unchanged."
                        )
                        return False
                    record_collection_error(
                        "socialblade",
                        "page_load_timeout",
                        "Timed out waiting for the Subscribers metric.",
                        channel_id,
                        source_url,
                    )
                    record_collection_attempt(
                        "socialblade",
                        channel_id,
                        identifier,
                        identifier_kind,
                        source_url,
                        "failed",
                        "page_load_timeout",
                    )
                    continue
                if not {"Subscribers", "Views", "CREATOR STATISTICS"}.issubset(lines):
                    record_collection_error(
                        "socialblade",
                        "metrics_unavailable",
                        "The expected Social Blade metrics were not rendered.",
                        channel_id,
                        source_url,
                    )
                    record_collection_attempt(
                        "socialblade",
                        channel_id,
                        identifier,
                        identifier_kind,
                        source_url,
                        "failed",
                        "metrics_unavailable",
                    )
                    continue

                subscribers = value_after_label(lines, "Subscribers")
                views = value_after_label(lines, "Views")
                creator_statistics_index = lines.index("CREATOR STATISTICS")
                creator_statistics = lines[creator_statistics_index + 1 :]
                subscribers_change = value_before_label(
                    creator_statistics, "Subscribers for the last 30 days"
                )
                views_change = value_before_label(
                    creator_statistics, "Views for the last 30 days"
                )
                normalized_profile = normalize_channel_profile(
                    source="socialblade",
                    channel_id=channel_id,
                    subscribers_total=convert_compact_number(subscribers),
                    subscribers_change=convert_compact_number(subscribers_change),
                    views_total=convert_compact_number(views),
                    views_change=convert_compact_number(views_change),
                    source_url=source_url,
                    raw_rendered_text=lines,
                )
                print_normalized_profile(normalized_profile)
                try:
                    daily_rows = WebDriverWait(driver, 30).until(
                        lambda browser: browser.execute_script(
                            """
                            const table = Array.from(document.querySelectorAll('table')).find(
                                (candidate) => candidate.innerText.startsWith(
                                    'Date\\tSubscribers\\tViews\\tVideos\\tEstimated Earnings'
                                )
                            );
                            if (!table || table.rows.length < 2) {
                                return null;
                            }
                            return Array.from(table.rows)
                                .slice(1)
                                .map((row) =>
                                    Array.from(row.cells).map((cell) => cell.innerText.trim())
                                );
                            """
                        )
                    )
                except TimeoutException:
                    daily_rows = []
                daily_records = parse_daily_metrics(channel_id, daily_rows)
                if not daily_records:
                    record_collection_error(
                        "socialblade",
                        "daily_metrics_unavailable",
                        "Social Blade rendered no daily metric records.",
                        channel_id,
                        source_url,
                    )
                    record_collection_attempt(
                        "socialblade",
                        channel_id,
                        identifier,
                        identifier_kind,
                        source_url,
                        "failed",
                        "daily_metrics_unavailable",
                    )
                    continue
                inserted_stats = insert_socialblade_channel_stats(daily_records)
                record_collection_attempt(
                    "socialblade",
                    channel_id,
                    identifier,
                    identifier_kind,
                    source_url,
                    "succeeded",
                )
                mark_profile_succeeded(channel_id, "socialblade")
                print(f"Stored Social Blade stats for {channel_id}.")
                print(
                    "Stored "
                    f"{inserted_stats} new Social Blade metric rows from "
                    f"{len(daily_records)} rendered rows."
                )
                profile_stored = True
                break
            if not profile_stored:
                print(
                    f"Skipped Social Blade profile for {channel_id}: all identifiers failed."
                )
                mark_profile_failed(channel_id, "socialblade")
        return True
    finally:
        driver.quit()


if __name__ == "__main__":
    result = collect_socialblade_stats()
    raise SystemExit(1 if result is False else 2 if result is None else 0)
