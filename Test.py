"""
Jobright.ai Job Scraper - Modal Login Flow
==========================================
The correct flow:
  1. Open jobright.ai homepage
  2. Open the auth modal from the homepage
  3. Switch from signup to sign-in if needed
  4. Fill email + password in the modal
  5. Submit → redirects to /jobs/recommend
  6. Open the first recommended job and scrape its detail page

SETUP:
  pip install "scrapling[fetchers]" python-dotenv
  scrapling install

  # Create .env file with your credentials:
  JOBRIGHT_EMAIL=your@email.com
  JOBRIGHT_PASSWORD=yourpassword

RUN:
  python Test.py            # local machine
  xvfb-run python Test.py   # linux server
  python Test.py --xvfb     # auto-start Xvfb
  python Test.py --debug    # save debug HTML
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

EMAIL    = os.getenv("JOBRIGHT_EMAIL", "").strip()
PASSWORD = os.getenv("JOBRIGHT_PASSWORD", "").strip()


# ─── CLI Args ─────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--xvfb",   action="store_true", help="Auto-start Xvfb virtual display")
parser.add_argument("--jobs",   type=int, default=1,                  help="Reserved for future multi-job support")
parser.add_argument("--wait",   type=int, default=6000,               help="Wait ms for jobs page to render")
parser.add_argument("--output", default="jobright_first_job.json",    help="Output file")
parser.add_argument("--debug",  action="store_true",                  help="Save debug HTML files")
args = parser.parse_args()


# ─── Xvfb ─────────────────────────────────────────────────────────────────────

xvfb_proc = None

def start_xvfb(display=":99", res="1920x1080x24"):
    global xvfb_proc
    print(f"🖥️  Starting Xvfb on display {display}...")
    try:
        xvfb_proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", res],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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


# ─── Playwright Page Actions ──────────────────────────────────────────────────

def do_homepage_login(page):
    """
    Full login flow inside the Playwright browser:
      1. Wait for homepage to load
      2. Open the auth dialog from the homepage
      3. Switch from signup to sign-in when the site opens signup first
      4. Fill email + password in the sign-in modal
      5. Wait for redirect to dashboard / jobs page
    """
    print("   ⏳ Waiting for homepage to fully load...")
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(800)

    # ── Step A: Open auth modal ────────────────────────────────────
    print("   🖱️  Opening auth dialog...")
    auth_trigger_selectors = [
        "text=SIGN IN",
        "text=Sign In",
        "text=Sign in",
        "button:has-text('JOIN NOW')",
        "button:has-text('Try For Free')",
    ]

    auth_trigger = None
    for sel in auth_trigger_selectors:
        auth_trigger = page.query_selector(sel)
        if auth_trigger:
            print(f"   ✅ Found auth trigger: {sel}")
            auth_trigger.click()
            page.wait_for_timeout(700)
            break

    if not auth_trigger:
        print("   ⚠️  No auth trigger found on homepage.")
        if args.debug:
            page.screenshot(path="debug_no_auth_trigger.png")
        return

    # Some homepage buttons open signup first. Switch to the login form explicitly.
    switch_to_signin = (
        page.query_selector("button:has-text('Already a member? Sign in now')")
        or page.query_selector("button:has-text('Sign in now')")
        or page.query_selector("button:has-text('Already a member')")
        or page.query_selector("button:has-text('Not a member? Sign up now')")
    )
    if switch_to_signin and "not a member" not in (switch_to_signin.inner_text() or "").strip().lower():
        print("   🔁 Auth dialog opened in signup mode — switching to sign in...")
        switch_to_signin.click()
        page.wait_for_timeout(700)

    # ── Step B: Fill login modal / form ───────────────────────────
    print("   🔐 Looking for sign-in fields in modal...")
    email_selector = "input[placeholder='Email'], input[placeholder*='email' i], input[type='email'], input[name='email']"
    password_selector = "input[placeholder='Password'], input[type='password']"

    try:
        page.wait_for_selector(email_selector, timeout=10000)
        page.wait_for_selector(password_selector, timeout=10000)
    except Exception:
        print("   ⚠️  Sign-in fields did not appear. Saving screenshot...")
        if args.debug:
            page.screenshot(path="debug_signin_fields.png")
        return

    email_input = page.query_selector(email_selector)
    password_input = page.query_selector(password_selector)

    if not email_input or not password_input:
        print("   ❌ Could not find both sign-in inputs.")
        return

    email_input.click()
    email_input.fill("")
    email_input.fill(EMAIL)
    print(f"   ✅ Email entered: {EMAIL}")

    page.wait_for_timeout(100)

    password_input.click()
    password_input.fill("")
    password_input.fill(PASSWORD)
    print("   ✅ Password entered.")

    page.wait_for_timeout(150)

    # ── Step C: Submit ─────────────────────────────────────────────
    print("   🚀 Submitting login form...")
    submit_btn = (
        page.query_selector("button[type='submit']:has-text('SIGN IN')")
        or page.query_selector("button[type='submit']:has-text('Sign In')")
        or page.query_selector("button:has-text('SIGN IN')")
        or page.query_selector("button:has-text('Sign In')")
        or page.query_selector("button:has-text('Sign in')")
    )

    if submit_btn:
        submit_btn.click()
    else:
        password_input.press("Enter")

    # ── Step E: Wait for post-login navigation ─────────────────────
    print("   ⏳ Waiting for redirect after login...")
    try:
        page.wait_for_url("**/jobs/recommend**", timeout=20000)
        print("   ✅ Redirected to jobs page!")
    except Exception:
        # May redirect to dashboard or home instead of directly to /jobs
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(1200)
        print(f"   Current URL after login: {page.url}")

    if args.debug:
        page.screenshot(path="debug_after_login.png")
        print("   📸 Screenshot saved: debug_after_login.png")


def scroll_and_wait(page):
    """Scroll down progressively to trigger lazy-loaded job cards."""
    page.wait_for_timeout(500)
    for y in [500, 1100, 1800]:
        page.evaluate(f"window.scrollTo(0, {y})")
        page.wait_for_timeout(250)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(250)


def dismiss_detail_modals(page):
    """Close popups that can block clicks on the job detail page."""
    modal_buttons = [
        "button:has-text('EXIT')",
        "button:has-text('Close')",
        "button:has-text('Not now')",
    ]
    for sel in modal_buttons:
        btn = page.query_selector(sel)
        if btn:
            try:
                btn.click()
                page.wait_for_timeout(500)
            except Exception:
                pass


def get_html_content(target):
    """Return HTML from either a Playwright page or a Scrapling response."""
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


def open_first_job_from_recommendations(page):
    """Open the first visible recommended job while staying in the same session."""
    print("   📄 Opening the first recommended job...")
    page.wait_for_url("**/jobs/recommend**", timeout=20000)
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(max(args.wait, 2500))
    dismiss_detail_modals(page)
    scroll_and_wait(page)

    first_href = None

    for attempt in range(2):
        first_href = page.evaluate(
            """() => {
                const seen = new Set();
                const links = Array.from(document.querySelectorAll("a[href*='/jobs/info/']"));
                for (const link of links) {
                    const href = link.getAttribute("href") || "";
                    if (!href || seen.has(href)) continue;
                    seen.add(href);
                    const text = (link.innerText || link.textContent || "").trim();
                    if (text.length > 30) return link.href;
                }
                return links[0]?.href || null;
            }"""
        )
        if first_href:
            break

        print(f"   ⏳ Job links not ready yet (attempt {attempt + 1}/2). Waiting...")
        page.wait_for_timeout(1500)
        scroll_and_wait(page)

    if not first_href:
        html_doc = get_html_content(page)
        match = re.search(r'https://jobright\\.ai/jobs/info/[^"\\\'\\s<]+', html_doc)
        if not match:
            match = re.search(r'/jobs/info/[^"\\\'\\s<]+', html_doc)
        if match:
            first_href = match.group(0)
            if first_href.startswith("/"):
                first_href = "https://jobright.ai" + first_href

    if not first_href:
        print("   ❌ Could not resolve the first job link.")
        if args.debug:
            with open("debug_recommend_page.html", "w", encoding="utf-8") as f:
                f.write(get_html_content(page))
            page.screenshot(path="debug_no_job_links.png")
            print("   Saved debug_recommend_page.html and debug_no_job_links.png")
        return

    print(f"   ✅ First job link: {first_href}")
    page.goto(first_href, wait_until="networkidle")
    page.wait_for_timeout(800)
    dismiss_detail_modals(page)
    page.wait_for_selector(
        "script#jobright-helper-job-detail-info",
        timeout=8000,
        state="attached",
    )
    print("   ✅ Job detail page loaded.")


def login_and_open_first_job(page):
    do_homepage_login(page)

    if "/jobs/recommend" not in page.url:
        print("   🔁 Navigating to recommendations page after login...")
        page.goto("https://jobright.ai/jobs/recommend", wait_until="networkidle")
        page.wait_for_timeout(800)

    open_first_job_from_recommendations(page)


# ─── Detail Extraction ────────────────────────────────────────────────────────

def detect_job_cards(page):
    candidates = [
        "div[class*='JobCard']",
        "div[class*='job-card']",
        "div[class*='jobCard']",
        "div[class*='job_card']",
        "div[class*='JobItem']",
        "div[class*='job-item']",
        "li[class*='job']",
        "article[class*='job']",
        "[data-testid*='job-card']",
        "[data-testid*='jobCard']",
        "div[class*='card']",
        "div[class*='Card']",
    ]
    for sel in candidates:
        found = page.css(sel)
        real  = [el for el in found if len(" ".join(el.css("*::text").getall())) > 30]
        if real:
            print(f"   ✅ Selector '{sel}' → {len(real)} cards")
            return real
    return []


def extract(card, *selectors, default="N/A"):
    for sel in selectors:
        val = card.css(sel).get()
        if val and val.strip():
            return val.strip()
    return default


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        return [part for part in parts if part]
    return [value]


def extract_job_payload(page):
    html_doc = get_html_content(page)
    match = re.search(
        r'<script[^>]+id=["\']jobright-helper-job-detail-info["\'][^>]*>\s*(.*?)\s*</script>',
        html_doc,
        re.S,
    )
    payload_text = match.group(1) if match else None

    if not payload_text:
        payload_text = page.css("script#jobright-helper-job-detail-info::text").get()

    if not payload_text:
        raise ValueError("Could not find embedded job detail JSON.")

    return json.loads(html.unescape(payload_text.strip()))


def parse_job_detail(page):
    print("\n🔍 Parsing first recommended job detail page...")
    payload = extract_job_payload(page)
    job = payload.get("jobResult", {})
    company = payload.get("companyResult", {})

    detail = {
        "job_url": str(page.url),
        "match_score": payload.get("displayScore"),
        "match_rank": payload.get("rankDesc"),
        "job": {
            "id": job.get("jobId"),
            "title": job.get("jobTitle"),
            "company": company.get("companyName"),
            "seniority": job.get("jobSeniority"),
            "location": job.get("jobLocation"),
            "work_model": job.get("workModel"),
            "employment_type": job.get("employmentType"),
            "published_at": job.get("publishTime"),
            "published_text": job.get("publishTimeDesc"),
            "salary": job.get("salaryDesc"),
            "summary": job.get("jobSummary"),
            "applicants_count": job.get("applicantsCount"),
            "recommendation_tags": as_list(job.get("recommendationTags")),
            "job_tags": as_list(job.get("jobTags")),
            "core_skills": as_list(job.get("jdCoreSkills")),
            "responsibilities": as_list(job.get("coreResponsibilities")),
            "qualifications": as_list(job.get("qualifications")),
            "detail_qualifications": as_list(job.get("detailQualifications")),
            "benefits": as_list(job.get("benefitsSummaries")),
            "why_join_us": as_list(job.get("whyJoinUs")),
            "skill_summaries": as_list(job.get("skillSummaries")),
            "education_summaries": as_list(job.get("educationSummaries")),
            "original_url": job.get("originalUrl"),
            "apply_url": job.get("applyLink"),
            "is_remote": job.get("isRemote"),
            "is_h1b_sponsor": job.get("isH1bSponsor"),
            "is_work_auth_required": job.get("isWorkAuthRequired"),
            "is_citizen_only": job.get("isCitizenOnly"),
            "is_clearance_required": job.get("isClearanceRequired"),
        },
        "company": {
            "id": company.get("companyId"),
            "name": company.get("companyName"),
            "size": company.get("companySize"),
            "description": company.get("companyDesc"),
            "categories": as_list(company.get("companyCategories")),
            "founded_year": company.get("companyFoundYear"),
            "location": company.get("companyLocation"),
            "website": company.get("companyURL"),
            "linkedin_url": company.get("companyLinkedinURL"),
            "twitter_url": company.get("companyTwitterURL"),
            "crunchbase_url": company.get("companyCrunchbaseURL"),
            "funding_stage": company.get("fundraisingCurrentStage"),
            "total_funding": company.get("fundraisingTotalFunding"),
            "key_investors": as_list(company.get("fundraisingKeyInvestors")),
            "latest_rounds": as_list(company.get("fundraisingLatestRounds")),
            "leadership": as_list(company.get("leadership")),
            "recent_news": as_list(company.get("pressReferences")),
            "h1b_annual_job_count": as_list(company.get("h1bAnnualJobCount")),
            "h1b_title_distribution": as_list(company.get("h1bTitleDistribution")),
            "glassdoor_rating": company.get("grating"),
        },
    }

    print("✅ First job scraped:")
    print(f"   Title    : {detail['job']['title']}")
    print(f"   Company  : {detail['job']['company']}")
    print(f"   Location : {detail['job']['location']}")
    print(f"   Salary   : {detail['job']['salary']}")
    print(f"   Match    : {detail['match_score']} ({detail['match_rank']})")
    print(f"   URL      : {detail['job_url']}")

    if args.debug:
        with open("debug_job_detail.html", "w", encoding="utf-8") as f:
            f.write(get_html_content(page))
        print("🐛 Debug file saved: debug_job_detail.html")

    return detail


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not EMAIL or not PASSWORD:
        print("❌ Missing credentials! Create a .env file:\n")
        print("   JOBRIGHT_EMAIL=your@email.com")
        print("   JOBRIGHT_PASSWORD=yourpassword\n")
        sys.exit(1)

    print("=" * 65)
    print("  Jobright.ai Scraper  |  Modal Login Flow")
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

        # ── STEP 1: Login and stay in the same session ────────────
        print("STEP 1 → Login and open the first recommended job\n")

        job_page = StealthyFetcher.fetch(
            "https://jobright.ai",
            headless=False,
            network_idle=True,
            wait=3000,
            page_action=login_and_open_first_job,
        )

        current_url = str(job_page.url)
        print(f"\n   Final URL after session flow: {current_url}")

        if "/jobs/info/" not in current_url:
            print("\n❌ Did not reach a job detail page.")
            print("   Login may have failed or the first job did not open.")
            if args.debug:
                with open("debug_login_fail.html", "w") as f:
                    f.write(get_html_content(job_page))
                print("   Saved debug_login_fail.html")
            sys.exit(1)

        print("\n✅ Authenticated session preserved and job detail opened.\n")

        # ── STEP 2: Parse the first job detail page ────────────────
        print("STEP 2 → Extracting the first job detail page\n")
        print(f"   HTTP {job_page.status} | {job_page.url}\n")

        job_detail = parse_job_detail(job_page)

        # ── STEP 3: Save results ───────────────────────────────────
        if job_detail:
            print("\n✅ Done! Scraped the first recommended job.")
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(job_detail, f, indent=2, ensure_ascii=False)
            print(f"💾 Saved → {args.output}")
        else:
            print("\n❌ No job detail scraped.")
            print("   Try: python Test.py --debug --wait 15000")

    finally:
        stop_xvfb()


if __name__ == "__main__":
    main()
