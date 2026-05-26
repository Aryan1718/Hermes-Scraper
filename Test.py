"""
Jobright.ai Job Scraper - Batch Flow (20 jobs, single browser session)
======================================================================
Flow:
  1. Login via auth modal on homepage (StealthyFetcher)
  2. Navigate to /jobs/recommend
  3. Collect N job links via /swan/recommend/list/jobs API
  4. For each job link (same browser session):
     a. page.goto(job_detail_url)
     b. Wait for script#jobright-helper-job-detail-info
     c. Parse full JD payload
     d. POST to Hermes dashboard ingest endpoint
  5. Save batch results JSON + print summary

RUN:
  python Test.py --xvfb                  # scrape 1 job (default)
  python Test.py --xvfb --jobs 20       # scrape 20 jobs
  python Test.py --xvfb --jobs 20 --debug
  python Test.py --xvfb --jobs 3 --wait 8000
"""

import argparse
import html
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

EMAIL                 = os.getenv("JOBRIGHT_EMAIL", "").strip()
PASSWORD              = os.getenv("JOBRIGHT_PASSWORD", "").strip()
SCRAPER_INGEST_SECRET = os.getenv("SCRAPER_INGEST_SECRET", "").strip()
INGEST_URL            = "https://hermes-dashboard-nine.vercel.app/api/job-descriptions"


# --- CLI Args ------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Jobright.ai batch job scraper")
parser.add_argument("--xvfb",         action="store_true", help="Auto-start Xvfb virtual display")
parser.add_argument("--jobs",         type=int, default=1, help="Number of jobs to scrape (default: 1)")
parser.add_argument("--wait",         type=int, default=6000, help="Wait ms for pages to render (default: 6000)")
parser.add_argument("--output",       default="jobright_batch.json", help="Batch output JSON file")
parser.add_argument("--debug",        action="store_true", help="Save debug HTML files on failure")
parser.add_argument("--dry-run",      action="store_true", help="Collect links only, don't scrape details")
args = parser.parse_args()


# --- Xvfb ----------------------------------------------------------------------

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
        try:
            xvfb_proc.wait(timeout=5)
        except Exception:
            xvfb_proc.kill()
        print("🖥️  Xvfb stopped.")


# --- Helpers -------------------------------------------------------------------

def dismiss_modals(page):
    """Close any popups or modals that appear."""
    selectors = [
        "button:has-text(\"EXIT\")",
        "button:has-text(\"Close\")",
        "button:has-text(\"Not now\")",
    ]
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                page.wait_for_timeout(400)
        except Exception:
            pass


def scroll_and_wait(page):
    """Progressively scroll to trigger lazy-loaded content."""
    page.wait_for_timeout(400)
    for y in [400, 900, 1500, 2200, 3000]:
        page.evaluate(f"window.scrollTo(0, {y})")
        page.wait_for_timeout(300)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)


def get_html_content(target):
    """Extract HTML string from a Playwright page or Scrapling response."""
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

    raise AttributeError(f"Could not extract HTML from {type(target).__name__}")


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [value]


# --- Phase 1: Login ------------------------------------------------------------

