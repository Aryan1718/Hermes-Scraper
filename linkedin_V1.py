"""
LinkedIn Public Profile Scraper
===============================
Best-effort public LinkedIn scraper for a single profile URL.

This script does not log into LinkedIn. If LinkedIn redirects the request
to an auth wall, the script returns a structured blocked result instead of
failing silently.

SETUP:
  pip install "scrapling[fetchers]" python-dotenv
  scrapling install

RUN:
  python linkedin_V1.py --profile-url https://www.linkedin.com/in/aryan-pandit/
  python linkedin_V1.py --profile-url https://www.linkedin.com/in/aryan-pandit/ --debug
  python linkedin_V1.py --profile-url https://www.linkedin.com/in/aryan-pandit/ --xvfb
"""

import argparse
import json
import re
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from dotenv import load_dotenv

load_dotenv()


parser = argparse.ArgumentParser()
parser.add_argument("--profile-url", required=True, help="LinkedIn profile URL to inspect")
parser.add_argument("--xvfb", action="store_true", help="Auto-start Xvfb virtual display")
parser.add_argument("--wait", type=int, default=6000, help="Wait ms for page to render")
parser.add_argument("--output", default="linkedin_profile.json", help="Output file")
parser.add_argument("--debug", action="store_true", help="Save debug HTML and screenshot")
args = parser.parse_args()


xvfb_proc = None


def start_xvfb(display=":99", res="1920x1080x24"):
    global xvfb_proc
    print(f"Starting Xvfb on display {display}...")
    try:
        xvfb_proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", res],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        os.environ["DISPLAY"] = display
        print(f"Xvfb started (PID {xvfb_proc.pid})\n")
        return True
    except FileNotFoundError:
        print("Xvfb not found. Install: sudo apt-get install -y xvfb\n")
        return False


def stop_xvfb():
    if xvfb_proc:
        xvfb_proc.terminate()
        print("Xvfb stopped.")


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


