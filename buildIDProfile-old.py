import os
import re
from pathlib import Path

from bronze_store import (
    get_unprocessed_youtube_channel_ids,
    insert_socialblade_daily_channel_metrics,
    insert_socialblade_channel_stats,
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
                "metric_date": date_match.group(0),
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
    channel_ids = get_unprocessed_youtube_channel_ids(
        "bronze_socialblade_channel_stats"
    )
    channel_limit = int(os.environ.get("SKIMMER_CHANNEL_LIMIT", "0"))
    print(f"Social Blade collection queue size: {len(channel_ids)}")
    if not channel_ids:
        print("No unprocessed channels found for Social Blade collector.")
        return

    driver = create_driver()
    driver.set_page_load_timeout(60)
    stored_profiles = 0
    try:
        for channel_id in channel_ids:
            if channel_limit > 0 and stored_profiles >= channel_limit:
                break
            source_url = socialblade_url(channel_id)
            try:
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
                print(
                    f"Skipped Social Blade profile for {channel_id}: page load timed out."
                )
                continue
            if not {"Subscribers", "Views", "CREATOR STATISTICS"}.issubset(lines):
                print(
                    f"Skipped Social Blade profile for {channel_id}: metrics were unavailable."
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
            daily_rows = driver.execute_script(
                """
                const table = Array.from(document.querySelectorAll('table')).find(
                    (candidate) => candidate.innerText.startsWith(
                        'Date\\tSubscribers\\tViews\\tVideos\\tEstimated Earnings'
                    )
                );
                if (!table) {
                    return [];
                }
                return Array.from(table.rows)
                    .slice(1)
                    .map((row) =>
                        Array.from(row.cells).map((cell) => cell.innerText.trim())
                    );
                """
            )
            insert_socialblade_channel_stats(
                {
                    "channel_id": normalized_profile["channel_id"],
                    "subscribers": normalized_profile["subscribers_total"],
                    "subscribers_change": normalized_profile["subscribers_change"],
                    "subscribers_change_percentage": normalized_profile[
                        "subscribers_change_percentage"
                    ],
                    "views_change": normalized_profile["views_change"],
                    "views_change_percentage": normalized_profile[
                        "views_change_percentage"
                    ],
                    "source_url": normalized_profile["source_url"],
                    "raw_rendered_text": normalized_profile["raw_rendered_text"],
                }
            )
            inserted_daily_metrics = insert_socialblade_daily_channel_metrics(
                parse_daily_metrics(channel_id, daily_rows)
            )
            stored_profiles += 1
            print(f"Stored Social Blade stats for {channel_id}.")
            print(f"Stored {inserted_daily_metrics} Social Blade daily metric rows.")
    finally:
        driver.quit()


if __name__ == "__main__":
    collect_socialblade_stats()
