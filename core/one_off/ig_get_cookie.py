"""Open Chrome, let user log into Instagram, then export cookies."""

import os
import time
import tempfile
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "cookies.txt")


def export():
    opts = Options()
    tmp_profile = os.path.join(tempfile.gettempdir(), "ig_chrome_profile")
    opts.add_argument(f"--user-data-dir={tmp_profile}")

    driver = webdriver.Chrome(options=opts)
    driver.get("https://www.instagram.com/accounts/login/")

    print("Log into Instagram in the browser window that just opened.")
    print("Once you're on the Instagram home feed, press ENTER here...")
    input()

    cookies = driver.get_cookies()
    driver.quit()

    with open(OUT, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain = c.get("domain", "")
            dot = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = str(int(c.get("expiry", 0)))
            name = c.get("name", "")
            value = c.get("value", "")
            f.write(f"{domain}\t{dot}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")

    print(f"Exported {len(cookies)} cookies to {OUT}")


if __name__ == "__main__":
    export()