def do_homepage_login(page):
    """Full login flow via the auth modal. Returns True on success."""
    print("   ⏳ Waiting for homepage to fully load...")
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(800)

    print("   🖱️  Opening auth dialog...")
    auth_trigger_selectors = [
        "text=SIGN IN",
        "text=Sign In",
        "text=Sign in",
        "button:has-text(\"JOIN NOW\")",
        "button:has-text(\"Try For Free\")",
    ]
    auth_trigger = None
    for sel in auth_trigger_selectors:
        try:
            auth_trigger = page.query_selector(sel)
            if auth_trigger:
                print(f"   ✅ Found auth trigger: {sel}")
                auth_trigger.click()
                page.wait_for_timeout(700)
                break
        except Exception:
            continue

    if not auth_trigger:
        print("   ⚠️  No auth trigger found on homepage.")
        if args.debug:
            try:
                page.screenshot(path="debug_no_auth_trigger.png")
            except Exception:
                pass
        return False

    # Switch to sign-in if dialog opened in signup mode
    switch_to_signin = (
        page.query_selector("button:has-text(\"Already a member? Sign in now\")")
        or page.query_selector("button:has-text(\"Sign in now\")")
        or page.query_selector("button:has-text(\"Already a member\")")
        or page.query_selector("button:has-text(\"Not a member? Sign up now\")")
    )
    if switch_to_signin and "not a member" not in (switch_to_signin.inner_text() or "").strip().lower():
        print("   🔁 Switching auth dialog to sign in...")
        try:
            switch_to_signin.click()
            page.wait_for_timeout(700)
        except Exception:
            pass

    # Fill credentials
    print("   🔐 Filling login form...")
    email_sel = "input[placeholder='Email'], input[placeholder*='email' i], input[type='email'], input[name='email']"
    password_sel = "input[placeholder='Password'], input[type='password']"

    try:
        page.wait_for_selector(email_sel, timeout=10000)
        page.wait_for_selector(password_sel, timeout=10000)
    except Exception:
        print("   ⚠️  Sign-in fields did not appear.")
        if args.debug:
            try:
                page.screenshot(path="debug_signin_fields.png")
            except Exception:
                pass
        return False

    email_input = page.query_selector(email_sel)
    password_input = page.query_selector(password_sel)
    if not email_input or not password_input:
        print("   ❌ Could not find both sign-in inputs.")
        return False

    email_input.click()
    email_input.fill("")
    email_input.fill(EMAIL)
    password_input.click()
    password_input.fill("")
    password_input.fill(PASSWORD)

    # Submit
    submit_btn = (
        page.query_selector("button[type='submit']:has-text(\"SIGN IN\")")
        or page.query_selector("button[type='submit']:has-text(\"Sign In\")")
        or page.query_selector("button:has-text(\"SIGN IN\")")
        or page.query_selector("button:has-text(\"Sign In\")")
        or page.query_selector("button:has-text(\"Sign in\")")
    )
    print("   🚀 Submitting login form...")
    if submit_btn:
        submit_btn.click()
    else:
        password_input.press("Enter")

    # Wait for redirect to recommendations
    print("   ⏳ Waiting for redirect after login...")
    try:
        page.wait_for_url("**/jobs/recommend**", timeout=20000)
        print("   ✅ Redirected to recommendations page!")
    except Exception:
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(1200)
        print(f"   Current URL after login: {page.url}")
        if "/jobs/recommend" not in page.url:
            print("   ❌ Did not reach recommendations page.")
            return False

    if args.debug:
        try:
            page.screenshot(path="debug_after_login.png")
        except Exception:
            pass
    return True


# --- Phase 2: Collect Job Links ------------------------------------------------

