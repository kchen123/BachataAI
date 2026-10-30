"""Download Instagram reels by intercepting CDN URLs from network logs."""

import os
import time
import json
import tempfile
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

URLS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "ig_urls.txt")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "downloads", "melvin_gatica")
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "cookies.txt")


def get_video_cdn_url(driver, page_url):
    """Visit a reel page and extract the CDN video URL from network logs."""
    # Clear logs
    try:
        driver.get_log("performance")
    except Exception:
        pass

    driver.get(page_url)
    time.sleep(5)

    # Parse network logs for video CDN URLs
    logs = driver.get_log("performance")
    cdn_urls = []
    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
            if msg["method"] == "Network.responseReceived":
                url = msg["params"]["response"]["url"]
                mime = msg["params"]["response"].get("mimeType", "")
                if "video" in mime or (".mp4" in url and "scontent" in url):
                    cdn_urls.append(url)
            elif msg["method"] == "Network.requestWillBeSent":
                url = msg["params"]["request"]["url"]
                if "scontent" in url and (".mp4" in url or "video" in url.lower()):
                    cdn_urls.append(url)
        except (KeyError, json.JSONDecodeError):
            continue

    # Also try extracting from page source
    if not cdn_urls:
        source = driver.page_source
        import re
        matches = re.findall(r'"(https://scontent[^"]*?\.mp4[^"]*?)"', source)
        cdn_urls.extend(matches)
        # Also look for video_url in JSON
        matches = re.findall(r'"video_url"\s*:\s*"(https://[^"]+)"', source)
        cdn_urls.extend(matches)

    return cdn_urls[0] if cdn_urls else None


def download_reels():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    with open(URLS_FILE) as f:
        urls = [line.strip() for line in f if line.strip()]

    print(f"Downloading {len(urls)} reels to {DOWNLOAD_DIR}")

    opts = Options()
    tmp_profile = os.path.join(tempfile.gettempdir(), "ig_chrome_profile")
    opts.add_argument(f"--user-data-dir={tmp_profile}")
    opts.add_argument("--headless=new")
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=opts)

    # Load cookies
    driver.get("https://www.instagram.com/")
    time.sleep(2)
    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.strip().split("\t")
                if len(parts) < 7:
                    continue
                try:
                    driver.add_cookie({
                        "domain": parts[0],
                        "name": parts[5],
                        "value": parts[6],
                        "path": parts[2],
                        "secure": parts[3] == "TRUE",
                    })
                except Exception:
                    pass

    downloaded = 0
    failed = 0

    for i, url in enumerate(urls):
        reel_id = url.rstrip("/").split("/")[-1]
        out_path = os.path.join(DOWNLOAD_DIR, f"{reel_id}.mp4")

        if os.path.exists(out_path):
            print(f"  [{i+1}/{len(urls)}] {reel_id} — already exists, skipping")
            downloaded += 1
            continue

        try:
            cdn_url = get_video_cdn_url(driver, url)

            if not cdn_url:
                print(f"  [{i+1}/{len(urls)}] {reel_id} — no CDN URL found")
                failed += 1
                continue

            resp = requests.get(cdn_url, stream=True, timeout=30)
            if resp.status_code == 200:
                with open(out_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                size_mb = os.path.getsize(out_path) / (1024 * 1024)
                print(f"  [{i+1}/{len(urls)}] {reel_id} — {size_mb:.1f}MB")
                downloaded += 1
            else:
                print(f"  [{i+1}/{len(urls)}] {reel_id} — HTTP {resp.status_code}")
                failed += 1

        except Exception as e:
            print(f"  [{i+1}/{len(urls)}] {reel_id} — error: {e}")
            failed += 1

    driver.quit()
    print(f"\nDone! Downloaded: {downloaded}, Failed: {failed}")


if __name__ == "__main__":
    download_reels()
