# BBB Scraper by 2scraper

**Open-source Better Business Bureau scraper for bbb.org — three engines, your own infrastructure by default, 2Captcha's paid products only where they are actually needed.**

Pull business listings and full business profiles — name, address, phone numbers, BBB letter grade, accreditation status and date, categories, coordinates, complaint and review counts — straight from BBB into JSON or CSV.

[**View source on GitHub →**](https://github.com/2scraper/bbb-scraper)

---

## What was actually measured

**The search and category modes need no account, no key and no proxy.**

BBB renders its listings out of its own JSON endpoint, and that endpoint is not behind Cloudflare. Measured on 2026-09-16 from a datacenter VPS — an address that gets HTTP **403 on every HTML page on the site** — the endpoint answered **HTTP 200 with 15 complete business records**. A two-page run from that machine, with no credentials configured at all, returned 30 rows and `status: complete`.

Say that plainly instead of selling around it. What the paid products below actually buy on this site is **one specific thing**: a business **profile** page, which *is* Cloudflare-gated and has no endpoint behind it — so accreditation dates, complaint counts and the full rating rationale are reachable only through a rendered page from a residential exit.

Full numbers are in the [repository README](https://github.com/2scraper/bbb-scraper#readme).

## What you get

- Free, open-source scraper, one script per engine — **Playwright** (primary), **Selenium** and **Puppeteer** (via pyppeteer), all producing the identical output schema and exit codes, plus a browserless client for 2Captcha's Scraper API
- Three modes: a keyword **search**, a **category** listing, or a single business **profile**
- Reads BBB's own embedded state payload (`window.__PRELOADED_STATE__`) — the same object its endpoint returns — so there is no fragile DOM scraping anywhere in the primary path
- JSON and CSV export, a documented 53-column `Business` schema, and a `.meta.json` sidecar on every run recording status, pages completed, and **BBB's own result arithmetic**
- 340 offline checks, and a daily canary that runs a real 3-page scrape **with no secrets**
- Optional 2Captcha integration, wired in but never required to get started

## Three things about BBB worth knowing before you start

**"Best Match" is not a relevance ordering.** BBB's own default sort returned **15 of 15 BBB Accredited businesses** on every page tested and, for a search on `restaurants`, not one restaurant. The same query sorted A-Z returned **0 of 15 accredited** and real name-matched restaurants. This scraper therefore defaults to A-Z and records the ordering as a column, because it changes *which* businesses end up in your file rather than just their order.

**A complete run can still be a small sample.** BBB caps every query at 15 pages of 15 — 225 rows — however many it matched. One measured search reported **19,016 results**. The run is genuinely complete; the sidecar records both numbers so you can tell "complete" from "exhaustive", and narrowing by state or category is how you go deeper.

**An unrated business is not rated zero.** BBB returns an empty grade with a score of `0.0` for a business it has not graded. Written through, that zero quietly drags every average you compute, so both columns come back null instead.

## 2Captcha products, when you want them

| Product | What it's for on this site |
|---|---|
| **Residential proxies** | The thing that makes `--mode profile` work. BBB refuses a datacenter address outright — measured identical on six consecutive attempts over 30 seconds, headless and headful alike. |
| **Scraping Browser API** | A remote browser you do not run or patch, with a chosen exit country. Every live profile measurement in the README was taken through it. |
| **Captcha solving** | For BBB's Managed Challenge only. The site refuses in two shapes and only one of them is a solvable test — the scraper tells them apart and never spends on the other. |
| **Fingerprints** | A consistent device identity for a local browser. |

One key, four separately-billed products: [2captcha.com](https://2captcha.com)

## What it deliberately does not do

BBB lists a business's officers by name. There is no column for them, and the committed test fixtures carry placeholders instead: republishing a named person's details is a separate act from the site showing them on its own page.

It reads public pages only, never submits BBB's review or complaint forms, and defaults to a 2-second delay between pages.

---

MIT licensed. Not affiliated with or endorsed by the Better Business Bureau.