def is_usa_location(location):
    """
    Check if a job location string is USA-based.
    Handles formats like: 'United States', 'San Francisco, CA', 'NYC Metro Area',
    'Boulder, Colorado', 'American Fork, UT', 'Remote', etc.
    Returns True if USA, False otherwise.
    """
    if not location or not location.strip():
        return True  # include if no location data

    loc = location.strip()
    loc_lower = loc.lower()

    # --- Quick USA keyword match ---
    usa_keywords = [
        'united states', 'usa', 'u.s.a', 'u.s.', ' us ',
        'remote', 'nyc', 'metro area', 'bay area', 'silicon valley',
    ]
    for kw in usa_keywords:
        if kw in loc_lower:
            return True

    # --- US state abbreviations (e.g. ', CA', ', NY', ', TX') ---
    import re as _re
    state_abbrs = (
        'AL,AK,AZ,AR,CA,CO,CT,DE,FL,GA,HI,ID,IL,IN,IA,KS,KY,LA,ME,MD,'
        'MA,MI,MN,MS,MO,MT,NE,NV,NH,NJ,NM,NY,NC,ND,OH,OK,OR,PA,RI,SC,'
        'SD,TN,TX,UT,VT,VA,WA,WV,WI,WY,DC'
    )
    abbr_pattern = r',\s*(' + '|'.join(state_abbrs.split(',')) + r')\s*$'
    if _re.search(abbr_pattern, loc, _re.IGNORECASE):
        return True

    # --- Full US state names ---
    state_names = [
        'alabama','alaska','arizona','arkansas','california','colorado',
        'connecticut','delaware','florida','georgia','hawaii','idaho',
        'illinois','indiana','iowa','kansas','kentucky','louisiana','maine',
        'maryland','massachusetts','michigan','minnesota','mississippi',
        'missouri','montana','nebraska','nevada','new hampshire',
        'new jersey','new mexico','new york','north carolina','north dakota',
        'ohio','oklahoma','oregon','pennsylvania','rhode island',
        'south carolina','south dakota','tennessee','texas','utah',
        'vermont','virginia','west virginia','wisconsin','wyoming',
        'washington dc', 'district of columbia',
    ]
    for state in state_names:
        if state in loc_lower:
            return True

    # --- Major US cities (top 100) ---
    us_cities = [
        'new york','los angeles','chicago','houston','phoenix','philadelphia',
        'san antonio','san diego','dallas','san jose','austin','jacksonville',
        'fort worth','columbus','charlotte','san francisco','indianapolis',
        'seattle','denver','boston','el paso','detroit','nashville','portland',
        'oklahoma city','las vegas','louisville','baltimore','milwaukee',
        'albuquerque','tucson','fresno','sacramento','mesa','kansas city',
        'atlanta','long beach','colorado springs','raleigh','miami',
        'virginia beach','omaha','oakland','minneapolis','tulsa','arlington',
        'new orleans','wichita','cleveland','bakersfield','tampa','aurora',
        'honolulu','anaheim','santa ana','corpus christi','riverside',
        'lexington','stockton','henderson','st. louis','pittsburgh',
        'cincinnati','irvine','orlando','plano','newark','toledo',
        'greensboro','durham','lincoln','buffalo','madison','lubbock',
        'chandler','scottsdale','glendale','reno','norfolk','winston-salem',
        'north las vegas','irving','chesapeake','gilbert','hialeah',
        'garland','fremont','boise','richmond','baton rouge','spokane',
        'des moines','tacoma','san bernardino','modesto','fontana',
        'santa clarita','birmingham','oxnard','fayetteville','rochester',
        'moreno valley','springfield','fort collins','jackson','alexandria',
        'hayward','lancaster','lakewood','clarksville','palmdale','salinas',
        'pasadena','sunnyvale','macon','pomona','escondido','killeen',
        'hampton','warren','midland','carrollton','cedar rapids',
        'sterling heights','new haven','denton','concord','topeka',
        'elizabeth','thousand oaks','charleston','visalia','beaumont',
        'miami gardens','coral springs','simi valley','hartford','lafayette',
        'athens','ventura','abilene','norman','vallejo','evansville',
        'ann arbor','allentown','provo','peoria','downey','carlsbad',
        'waco','independence','elgin','albany','odessa','daly city',
        'new bedford','conroe','redding','green bay','boulder',
        'american fork','san mateo','menlo park','palo alto',
        'mountain view','cupertino','santa clara','milpitas','pleasanton',
        'dublin','livermore','walnut creek','san ramon','danville',
        'alameda','berkeley','el cerrito','san pablo','pinole','hercules',
        'martinez','pleasant hill','san leandro','castro valley',
        'union city','hayward','lafayette','orinda','moraga',
        'south san francisco','brisbane','millbrae','burlingame',
        'san bruno','menlo park','redwood city','san jose','campbell',
        'los gatos','saratoga','mountain view','palo alto',
    ]
    for city in us_cities:
        if city in loc_lower:
            return True

    # If none of the above matched, assume non-USA
    return False


