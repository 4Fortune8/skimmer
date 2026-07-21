import random
import scrapy
from scrapy.crawler import CrawlerProcess
from bs4 import BeautifulSoup, Tag
import time
import os
import re
from pathlib import Path

from skimmer.config import PROJECT_ROOT
from skimmer.storage.bronze import (
    claim_profile_batch,
    get_profile_queue,
    insert_vidiq_channel_profile,
    insert_vidiq_channel_stats,
    mark_profile_failed,
    mark_profile_succeeded,
    profile_identifier_candidates,
    record_collection_attempt,
    record_collection_error,
)
from skimmer.domain.normalization import normalize_channel_profile, print_normalized_profile
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.wait import WebDriverWait

        
def print_css_tree(html):
    soup = BeautifulSoup(html, 'html.parser')

    def recursive_print(tag, prefix="."):
        if isinstance(tag, Tag):  # Check if tag is a BeautifulSoup Tag object
            class_str = ".".join(tag.get('class', []))
            print(f"{prefix}{tag.name}.{class_str}")
            prefix += "  "
            for child in tag.children:
                recursive_print(child, prefix)
                
    recursive_print(soup)



def checkProfile(row,profiles):
    try:
        # Ensure row is a string and profiles is a list of lists (of strings)
        if not isinstance(row, str):
            return False
        for sublist in profiles:
            if isinstance(sublist, list) and row in sublist:
                return True
        return False
    except Exception as e:
        print(f"Error in checkProfile: {e}")
        return False

def convert_to_number(s):
            multipliers = {'K': 1000, 'M': 1000000, 'B': 1000000000}
            # Check the last character of the string
            if s[-1] in multipliers:
                # If the last character is a multiplier, remove it from the string,
                # convert the remaining string to a float, multiply by the appropriate value,
                # and convert the result to an integer
                return int(float(s[:-1]) * multipliers[s[-1]])
            else:
                # If the last character is not a multiplier, just convert the string to an integer
                try:
                    return int(s)
                except ValueError:
                    try:
                        return float(s)
                    except ValueError:
                        return s
            
            
def extract_values(text):
    # Find the index of the item with '%'
    index = next((i for i, s in enumerate(text) if '%' in s), None)
    if index is not None and index > 0:
        # If found and it's not the first item, return the item and the one before it
        return [text[index - 1], text[index]]
    else:
        # If not found or it's the first item, return None
        return [None, None]


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


def metric_value(lines, label):
    try:
        return lines[lines.index(label) + 1]
    except ValueError:
        return None


def metrics_between_labels(lines, start_label, end_label):
    try:
        start = lines.index(start_label) + 1
        end = lines.index(end_label, start)
    except ValueError:
        return []
    return lines[start:end]


def long_form_vs_shorts_metrics(lines):
    try:
        section_start = lines.index("Long-form vs Shorts")
    except ValueError:
        return {}
    section = lines[section_start:]
    try:
        uploads = metrics_between_labels(section, "Uploads", "Views")
    except ValueError:
        uploads = []
    views = metrics_between_labels(section, "Views", "Subscribers")
    return {
        "content_period": section[1] if len(section) > 1 and section[1].startswith("Since ") else None,
        "long_form_uploads": convert_compact_number(metric_value(uploads, "Long-form")),
        "shorts_uploads": convert_compact_number(metric_value(uploads, "Shorts")),
        "long_form_views": convert_compact_number(metric_value(views, "Long-form")),
        "shorts_views": convert_compact_number(metric_value(views, "Shorts")),
    }


def vidiq_rankings(lines):
    try:
        ranking_index = lines.index("Ranking (30 days)")
    except ValueError:
        return None, None
    rankings = lines[ranking_index + 1 : ranking_index + 3]
    return (
        rankings[0] if len(rankings) > 0 else None,
        rankings[1] if len(rankings) > 1 else None,
    )


def vidiq_about_details(driver):
    WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable(
            (By.XPATH, "//*[self::a or self::button][normalize-space()='About']")
        )
    ).click()
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.XPATH, "//*[normalize-space()='Joined']"))
    )
    lines = [
        line.strip()
        for line in driver.find_element(By.TAG_NAME, "body").text.splitlines()
        if line.strip()
    ]
    return {
        "joined_at": metric_value(lines, "Joined"),
        "location": metric_value(lines, "Location"),
        "category": metric_value(lines, "Category"),
        "videos_total": convert_compact_number(metric_value(lines, "Videos")),
        "subscribers_total": convert_compact_number(metric_value(lines, "Subscribers")),
    }


