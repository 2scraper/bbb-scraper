"""
BBB (Better Business Bureau) Scraper — Playwright
GitHub: https://github.com/2scraper/bbb-scraper
License: MIT

⚠️  IMPORTANT: BBB uses Cloudflare Managed Challenge.
    This script requires a residential proxy routed through mitmproxy.
    Use run_with_proxy.sh which handles everything automatically, or:

    1. Start mitmproxy relay:
       mitmdump --listen-port 18080 \\
           --mode upstream:http://PROXY_HOST:PORT \\
           --upstream-auth USER:PASS --ssl-insecure --quiet &

    2. Run this script:
       python bbb_playwright.py --mode search \\
           --keyword "restaurants" --location "New York, NY" \\
           --proxy http://localhost:18080

    For the verified working setup see bbb_uc.py and run_with_proxy.sh.

Requirements:
    pip install playwright mitmproxy 2captcha-python
    playwright install chromium

Two scraping modes:
    search    /search?find_text=...&find_loc=...
    category  /us/category/{slug}?page=N

Usage:
    python bbb_playwright.py --mode search \\
        --keyword "restaurants" --location "New York, NY"
    python bbb_playwright.py --mode category \\
        --category-url https://www.bbb.org/us/category/restaurants
    python bbb_playwright.py --mode search \\
        --keyword "plumbers" --location "Chicago, IL" \\
        --output results.csv --max-pages 5 --proxy http://localhost:18080 \\
        --enrich --debug
"""

import asyncio
import argparse
import json
import csv
import os
import re
import time
import random
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

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


def log(msg: str):
    print(f"[BBB] {msg}", flush=True)


def save_debug(name: str, content: str, ext: str = "html"):
    DEBUG_DIR.mkdir(exist_ok=True)
    (DEBUG_DIR / f"{name}.{ext}").write_text(content, encoding="utf-8")
    log(f"[debug] {name}.{ext}")


async def rand_sleep(lo: float = 1.5, hi: float = 3.5):
    await asyncio.sleep(random.uniform(lo, hi))


async def is_blocked(page) -> bool:
    try:
        title = await page.title()
        if "just a moment" in title.lower():
            return True
    except Exception:
        pass
    for frame in page.frames:
        if "challenges.cloudflare.com" in frame.url and "turnstile" in frame.url:
            return True
    return False


async def wait_for_cf(page, timeout: int = 30) -> bool:
    for i in range(timeout):
        if not await is_blocked(page):
            if i > 0:
                log(f"Cloudflare cleared after {i}s")
            return True
        await asyncio.sleep(1)
    return False


async def dismiss_modals(page):
    for sel in [SEL_MODAL_CTRY, SEL_MODAL_DLG]:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.click()
                await rand_sleep(0.5, 1.0)
        except Exception:
            pass


async def wait_ready(page, timeout: int = 20000):
    try:
        await page.wait_for_function("document.readyState === 'complete'", timeout=timeout)
    except Exception:
        pass
    await rand_sleep(1.0, 2.0)


async def get_last_page(page) -> int:
    try:
        el = await page.query_selector(SEL_LAST_PAGE)
        if not el:
            return 1
        href = await el.get_attribute("href")
        if href:
            qs = parse_qs(urlparse(href).query)
            return int(qs.get("page", [1])[0])
    except Exception:
        pass
    return 1


async def extract_cards(page, debug: bool, label: str = "") -> list:
    if debug:
        content = await page.content()
        save_debug(f"listing_{label or int(time.time())}", content)

    try:
        await page.wait_for_selector(SEL_CARD_LIST, timeout=15000)
    except PWTimeout:
        log("No result cards found")
        return []

    cards = await page.evaluate(f"""
        () => {{
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
        }}
    """) or []

    log(f"Extracted {len(cards)} businesses")
    return cards


