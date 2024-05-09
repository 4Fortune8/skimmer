import scrapy
import csv
from scrapy.crawler import CrawlerProcess



class BlogSpider(scrapy.Spider):    
    name = 'blogspider'
    base= 'https://socialblade.com/youtube/c/'
    start_urls =[]

    with open('data\output2024-05-08-225040.csv', 'r', encoding='utf-8') as file:
        reader = csv.reader(file)
        data = list(reader)
        for row in data[1:2]:
            start_urls.append(base+(row[4]))
    
        
    def parse(self, response):
        for title in response.css('#socialblade-user-content > div:nth-child(3) > div:nth-child(1)'):
            print("LOOK HERE THIS IS IT")
            text=  title.css('::text').extract()
            data= []
            color= title.css('p>sup>span::attr(style)').extract()
            if color[0] == "color:#e53b00;":
                text[2]='-'+text[2][:-1]
                print(text[2])
                data.append((text[1],text[2]))
            data.append((text[1],text[2]))
            yield data



if __name__ == "__main__":
    process = CrawlerProcess({
        'USER_AGENT': 'Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 5.1)'
    })

    process.crawl(BlogSpider)
    process.start() # the script will block here until the crawling is finished

