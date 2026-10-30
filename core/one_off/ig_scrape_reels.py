"""Scrape all reel/video URLs from an Instagram profile using Selenium."""

import os
import time
import json
import tempfile
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "ig_urls.txt")
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "cookies.txt")


def load_cookies(driver):
    """Load cookies from cookies.txt into the driver."""
    if not os.path.exists(COOKIES_FILE):
        return False
    driver.get("https://www.instagram.com/")
    time.sleep(2)
    with open(COOKIES_FILE) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) < 7:
                continue
            cookie = {
                "domain": parts[0],
                "name": parts[5],
                "value": parts[6],
                "path": parts[2],
                "secure": parts[3] == "TRUE",
            }
            try:
                driver.add_cookie(cookie)
            except Exception:
                pass
    return True


def scrape(username):
    opts = Options()
    tmp_profile = os.path.join(tempfile.gettempdir(), "ig_chrome_profile")
    opts.add_argument(f"--user-data-dir={tmp_profile}")

    driver = webdriver.Chrome(options=opts)

    # Try loading cookies first
    has_cookies = load_cookies(driver)

    # Go to reels tab
    url = f"https://www.instagram.com/{username}/reels/"
    driver.get(url)
    time.sleep(4)

    # Check if logged in
    if "login" in driver.current_url.lower():
        print("Not logged in. Please log in in the browser window.")
        print("Once you can see the profile reels, press ENTER here...")
        input()
        driver.get(url)
        time.sleep(4)

    # Scroll to load all reels
    print("Scrolling to load all reels...")
    last_height = 0
    urls = set()
    no_change_count = 0

    while True:
        # Collect reel links
        links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/reel/']")
        for link in links:
            href = link.get_attribute("href")
            if href:
                urls.add(href.split("?")[0])

        print(f"  Found {len(urls)} reels so far...")

        # Scroll down
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            no_change_count += 1
            if no_change_count >= 3:
                break
        else:
            no_change_count = 0
        last_height = new_height

    driver.quit()

    # Save URLs
    with open(OUT, "w") as f:
        for u in sorted(urls):
            f.write(u + "\n")

    print(f"\nDone! Saved {len(urls)} reel URLs to {OUT}")


if __name__ == "__main__":
    import sys
    username = sys.argv[1] if len(sys.argv) > 1 else "melvin_gatica_bachata"
    scrape(username)
