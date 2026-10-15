"""Download Instagram videos using gallery-dl with browser cookies."""

import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "..", "downloads")


def download(url, output_dir=None):
    if output_dir is None:
        output_dir = DOWNLOAD_DIR
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        sys.executable, "-m", "gallery_dl",
        "--cookies-from-browser", "chrome",
        "-d", output_dir,
        url,
    ]

    print(f"Downloading: {url}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print("Download complete!")
        print(result.stdout)
    else:
        print(f"Error:\n{result.stderr}")

    return result.returncode == 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python instagram_downloader.py <instagram_url> [output_dir]")
        print("  Note: Close Chrome before running (cookies can't be read while Chrome is open)")
        sys.exit(1)

    url = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    download(url, out)