def normalize_space(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def slug_from_url(url):
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "in":
        return parts[1]
    return ""


def build_public_profile_url(requested_url):
    slug = slug_from_url(requested_url)
    if not slug:
        return requested_url
    return f"https://in.linkedin.com/in/{slug}?trk=public_profile_samename-profile"


def guessed_name_from_slug(slug):
    if not slug:
        return ""
    words = [part for part in re.split(r"[-_]+", slug) if part]
    if not words:
        return ""
    return " ".join(word.capitalize() for word in words)


def debug_paths():
    return {
        "html": "debug_linkedin_profile.html",
        "screenshot": "debug_linkedin_profile.png",
        "search_screenshot": "debug_linkedin_search.png",
    }


def prepare_linkedin_page(page):
    print("   Waiting for page to load...")
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        page.wait_for_timeout(2000)

    page.wait_for_timeout(max(args.wait, 2500))

    if args.debug:
        page.screenshot(path=debug_paths()["screenshot"], full_page=True)
        print(f"   Screenshot saved: {debug_paths()['screenshot']}")


def prepare_search_page(page):
    print("   Waiting for search results to render...")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        page.wait_for_timeout(2000)
    page.wait_for_timeout(2000)

    if args.debug:
        page.screenshot(path=debug_paths()["search_screenshot"], full_page=True)
        print(f"   Search screenshot saved: {debug_paths()['search_screenshot']}")


def get_page_title(target):
    title = target.css("title::text").get()
    return normalize_space(title)


def extract_meta_content(target, *selectors):
    for selector in selectors:
        nodes = target.css(selector)
        value = None
        if nodes:
            value = nodes[0].attrib.get("content")
        if value:
            return normalize_space(value)
    return ""


def extract_text(target, *selectors):
    for selector in selectors:
        value = target.css(selector).get()
        if value:
            return normalize_space(value)
    return ""


def extract_text_list(target, selector, limit=None):
    values = []
    seen = set()
    for item in target.css(selector):
        text = normalize_space(" ".join(item.css("*::text").getall()) or item.css("::text").get() or "")
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
        if limit and len(values) >= limit:
            break
    return values


def authwall_redirect_target(final_url):
    query = parse_qs(urlparse(final_url).query)
    redirect = query.get("sessionRedirect", [""])[0]
    return unquote(redirect) if redirect else ""


def build_search_query(requested_url):
    slug = slug_from_url(requested_url)
    name = guessed_name_from_slug(slug)
    parts = []
    if name:
        parts.append(f'"{name}"')
    if slug:
        parts.append(f'"{slug}"')
    parts.append("site:linkedin.com/in/")
    parts.append("linkedin")
    return " ".join(parts)


def extract_search_result_url(result_text):
    match = re.search(r"https://(?:www|[a-z]{2})\.linkedin\.com\s*›\s*in\s*›\s*([A-Za-z0-9\\-_]+)", result_text)
    if match:
        return f"https://www.linkedin.com/in/{match.group(1)}/"
    return ""


def search_engine_fallback(search_response, requested_url):
    query = build_search_query(requested_url)
    search_url = str(search_response.url)
    print(f"   Parsing search-engine fallback results: {query}")

    results = []
    for node in search_response.css('[data-testid="result"]'):
        title = normalize_space(" ".join(node.css("h2 *::text").getall()))
        snippet = normalize_space(" ".join(node.css("*::text").getall()))
        if not title and not snippet:
            continue
        results.append({"title": title, "snippet": snippet})
        if len(results) >= 5:
            break

    requested_slug = slug_from_url(requested_url)
    preferred = None
    for result in results:
        snippet = result.get("snippet", "")
        title = result.get("title", "")
        combined = f"{title}\n{snippet}"
        candidate_url = extract_search_result_url(combined)
        if requested_slug and requested_slug in candidate_url:
            preferred = {
                "query": query,
                "search_url": search_url,
                "matched_url": candidate_url,
                "title": title,
                "snippet": snippet,
            }
            break

    if not preferred and results:
        first = results[0]
        combined = f"{first.get('title', '')}\n{first.get('snippet', '')}"
        preferred = {
            "query": query,
            "search_url": search_url,
            "matched_url": extract_search_result_url(combined),
            "title": first.get("title", ""),
            "snippet": first.get("snippet", ""),
        }

    return preferred


def detect_linkedin_block_state(target, requested_url, final_url):
    title = get_page_title(target)
    page_text = normalize_space(" ".join(target.css("body *::text").getall()[:120]))
    headings = visible_headings(target)
    parsed_final = urlparse(final_url)
    is_authwall = parsed_final.path.startswith("/authwall")

    authwall_markers = [
        "Join LinkedIn",
        "Sign Up | LinkedIn",
        "Already on Linkedin?",
        "Sign in",
    ]
    has_authwall_text = any(marker.lower() in f"{title} {page_text}".lower() for marker in authwall_markers)

    not_found_markers = [
        "Page not found",
        "لم يتم العثور على الصفحة",
        "Stránka nenalezena",
        "Siden blev ikke fundet",
        "Seite nicht gefunden",
        "Página no encontrada",
        "Impossible de trouver cette page",
        "Halaman ini tidak dapat ditemukan",
        "Pagina non trovata",
        "ページが見つかりませんでした",
        "페이지 없음",
        "Laman tidak ditemui",
        "Pagina niet gevonden",
        "Fant ikke siden",
        "Nie znaleziono strony",
        "Pagina nu a fost găsită",
        "Страница не найдена",
        "Sidan kunde inte hittas.",
        "ไม่พบหน้าเพจ",
        "Hindi Nahanap ang Pahina",
        "Sayfa bulunamadı",
        "未找到网页",
        "系統找不到頁面。",
    ]
    heading_blob = " ".join(headings)
    has_not_found_text = any(marker.lower() in f"{title} {page_text} {heading_blob}".lower() for marker in not_found_markers)

    is_blocked = is_authwall or has_authwall_text or has_not_found_text

    return {
        "is_blocked": is_blocked,
        "is_authwall": is_authwall,
        "is_not_found": has_not_found_text,
        "title": title,
        "page_text": page_text,
        "headings": headings,
        "requested_slug": slug_from_url(requested_url),
        "redirect_target": authwall_redirect_target(final_url),
    }


def visible_headings(target):
    headings = []
    seen = set()
    for selector in ("h1::text", "h2::text", "h3::text"):
        for text in target.css(selector).getall():
            clean = normalize_space(text)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            headings.append(clean)
    return headings


def parse_profile_title(title_text):
    title_text = normalize_space(title_text)
    if not title_text:
        return "", ""
    if title_text.endswith("| LinkedIn"):
        title_text = title_text[:-10].strip()
    if " - " in title_text:
        name, headline = title_text.split(" - ", 1)
        return normalize_space(name), normalize_space(headline)
    return title_text, ""


def parse_profile_description(description):
    description = normalize_space(description)
    parsed = {
        "about": None,
        "experience": [],
        "education": [],
        "location": None,
    }
    if not description:
        return parsed

    about_match = re.search(r"^(.*?)(?:\s*·\s*Experience:|\s*·\s*Education:|\s*·\s*Location:|$)", description, re.I)
    experience_match = re.search(r"Experience:\s*([^·]+)", description, re.I)
    education_match = re.search(r"Education:\s*([^·]+)", description, re.I)
    location_match = re.search(r"Location:\s*([^·]+)", description, re.I)

    if about_match:
        parsed["about"] = normalize_space(about_match.group(1))
    if experience_match:
        parsed["experience"] = [normalize_space(experience_match.group(1))]
    if education_match:
        parsed["education"] = [normalize_space(education_match.group(1))]
    if location_match:
        parsed["location"] = normalize_space(location_match.group(1))

    return parsed


def extract_snippet_profile(snippet_payload, requested_url, final_url):
    title = normalize_space(snippet_payload.get("title"))
    snippet = normalize_space(snippet_payload.get("snippet"))
    matched_url = snippet_payload.get("matched_url") or requested_url

    name = None
    headline = None
    if " - " in title and " | LinkedIn" in title:
        left = title.replace(" | LinkedIn", "").strip()
        if " - " in left:
            name, headline = [normalize_space(part) for part in left.split(" - ", 1)]
        else:
            name = left

    about_match = re.search(r"My name is .*?(?:\.|…)", snippet, re.I)
    experience_match = re.search(r"Experience:\s*([^·]+)", snippet, re.I)
    education_match = re.search(r"Education:\s*([^·]+)", snippet, re.I)
    location_match = re.search(r"Location:\s*([^·]+)", snippet, re.I)
    connections_match = re.search(r"(\d+\+?\s+connections?)", snippet, re.I)

    summary_bits = []
    for match in (about_match, connections_match):
        if match:
            summary_bits.append(normalize_space(match.group(0)))

    return {
        "status": "partial",
        "requested_url": requested_url,
        "resolved_url": final_url,
        "source": "duckduckgo_snippet",
        "profile": {
            "name": name or guessed_name_from_slug(slug_from_url(requested_url)) or None,
            "headline": headline or None,
            "location": normalize_space(location_match.group(1)) if location_match else None,
            "about": normalize_space(about_match.group(0)) if about_match else None,
            "current_company": headline or None,
            "experience": [normalize_space(experience_match.group(1))] if experience_match else [],
            "education": [normalize_space(education_match.group(1))] if education_match else [],
            "skills": [],
            "certifications": [],
            "raw_visible_sections": {
                "search_query": snippet_payload.get("query"),
                "matched_url": matched_url,
                "title": title or None,
                "snippet": snippet or None,
                "summary_snippets": summary_bits,
            },
        },
    }


def extract_public_profile_fields(target, requested_url, final_url):
    requested_slug = slug_from_url(requested_url)
    og_title = extract_meta_content(
        target,
        "meta[property='og:title']",
        "meta[name='title']",
    )
    og_description = extract_meta_content(
        target,
        "meta[property='og:description']",
        "meta[name='description']",
    )
    h1 = extract_text(target, "h1::text")
    title = get_page_title(target)
    body_text = normalize_space(" ".join(target.css("body *::text").getall()[:300]))

    title_name, title_headline = parse_profile_title(og_title or title)
    profile_name = h1 or title_name or ""
    parsed_description = parse_profile_description(og_description)

    summary_candidates = extract_text_list(
        target,
        "section, main, article, div",
        limit=50,
    )

    section_map = {}
    known_sections = {
        "about": "about",
        "experience": "experience",
        "education": "education",
        "skills": "skills",
        "certifications": "certifications",
    }
    for item in summary_candidates:
        lowered = item.lower()
        for marker, key in known_sections.items():
            if lowered.startswith(marker) and key not in section_map:
                section_map[key] = item

    experience = parsed_description["experience"] or extract_text_list(target, "section li, main li", limit=12)
    education = parsed_description["education"] or [
        item for item in experience if "university" in item.lower() or "college" in item.lower()
    ][:6]
    skills = []
    certifications = []
    followers_match = re.search(r"(\d+\s+followers)", body_text, re.I)
    connections_match = re.search(r"(\d+\s+connections)", body_text, re.I)

    return {
        "status": "ok",
        "requested_url": requested_url,
        "resolved_url": final_url,
        "profile": {
            "name": profile_name or guessed_name_from_slug(requested_slug) or None,
            "headline": title_headline or None,
            "location": parsed_description["location"],
            "about": parsed_description["about"] or section_map.get("about"),
            "current_company": title_headline or None,
            "experience": experience,
            "education": education,
            "skills": skills,
            "certifications": certifications,
            "raw_visible_sections": {
                "title": title or None,
                "headings": visible_headings(target),
                "followers": normalize_space(followers_match.group(1)) if followers_match else None,
                "connections": normalize_space(connections_match.group(1)) if connections_match else None,
                "summary_snippets": summary_candidates[:20],
            },
        },
    }


def build_blocked_result(target, requested_url, final_url):
    state = detect_linkedin_block_state(target, requested_url, final_url)
    guessed_name = guessed_name_from_slug(state["requested_slug"])

    result = {
        "status": "blocked",
        "requested_url": requested_url,
        "resolved_url": final_url,
        "block_reason": (
            "linkedin_authwall"
            if state["is_authwall"]
            else "linkedin_page_not_found"
            if state["is_not_found"]
            else "linkedin_access_restricted"
        ),
        "authwall_detected": state["is_authwall"],
        "discovered_metadata": {
            "requested_slug": state["requested_slug"] or None,
            "guessed_name": guessed_name or None,
            "page_title": state["title"] or None,
            "redirect_target": state["redirect_target"] or None,
            "visible_headings": state["headings"],
        },
    }

    if args.debug:
        result["debug_files"] = debug_paths()

    return result


def save_debug_html(target):
    if not args.debug:
        return
    with open(debug_paths()["html"], "w", encoding="utf-8") as handle:
        handle.write(get_html_content(target))
    print(f"   HTML saved: {debug_paths()['html']}")


def main():
    requested_url = args.profile_url.strip()
    if not requested_url:
        print("Missing LinkedIn profile URL.")
        sys.exit(1)

    print("=" * 65)
    print("  LinkedIn Scraper  |  Public-Only Best Effort")
    print("=" * 65)
    print(f"  Profile : {requested_url}")
    print(f"  Wait    : {args.wait}ms")
    print(f"  Debug   : {args.debug}")
    print("=" * 65 + "\n")

    if args.xvfb and not start_xvfb():
        sys.exit(1)

    try:
        from scrapling.fetchers import StealthyFetcher

        print("STEP 1 -> Open the LinkedIn profile URL\n")
        primary_response = StealthyFetcher.fetch(
            requested_url,
            headless=False,
            network_idle=True,
            wait=2000,
            page_action=prepare_linkedin_page,
        )

        response = primary_response
        final_url = str(response.url)
        state = detect_linkedin_block_state(response, requested_url, final_url)
        print(f"   HTTP {response.status} | {final_url}")

        public_variant_url = build_public_profile_url(requested_url)
        should_try_public_variant = (
            public_variant_url != requested_url
            and (state["is_blocked"] or state["is_not_found"] or "linkedin.com/in/" in final_url)
        )

        if should_try_public_variant:
            print(f"   Trying public-profile variant: {public_variant_url}")
            public_response = StealthyFetcher.fetch(
                public_variant_url,
                headless=False,
                network_idle=True,
                wait=2000,
                page_action=prepare_linkedin_page,
            )
            public_final_url = str(public_response.url)
            public_state = detect_linkedin_block_state(public_response, requested_url, public_final_url)
            print(f"   HTTP {public_response.status} | {public_final_url}")

            if not public_state["is_blocked"]:
                print("   Public-profile variant is accessible.")
                response = public_response
                final_url = public_final_url
                state = public_state
            else:
                print("   Public-profile variant did not produce a usable profile page.")

        save_debug_html(response)

        if state["is_blocked"]:
            print("   LinkedIn redirected to an auth wall or access-restricted page.")
            query = build_search_query(requested_url)
            search_url = f"https://duckduckgo.com/?q={quote_plus(query)}"
            search_response = StealthyFetcher.fetch(
                search_url,
                headless=False,
                network_idle=True,
                wait=1500,
                page_action=prepare_search_page,
            )
            search_match = search_engine_fallback(search_response, requested_url)

            if search_match:
                print("   Search-engine fallback produced a public snippet.")
                if args.debug:
                    save_debug_html(search_response)
                result = extract_snippet_profile(search_match, requested_url, final_url)
                if args.debug:
                    result["debug_files"] = debug_paths()
            else:
                result = build_blocked_result(response, requested_url, final_url)
        else:
            print("   Public profile page appears accessible.")
            result = extract_public_profile_fields(response, requested_url, final_url)

        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)

        print(f"\nSaved -> {args.output}")

    finally:
        stop_xvfb()


if __name__ == "__main__":
    main()
