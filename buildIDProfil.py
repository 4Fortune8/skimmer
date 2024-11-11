import random
import scrapy
import csv
from scrapy.crawler import CrawlerProcess
from bs4 import BeautifulSoup, Tag
import time

profilesData = 'data\\profiles\\profiles2024-11.csv'
profilesids = 'data\\profiles\\profilesids2024-11.csv'
        
        
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
        for sublist in profiles:
            if row in sublist:
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
        self.profiles = []

        with open(profilesData, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            profiles = list(reader)


        with open(profilesids, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            data = list(reader)
            print(data[0])
            for row in data:
                print(row)
                booler = checkProfile(row[0],profiles)
                if not booler:
                    self.start_urls.append(self.base+(row[0]))

    
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

    # Load the CSV file into a DataFrame
    df = pd.read_csv(profilesids)

    # Drop duplicate rows
    df = df.drop_duplicates()
    # Write the DataFrame back to the CSV file
    df.to_csv(profilesids, index=False)


    with open(profilesData, 'r+', newline='', encoding='utf-8') as file:
        reader = csv.reader(file)
        first_row = next(reader, None)
        if first_row != headers:
            # Move the file pointer to the beginning of the file
            file.seek(0)
            # Create a writer object
            writer = csv.writer(file)
            # Write the headers
            writer.writerow(headers)
            # Write the original first row if it exists
            if first_row:
                writer.writerow(first_row)
    process.crawl(BlogSpider)
    process.start() # the script will block here until the crawling is finished

