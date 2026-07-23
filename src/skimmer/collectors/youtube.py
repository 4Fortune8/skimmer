

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.service import Service
from selenium.common.exceptions import TimeoutException, WebDriverException

import time

import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from skimmer.config import PROJECT_ROOT
from skimmer.storage.bronze import insert_youtube_skimmed, refresh_profile_queue
# Initialize the Firefox webdriver
 

def parse_bool_env(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of: 1/0, true/false, yes/no, on/off.")


def resolve_geckodriver_path():
    configured = os.environ.get("GECKODRIVER_PATH")
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return str(path)
        raise RuntimeError(f"GECKODRIVER_PATH does not exist: {path}")

    installed = shutil.which("geckodriver")
    if installed and not installed.startswith("/snap/"):
        return installed

    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        archive_name = "geckodriver-v0.37.0-linux-aarch64.tar.gz"
    elif machine in {"x86_64", "amd64"}:
        archive_name = "geckodriver-v0.37.0-linux64.tar.gz"
    elif machine.startswith("armv7"):
        archive_name = "geckodriver-v0.37.0-linux-arm7hf.tar.gz"
    else:
        raise RuntimeError(
            f"Unsupported architecture for automatic geckodriver download: {machine}"
        )

    target_dir = PROJECT_ROOT / ".drivers"
    target_dir.mkdir(parents=True, exist_ok=True)
    driver_path = target_dir / "geckodriver"
    if driver_path.exists():
        driver_path.chmod(0o755)
        return str(driver_path)

    archive_path = target_dir / archive_name
    download_url = (
        "https://github.com/mozilla/geckodriver/releases/download/v0.37.0/"
        + archive_name
    )
    urllib.request.urlretrieve(download_url, archive_path)
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extract("geckodriver", target_dir)
    archive_path.unlink(missing_ok=True)
    driver_path.chmod(0o755)
    return str(driver_path)


def is_usable_firefox_binary(path):
    try:
        result = subprocess.run(
            [str(path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return False
    output = (result.stdout or "").lower()
    if "snap-confine" in output or "requires the firefox snap" in output:
        return False
    return result.returncode == 0


def resolve_firefox_binary_path():
    configured = os.environ.get("FIREFOX_BINARY_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.exists() and is_usable_firefox_binary(configured_path):
            return str(configured_path)
        raise RuntimeError(f"FIREFOX_BINARY_PATH is not usable: {configured_path}")

    target_dir = PROJECT_ROOT / ".drivers"
    target_dir.mkdir(parents=True, exist_ok=True)

    local_binary = target_dir / "firefox" / "firefox"
    candidates = [
        local_binary,
        Path("/usr/lib/firefox/firefox"),
        Path("/usr/lib/firefox-esr/firefox"),
    ]
    system_firefox = shutil.which("firefox")
    if system_firefox:
        candidates.append(Path(system_firefox))

    for candidate in candidates:
        if candidate.exists() and is_usable_firefox_binary(candidate):
            return str(candidate)

    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        platform_key = "linux64-aarch64"
    elif machine in {"x86_64", "amd64"}:
        platform_key = "linux64"
    else:
        raise RuntimeError(
            f"Unsupported architecture for automatic Firefox download: {machine}"
        )

    archive_path = target_dir / "firefox.tar.xz"
    download_url = (
        "https://download.mozilla.org/?product=firefox-latest-ssl"
        f"&os={platform_key}&lang=en-US"
    )
    urllib.request.urlretrieve(download_url, archive_path)
    if (target_dir / "firefox").exists():
        shutil.rmtree(target_dir / "firefox")
    with tarfile.open(archive_path, "r:xz") as archive:
        archive.extractall(target_dir)
    archive_path.unlink(missing_ok=True)

    if local_binary.exists() and is_usable_firefox_binary(local_binary):
        local_binary.chmod(0o755)
        return str(local_binary)
    raise RuntimeError("Downloaded Firefox binary is not usable in this environment.")


def create_driver():
    options = Options()
    options.set_preference("media.volume_scale", "0.0")
    options.binary_location = resolve_firefox_binary_path()
    headless_env = os.environ.get("YOUTUBE_HEADLESS")
    if headless_env is None:
        has_display = bool(
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        )
        run_headless = not has_display
    else:
        run_headless = parse_bool_env("YOUTUBE_HEADLESS", default=False)
    if run_headless:
        options.add_argument("-headless")
    try:
        return webdriver.Firefox(
            service=Service(resolve_geckodriver_path()),
            options=options,
        )
    except Exception as exc:
        raise RuntimeError(
            "Unable to start Firefox WebDriver. Set GECKODRIVER_PATH and/or "
            "FIREFOX_BINARY_PATH to valid binaries, or allow automatic downloads."
        ) from exc


def parse_int_env(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1.")
    return parsed


def scroll_home_feed(driver, steps=10, delay=1):
    for _ in range(steps):
        driver.execute_script(
            "window.scrollTo(0, document.documentElement.scrollHeight);"
        )
        time.sleep(delay)


def extract_home_records(driver):
    return driver.execute_script(
        """
        return Array.from(document.querySelectorAll('ytd-rich-item-renderer'))
            .map((item) => {
                const channel = item.querySelector(
                    'a[href*="/@"], a[href*="/channel/"], a[href*="/c/"], a[href*="/user/"]'
                );
                const lines = item.innerText
                    .split('\\n')
                    .map((line) => line.trim())
                    .filter((line) => line && line !== '•');
                const viewsIndex = lines.findIndex(
                    (line) => /\\bviews?\\b/i.test(line)
                );

                if (!channel || viewsIndex < 2 || !lines[viewsIndex + 1]) {
                    return null;
                }

                const channelUrl = channel.href;
                const channelPath = new URL(channelUrl).pathname;
                const channelId = channelPath.split('/').filter(Boolean).pop();
                return {
                    video_name: lines[viewsIndex - 2],
                    channel_display_name: lines[viewsIndex - 1],
                    views: lines[viewsIndex],
                    age: lines[viewsIndex + 1],
                    channel_id: channelId,
                    youtube_channel_id: channelPath.startsWith('/channel/')
                        ? channelId
                        : null,
                };
            })
            .filter(Boolean);
        """
    )


def collect_youtube_feed():
    """Render the YouTube homepage and persist its visible feed items."""
    driver = create_driver()
    driver.set_page_load_timeout(45)
    try:
        print("Firefox driver initialized successfully.")
        driver.get("https://www.youtube.com")
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "ytd-mini-guide-entry-renderer:nth-child(2) > a",
                )
            )
        )

        driver.find_element(
            By.CSS_SELECTOR,
            "ytd-mini-guide-entry-renderer:nth-child(2) > a",
        ).click()
        shorts_container = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#shorts-container"))
        )
        for _ in range(10):
            driver.execute_script(
                "arguments[0].scrollTop = arguments[0].scrollHeight;",
                shorts_container,
            )
            time.sleep(1)

        driver.find_element(
            By.CSS_SELECTOR,
            "ytd-mini-guide-entry-renderer:nth-child(1) > a",
        ).click()
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "ytd-rich-item-renderer")
            )
        )
        home_passes = parse_int_env("YOUTUBE_HOME_PASSES", default=3)
        scroll_steps = parse_int_env("YOUTUBE_HOME_SCROLL_STEPS", default=10)
        records = []
        for pass_index in range(home_passes):
            print("pass_index")
            print(pass_index)
            scroll_home_feed(driver, steps=scroll_steps, delay=1)
            records.extend(extract_home_records(driver))
            if pass_index < home_passes - 1:
                try:
                    driver.refresh()
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "ytd-rich-item-renderer")
                        )
                    )
                except (TimeoutException, WebDriverException) as exc:
                    print(
                        f"Refresh failed on pass {pass_index + 1}/{home_passes} "
                        f"({exc.__class__.__name__}); stopping early with "
                        f"{len(records)} records collected so far."
                    )
                    break

        inserted = insert_youtube_skimmed(
            records,
            "https://www.youtube.com",
        )
        refresh_profile_queue()
        print(f"Stored {inserted} YouTube feed records.")
    finally:
        driver.quit()


