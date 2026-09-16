# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) as closely as a
CLI toolkit can. A PATCH release means fixes — it does not promise that every
flag and default is frozen, so a behaviour-changing default can appear in one.
When it does, the release notes lead with it.

## [1.0.1] — 2026-09-16

> **A marker was wrong, and it was one that could have cost money.**
> `cf-turnstile` fired on **five of five** pages BBB actually served. Only
> the signal ordering in `detect_page_state` kept a good page from being
> reported as a challenge — and `challenge` is a state that spends.

### Fixed

- **`cf-turnstile` removed from `BOT_CHALLENGE_MARKERS`.** It fires on every
  page fetched through the 2Captcha Scraping Browser, because that product's
  auto-solve extension injects its own hunters into every page it loads —
  and it missed one of the two real challenges. `/turnstile/v0/api.js`
  removed with it: 0 occurrences anywhere, served or refused.
  `challenges.cloudflare.com` added.
- The marker guard in `smoke_test.py` had been passing for the WRONG reason
  — it ran only against BBB's 404, fetched with plain curl, which carries no
  extension injection. It now also runs against a listing fetched THROUGH
  the Scraping Browser, and asserts in both directions: no marker on a
  served page, every marker firing on a real challenge, and each excluded
  string really present on a served page.

### Documented

- **BBB's own captcha**, which is not the one above: every served page
  carries a reCAPTCHA **Enterprise** configuration
  (`render=<sitekey>`, so v3 rather than a v2 checkbox) guarding its review
  and complaint forms. This scraper never touches those forms.

[1.0.1]: https://github.com/2scraper/bbb-scraper/releases/tag/v1.0.1

## [1.0.0] — 2026-09-16

First release of the rewritten scraper. Everything before this was four
standalone scripts with no shared row model, no exit-code contract, no
sidecar and no tests; none of it is carried forward.

### The headline

**The search and category modes need no account, no key and no proxy.** BBB
renders its listings out of its own JSON endpoint, and that endpoint is not
behind Cloudflare — measured from a datacenter VPS that gets HTTP 403 on
every HTML page on the site. A two-page run from that machine with no `.env`
present returned 30 rows and `status: complete`.

`--mode profile` is the one that needs a residential exit, and it is the one
the 2Captcha products are for: a profile page IS gated, and there is no
endpoint for it.

### Added

- Three engines — `playwright_scraper.py` (primary, the only one with
  `--concurrency`), `selenium_scraper.py`, `puppeteer_scraper.py` — plus
  `scraper_api_client.py` for the browserless 2Captcha Scraper API path.
- Three modes: `search`, `category`, `profile`.
- `Business`, a 53-column row model. The family prefix (`source`,
  `scraped_at`, `url`, `sku`, `title`) is byte-identical to the sibling
  repos'; the six commerce columns are absent because BBB is a directory and
  they would be null on every row of every run.
- `page_flow.py` — the retry/solve/blocked decision as data, so three engines
  cannot disagree about it.
- `diff_runs.py`, rewritten for a directory: it tracks a business's standing
  and contact details rather than prices, and refuses to diff two runs that
  used different orderings.
- `smoke_test.py` — 340 offline checks on fixtures cut from real captures,
  passing with no engine library installed.
- A canary that runs a real 3-page listing daily **with no secrets**, plus a
  profile half that skips with a notice when none is set.

### Site behaviour worth knowing before the first run

- **"Best Match" is not a relevance ordering.** BBB's own default returned
  15/15 BBB Accredited businesses and, for `find_text=restaurants`, not one
  restaurant. `--sort` therefore defaults to **`a-z`** — this repo's one
  deliberate disagreement with the site — and `sort` is a COLUMN, because it
  changes which businesses are in the file rather than only their order.
  Pass `--sort best-match` to reproduce what a visitor sees.
- **A complete run can be a 1.2% sample.** Every query is capped at 15 pages
  of 15 however many it matched; one measured search reported 19,016 results
  against 15 pages. The sidecar records `total_results`, `pages_available`,
  `capped_by_site` and `reachable_max`. Page 16 answers HTTP 500, so runs
  plan against the site's own number and never ask for it.
- **An ungraded business is null, not zero.** `rating: ""` with
  `ratingScore: 0.0` is "not graded" — 7 of 105 measured rows — and both
  columns go null together.
- **`sku` identifies a business at a LOCATION.** One business appeared twice
  on a page of fifteen under two address ids. Dedupe on `sku`, never on
  `businessId`.
- **`position` is not stable between runs.** BBB's A-Z ordering does not
  break a tie between two locations of one business deterministically:
  three engines running the identical query returned the identical 30 `sku`s
  with two rows swapped. The set is stable; the order within a tie is not.

### Engine limits, stated rather than left to be discovered

- `selenium_scraper.py` cannot authenticate a remote CDP endpoint or a
  proxy, so it refuses a credentialled `--cdp-endpoint` with exit 2. That
  makes it the wrong engine for `--mode profile` and a perfectly good one for
  the two listing modes.
- `puppeteer_scraper.py` downloads its own Chromium, which failed to launch
  on the development machine; `--chromium-path` points it at another one.
- `scraper_api_client.py` requires `--cdp-url` on this site: its own exits
  are datacenter addresses and BBB refuses them.

### Not collected, deliberately

BBB names a business's officers as individuals. There is no column for them
and the committed profile fixture carries placeholders: republishing a named
person's details is a separate act from the site showing them on its own
page.

[1.0.0]: https://github.com/2scraper/bbb-scraper/releases/tag/v1.0.0
