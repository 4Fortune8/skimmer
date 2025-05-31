import random
import scrapy
import csv
from scrapy.crawler import CrawlerProcess
from bs4 import BeautifulSoup, Tag
import time
import datetime
import os


now_for_processed = datetime.datetime.now() # Use a consistent timestamp for directory           

# Corrected path construction
data_dir = "data"
profiles_subdir = "profiles"
profiles_dir_path = os.path.join(data_dir, profiles_subdir)

# Ensure the profiles directory exists
os.makedirs(profiles_dir_path, exist_ok=True)

profilesData = os.path.join(profiles_dir_path, f'profiles{now_for_processed.year}-{now_for_processed.month}.csv')
profilesids = os.path.join(profiles_dir_path, f'profilesids{now_for_processed.year}-{now_for_processed.month}.csv')

print(f"Profiles data path: {profilesData}")
print(f"Profiles IDs path: {profilesids}")

        
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

# Usage:            
class BlogSpider(scrapy.Spider):  
    
    def __init__(self):
  
        self.name = 'blogspider'
        self.base= 'https://vidiq.com/youtube-stats/channel/'
        self.start_urls =[]
        self.profiles = [] # This will store data from profilesData

        # Ensure profilesData file exists before trying to read, create if not
        if not os.path.exists(profilesData):
            with open(profilesData, 'w', newline='', encoding='utf-8') as f:
                pass # Creates an empty file

        with open(profilesData, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            self.profiles = list(reader) 


        # Ensure profilesids file exists before trying to read, create if not
        if not os.path.exists(profilesids):
            with open(profilesids, 'w', newline='', encoding='utf-8') as f:
                pass # Creates an empty file

        with open(profilesids, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            data_ids = list(reader) # Renamed to avoid conflict with global 'data' if any
            if data_ids: # Check if data_ids is not empty
                print(f"First row of profile IDs: {data_ids[0]}")
                for row_id in data_ids: # Renamed to avoid conflict
                    if row_id: # Check if row_id is not empty
                        booler = checkProfile(row_id[0], self.profiles)
                        if not booler:
                            self.start_urls.append(self.base+(row_id[0]))
            else:
                print(f"{profilesids} is empty or could not be read properly.")

    
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
        with open(profilesData, 'a',newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow([currentAccount,suscribers,suscribersChange,view,viewChange,earningsLow, earninghigh,engagment,uploadFrequency,avgLength,page])



if __name__ == "__main__":
    process = CrawlerProcess({
        'USER_AGENT': 'Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 5.1)'
    })
    import csv
    import pandas as pd
    headers = ['currentAccount','catagory', 'suscribers', 'suscribersChange', 'view', 'viewChange', 'earningsLow', 'earninghigh', 'engagment', 'uploadFrequency', 'avgLength','page','performanceData']

    # Check and write headers for profilesData
    if not os.path.exists(profilesData) or os.path.getsize(profilesData) == 0:
        with open(profilesData, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(headers)
    else:
        with open(profilesData, 'r+', newline='', encoding='utf-8') as file:
            reader = csv.reader(file)
            try:
                first_row = next(reader)
                if first_row != headers:
                    file.seek(0)
                    content = list(reader)
                    file.seek(0)
                    file.truncate()
                    writer = csv.writer(file)
                    writer.writerow(headers)
                    if first_row:
                        writer.writerow(first_row)
                    writer.writerows(content)
            except StopIteration:
                file.seek(0)
                writer = csv.writer(file)
                writer.writerow(headers)

    # De-duplicate profilesids before crawling
    if os.path.exists(profilesids) and os.path.getsize(profilesids) > 0:
        try:
            df_ids = pd.read_csv(profilesids, header=None, names=['profile_id', 'originalName'])
            df_ids_deduplicated = df_ids.drop_duplicates()
            df_ids_deduplicated.to_csv(profilesids, index=False, header=False)
            print(f"De-duplicated {profilesids}")
        except pd.errors.EmptyDataError:
            print(f"{profilesids} is empty, skipping de-duplication.")
        except Exception as e:
            print(f"Error de-duplicating {profilesids}: {e}")
    else:
        print(f"{profilesids} does not exist or is empty. Skipping de-duplication.")

    process.crawl(BlogSpider)
    process.start() # the script will block here until the crawling is finished

