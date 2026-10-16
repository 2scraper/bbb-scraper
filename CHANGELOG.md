# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) as closely as a
CLI toolkit can. A PATCH release means fixes — it does not promise that every
flag and default is frozen, so a behaviour-changing default can appear in one.
When it does, the release notes lead with it.

## [Unreleased]

### Changed

- CI only: the canary's paid `profile` job now runs weekly (Mondays 05:40 UTC)
  and on a manual dispatch; the free `listing` job still runs daily.

## [1.1.0] — 2026-10-15

> **Behaviour change:** `--retries 0` is now a usage error (exit 2). It used
> to be accepted and made the page-load loop unreachable, so nothing was
> fetched and the engine reported the browser's own blank page as a result:
> exit 3 or 4 with a 39-byte "capture", in tens of milliseconds. If a
> script of yours passes 0, pass **1**: the flag counts ATTEMPTS, so 1 means
> "try once and do not retry".

> **Behaviour change for existing users:** a page BBB serves that cannot be
> read now fails the run instead of ending it. A run that used to finish
> `complete` with exit 0 on such a page now finishes `partial` with exit 6
> (rows were gathered) or exit 5 (none were), and its sidecar names the page
> in `pages_failed` with `stop_reason: "unreadable_response"`. A pipeline
> that branched on exit 0 was reading an unread page as the end of a listing.

> **Behaviour change:** a URL BBB answers with its own 404 page now stops at
> once with `stop_reason: "not_found"` (exit 5 when nothing was gathered).
> It used to wait 30 s, re-fetch, and end as `unreadable_response`.

> **Behaviour change:** negative values for `--pages`, `--delay`,
> `--retry-delay`, `--proxy-block-retries` and `--concurrency` (and 0 for
> `--pages` and `--concurrency`) are now usage errors (exit 2) in every
> engine, and so is a negative `--retries`/`--retry-delay` in
> `scraper_api_client.py`.

> **Behaviour change:** CSV cells that begin with `=`, `+`, `-`, `@`, a tab
> or a line break are now prefixed with an apostrophe so a spreadsheet does
> not run them as formulas. The JSON output is unchanged; the sidecar says
> how many cells differ in `csv_cells_escaped`. Output files are now written
> through a temporary file and renamed, keeping the mode `open()` would give
> a new file and the existing mode of a file being replaced.

> **Correction to v1.0.1.** Its README billed the Managed Challenge solve to
> the wrong product. It read "**Captcha solving** (`--twocaptcha-key`) — for
> the Managed Challenge only" with a table marking that challenge
> **solvable**. The challenge is indeed solvable, but not by anything a
> `--twocaptcha-key` buys here: `captcha_solver.py` builds `RecaptchaV2Task`,
> `RecaptchaV2TaskProxyless` and `RecaptchaV3TaskProxyless` and no Turnstile
> task at all. What actually clears it is `Captcha.setAutoSolve` over
> `--cdp-endpoint`, which is the Scraping Browser API — a separately billed
> product. A reader following the old README would have set a key, met the
> challenge anyway, and had no way to tell why.

### Fixed

- **The Selenium and pyppeteer engines crashed on a block with no proxy.**
  With no `--proxy`, a blocked page (and, in pyppeteer, a dead exit) reached
  `pool.current`/`pool.advance` on None: a traceback, exit 1, and every row
  from earlier pages lost. Both now re-fetch on the same path, as the
  Playwright engine always did, and report exit 3.
- **An interrupted write destroyed the previous good output.** `out.json`,
  `out.csv` and `out.meta.json` were truncated before being written, so a
  failure halfway left a short or empty file. They are now written to a
  temporary file beside the target, synced and renamed over it.
- **The HTTP status never reached the classifier.** Playwright and
  pyppeteer now pass the response status, so BBB's 404 is terminal and a
  marker-less 403 is a block. Selenium has no status to pass; BBB's own 404
  title is recognised in the body for it.
- **A `BBB_URL` from `.env` blocked `--text`/`--location`.** A URL from the
  environment is now a default that the query flags replace; a TYPED
  `--url` with query flags is still refused. `.env.example` ships `BBB_URL`
  commented out.
- **The credential scan judged exclusions on the absolute path**, so a
  clone under a directory named `venv` scanned 0 files and reported clean.
  Exclusions are now relative to the repository, a scan of 0 files fails,
  and `.env*` copies plus `.json`, `.csv` and `.html` files are read too.
