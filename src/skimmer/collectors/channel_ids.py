"""Resolve SocialBlade queue handles to canonical YouTube channel IDs."""

import os
import re
import time
from pathlib import Path

from skimmer.config import PROJECT_ROOT
from skimmer.storage.bronze import (
    claim_channel_id_resolution_batch,
    release_channel_id_resolution_batch,
    store_youtube_channel_id,
)
from selenium.common.exceptions import TimeoutException
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.wait import WebDriverWait


def create_driver():
    options = Options()
    options.set_preference("media.volume_scale", "0.0")
    options.add_argument("-headless")
    project_root = PROJECT_ROOT
    firefox_path = os.environ.get(
        "FIREFOX_BINARY_PATH", project_root / ".drivers" / "firefox" / "firefox"
    )
    geckodriver_path = os.environ.get(
        "GECKODRIVER_PATH", project_root / ".drivers" / "geckodriver"
    )
    options.binary_location = str(firefox_path)
    return webdriver.Firefox(service=Service(str(geckodriver_path)), options=options)


def canonical_channel_id(driver, channel_id):
    if channel_id.startswith("UC"):
        return channel_id

    driver.get(f"https://www.youtube.com/@{channel_id.lstrip('@')}")
    return WebDriverWait(driver, 30).until(
        lambda browser: browser.execute_script(
            """
            return document.querySelector('meta[itemprop="channelId"]')?.content
                || document.querySelector('link[itemprop="url"]')?.href
                    ?.match(/\\/channel\\/(UC[\\w-]+)/)?.[1]
                || null;
            """
        )
    )


def resolve_channel_ids():
    batch_size = int(os.environ.get("SKIMMER_BATCH_SIZE", "100"))
    if batch_size < 1:
        batch_size = 100
    worker_id = os.environ.get("SKIMMER_WORKER_ID", f"youtube-id-{os.getpid()}")
    channels = claim_channel_id_resolution_batch(worker_id, batch_size)
    print(f"YouTube channel ID resolution queue size: {len(channels)}")
    if not channels:
        return None

    delay_seconds = int(os.environ.get("SKIMMER_CHANNEL_ID_RESOLUTION_DELAY_SECONDS", "5"))
    driver = create_driver()
    driver.set_page_load_timeout(45)
    resolved = 0
    try:
        for channel_key, channel_id in channels:
            if resolved:
                time.sleep(delay_seconds)
            try:
                resolved_id = canonical_channel_id(driver, channel_id)
            except TimeoutException:
                print(f"Timed out resolving canonical YouTube ID for {channel_id}.")
                continue
            if not re.fullmatch(r"UC[\w-]{20,}", resolved_id or ""):
                print(f"Could not resolve canonical YouTube ID for {channel_id}.")
                continue
            store_youtube_channel_id(channel_key, resolved_id)
            resolved += 1
            print(f"Resolved {channel_id} to {resolved_id}.")
        return True
    finally:
        release_channel_id_resolution_batch(worker_id)
        driver.quit()


def main():
    result = resolve_channel_ids()
    return 2 if result is None else 0


if __name__ == "__main__":
    raise SystemExit(main())
