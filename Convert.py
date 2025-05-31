import scrapy
import csv
from scrapy.crawler import CrawlerProcess
from bs4 import BeautifulSoup, Tag
import time
import datetime
import os
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
        print(row[1:].lower())
        for sublist in profiles:
            if row.lower() in sublist[1].lower():
                return True
            elif row.lower() == "@"+sublist[1].lower().strip():
                return True
            elif row.lower() == "@"+sublist[1][:-1].lower().strip():
                return True
        return False
    except:
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
                return int(s)
            
            
def extract_values(text):
    # Find the index of the item with '%'
    data= []
    index = next((i for i, s in enumerate(text) if '%' in s), None)
    

    if index is not None and index > 0:
        # If found and it's not the first item, return the item and the one before it
        data.append(text[index - 1])
        data.append(text[index])
        data.append(text[-7])
        data.append(text[-6])
        
    else:
        # If not found or it's the first item, return None
        return [None, None,None, None]
    return data
# Usage:     
import shutil

class BlogSpider(scrapy.Spider):  
    def __init__(self):
        import glob
        import pandas as pd
        # Define the path to the CSV files
        csv_files_path = 'data/output/*.csv'

        # Use glob to get all CSV files in the directory
        csv_files = glob.glob(csv_files_path)

        # Read each CSV file into a DataFrame and store them in a list
        dataframes = [pd.read_csv(file) for file in csv_files]

        combined_list = []
        headers = []

        if dataframes:
            # Concatenate all DataFrames into one
            combined_df = pd.concat(dataframes, ignore_index=True)
            combined_df = combined_df.drop_duplicates()
            # Convert the combined DataFrame to a list of lists (including headers)
            combined_list = combined_df.values.tolist()

            # Optionally, include the headers as the first row
            headers = combined_df.columns.tolist()
            combined_list.insert(0, headers)

            # Make the subfolder if it does not exist
            now_for_processed = datetime.datetime.now() # Use a consistent timestamp for directory
            processed_dir = f"data/{now_for_processed.year}-{now_for_processed.month}-processed"
            os.makedirs(processed_dir, exist_ok=True)

            # Move processed files into the subfolder
            for file_path in csv_files: # Iterate over csv_files found by glob
                if os.path.exists(file_path): # Ensure file still exists before moving
                    shutil.move(file_path, os.path.join(processed_dir, os.path.basename(file_path)))
        else:
            print("No CSV files found to process.")
            # If combined_list is intended to always have headers, initialize it here
            # For example, if you expect specific column names even if no data:
            # headers = ["ExpectedHeader1", "ExpectedHeader2", ...] 
            # combined_list.insert(0, headers)
            # However, current logic implies combined_list[1:] so an empty list is fine.

        now = datetime.datetime.now()
        # Define the directory for profiles
        profiles_dir = os.path.join('data', 'profiles')
        # Create the profiles directory if it doesn't exist
        os.makedirs(profiles_dir, exist_ok=True)
        
        self.store = os.path.join(profiles_dir, f"profilesids{now.year}-{now.month}.csv")

        # Ensure file exists (or create it empty) before reading
        if not os.path.exists(self.store):
            open(self.store, 'w', encoding='utf-8').close()

        with open(self.store, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            profiles = list(reader)

        self.name = 'blogspider'
        self.base= 'https://www.youtube.com/@'
        self.start_urls =[]
        self.profiles = profiles
        
        if combined_list and len(combined_list) > 1: # Ensure combined_list has data rows
            for row in combined_list[1:]:
                # Ensure row has enough elements before accessing row[4]
                if len(row) > 4 and row[4]: 
                    booler = checkProfile(row[4], profiles)
                    if not booler: 
                        if row[4][0] == '@':
                            self.start_urls.append(self.base + (row[4][1:]))
                        else:
                            self.addProfile(row[4], row[4])
                else:
                    print(f"Skipping row due to insufficient columns or empty profile ID: {row}")
        else:
            print("No data in combined_list to process for start_urls.")

    def parse(self, response):
        time.sleep(4)
        # Extract data from the new CSS selector
        for element in response.css('link[rel="canonical"]'):
            href = element.attrib['href']
            current_url = response.url
            self.addProfile(href.split('/')[-1], current_url.split('/')[-1])
    
    def addProfile(self, profile, orignalName):
        with open(self.store, 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow([profile, orignalName])



if __name__ == "__main__":
    process = CrawlerProcess({
        'USER_AGENT': 'Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 5.1)'
    })

    process.crawl(BlogSpider)
    process.start() # the script will block here until the crawling is finished

    # After crawling, de-duplicate the profiles CSV file
    import pandas as pd
    now = datetime.datetime.now()
    profiles_dir = os.path.join('data', 'profiles')
    store_path = os.path.join(profiles_dir, f"profilesids{now.year}-{now.month}.csv")

    if os.path.exists(store_path):
        try:
            # Read the CSV file
            df = pd.read_csv(store_path, header=None, names=['profile', 'originalName']) # Assuming no header in the CSV
            # Drop duplicate rows
            df_deduplicated = df.drop_duplicates(subset=['profile', 'originalName'], keep='first')
            # Save the de-duplicated DataFrame back to the CSV
            df_deduplicated.to_csv(store_path, index=False, header=False) # Save without index and header
            print(f"De-duplicated profiles saved to {store_path}")
        except pd.errors.EmptyDataError:
            print(f"File {store_path} is empty. No de-duplication needed.")
        except Exception as e:
            print(f"An error occurred during de-duplication: {e}")
    else:
        print(f"File {store_path} not found. Skipping de-duplication.")

