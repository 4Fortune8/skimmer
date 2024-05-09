import scrapy
import csv
from scrapy.crawler import CrawlerProcess
from bs4 import BeautifulSoup, Tag

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
    print(row, "and", profiles)
    try:
        return any(row == sublist[0] for sublist in profiles)
    except:
        return False

def convert_to_number(s):
            print(s)

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
        self.base= 'https://socialblade.com/youtube/c/@'
        self.start_urls =[]
        self.profiles = []
        self.currentAccount = ''


       


        with open('data\profiles\profiles2024-5.csv', 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            profiles = list(reader)


        with open('data\output2024-05-08-225040.csv', 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            data = list(reader)
            for row in data[1:3]:
                booler = checkProfile(row[4],profiles)
                if not booler:
                    print("WAAAAAAAAAAAATTTTTTTATAT")
                    self.currentAccount = row[4]
                    self.start_urls.append(self.base+(row[4]))

    
    def parse(self, response):
        
        
        for title in response.css('#socialblade-user-content > div:nth-child(3) > div:nth-child(1)>p:nth-child(1)'):
            
            print("LOOK HERE THIS IS IT")
            text=  title.css('::text').extract()
            data= []
            print(text)
            text = extract_values(text)
            
            print(text)
            text[0] = text[0].strip()  # Remove leading and trailing whitespace
            print(text[0])  # Outputs: 3600
            subscribers = convert_to_number(text[0])
            print(subscribers)  # Outputs: 3600
            
            color= title.css('sup>span::attr(style)').extract()
            if color[0] == "color:#e53b00;":
                text[1]='-'+text[1][:-1]
                print(text[1])
                data.append((self.currentAccount,subscribers,text[1]))
            else:
                data.append((self.currentAccount,subscribers,text[1]))
            yield self.addProfile(data)
            
    
    
    def addProfile(self, profile):
        self.profiles.append(profile)
        with open('data\profiles\profiles2024-5.csv', 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerows(self.profiles)



if __name__ == "__main__":
    process = CrawlerProcess({
        'USER_AGENT': 'Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 5.1)'
    })

    process.crawl(BlogSpider)
    process.start() # the script will block here until the crawling is finished

