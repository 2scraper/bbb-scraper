"""
BBB (Better Business Bureau) Scraper — undetected-chromedriver
GitHub: https://github.com/2scraper/bbb-scraper
License: MIT

BBB is protected by Cloudflare Managed Challenge. The only reliable way to
bypass it is undetected-chromedriver (removes automation flags from Chrome)
combined with residential proxies routed through a local mitmproxy relay.

VERIFIED WORKING SETUP:
    pip install undetected-chromedriver mitmproxy twocaptcha-python

    # One-time: trust mitmproxy certificate so Chrome doesn't prompt
    mitmdump --listen-port 18080 &  # run once, then Ctrl-C
    sudo security add-trusted-cert -d -r trustRoot \\
        -k /Library/Keychains/System.keychain \\
        ~/.mitmproxy/mitmproxy-ca-cert.pem

    # Run via the included shell wrapper (handles mitmproxy automatically):
    ./run_with_proxy.sh 'http://user:pass@proxy.host:port' 'restaurants' 'New York, NY'

    # Or run directly if proxy has no auth (or you set up mitmproxy manually):
    python bbb_uc.py --mode search --keyword "restaurants" --location "New York, NY" \\
        --proxy http://localhost:18080

TWO SCRAPING MODES:
    search    /search?find_text=...&find_loc=...  (keyword + city)
    category  /us/category/{slug}?page=N          (full category browse)

Usage examples:
    python bbb_uc.py --mode search --keyword "restaurants" --location "New York, NY"
    python bbb_uc.py --mode category --category-url https://www.bbb.org/us/category/restaurants
    python bbb_uc.py --mode search --keyword "plumbers" --location "Chicago, IL" \\
        --output results.csv --max-pages 5 --proxy http://localhost:18080 --debug
"""

import argparse
import json
import csv
import os
import re
import time
import random
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

try:
    from twocaptcha import TwoCaptcha
    HAS_2CAPTCHA = True
except ImportError:
    HAS_2CAPTCHA = False

DEBUG_DIR = Path("debug")

# ── Verified selectors (live BBB DOM, April 2025) ────────────────────────────
SEL_CARD_LIST  = "div.page-vertical-padding > div:nth-child(3) > div > div.not-sidebar > div.stack > div > div"
SEL_LAST_PAGE  = ".bds-last-page"
SEL_FILTERS    = ".search-filters"
SEL_JSON_LD    = 'script[type="application/ld+json"]'
SEL_MODAL_CTRY = "dialog .dtm-country-selection-modal-country-link"
SEL_MODAL_DLG  = "dialog:nth-child(2) > form > button"


def log(msg):
    print(f"[BBB] {msg}", flush=True)


def save_debug(name, content, ext="html"):
    DEBUG_DIR.mkdir(exist_ok=True)
    (DEBUG_DIR / f"{name}.{ext}").write_text(content, encoding="utf-8")
    log(f"[debug] {name}.{ext}")


def rand_sleep(lo=1.5, hi=3.5):
    time.sleep(random.uniform(lo, hi))


def create_driver(args):
    options = uc.ChromeOptions()
    options.add_argument("--window-size=1440,900")
    options.add_argument("--lang=en-US")
    # Accept mitmproxy self-signed certificate
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--allow-insecure-localhost")

    if args.proxy:
        m = re.match(r"https?://(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)", args.proxy)
        if m:
            user, password, host, port = m.group(1), m.group(2), m.group(3), m.group(4)
            log(f"Proxy: {host}:{port}" + (" (auth)" if user else ""))
            if user and password:
                log("WARNING: Chrome ignores credentials in --proxy-server.")
                log("Use run_with_proxy.sh which routes through mitmproxy.")
            options.add_argument(f"--proxy-server=http://{host}:{port}")
        else:
            log(f"ERROR: invalid proxy URL format: {args.proxy!r}")
            log("Expected: http://[user:pass@]host:port")

    kwargs = {
        "options": options,
        "headless": args.headless,
        "use_subprocess": True,
    }
    if args.chrome_version:
        kwargs["version_main"] = args.chrome_version

    return uc.Chrome(**kwargs)


