"""
BBB (Better Business Bureau) Scraper — Selenium
GitHub: https://github.com/2scraper/bbb-scraper
License: MIT

⚠️  IMPORTANT: BBB uses Cloudflare Managed Challenge.
    This script requires a residential proxy routed through mitmproxy.
    Use run_with_proxy.sh which handles everything automatically, or start
    mitmproxy manually and pass --proxy http://localhost:18080

    For the verified working setup see bbb_uc.py and run_with_proxy.sh.

Requirements:
    pip install selenium webdriver-manager mitmproxy 2captcha-python

Two scraping modes:
    search    /search?find_text=...&find_loc=...
    category  /us/category/{slug}?page=N

Usage:
    python bbb_selenium.py --mode search \\
        --keyword "restaurants" --location "New York, NY" \\
        --proxy http://localhost:18080
    python bbb_selenium.py --mode category \\
        --category-url https://www.bbb.org/us/category/restaurants \\
        --proxy http://localhost:18080 --output results.csv --max-pages 5
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

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False

try:
    from twocaptcha import TwoCaptcha
    HAS_2CAPTCHA = True
except ImportError:
    HAS_2CAPTCHA = False

DEBUG_DIR = Path("debug")

SEL_CARD_LIST  = "div.page-vertical-padding > div:nth-child(3) > div > div.not-sidebar > div.stack > div > div"
SEL_LAST_PAGE  = ".bds-last-page"
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
    options = Options()
    if not args.headed:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1440,900")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--lang=en-US")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if args.proxy:
        m = re.match(r"https?://(?:[^:@]+:[^@]+@)?([^:]+):(\d+)", args.proxy)
        if m:
            options.add_argument(f"--proxy-server=http://{m.group(1)}:{m.group(2)}")
            log(f"Proxy: {m.group(1)}:{m.group(2)}")

    if HAS_WDM:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver


def wait_ready(driver, timeout=20):
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
    try:
        if "just a moment" in driver.title.lower():
            return True
    except Exception:
        pass
    return False


def wait_for_cf(driver, timeout=30) -> bool:
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
                        logo, site_url: siteUrl,
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
    log("Starting Selenium...")
    driver = create_driver(args)
    all_results = []

    try:
        log("Warming up bbb.org...")
        driver.get("https://www.bbb.org/")
        if not wait_for_cf(driver, timeout=30):
            log("Cloudflare did not clear. Use run_with_proxy.sh with a residential proxy.")
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
                if not wait_for_cf(driver, timeout=30):
                    log("Could not bypass Cloudflare")
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
    parser = argparse.ArgumentParser(description="BBB scraper — Selenium")
    parser.add_argument("--mode", choices=["search", "category"], default="search")
    parser.add_argument("--keyword",      default="")
    parser.add_argument("--location",     default="")
    parser.add_argument("--category-url", default="")
    parser.add_argument("--output",       default="bbb_results.json")
    parser.add_argument("--max-pages",    type=int, default=0)
    parser.add_argument("--proxy",        default=os.environ.get("TWO_PRX_URL", ""))
    parser.add_argument("--2captcha-key", dest="captcha_key",
                        default=os.environ.get("APIKEY_2CAPTCHA", ""))
    parser.add_argument("--enrich",  action="store_true")
    parser.add_argument("--headed",  action="store_true")
    parser.add_argument("--debug",   action="store_true")
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
