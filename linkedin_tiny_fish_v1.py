"""
LinkedIn TinyFish Fetch Test
============================
Fetches the rendered content of a LinkedIn profile using the TinyFish SDK.

Setup:
  pip install tinyfish python-dotenv

  Add your API key to .env:
  TINYFISH_API_KEY=your_api_key_here

Run:
  python3 linkedin_tiny_fish_v1.py --profile-url https://www.linkedin.com/in/example/
  python3 linkedin_tiny_fish_v1.py --profile-url https://www.linkedin.com/in/example/ --output linkedin_fetch.json
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from tinyfish import TinyFish

load_dotenv()


parser = argparse.ArgumentParser()
parser.add_argument("--profile-url", required=True, help="LinkedIn profile URL to fetch")
parser.add_argument("--format", default="markdown", choices=["markdown", "html", "json"])
parser.add_argument("--output", default="linkedin_tinyfish_fetch.json", help="Output JSON path")
args = parser.parse_args()


def main():
    api_key = os.getenv("TINYFISH_API_KEY", "").strip()
    if not api_key:
        print("Missing TINYFISH_API_KEY in .env", file=sys.stderr)
        sys.exit(1)

    client = TinyFish(api_key=api_key)

    response = client.fetch.get_contents(
        [args.profile_url],
        format=args.format,
    )

    result = {
        "requested_url": args.profile_url,
        "format": args.format,
        "results": [],
        "errors": [],
    }

    for page in response.results:
        page_payload = {
            "url": getattr(page, "url", None),
            "final_url": getattr(page, "final_url", None),
            "title": getattr(page, "title", None),
            "description": getattr(page, "description", None),
            "text": getattr(page, "text", None),
            "latency_ms": getattr(page, "latency_ms", None),
        }
        result["results"].append(page_payload)
        print(page.text)

    for item in response.errors:
        error_payload = {
            "url": getattr(item, "url", None),
            "error": getattr(item, "error", None),
        }
        result["errors"].append(error_payload)

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)

    print(f"\nSaved fetch response to {args.output}")


if __name__ == "__main__":
    main()