- **Dump and capture files were not ignored**: `X.page3` from
  `--dump-html`, a raw `.html` at the repository root, and `live/`,
  `out/`-style working directories.
- **The profile canary went green without testing anything.** With no
  `BBB_CDP_ENDPOINT` secret it skipped with a notice, and with one it
  passed on exit 3 or 5. It now fails with an error naming the missing
  secret, and every non-zero exit or empty result is red. The listing
  canary needs no secret and stays scheduled; it now also fails explicitly
  on zero rows.
- `--solve-captcha` help, a docstring and the README's exit-code table
  described refusals and exit 5 inaccurately; corrected. Comments no longer
  point at documents a reader of this repository cannot open.

- **`--retries 0` fetched nothing and called it a result.** The load loop is
  `range(1, retries + 1)`, so 0 never runs it and the engine judged
  `about:blank`. Three third-party audits of this family ran exactly that and
  reported a working scraper as broken. `--retries` now goes through
  `page_flow.attempts` in every entry point; a check reads the source of each
  one and calls the validator, so a new engine declaring the flag with a
  plain int fails the suite.

- **A served page that could not be read was taken for the end of the
  listing.** `unknown` (BBB's page, neither the listing payload nor its own
  empty-result marker) left the page outcome `ok` with no rows, so the loop
  read "added nothing new" as the end of the results. Reproduced with the
  real fetch and finish code and only the browser replaced: page 1 good,
  page 2 with the row container renamed, page 3 good gave `complete`,
  `no_new_products`, exit 0, page 3 never requested. The same state on page 1
  was worse: pages 2 and 3 were still fetched and the run reported `complete`
  over 14 rows instead of 21, the first page lost without a trace. An
  unreadable page is now a failed page in all three engines. A CONFIRMED
  empty result set (`totalResults: 0`) is unchanged: it is still a finished
  page and still ends a run as complete.

- **The captcha budget was advisory.** `handle_captcha_if_present` runs once
  before a page is classified and once after, and only the second call was
  counted, while the first repeats on every block-retry: three solver calls
  against `SOLVES_PER_PAGE = 1`, reproduced with the solver stubbed and no
  proxy pool. The allowance is now one `page_flow.SolveBudget` per page,
  spent at the call to the solver itself in all three engines (counting calls
  would have spent it on a call that found no captcha). It does not reset on
  rotation. There is deliberately no run-wide cap on top of it: a run of N
  pages that each need a solve has bought what it needed.

- **`diff_runs.py` compared two different queries.** The sort guard caught two
  orderings of one query and nothing caught two queries, so `restaurants` in
  New York against `restaurants` in Chicago, both a-z, both complete, was
  accepted and every line of the diff was the change of city. The scope is
  now read from each sidecar's `final_url` (query text, location, `--state`,
  category; page, sort, host and `www.` ignored) and a mismatch is refused,
  with `--force` as before. A run the user cut at `--pages 3` is refused
  against one that read 15 for the same reason; a listing that merely ran
  out of pages is not. `diff_runs.py` had no behavioural check at all
  (CI ran `--help`), so its sort and completeness guards are pinned too.