def main():
    collect_youtube_feed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Legacy exploratory collector retained as inert source history during the
# package migration. The supported implementation is collect_youtube_feed above.
"""
driver = create_driver()


print("Firefox driver initialized successfully.")
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
driver.execute_script("window.scrollTo(-1, 100000);")
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


def scroll_home_feed(driver, passes=5):
    dataset = []
    for _ in range(passes):

        driver.refresh()

        time.sleep(3)  # Wait for 3 seconds
        driver.execute_script("window.scrollTo(0, 1000);")

        time.sleep(.5)  # Wait for 3 seconds
        driver.execute_script("window.scrollTo(0, 100000);")
        time.sleep(.5)  # Wait for 3 seconds
        driver.execute_script("window.scrollTo(0, 100000);")
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


        elements = driver.find_elements(
            By.CSS_SELECTOR,
            "ytd-rich-item-renderer.ytd-rich-grid-renderer",
        )
        for element in elements:
            try:
                text = element.text
                text_array = text.splitlines()
                child_element = element.find_element(
                    By.CSS_SELECTOR,
                    "* > div:nth-child(1) > ytd-rich-grid-media:nth-child(1) > div:nth-child(1) > div:nth-child(3) > div:nth-child(1) > a:nth-child(1)",
                )
                link = child_element.get_attribute('href')
                text_array.append(link.rsplit('/', 1)[-1])
                dataset.append(text_array)
            except:
                continue
    return dataset

dataset = scroll_home_feed(driver, passes=5)
print(len(dataset))





bronze_records = []
for data in dataset:
    if len(data) == 5:
        bronze_records.append(
            {
                "video_name": data[0],
                "channel_display_name": data[1],
                "views": data[2],
                "age": data[3],
                "channel_id": data[4],
            }
        )
    else:
        print(len(data))

insert_youtube_skimmed(bronze_records, "youtube.com")
driver.close()
"""
