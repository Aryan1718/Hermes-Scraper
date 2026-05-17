"""
Simple Tiny Fish scraper.

Usage:
  python3 job_site_tiny_fish.py --url https://example.com
  python3 job_site_tiny_fish.py --url https://example.com --output example.md
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from tinyfish import TinyFish


load_dotenv()


def slugify_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or "site"
    path = parsed.path.strip("/") or "home"
    raw = f"{host}_{path}"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "site"


def default_output_path(url: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"{slugify_url(url)}_{timestamp}.md")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a site with Tiny Fish and save it as Markdown.")
    parser.add_argument("--url", required=True, help="URL to fetch")
    parser.add_argument("--output", help="Optional output Markdown file path")
    args = parser.parse_args()

    api_key = os.getenv("TINYFISH_API_KEY", "").strip()
    if not api_key:
        print("Missing TINYFISH_API_KEY in .env or environment.", file=sys.stderr)
        return 1

    client = TinyFish(api_key=api_key)

    try:
        response = client.fetch.get_contents([args.url], format="markdown")
    except Exception as exc:
        print(f"Fetch failed: {exc}", file=sys.stderr)
        return 1

    if getattr(response, "errors", None):
        first_error = response.errors[0]
        print(f"Fetch failed: {getattr(first_error, 'error', 'Unknown error')}", file=sys.stderr)
        return 1

    results = getattr(response, "results", [])
    if not results:
        print("Tiny Fish returned no results.", file=sys.stderr)
        return 1

    page = results[0]
    markdown_text = getattr(page, "text", "") or ""
    if not markdown_text.strip():
        print("Tiny Fish returned empty content.", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else default_output_path(args.url)
    output_path.write_text(markdown_text, encoding="utf-8")

    print(f"Saved markdown to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