- **The Scraper API path (`scraper_api_client.py`) failed whenever a wait flag
  was given, and never saw the target's status.** Measured 2026-09-23 against
  the live `/tasks/sync` endpoint: `waitFor` sent as a JSON-encoded string
  (what this client sent) is answered HTTP 422 "params.waitFor must be an
  object" and is still billed ($0.0005); sent as an object it is answered
  HTTP 200. It is now an object. And the response's `status` is the API's own
  verdict ("success"), while the target site's HTTP code is `http_code` — the
  client handed `status` onward, so a target 403/503 never reached the page
  classifier. It now reads `http_code`, falling back to `status` only if that
  is an integer. After the fix, one live call (`--mode profile --wait-text BBB`, the README's profile URL, no `--cdp-url`) answered HTTP 200, upstream 200, 1 business row.

- **Issue templates described Etsy, not BBB.** The bug-report hints talked
  about DataDome, `t=bv`, `shop_rating` and exit-IP storefronts, the example
  command scraped an Etsy search, and the site-change template pointed at a
  JSON-LD parser this repo does not have. Rewritten from this repo's README:
  the Cloudflare-gated profile mode, the 15-pages-of-15 cap, unrated
  businesses written as null, and the `__PRELOADED_STATE__` anchors. The
  bug-report and question templates no longer link a `TROUBLESHOOTING.md`
  this repo never had.
- **`--retries` help said an empty "hub category" is a correct answer** in
  all three engines. BBB has no hub categories; the empty case here is a
  query that matched nothing, and the help now says so. The matching engine
  comments were corrected the same way.
- Comments that described another site's pages as this one's: the
  `akamaihd.net` hub-page incident is now attributed to the sibling
  tokopedia-scraper where it happened, the pyppeteer engine's single-page
  note no longer talks about a `--mode shop` this repo does not have, and
  `captcha_solver.py` no longer points at a "No DataDome solver" section
  that does not exist.
- `SECURITY.md` said this project has no releases or tags; it has both.
- The access badge's alt text now says what its label already did: the
  listing modes run without an account (profile mode needs a residential
  exit).

- **The README now names the product that clears each refusal**, and says in
  those words that **this repo does not implement `TurnstileTaskProxyless`**
  — nor the init script that captures `sitekey`, `action`, `cData` and
  `chlPageData` from Cloudflare's single call to `turnstile.render()`, which
  is the only way to obtain them because they appear nowhere in the served
  HTML. That is a gap in this repo, not in 2Captcha, which solves Turnstile;
  `foodpanda-scraper` in this family implements the interception.
- **Why it is not implemented is now stated with the measurement behind it**:
  the listing path is not behind Cloudflare at all, and the profile path —
  which is — already needs a Scraping Browser session to be reachable
  (measured 2026-09-16: the same profile URL returned 403 direct and 200 in
  full through one). On that path the auto-solve clears the challenge before
  a local solver would get a turn.

- **Fifteen lines of unreachable code removed from `playwright_scraper.py`.**
  A function's `def` line had been lost before this repo's first commit,
  leaving its docstring and its `try: return page.content()` body indented
  into the end of `_mask_credentials`, where the control flow can never
  arrive. Nothing called it — `_content_when_settled` below it does the job.
  The same fifteen lines, byte for byte, were in six repos of this family.
- **Five dead helpers removed from the engines**, two of them calling a
  `page_flow` API that does not exist in this repo: `page_flow.comparable`,
  `page_flow.next_page_selector` and `page_flow.next_page_candidates` are
  Tokopedia's, and came here with the code — one docstring still described
  Tokopedia's `keyword=kopi&search_id=` tracking tail. Nothing called any of
  the five. This repo paginates on `page_url()` and the endpoint's own
  `totalPages`, which is the arithmetic the site does for us, so a
  selector-walking next-page helper had no role here even had it worked.
- `parse_qsl` is no longer imported by the two engines that only used it in
  the helper above. Six other unused imports predate this work and are left
  alone deliberately, so this diff stays attributable.

### Added

- **The signature-binding check no longer swallows a name that is absent.**
  It resolved the callee with `getattr(owner, attr, None)` and skipped
  anything that came back not-callable — so a call into a shared module
  function that **does not exist** was treated as "nothing to bind" rather
  than as the loudest failure available. That is how three calls into a
  `page_flow` API this repo does not have sat in two engines under a green
  run of that very check. Absent is now a failure, naming the caller and its
  line. Verified by control: adding a call to `page_flow.does_not_exist()`
  turns the suite red.

- **`check_captcha_capability_claims_match_the_code`** — a guard in both
  directions. It fails the build on a documented claim that a captcha cannot
  be solved, and on a README that credits a Turnstile solve to this repo
  while no Turnstile task type is built. The second is asserted as a PAIRING
  — task type absent ⇒ the README must carry the disclaimer in those words —
  so it cannot go quiet the way a keyword search can. Verified by control:
  flipping the disclaimer turns the suite red.

- **A check for a statement the control flow can never reach**, and one that
  the existing undefined-name walk cannot see by design: that walk pools
  every binding in a file rather than tracking scopes, so a name used inside
  dead code passes as long as anything else in the module binds it. The new
  check is narrow — a statement after a `return`/`raise`/`break`/`continue`
  in the SAME block — and across the eighteen repos of this family it found
  six real problems and zero false positives. Verified by control.

[1.1.0]: https://github.com/2scraper/bbb-scraper/releases/tag/v1.1.0

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