def create_driver():
    options = Options()
    options.set_preference("media.volume_scale", "0.0")
    firefox_path = os.environ.get(
        "FIREFOX_BINARY_PATH",
        PROJECT_ROOT / ".drivers" / "firefox" / "firefox",
    )
    geckodriver_path = os.environ.get(
        "GECKODRIVER_PATH",
        PROJECT_ROOT / ".drivers" / "geckodriver",
    )
    options.binary_location = str(firefox_path)
    if os.environ.get("VIDIQ_HEADLESS", "").lower() in {"1", "true", "yes"}:
        options.add_argument("-headless")
    return webdriver.Firefox(service=Service(str(geckodriver_path)), options=options)


def collect_vidiq_stats():
    channel_limit = int(
        os.environ.get("SKIMMER_BATCH_SIZE", os.environ.get("SKIMMER_CHANNEL_LIMIT", "100"))
    )
    if channel_limit < 1:
        channel_limit = 100
    worker_id = os.environ.get("SKIMMER_WORKER_ID", f"vidiq-{os.getpid()}")
    channel_ids = claim_profile_batch("vidiq", worker_id, channel_limit)
    print(f"vidIQ collection queue size: {len(channel_ids)}")
    if not channel_ids:
        print("No unprocessed channels found for vidIQ collector.")
        return None

    driver = create_driver()
    driver.set_page_load_timeout(60)
    stored_profiles = 0
    try:
        for channel_id in channel_ids:
            if stored_profiles:
                time.sleep(15)
            profile_stored = False
            for identifier, identifier_kind in profile_identifier_candidates(
                "vidiq", channel_id
            ):
                source_url = f"https://vidiq.com/youtube-stats/channel/{identifier}"
                try:
                    driver.get(source_url)
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
                    )
                    lines = [
                        line.strip()
                        for line in driver.find_element(By.TAG_NAME, "body").text.splitlines()
                        if line.strip()
                    ]
                except TimeoutException:
                    failure_type = "page_load_timeout"
                else:
                    failure_type = (
                        None
                        if {"Subscribers", "Total Video Views"}.issubset(lines)
                        else "metrics_unavailable"
                    )
                if failure_type:
                    record_collection_error(
                        "vidiq",
                        failure_type,
                        f"vidIQ {failure_type.replace('_', ' ')}.",
                        channel_id,
                        source_url,
                    )
                    record_collection_attempt(
                        "vidiq",
                        channel_id,
                        identifier,
                        identifier_kind,
                        source_url,
                        "failed",
                        failure_type,
                    )
                    continue

                channel_name = (
                    driver.find_element(By.CSS_SELECTOR, "h1").text.strip().splitlines()[0]
                )
                normalized_profile = normalize_channel_profile(
                    source="vidiq",
                    channel_id=channel_id,
                    channel_name=channel_name,
                    subscribers_total=convert_compact_number(metric_value(lines, "Subscribers")),
                    views_total=convert_compact_number(metric_value(lines, "Total Video Views")),
                    earnings_low=convert_compact_number(
                        metric_value(lines, "Est. Monthly Earnings")
                    ),
                    source_url=source_url,
                    raw_rendered_text=lines,
                )
                print_normalized_profile(normalized_profile)
                format_metrics = long_form_vs_shorts_metrics(lines)
                country_ranking, worldwide_ranking = vidiq_rankings(lines)
                try:
                    about_details = vidiq_about_details(driver)
                except TimeoutException:
                    about_details = {}
                    record_collection_error(
                        "vidiq",
                        "profile_details_unavailable",
                        "vidIQ did not render the About profile details.",
                        channel_id,
                        source_url,
                    )
                insert_vidiq_channel_profile(
                    {
                        "channel_id": normalized_profile["channel_id"],
                        "channel_name": normalized_profile["channel_name"],
                        "joined_at": about_details.get("joined_at"),
                        "location": about_details.get("location"),
                        "category": about_details.get("category"),
                        "videos_total": about_details.get("videos_total"),
                        "subscribers_total": (
                            about_details.get("subscribers_total")
                            or normalized_profile["subscribers_total"]
                        ),
                        "views_total": normalized_profile["views_total"],
                        "estimated_monthly_earnings": normalized_profile["earnings_low"],
                        **format_metrics,
                        "ranking_30_day_country": country_ranking,
                        "ranking_30_day_worldwide": worldwide_ranking,
                    }
                )
                insert_vidiq_channel_stats(
                    {
                        "channel_name": normalized_profile["channel_name"],
                        "subscribers": normalized_profile["subscribers_total"],
                        "subscribers_change": normalized_profile["subscribers_change"],
                        "views": normalized_profile["views_total"],
                        "views_change": normalized_profile["views_change"],
                        "earnings_low": normalized_profile["earnings_low"],
                        "earnings_high": normalized_profile["earnings_high"],
                        "engagement": normalized_profile["engagement"],
                        "upload_frequency": normalized_profile["upload_frequency"],
                        "average_length": normalized_profile["average_length"],
                        "channel_id": normalized_profile["channel_id"],
                    }
                )
                record_collection_attempt(
                    "vidiq",
                    channel_id,
                    identifier,
                    identifier_kind,
                    source_url,
                    "succeeded",
                )
                stored_profiles += 1
                mark_profile_succeeded(channel_id, "vidiq")
                print(f"Stored vidIQ stats for {channel_id}.")
                profile_stored = True
                break
            if not profile_stored:
                mark_profile_failed(channel_id, "vidiq")
        return True
    finally:
        driver.quit()


