import scrapy
import csv
from scrapy.crawler import CrawlerProcess
from bs4 import BeautifulSoup, Tag
import time
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
class BlogSpider(scrapy.Spider):  
    
    def __init__(self):
        self.check = 'data\\eeee.csv'
        self.store = 'data\\profiles\\profilesids2024-10 copy.csv'
        import pandas as pd
        df = pd.read_csv(self.check)

        # Drop duplicate rows
        df = df.drop_duplicates()

        # Write the DataFrame back to the CSV file
        df.to_csv(self.check, index=False)
        self.name = 'blogspider'
        self.base= 'https://www.youtube.com/@'
        self.start_urls =[]
        self.profiles = []
        
        with open(self.store, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            profiles = list(reader)


        with open(self.check, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            data = list(reader)
            for row in data[1:]:
                booler = checkProfile(row[4],profiles)
                if not booler: 
                    if row[4][0] == '@':
                        self.start_urls.append(self.base+(row[4][1:]))
                    else:
                        self.addProfile(row[4],row[4])


    def parse(self, response):
        time.sleep(4)
        # Extract data from the new CSS selector
        for element in response.css('link[rel="canonical"]'):
            href = element.attrib['href']
            current_url = response.url
            
            self.addProfile(href.split('/')[-1],current_url.split('/')[-1])
    
    def addProfile(self, profile, orignalName):
        with open(self.store, 'a',newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow([profile,orignalName])



if __name__ == "__main__":
    process = CrawlerProcess({
        'USER_AGENT': 'Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 5.1)'
    })

    process.crawl(BlogSpider)
    process.start() # the script will block here until the crawling is finished