def wait_ready(driver, timeout=20):
    """Poll document.readyState — Selenium has no networkidle equivalent."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if driver.execute_script("return document.readyState") == "complete":
                break
        except Exception:
            pass
        time.sleep(0.4)
    rand_sleep(1.0, 2.0)


def is_blocked(driver) -> bool:
    """Check if Cloudflare is blocking the page."""
    try:
        title = driver.title
        if "just a moment" in title.lower():
            return True
        src = driver.page_source[:3000]
        if "challenges.cloudflare.com" in src and "just a moment" in src.lower():
            return True
    except Exception:
        pass
    return False


def wait_for_cf(driver, timeout=30) -> bool:
    """Wait for Cloudflare to clear. With proper residential proxy it passes quickly."""
    for i in range(timeout):
        if not is_blocked(driver):
            if i > 0:
                log(f"Cloudflare cleared after {i}s")
            return True
        time.sleep(1)
    return False


def dismiss_modals(driver):
    for sel in [SEL_MODAL_CTRY, SEL_MODAL_DLG]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            el.click()
            rand_sleep(0.5, 1.0)
        except NoSuchElementException:
            pass
        except Exception:
            pass


def get_last_page(driver) -> int:
    try:
        el = driver.find_element(By.CSS_SELECTOR, SEL_LAST_PAGE)
        href = el.get_attribute("href")
        if href:
            qs = parse_qs(urlparse(href).query)
            return int(qs.get("page", [1])[0])
    except Exception:
        pass
    return 1


def extract_cards(driver, debug, label="") -> list:
    if debug:
        save_debug(f"listing_{label or int(time.time())}", driver.page_source)

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SEL_CARD_LIST))
        )
    except TimeoutException:
        log("No result cards found")
        return []

    cards = driver.execute_script(f"""
        const results = [];
        const items = document.querySelectorAll({json.dumps(SEL_CARD_LIST)});
        items.forEach(card => {{
            const nameEl = card.querySelector('.result-business-name > a');
            const nameSpan = nameEl ? nameEl.querySelector('span') : null;
            const name = nameSpan ? nameSpan.textContent.trim()
                       : (nameEl ? nameEl.textContent.trim() : '');
            const profileUrl = nameEl ? nameEl.href : '';
            const taglineEl = card.querySelector('div > div > div > p');
            const tagline = taglineEl ? taglineEl.textContent.trim() : '';
            const phoneEl = card.querySelector('.result-business-info > div > div > a');
            const phone = phoneEl ? phoneEl.textContent.trim() : '';
            const locEl = card.querySelector('.result-business-info > div > div > p');
            const location = locEl ? locEl.textContent.trim() : '';
            let logo = card.querySelector('div.result-image-wrapper > img')
                ?.getAttribute('src')?.trim() || '';
            if (logo.includes('non-ab-icon')) logo = '';
            if (name) results.push({{
                name, profile_url: profileUrl, tagline, phone, location, logo
            }});
        }});
        return results;
    """) or []

    log(f"Extracted {len(cards)} businesses")
    return cards


def enrich_profile(driver, url, debug) -> dict:
    """Visit profile page and extract full data from JSON-LD structured data."""
    try:
        driver.get(url)
        wait_ready(driver)
        if debug:
            slug = re.sub(r"[^a-z0-9]", "_", url.split("/")[-1][:40])
            save_debug(f"profile_{slug}", driver.page_source)

        return driver.execute_script(f"""
            const ldEl = document.querySelector({json.dumps(SEL_JSON_LD)});
            if (ldEl) {{
                try {{
                    const ld = JSON.parse(ldEl.textContent);
                    const p = Array.isArray(ld) ? ld[0] : ld;
                    const addr = p.address || {{}};
                    let logo = (p.logo || '').trim();
                    if (logo.includes('non-ab-icon')) logo = '';
                    let desc = (p.description || '').trim();
                    if (desc.includes('<p>')) {{
                        const d = document.createElement('div');
                        d.innerHTML = desc;
                        desc = d.textContent.trim();
                    }}
                    let siteUrl = document.querySelector('a.dtm-url')?.href?.trim() || '';
                    return {{
                        name:         (p.name || '').trim(),
                        description:  desc,
                        founded_at:   (p.foundingDate || '').trim(),
                        logo,
                        site_url:     siteUrl,
                        phone:        (p.telephone || '').trim(),
                        street:       (addr.streetAddress || '').trim(),
                        city:         (addr.addressLocality || '').trim(),
                        state:        (addr.addressRegion || '').trim(),
                        zip:          (addr.postalCode || '').trim(),
                        country:      (addr.addressCountry || '').trim(),
                        latitude:     (p.geo || {{}}).latitude || '',
                        longitude:    (p.geo || {{}}).longitude || '',
                        rating:       (p.aggregateRating || {{}}).ratingValue || '',
                        rating_count: (p.aggregateRating || {{}}).reviewCount || '',
                    }};
                }} catch(e) {{}}
            }}
            return {{}};
        """) or {}
    except Exception as e:
        log(f"Enrich failed for {url}: {e}")
        return {}


def build_search_url(keyword, location, page_num=1):
    url = f"https://www.bbb.org/search?find_text={quote(keyword)}&find_loc={quote(location)}"
    if page_num > 1:
        url += f"&page={page_num}"
    return url


def build_category_url(base, page_num=1):
    base = base.rstrip("/").split("?")[0]
    return f"{base}?page={page_num}" if page_num > 1 else base


def scrape(args) -> list:
    log("Starting undetected-chromedriver...")
    driver = create_driver(args)
    all_results = []

    try:
        log("Warming up bbb.org...")
        driver.get("https://www.bbb.org/")
        if not wait_for_cf(driver, timeout=30):
            log("Cloudflare did not clear on homepage.")
            log("Make sure you are using a residential proxy via run_with_proxy.sh")
        else:
            dismiss_modals(driver)

        max_pages = args.max_pages or 9999
        page_num = 1

        while page_num <= max_pages:
            if args.mode == "search":
                url = build_search_url(args.keyword, args.location, page_num)
            else:
                url = build_category_url(args.category_url, page_num)

            log(f"Page {page_num}: {url}")
            driver.get(url)
            wait_ready(driver)

            if is_blocked(driver):
                log("Cloudflare block detected — waiting...")
                if not wait_for_cf(driver, timeout=30):
                    log("Could not bypass Cloudflare. Check your proxy.")
                    break

            dismiss_modals(driver)

            cards = extract_cards(driver, args.debug, str(page_num))
            if not cards:
                log("No results — stopping")
                break

            if args.enrich:
                for i, card in enumerate(cards):
                    if card.get("profile_url"):
                        log(f"  Enriching {i+1}/{len(cards)}: {card['name']}")
                        extra = enrich_profile(driver, card["profile_url"], args.debug)
                        card.update(extra)
                        rand_sleep(1.5, 3.0)
                # Return to listing
                driver.get(url)
                wait_ready(driver)

            all_results.extend(cards)
            log(f"Total collected: {len(all_results)}")

            last_page = get_last_page(driver)
            if page_num >= last_page:
                log(f"Reached last page ({last_page})")
                break

            page_num += 1
            rand_sleep(2.0, 4.0)

    finally:
        driver.quit()

    return all_results


def save_results(results, output):
    if not results:
        log("No results to save")
        return
    ext = Path(output).suffix.lower()
    if ext == ".csv":
        keys = list(results[0].keys())
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(results)
    else:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    log(f"Saved {len(results)} records → {output}")


def main():
    parser = argparse.ArgumentParser(
        description="BBB scraper — undetected-chromedriver + mitmproxy"
    )
    parser.add_argument("--mode", choices=["search", "category"], default="search")
    parser.add_argument("--keyword",      default="", help="Business type (search mode)")
    parser.add_argument("--location",     default="", help="City/state, e.g. 'New York, NY'")
    parser.add_argument("--category-url", default="",
                        help="e.g. https://www.bbb.org/us/category/restaurants")
    parser.add_argument("--output",       default="bbb_results.json")
    parser.add_argument("--max-pages",    type=int, default=0, help="0 = all pages")
    parser.add_argument("--proxy",        default=os.environ.get("TWO_PRX_URL", ""),
                        help="http://host:port (use run_with_proxy.sh for auth)")
    parser.add_argument("--2captcha-key", dest="captcha_key",
                        default=os.environ.get("APIKEY_2CAPTCHA", ""))
    parser.add_argument("--chrome-version", type=int, default=0,
                        help="Chrome major version if uc can't detect it")
    parser.add_argument("--enrich",   action="store_true",
                        help="Visit each profile for full JSON-LD data")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--debug",    action="store_true", help="Save HTML to debug/")
    args = parser.parse_args()

    if args.mode == "search" and not (args.keyword and args.location):
        parser.error("--mode search requires --keyword and --location")
    if args.mode == "category" and not args.category_url:
        parser.error("--mode category requires --category-url")
    if args.debug:
        DEBUG_DIR.mkdir(exist_ok=True)

    results = scrape(args)
    save_results(results, args.output)


if __name__ == "__main__":
    main()