# Usage:            
class BlogSpider(scrapy.Spider):  
    
    def __init__(self):
  
        self.name = 'blogspider'
        self.base= 'https://vidiq.com/youtube-stats/channel/'
        self.start_urls =[]
        channel_limit = int(os.environ.get("SKIMMER_CHANNEL_LIMIT", "0"))
        channel_ids = get_profile_queue("vidiq", channel_limit)
        self.start_urls = [self.base + channel_id for channel_id in channel_ids]

    
    def parse(self, response):
        time.sleep(12)
        time.sleep(random.randint(1, 4))
        # Extract data from the new CSS selector
        URL = response.url
        page= URL.rsplit('/')[-2]
        currentAccount = response.css('h1::text').get()
        element = response.xpath('/html/body/main/section/div/div[2]/div[2]/div[2]/div[1]/div' )
        time.sleep(1)
        subscribers = element.css('div:nth-child(2)>div>div>p:nth-child(2)::text').get()
        subscribersChange = element.css('div:nth-child(2)>div>div>p:nth-child(3)>*::text').getall()
        view = element.css('div:nth-child(3)>div>div>p:nth-child(2)::text').get()
        viewChange = element.css('div:nth-child(3)>div>div>p:nth-child(3)>*::text').getall()
        earnings = element.css('div:nth-child(4)>div>div>p::text').getall()
        engagment = element.css('div:nth-child(5)>div>div>p::text').get()
        uploadFrequency = element.css('div:nth-child(6)>div>div>p::text').get()
        avgLength = element.css('div:nth-child(7)>div>div>p::text').get()
        try:
            subscribers = convert_to_number(subscribers)
            earningsLow = convert_to_number(earnings[1])
            earninghigh = convert_to_number(earnings[3])         
            yield self.addProfile(currentAccount,subscribers,''.join(subscribersChange[:2]),view,''.join(viewChange[:2]),earningsLow, earninghigh,engagment,uploadFrequency,avgLength,page)
        except:
            print("something is wrong here:", self.base+page)
        
    
    
    def addProfile(self,currentAccount,suscribers,suscribersChange,view,viewChange,earningsLow, earninghigh,engagment,uploadFrequency,avgLength,page):
        insert_vidiq_channel_stats(
            {
                "channel_name": currentAccount,
                "subscribers": suscribers,
                "subscribers_change": suscribersChange,
                "views": view,
                "views_change": viewChange,
                "earnings_low": earningsLow,
                "earnings_high": earninghigh,
                "engagement": engagment,
                "upload_frequency": uploadFrequency,
                "average_length": avgLength,
                "channel_id": page,
            }
        )



def main():
    result = collect_vidiq_stats()
    return 2 if result is None else 0


if __name__ == "__main__":
    raise SystemExit(main())