def collect_job_links(page, limit):
    """
    Collect up to `limit` unique job links from /jobs/recommend.
    Only includes USA-based jobs.
    Primary: /swan/recommend/list/jobs XHR API.
    Fallback: DOM scrolling.
    """
    print(f"\n📋 Collecting up to {limit} USA job links...")
    page.wait_for_url("**/jobs/recommend**", timeout=20000)
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    page.wait_for_timeout(max(args.wait, 3000))

    # Accumulators for both strategies
    all_links = []
    seen_ids = set()

    # -- Strategy 1: XHR API -------------------------------------------
    print("   Trying /swan/recommend/list/jobs API...")
    try:
        api_result = page.evaluate(
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

                    const response = await fetch(
                        `/swan/recommend/list/jobs?${params.toString()}`,
                        {
                            method: "GET",
                            credentials: "include",
                            headers: {
                                "accept": "application/json, text/plain, */*",
                                "x-requested-with": "XMLHttpRequest",
                            },
                        }
                    );

                    if (!response.ok) {
                        return { error: `API returned ${response.status}`, jobs: allJobs };
                    }

                    const data = await response.json();
                    const batch = data?.result?.jobList || [];
                    if (!batch.length) break;

                    for (const item of batch) {
                        const job = item?.jobResult || {};
                        if (!job.jobId) continue;
                        allJobs.push({
                            job_id: String(job.jobId),
                            title: job.jobTitle || "N/A",
                            company: item?.companyResult?.companyName || "",
                            location: job.jobLocation || "",
                            url: `https://jobright.ai/jobs/info/${job.jobId}`,
                        });
                        if (allJobs.length >= limit) break;
                    }

                    if (batch.length < count) break;
                    position += count;
                    refresh = false;
                }

                return { jobs: allJobs, fetched: allJobs.length };
            }""",
            limit,
        )

        api_jobs = (api_result or {}).get("jobs", [])
        if api_jobs:
            # Filter to USA-only jobs
            for j in api_jobs:
                loc = j.get("location", "")
                if is_usa_location(loc):
                    if j["job_id"] not in seen_ids:
                        seen_ids.add(j["job_id"])
                        all_links.append(j)
                # else: silently drop non-USA
            dropped = len(api_jobs) - len(all_links)
            if dropped > 0:
                print(f"   🚫 Filtered out {dropped} non-USA jobs from API.")
            if len(all_links) >= limit:
                print(f"   ✅ API returned {len(all_links)} USA job links.")
                return all_links[:limit]
            print(f"   ℹ️  API gave {len(all_links)} USA jobs (need {limit}). Supplementing from DOM...")
        else:
            err = (api_result or {}).get("error", "no jobs returned")
            print(f"   ⚠️  API failed: {err}. Falling back to DOM...")
    except Exception as e:
        print(f"   ⚠️  API error: {e}. Falling back to DOM...")

    # -- Strategy 2: DOM scrolling --------------------------------------
    # Continue from partial API results (all_links / seen_ids already initialized above)
    _dropped_non_usa = 0

    for scroll_round in range(5):
        dismiss_modals(page)
        page.wait_for_timeout(500)
        scroll_and_wait(page)

        try:
            dom_links = page.evaluate(
                """() => {
                    const items = [];
                    const seen = new Set();
                    const links = Array.from(
                        document.querySelectorAll("a[href*='/jobs/info/']")
                    );
                    for (const link of links) {
                        const href = link.href || "";
                        if (!href || seen.has(href)) continue;
                        seen.add(href);
                        const card = link.closest("article, li, div[class*='card'], div[class*='Card']");
                        const cardText = card
                            ? (card.innerText || card.textContent || "").replace(/\\s+/g, " ").trim()
                            : "";
                        const text = (link.innerText || link.textContent || "")
                            .replace(/\\s+/g, " ").trim();
                        // Try to extract location from the card text
                        let location = "";
                        const lines = cardText.split("\\n").map(l => l.trim()).filter(l => l);
                        // Location is often a line that looks like a city/state
                        for (const line of lines) {
                            if (/^[A-Z][a-z]+(, [A-Z]{2})?$/.test(line) ||
                                /United States|Remote|Hybrid/.test(line)) {
                                location = line;
                                break;
                            }
                        }
                        items.push({ url: href, text: text, location: location });
                    }
                    return items;
                }"""
            )
        except Exception:
            dom_links = []

        for item in dom_links:
            m = re.search(r'/jobs/info/([^/?#]+)', item["url"])
            job_id = m.group(1) if m else item["url"]
            if job_id not in seen_ids:
                # USA filter: skip non-USA jobs
                item_loc = item.get("location", "")
                item_text = item.get("text", "")
                if not is_usa_location(item_loc):
                    # Try to check text as fallback
                    if not is_usa_location(item_text):
                        _dropped_non_usa += 1
                        continue  # skip non-USA
                seen_ids.add(job_id)
                all_links.append({
                    "job_id": job_id,
                    "title": item["text"][:120] if len(item["text"]) > 5 else "N/A",
                    "company": "",
                    "location": item_loc,
                    "url": item["url"],
                })

        print(f"   📌 Scroll round {scroll_round + 1}: {len(all_links)} unique USA links...")
        if len(all_links) >= limit:
            break

    if _dropped_non_usa > 0:
        print(f"   🚫 Filtered out {_dropped_non_usa} non-USA jobs from DOM.")
    print(f"   ✅ Collected {len(all_links)} USA job links total.")
    return all_links[:limit]


# --- Phase 3: Detail Scraping (same browser session) ---------------------------

def scrape_single_job(page, job_link, index, total):
    """
    Navigate to a single job detail page, parse it, and return (detail, error).
    Uses the SAME browser page — no new session needed.
    """
    job_url = job_link.get("url", "")
    title_hint = job_link.get("title", "")[:60]

    print(f"\n[{index}/{total}] {title_hint}")
    print(f"   🌐 {job_url}")

    # Navigate to detail page
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"   ⚠️  Navigation timed out: {e}")
        print(f"   🌐 Current URL: {page.url}")

    page.wait_for_timeout(1500)
    dismiss_modals(page)

    # Wait for detail payload script tag
    try:
        page.wait_for_selector(
            "script#jobright-helper-job-detail-info",
            timeout=25000,
            state="attached",
        )
    except Exception as e:
        print(f"   ⚠️  Detail payload not found: {e}")
        if args.debug:
            try:
                prefix = f"debug_detail_{job_link.get('job_id', 'unknown')}"
                with open(f"{prefix}.html", "w", encoding="utf-8") as f:
                    f.write(get_html_content(page))
                page.screenshot(path=f"{prefix}.png")
            except Exception:
                pass
        return None, str(e)

    # Parse
    try:
        detail = build_job_detail(page)
    except Exception as e:
        print(f"   ❌ Parse error: {e}")
        return None, str(e)

    print(f"   ✅ {detail['job']['title']} @ {detail['job']['company']} "
          f"(match: {detail['match_score']})")
    return detail, None


def build_job_detail(page):
    """Parse the page into our standard job detail dict."""
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
        raise ValueError("Could not find embedded job detail JSON on page.")

    payload = json.loads(html.unescape(payload_text.strip()))
    job = payload.get("jobResult", {})
    company = payload.get("companyResult", {})

    return {
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


# --- Phase 4: Ingest -----------------------------------------------------------

def submit_to_ingest(job_detail):
    """
    POST a single job detail to the Hermes dashboard ingest endpoint.
    Returns: (status, error)
      status = "ok" | "duplicate" | "error"
      error = None or error message string
    """
    if not SCRAPER_INGEST_SECRET:
        print("   ⚠️  SCRAPER_INGEST_SECRET not set — skipping ingest.")
        return "error", "no_secret"

    payload = json.dumps(job_detail).encode()
    req = urllib.request.Request(
        INGEST_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SCRAPER_INGEST_SECRET}",
        },
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        body = resp.read().decode()
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {}

        if parsed.get("success") is True:
            print(f"   📤 Ingest OK ({resp.status})")
            return "ok", None

        # success:false — check if duplicate or real error
        message = parsed.get("message", "")
        if "already exist" in message.lower() or "already" in message.lower():
            print(f"   ♻️  Already in DB (duplicate) — will find replacement")
            return "duplicate", None

        # Other error
        print(f"   ❌ Ingest failed: {message}")
        return "error", message

    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {}
        message = parsed.get("message", f"HTTP {e.code}")
        if "already exist" in message.lower():
            print(f"   ♻️  Already in DB (duplicate, HTTP {e.code}) — will find replacement")
            return "duplicate", None
        print(f"   ❌ Ingest HTTP error ({e.code}): {message}")
        return "error", message
    except Exception as e:
        print(f"   ❌ Ingest error: {e}")
        return "error", str(e)


# --- Main ----------------------------------------------------------------------

def main():
    if not EMAIL or not PASSWORD:
        print("❌ Missing credentials! Check your .env file.\n")
        sys.exit(1)

    print("=" * 65)
    print(f"  Jobright.ai Batch Scraper  |  Target: {args.jobs} jobs")
    print("=" * 65)
    print(f"  Account : {EMAIL}")
    print(f"  Output  : {args.output}")
    print(f"  Debug   : {args.debug}")
    print(f"  Dry-run : {args.dry_run}")
    print("=" * 65 + "\n")

    if args.xvfb and not start_xvfb():
        sys.exit(1)

    batch_results = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "target_count": args.jobs,
        "succeeded": 0,
        "failed": 0,
        "jobs": [],
    }

    try:
        from scrapling.fetchers import StealthyFetcher

        # ================================================================
        # Single page_action: login → collect links → scrape all → ingest all
        # ================================================================

        class _BatchScraper:
            """
            One browser session that:
            1. Logs in
            2. Collects N job links
            3. Loops through each: navigate → parse → ingest
            """
            def __init__(self):
                self.batch_results = batch_results
                self.job_links = []

            def __call__(self, page):
                # -- Login --
                ok = do_homepage_login(page)
                if not ok:
                    raise RuntimeError("Login failed")

                # Ensure we're on recommendations
                if "/jobs/recommend" not in page.url:
                    page.goto(
                        "https://jobright.ai/jobs/recommend",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    page.wait_for_timeout(1000)

                # -- Collect initial batch of USA job links --
                # Request extra to account for duplicates we'll need to replace
                fetch_limit = args.jobs * 3  # over-fetch so we have replacements ready
                self.job_links = collect_job_links(page, fetch_limit)

                if not self.job_links:
                    raise RuntimeError("No job links collected")

                if args.dry_run:
                    return  # skip scraping

                # -- Scrape each job (same browser session) --
                attempted_ids = set()       # all job_ids we've tried
                duplicate_count = 0
                replace_position = args.jobs  # pointer into self.job_links for replacements

                i = 0  # index into self.job_links
                processed = 0  # how many we've attempted to scrape+ingest

                while processed < args.jobs and i < len(self.job_links):
                    job_link = self.job_links[i]
                    job_id = job_link.get("job_id", "")

                    # Skip if we already tried this job_id
                    if job_id in attempted_ids:
                        i += 1
                        continue

                    attempted_ids.add(job_id)
                    processed += 1

                    detail, error = scrape_single_job(
                        page, job_link, processed, args.jobs
                    )

                    # USA safety check
                    if detail is not None:
                        loc = detail.get("job", {}).get("location", "")
                        if not is_usa_location(loc):
                            print(f"   🚫 Non-USA, skipping: {loc}")
                            detail = None
                            error = f"Non-USA: {loc}"

                    # Ingest
                    ingest_status = "error"
                    ingest_err = None
                    if detail is not None:
                        ingest_status, ingest_err = submit_to_ingest(detail)

                    if ingest_status == "ok":
                        self.batch_results["succeeded"] += 1
                    elif ingest_status == "duplicate":
                        duplicate_count += 1
                        # Don't count as succeeded or failed — we'll replace it
                        # The while loop continues and will pick the next replacement
                        processed -= 1  # don't count this toward our target
                    else:
                        self.batch_results["failed"] += 1
                        if not error:
                            error = ingest_err

                    self.batch_results["jobs"].append({
                        "job_id": job_id,
                        "url": job_link.get("url", ""),
                        "title": job_link.get("title", ""),
                        "scraped": detail is not None,
                        "ingest_ok": ingest_status == "ok",
                        "ingest_status": ingest_status,
                        "error": error,
                        "detail": detail if detail else None,
                    })

                    # Save progress after every job
                    with open(args.output, "w", encoding="utf-8") as f:
                        json.dump(self.batch_results, f, indent=2, ensure_ascii=False)

                    i += 1

                self.batch_results["duplicates"] = duplicate_count

        scraper = _BatchScraper()

        StealthyFetcher.fetch(
            "https://jobright.ai",
            headless=False,
            network_idle=True,
            wait=3000,
            page_action=scraper,
        )

        # ================================================================
        # Summary
        # ================================================================
        _print_summary(scraper.batch_results, scraper.job_links)

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user.")
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(batch_results, f, indent=2, ensure_ascii=False)
        print(f"💾 Partial results saved → {args.output}")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(batch_results, f, indent=2, ensure_ascii=False)
        print(f"💾 Partial results saved → {args.output}")
    finally:
        stop_xvfb()


def _print_summary(batch_results, job_links):
    """Print final batch summary."""
    duplicates = batch_results.get("duplicates", 0)
    succeeded = batch_results["succeeded"]
    failed = batch_results["failed"]
    target = batch_results["target_count"]

    print("\n" + "=" * 65)
    print("  BATCH SUMMARY")
    print("=" * 65)
    print(f"  Target              : {target}")
    print(f"  Links collected     : {len(job_links)}")
    print(f"  New + Ingested      : {succeeded}")
    if duplicates > 0:
        print(f"  Duplicates replaced : {duplicates}")
    print(f"  Failed              : {failed}")
    print(f"  Output              : {args.output}")
    print("=" * 65)

    if succeeded >= target:
        print(f"\n  ✅ Target met! {succeeded}/{target} new jobs ingested.")
    else:
        short = target - succeeded
        print(f"\n  ⚠️  Short by {short} jobs ({succeeded}/{target}). Run again for more.")

    failed_jobs = [j for j in batch_results["jobs"] if j.get("ingest_status") == "error"]
    if failed_jobs:
        print(f"\n  Failed jobs ({len(failed_jobs)}):")
        for j in failed_jobs[:5]:
            print(f"    - {j.get('url', '')}  error: {j.get('error', 'unknown')}")
        if len(failed_jobs) > 5:
            print(f"    ... and {len(failed_jobs) - 5} more")


if __name__ == "__main__":
    main()