async def enrich_profile(page, url: str, debug: bool) -> dict:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await wait_ready(page)
        if debug:
            content = await page.content()
            slug = re.sub(r"[^a-z0-9]", "_", url.split("/")[-1][:40])
            save_debug(f"profile_{slug}", content)

        return await page.evaluate(f"""
            () => {{
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
            }}
        """) or {}
    except Exception as e:
        log(f"Enrich failed for {url}: {e}")
        return {}


def build_search_url(keyword: str, location: str, page_num: int = 1) -> str:
    url = f"https://www.bbb.org/search?find_text={quote(keyword)}&find_loc={quote(location)}"
    if page_num > 1:
        url += f"&page={page_num}"
    return url


def build_category_url(base: str, page_num: int = 1) -> str:
    base = base.rstrip("/").split("?")[0]
    return f"{base}?page={page_num}" if page_num > 1 else base


async def scrape(args) -> list:
    proxy_settings = {}
    if args.proxy:
        m = re.match(r"https?://(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)", args.proxy)
        if m:
            proxy_settings = {"server": f"http://{m.group(3)}:{m.group(4)}"}
            if m.group(1):
                proxy_settings["username"] = m.group(1)
                proxy_settings["password"] = m.group(2)

    all_results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not args.headed,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--ignore-certificate-errors",
            ],
            slow_mo=random.randint(50, 150),
            **({"proxy": proxy_settings} if proxy_settings else {}),
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            ignore_https_errors=True,
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()

        log("Warming up bbb.org...")
        try:
            await page.goto("https://www.bbb.org/", wait_until="domcontentloaded", timeout=30000)
            await wait_ready(page)
            if not await wait_for_cf(page, timeout=30):
                log("Cloudflare did not clear. Make sure you are using a residential proxy.")
                log("See run_with_proxy.sh for the verified working setup.")
            await dismiss_modals(page)
        except Exception as e:
            log(f"Warmup: {e}")

        max_pages = args.max_pages or 9999
        page_num = 1

        while page_num <= max_pages:
            if args.mode == "search":
                url = build_search_url(args.keyword, args.location, page_num)
            else:
                url = build_category_url(args.category_url, page_num)

            log(f"Page {page_num}: {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await wait_ready(page)
            except PWTimeout:
                log(f"Timeout on page {page_num}, stopping")
                break

            if await is_blocked(page):
                log("Cloudflare block detected")
                if not await wait_for_cf(page, timeout=30):
                    log("Could not bypass Cloudflare. Check your proxy.")
                    break

            await dismiss_modals(page)

            cards = await extract_cards(page, args.debug, str(page_num))
            if not cards:
                log("No results — stopping")
                break

            if args.enrich:
                ep = await context.new_page()
                for i, card in enumerate(cards):
                    if card.get("profile_url"):
                        log(f"  Enriching {i+1}/{len(cards)}: {card['name']}")
                        extra = await enrich_profile(ep, card["profile_url"], args.debug)
                        card.update(extra)
                        await rand_sleep(1.5, 3.0)
                await ep.close()

            all_results.extend(cards)
            log(f"Total collected: {len(all_results)}")

            last_page = await get_last_page(page)
            if page_num >= last_page:
                log(f"Reached last page ({last_page})")
                break

            page_num += 1
            await rand_sleep(2.0, 4.0)

        await browser.close()

    return all_results


def save_results(results: list, output: str):
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
        description="BBB scraper — Playwright (requires residential proxy via mitmproxy)"
    )
    parser.add_argument("--mode", choices=["search", "category"], default="search")
    parser.add_argument("--keyword",      default="")
    parser.add_argument("--location",     default="")
    parser.add_argument("--category-url", default="")
    parser.add_argument("--output",       default="bbb_results.json")
    parser.add_argument("--max-pages",    type=int, default=0)
    parser.add_argument("--proxy",        default=os.environ.get("TWO_PRX_URL", ""),
                        help="http://host:port — use run_with_proxy.sh for auth")
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

    results = asyncio.run(scrape(args))
    save_results(results, args.output)


if __name__ == "__main__":
    main()
