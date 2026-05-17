"""
Jobright.ai Recommendation Link Scraper
======================================
Logs into Jobright, opens the recommendations page, collects up to N
recommended job links, and saves them as JSON.

SETUP:
  pip install "scrapling[fetchers]" python-dotenv
  scrapling install

  # Create .env file with your credentials:
  JOBRIGHT_EMAIL=your@email.com
  JOBRIGHT_PASSWORD=yourpassword

RUN:
  python jobright_V2.py
  python jobright_V2.py --jobs 20
  python jobright_V2.py --debug
  python jobright_V2.py --xvfb
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time

from dotenv import load_dotenv

load_dotenv()

EMAIL = os.getenv("JOBRIGHT_EMAIL", "").strip()
PASSWORD = os.getenv("JOBRIGHT_PASSWORD", "").strip()


parser = argparse.ArgumentParser()
parser.add_argument("--xvfb", action="store_true", help="Auto-start Xvfb virtual display")
parser.add_argument("--jobs", type=int, default=20, help="Number of recommendation links to collect")
parser.add_argument("--wait", type=int, default=6000, help="Wait ms for recommendations page to render")
parser.add_argument("--output", default="jobright_recommend_jobs.json", help="Output file")
parser.add_argument("--debug", action="store_true", help="Save debug HTML files")
args = parser.parse_args()


xvfb_proc = None


def start_xvfb(display=":99", res="1920x1080x24"):
    global xvfb_proc
    print(f"🖥️  Starting Xvfb on display {display}...")
    try:
        xvfb_proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", res],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        os.environ["DISPLAY"] = display
        print(f"✅ Xvfb started (PID {xvfb_proc.pid})\n")
        return True
    except FileNotFoundError:
        print("❌ Xvfb not found. Install: sudo apt-get install -y xvfb\n")
        return False


def stop_xvfb():
    if xvfb_proc:
        xvfb_proc.terminate()
        print("🖥️  Xvfb stopped.")


def get_html_content(target):
    html_content = getattr(target, "html_content", None)
    if isinstance(html_content, str) and html_content:
        return html_content

    body = getattr(target, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return body.decode("utf-8", errors="replace")

    content_method = getattr(target, "content", None)
    if callable(content_method):
        return content_method()

    html_attr = getattr(target, "html", None)
    if isinstance(html_attr, str) and html_attr:
        return html_attr

    raise AttributeError(f"Could not extract HTML content from {type(target).__name__}")


def dismiss_popups(page):
    selectors = [
        "button:has-text('EXIT')",
        "button:has-text('Close')",
        "button:has-text('Not now')",
    ]
    for selector in selectors:
        btn = page.query_selector(selector)
        if not btn:
            continue
        try:
            btn.click()
            page.wait_for_timeout(400)
        except Exception:
            pass


def inject_recommendation_payload(page, payload):
    page.evaluate(
        """(payload) => {
            const existing = document.querySelector("#jobright-helper-recommend-list");
            if (existing) existing.remove();

            const script = document.createElement("script");
            script.id = "jobright-helper-recommend-list";
            script.type = "application/json";
            script.textContent = JSON.stringify(payload);
            document.body.appendChild(script);
        }""",
        payload,
    )


def do_homepage_login(page):
    print("   ⏳ Waiting for homepage to fully load...")
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(800)

    print("   🖱️  Opening auth dialog...")
    auth_trigger_selectors = [
        "text=SIGN IN",
        "text=Sign In",
        "text=Sign in",
        "button:has-text('JOIN NOW')",
        "button:has-text('Try For Free')",
    ]

    auth_trigger = None
    for selector in auth_trigger_selectors:
        auth_trigger = page.query_selector(selector)
        if auth_trigger:
            print(f"   ✅ Found auth trigger: {selector}")
            auth_trigger.click()
            page.wait_for_timeout(700)
            break

    if not auth_trigger:
        print("   ❌ No auth trigger found on homepage.")
        if args.debug:
            page.screenshot(path="debug_v2_no_auth_trigger.png")
        return

    switch_to_signin = (
        page.query_selector("button:has-text('Already a member? Sign in now')")
        or page.query_selector("button:has-text('Sign in now')")
        or page.query_selector("button:has-text('Already a member')")
        or page.query_selector("button:has-text('Not a member? Sign up now')")
    )
    if switch_to_signin and "not a member" not in (switch_to_signin.inner_text() or "").strip().lower():
        print("   🔁 Switching auth dialog to sign in...")
        switch_to_signin.click()
        page.wait_for_timeout(700)

    email_selector = "input[placeholder='Email'], input[placeholder*='email' i], input[type='email'], input[name='email']"
    password_selector = "input[placeholder='Password'], input[type='password']"

    print("   🔐 Filling login form...")
    page.wait_for_selector(email_selector, timeout=10000)
    page.wait_for_selector(password_selector, timeout=10000)

    email_input = page.query_selector(email_selector)
    password_input = page.query_selector(password_selector)
    if not email_input or not password_input:
        raise RuntimeError("Could not find both email and password inputs.")

    email_input.click()
    email_input.fill("")
    email_input.fill(EMAIL)

    password_input.click()
    password_input.fill("")
    password_input.fill(PASSWORD)

    submit_btn = (
        page.query_selector("button[type='submit']:has-text('SIGN IN')")
        or page.query_selector("button[type='submit']:has-text('Sign In')")
        or page.query_selector("button:has-text('SIGN IN')")
        or page.query_selector("button:has-text('Sign In')")
        or page.query_selector("button:has-text('Sign in')")
    )

    print("   🚀 Submitting login form...")
    if submit_btn:
        submit_btn.click()
    else:
        password_input.press("Enter")

    print("   ⏳ Waiting for redirect after login...")
    try:
        page.wait_for_url("**/jobs/recommend**", timeout=20000)
        print("   ✅ Redirected to jobs page!")
    except Exception:
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(1200)
        print(f"   Current URL after login: {page.url}")

    if args.debug:
        page.screenshot(path="debug_v2_after_login.png")


def collect_jobs_from_dom(page, limit):
    return page.evaluate(
        """(limit) => {
            const items = [];
            const seen = new Set();
            const links = Array.from(document.querySelectorAll("a[href*='/jobs/info/']"));
            for (const link of links) {
                const href = link.href || "";
                if (!href || seen.has(href)) continue;
                seen.add(href);

                const title = (link.innerText || link.textContent || "").replace(/\\s+/g, " ").trim();
                const card = link.closest("article, li, div");
                const cardText = card
                    ? (card.innerText || card.textContent || "").replace(/\\s+/g, " ").trim()
                    : "";

                items.push({
                    title: title || cardText.slice(0, 180) || "N/A",
                    jobright_url: href,
                    preview_text: cardText.slice(0, 300),
                });

                if (items.length >= limit) break;
            }
            return items;
        }""",
        limit,
    )


def fetch_recommendations_via_api(page, limit):
    return page.evaluate(
        """async (limit) => {
            const count = 10;
            const allJobs = [];
            let position = 0;
            let refresh = true;

            while (allJobs.length < limit) {
                const params = new URLSearchParams({
                    refresh: String(refresh),
                    sortCondition: "0",
                    position: String(position),
                    count: String(count),
                    syncRerank: "false",
                });

                const response = await fetch(`/swan/recommend/list/jobs?${params.toString()}`, {
                    method: "GET",
                    credentials: "include",
                    headers: {
                        "accept": "application/json, text/plain, */*",
                        "x-requested-with": "XMLHttpRequest",
                    },
                });

                if (!response.ok) {
                    return {
                        error: `Recommendation API returned ${response.status}`,
                        jobs: allJobs,
                    };
                }

                const data = await response.json();
                const batch = data?.result?.jobList || [];
                if (!batch.length) break;

                for (const item of batch) {
                    const job = item?.jobResult || {};
                    if (!job.jobId) continue;

                    allJobs.push({
                        rank: allJobs.length + 1,
                        title: job.jobTitle || "N/A",
                        company: item?.companyResult?.companyName || "",
                        published_text: job.publishTimeDesc || "",
                        jobright_url: `https://jobright.ai/jobs/info/${job.jobId}`,
                    });

                    if (allJobs.length >= limit) break;
                }

                if (batch.length < count) break;
                position += count;
                refresh = false;
            }

            return {
                jobs: allJobs,
                fetched: allJobs.length,
                nextPosition: position,
            };
        }""",
        limit,
    )


def scroll_until_jobs_loaded(page, limit):
    jobs = []
    previous_count = 0

    for attempt in range(8):
        dismiss_popups(page)
        page.wait_for_timeout(300)
        jobs = collect_jobs_from_dom(page, limit)
        print(f"   📌 Found {len(jobs)} unique job links so far...")
        if len(jobs) >= limit:
            return jobs

        if len(jobs) == previous_count:
            page.wait_for_timeout(1000)
        previous_count = len(jobs)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(900)
        page.evaluate("window.scrollBy(0, -300)")
        page.wait_for_timeout(400)

    return jobs


def parse_recommendation_jobs(response, limit):
    payload_text = response.css("script#jobright-helper-recommend-list::text").get()
    if payload_text:
        payload = json.loads(html.unescape(payload_text.strip()))
        jobs = payload.get("jobs", [])
        return jobs[:limit]

    jobs = []
    seen = set()

    for anchor in response.css("a[href*='/jobs/info/']"):
        href = anchor.attrib.get("href", "").strip()
        if not href:
            continue
        if href.startswith("/"):
            href = "https://jobright.ai" + href
        if href in seen:
            continue
        seen.add(href)

        title = " ".join(anchor.css("*::text").getall()).strip()
        title = re.sub(r"\s+", " ", title)
        jobs.append(
            {
                "rank": len(jobs) + 1,
                "title": title or "N/A",
                "jobright_url": href,
            }
        )
        if len(jobs) >= limit:
            return jobs

    html_doc = get_html_content(response)
    for href in re.findall(r'https://jobright\.ai/jobs/info/[^"\'\s<]+', html_doc):
        if href in seen:
            continue
        seen.add(href)
        jobs.append(
            {
                "rank": len(jobs) + 1,
                "title": "N/A",
                "jobright_url": href,
            }
        )
        if len(jobs) >= limit:
            break

    return jobs


def login_and_open_recommendations(page):
    do_homepage_login(page)
    if "/jobs/recommend" not in page.url:
        print("   🔁 Navigating to recommendations page after login...")
        page.goto("https://jobright.ai/jobs/recommend", wait_until="networkidle")

    print("   📄 Staying on recommendations page...")
    page.wait_for_url("**/jobs/recommend**", timeout=20000)
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(max(args.wait, 2500))
    dismiss_popups(page)

    api_payload = fetch_recommendations_via_api(page, args.jobs)
    api_jobs = api_payload.get("jobs", [])
    if api_jobs:
        inject_recommendation_payload(page, api_payload)
        print(f"   ✅ Loaded {len(api_jobs)} jobs from the recommendation API.")
    else:
        print("   ⚠️  Recommendation API returned no jobs. Falling back to DOM scrolling...")
        jobs = scroll_until_jobs_loaded(page, args.jobs)
        inject_recommendation_payload(page, {"jobs": jobs, "source": "dom_fallback"})
        print(f"   ✅ Prepared recommendations page with {len(jobs)} visible job links.")

    if args.debug:
        page.screenshot(path="debug_v2_recommendations.png")


def main():
    if not EMAIL or not PASSWORD:
        print("❌ Missing credentials! Create a .env file:\n")
        print("   JOBRIGHT_EMAIL=your@email.com")
        print("   JOBRIGHT_PASSWORD=yourpassword\n")
        sys.exit(1)

    print("=" * 65)
    print("  Jobright.ai Scraper  |  Recommendations Link Collector")
    print("=" * 65)
    print(f"  Account : {EMAIL}")
    print(f"  Jobs    : {args.jobs}")
    print(f"  Wait    : {args.wait}ms")
    print(f"  Debug   : {args.debug}")
    print("=" * 65 + "\n")

    if args.xvfb and not start_xvfb():
        sys.exit(1)

    try:
        from scrapling.fetchers import StealthyFetcher

        print("STEP 1 → Login and load the recommendations page\n")
        recommend_page = StealthyFetcher.fetch(
            "https://jobright.ai",
            headless=False,
            network_idle=True,
            wait=3000,
            page_action=login_and_open_recommendations,
        )

        current_url = str(recommend_page.url)
        print(f"\n   Final URL after session flow: {current_url}")
        if "/jobs/recommend" not in current_url:
            print("\n❌ Did not reach the recommendations page.")
            if args.debug:
                with open("debug_v2_recommend_fail.html", "w", encoding="utf-8") as handle:
                    handle.write(get_html_content(recommend_page))
                print("   Saved debug_v2_recommend_fail.html")
            sys.exit(1)

        print("\nSTEP 2 → Extracting recommendation links\n")
        print(f"   HTTP {recommend_page.status} | {recommend_page.url}\n")

        jobs = parse_recommendation_jobs(recommend_page, args.jobs)
        if not jobs:
            print("❌ No recommendation links were extracted.")
            if args.debug:
                with open("debug_v2_recommendations.html", "w", encoding="utf-8") as handle:
                    handle.write(get_html_content(recommend_page))
                print("   Saved debug_v2_recommendations.html")
            sys.exit(1)

        payload = {
            "source_url": str(recommend_page.url),
            "count": len(jobs),
            "jobs": jobs,
        }

        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

        print(f"✅ Collected {len(jobs)} recommendation links.")
        print(f"💾 Saved → {args.output}")

    finally:
        stop_xvfb()


if __name__ == "__main__":
    main()
