from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

from selenium.webdriver.support import expected_conditions as EC
import time

# Initialize the Firefox webdriver
 



driver = webdriver.Firefox()

# Navigate to the webpage
driver.get('https://www.youtube.com')

# Find all elements with the class name 'ytd-video-meta-block'
wait = WebDriverWait(driver, 10)
element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "ytd-mini-guide-entry-renderer.style-scope:nth-child(2) > a:nth-child(1)")))


driver.find_element(By.CSS_SELECTOR, ".ytd-mini-guide-renderer:nth-child(2) > #endpoint").click()

time.sleep(3)  # Wait for 3 seconds
scrollable_element = driver.find_element(By.CSS_SELECTOR, "html body ytd-app div#content.style-scope.ytd-app ytd-page-manager#page-manager.style-scope.ytd-app ytd-shorts.style-scope.ytd-page-manager div#shorts-container.style-scope.ytd-shorts")
driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', scrollable_element)

time.sleep(3)  # Wait for 3 seconds
scrollable_element = driver.find_element(By.CSS_SELECTOR, "html body ytd-app div#content.style-scope.ytd-app ytd-page-manager#page-manager.style-scope.ytd-app ytd-shorts.style-scope.ytd-page-manager div#shorts-container.style-scope.ytd-shorts")
driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', scrollable_element)
time.sleep(1)  # Wait for 3 seconds
scrollable_element = driver.find_element(By.CSS_SELECTOR, "html body ytd-app div#content.style-scope.ytd-app ytd-page-manager#page-manager.style-scope.ytd-app ytd-shorts.style-scope.ytd-page-manager div#shorts-container.style-scope.ytd-shorts")
driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', scrollable_element)

time.sleep(1)  # Wait for 3 seconds
driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', scrollable_element)
time.sleep(3)  # Wait for 3 secondstime.sleep(3)  # Wait for 3 seconds
scrollable_element = driver.find_element(By.CSS_SELECTOR, "html body ytd-app div#content.style-scope.ytd-app ytd-page-manager#page-manager.style-scope.ytd-app ytd-shorts.style-scope.ytd-page-manager div#shorts-container.style-scope.ytd-shorts")
driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', scrollable_element)

time.sleep(3)  # Wait for 3 seconds
scrollable_element = driver.find_element(By.CSS_SELECTOR, "html body ytd-app div#content.style-scope.ytd-app ytd-page-manager#page-manager.style-scope.ytd-app ytd-shorts.style-scope.ytd-page-manager div#shorts-container.style-scope.ytd-shorts")
driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', scrollable_element)
time.sleep(1)  # Wait for 3 seconds
scrollable_element = driver.find_element(By.CSS_SELECTOR, "html body ytd-app div#content.style-scope.ytd-app ytd-page-manager#page-manager.style-scope.ytd-app ytd-shorts.style-scope.ytd-page-manager div#shorts-container.style-scope.ytd-shorts")
driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', scrollable_element)

time.sleep(1)  # Wait for 3 seconds
driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', scrollable_element)
time.sleep(3)  # Wait for 3 seconds

scrollable_element = driver.find_element(By.CSS_SELECTOR, "html body ytd-app div#content.style-scope.ytd-app ytd-page-manager#page-manager.style-scope.ytd-app ytd-shorts.style-scope.ytd-page-manager div#shorts-container.style-scope.ytd-shorts")
driver.execute_script('arguments[0].scrollTop = arguments[0].scrollHeight;', scrollable_element)

time.sleep(3)  # Wait for 3 seconds

driver.find_element(By.CSS_SELECTOR, ".ytd-mini-guide-renderer:nth-child(1) > #endpoint").click()
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        # Iterate over the elements and get their CSS dat``````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````````a
time.sleep(3)  # Wait for 3 seconds

driver.find_element(By.CSS_SELECTOR, ".ytd-mini-guide-renderer:nth-child(2) > #endpoint").click()

time.sleep(3)  # Wait for 3 seconds
driver.find_element(By.CSS_SELECTOR, ".ytd-mini-guide-renderer:nth-child(1) > #endpoint").click()
time.sleep(3)  # Wait for 3 seconds

driver.find_element(By.CSS_SELECTOR, ".ytd-mini-guide-renderer:nth-child(1) > #endpoint").click()
# Iterate over the elements and get their CSS data
time.sleep(3)  # Wait for 3 seconds

driver.find_element(By.CSS_SELECTOR, ".ytd-mini-guide-renderer:nth-child(2) > #endpoint").click()

time.sleep(3)  # Wait for 3 seconds
driver.find_element(By.CSS_SELECTOR, ".ytd-mini-guide-renderer:nth-child(1) > #endpoint").click()

driver.refresh()

time.sleep(3)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 1000);")

time.sleep(.5)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")
time.sleep(.5)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.5)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.5)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")
    
time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.5)  # Wait for 3 seconds


# Close the driver



elements = driver.find_elements(By.CSS_SELECTOR,"ytd-rich-item-renderer.ytd-rich-grid-renderer")

dataset = []
print(len(elements))
for element in elements:
    # Get the outer HTML of the element
    
    try:
        text = element.text
        text_array = text.splitlines()

        child_element = element.find_element(By.CSS_SELECTOR,"* > div:nth-child(1) > ytd-rich-grid-media:nth-child(1) > div:nth-child(1) > div:nth-child(3) > div:nth-child(1) > a:nth-child(1)")
        link = child_element.get_attribute('href')
        text_array.append(link.rsplit('/', 1)[-1])
        dataset.append(text_array)
    except:
        continue




driver.refresh()

time.sleep(3)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 1000);")

time.sleep(.5)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")
time.sleep(.5)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.5)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.5)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 100000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.8)  # Wait for 3 seconds
driver.execute_script("window.scrollTo(0, 200000);")

time.sleep(.5)  # Wait for 3 seconds


elements = driver.find_elements(By.CSS_SELECTOR,"ytd-rich-item-renderer.ytd-rich-grid-renderer")

dataset = []
print(len(elements))
for element in elements:
    # Get the outer HTML of the element
    
    try:
        text = element.text
        text_array = text.splitlines()

        child_element = element.find_element(By.CSS_SELECTOR,"* > div:nth-child(1) > ytd-rich-grid-media:nth-child(1) > div:nth-child(1) > div:nth-child(3) > div:nth-child(1) > a:nth-child(1)")
        link = child_element.get_attribute('href')
        text_array.append(link.rsplit('/', 1)[-1])
        dataset.append(text_array)
    except:
        continue





import csv
from datetime import datetime
savetime= datetime.now().strftime("%Y-%m-%d%H%M%S")
# Define your headers
headers = ["video_name", "chanel_display_name", "views", 'age', 'chanel_id']  # Replace with your actual headers

# Open the CSV file in write mode
with open('data/output'+savetime+'.csv',  'w', newline='', encoding='utf-8') as file:
    writer = csv.writer(file)

    # Write the headers
    writer.writerow(headers)

    # Write the data
    for data in dataset:
        if len(data) == 5:
            writer.writerow(data)
        else:
            print(len(data))
    driver.close()