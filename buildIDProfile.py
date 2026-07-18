import random
import scrapy
from scrapy.crawler import CrawlerProcess
from bs4 import BeautifulSoup, Tag
import time
import os
import re
from pathlib import Path

from bronze_store import get_unprocessed_youtube_channel_ids, insert_vidiq_channel_stats
from profile_normalization import normalize_channel_profile, print_normalized_profile
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
    if os.environ.get("VIDIQ_HEADLESS", "").lower() in {"1", "true", "yes"}:
        options.add_argument("-headless")
    return webdriver.Firefox(service=Service(str(geckodriver_path)), options=options)


def collect_vidiq_stats():
    channel_ids = get_unprocessed_youtube_channel_ids(
        "bronze_vidiq_channel_stats"
    )
    channel_limit = int(os.environ.get("SKIMMER_CHANNEL_LIMIT", "0"))
    print(f"vidIQ collection queue size: {len(channel_ids)}")
    if not channel_ids:
        print("No unprocessed channels found for vidIQ collector.")
        return

    driver = create_driver()
    driver.set_page_load_timeout(60)
    stored_profiles = 0
    try:
        for channel_id in channel_ids:
            if channel_limit > 0 and stored_profiles >= channel_limit:
                break
            source_url = f"https://vidiq.com/youtube-stats/channel/{channel_id}"
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
                print(f"Skipped vidIQ profile for {channel_id}: page load timed out.")
                continue
            if not {"Subscribers", "Total Video Views"}.issubset(lines):
                print(f"Skipped vidIQ profile for {channel_id}: metrics were unavailable.")
                continue

            channel_name = (
                driver.find_element(By.CSS_SELECTOR, "h1").text.strip().splitlines()[0]
            )
            subscribers = metric_value(lines, "Subscribers")
            views = metric_value(lines, "Total Video Views")
            earnings = metric_value(lines, "Est. Monthly Earnings")
            normalized_profile = normalize_channel_profile(
                source="vidiq",
                channel_id=channel_id,
                channel_name=channel_name,
                subscribers_total=convert_compact_number(subscribers),
                views_total=convert_compact_number(views),
                earnings_low=convert_compact_number(earnings),
                source_url=source_url,
                raw_rendered_text=lines,
            )
            print_normalized_profile(normalized_profile)
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
                    "source_url": normalized_profile["source_url"],
                    "raw_rendered_text": normalized_profile["raw_rendered_text"],
                }
            )
            stored_profiles += 1
            print(f"Stored vidIQ stats for {channel_id}.")
    finally:
        driver.quit()


# Usage:            
class BlogSpider(scrapy.Spider):  
    
    def __init__(self):
  
        self.name = 'blogspider'
        self.base= 'https://vidiq.com/youtube-stats/channel/'
        self.start_urls =[]
        channel_ids = get_unprocessed_youtube_channel_ids(
            "bronze_vidiq_channel_stats"
        )
        channel_limit = int(os.environ.get("SKIMMER_CHANNEL_LIMIT", "0"))
        if channel_limit > 0:
            channel_ids = channel_ids[:channel_limit]
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



if __name__ == "__main__":
    collect_vidiq_stats()
