"""
page_flow.py
------------
The retry / solve / blocked decision, as DATA rather than as three copies of
an if-chain.

BBB answers a request five ways, and four of them want a different response:

    the /api/search payload with businesses in it      -> parse
    the same payload with `totalResults: 0`            -> parse, it is an answer
    a Cloudflare Managed Challenge, HTTP 403           -> solve, or rotate
    a hard "You have been blocked", HTTP 403           -> rotate; nothing to solve
    something BBB served that is not the payload       -> wait, then retry

Three copies of that triage across three engines would drift, and the drift
would be silent — one engine reporting exit 3 where its twin reports exit 0
on the same response.

Nothing here imports a browser, and **no JavaScript crosses this boundary**:
Selenium's `execute_script` takes a function BODY with an explicit `return`
while Playwright and pyppeteer take `() => expr`, so a shared snippet would
quietly acquire one driver's dialect. The callbacks below are named for the
OPERATION instead, and each engine spells it in its own dialect.
"""

import logging
from typing import Callable, Optional
from urllib.parse import urlparse

from product_parser import (MAX_PAGES, detect_bot_challenge,  # noqa: F401
                            detect_page_state)

log = logging.getLogger("page_flow")


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

# How many business records mean "this response is a listing".
#
# `> 1` on purpose: waiting for ONE match resolves on
# something unrelated long before the listing is really there. BBB serves a
# fixed `pageSize` of 15 and every captured page held exactly 15, so four is
# a floor that a real page clears instantly and a half-arrived one does not.
MIN_CARD_MATCHES = 4

# A listing page's own readiness anchor for the browser engines. BBB's search
# results are a list of profile links, and a profile path is a six-segment
# contract with search engines rather than a build-generated class: anchor
# on a URL pattern, never on a CSS class.
READY_SELECTOR_LISTING = 'a[href*="/profile/"]'

# A profile page states the business's name in an `h1`.
READY_SELECTOR_PROFILE = 'h1'

CONTENT_TIMEOUT_MS = 45_000
CONTENT_TIMEOUT_MS_PROFILE = 30_000


def ready_selector(mode: str) -> str:
    return READY_SELECTOR_PROFILE if mode == "profile" else READY_SELECTOR_LISTING


def min_matches(mode: str) -> int:
    return 1 if mode == "profile" else MIN_CARD_MATCHES


def content_timeout_ms(mode: str) -> int:
    return CONTENT_TIMEOUT_MS_PROFILE if mode == "profile" else CONTENT_TIMEOUT_MS


# How long to keep polling for an anchor, and how often.
#
# Polled through `count(selector)` — a callback each engine implements with
# its own `querySelectorAll` call — and NEVER by handing the browser a string
# to evaluate. Measured on a sibling site: a site whose Content-Security-Policy omits
# `unsafe-eval` kills `wait_for_function` with an `EvalError` and takes the
# run down with exit 1. BBB has not been measured for that, and the cheap
# habit costs nothing on a site that would have allowed it.
READY_POLL_MS = 500


def wait_for_count(count: Callable[[str], int], selector: str, minimum: int,
                   timeout_ms: int, sleep_ms: Callable[[int], None]) -> int:
    """Poll `count(selector)` until it reaches `minimum` or the budget runs out.

    Returns the last count seen, so a caller can report "3 of 4 expected"
    rather than only that it timed out.
    """
    waited = 0
    seen = 0
    while waited <= timeout_ms:
        seen = count(selector)
        if seen >= minimum:
            return seen
        sleep_ms(READY_POLL_MS)
        waited += READY_POLL_MS
    return seen


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------

def classify(html: Optional[str], status: Optional[int] = None,
             url: str = "") -> str:
    """Name what BBB answered with. See product_parser.detect_page_state.

    The argument ORDER is the contract: every engine calls
    `classify(html, status, url)`. A sibling repo shipped `classify(html,
    url=...)` in two of three engines against a callee that took `status`
    second, and both crashed on their first fetch — invisible to import,
    `--help`, `compileall` and 400+ green offline assertions, because none of
    those calls a function the way a live run does. `smoke_test.py`
    binds every engine's call against this signature for that reason.
    """
    return detect_page_state(html or "", status, url)


STATE_POLICY = {
    # The payload, with businesses in it.
    "content":   {"retry": False, "solve": False, "blocked": False, "parse": True},
    # The payload, with `totalResults: 0`. BBB served exactly what was asked
    # for and it matched nothing — a real answer, and EXIT_NO_PRODUCTS rather
    # than EXIT_BLOCKED. Reporting it as blocked sends a user hunting for a
    # proxy problem that is not there.
    "empty":     {"retry": False, "solve": False, "blocked": False, "parse": True},
    # The hard refusal: HTTP 403, "You have been blocked", no challenge
    # widget anywhere on it. There is nothing to solve — Cloudflare is not
    # offering a test, it is declining — so the only move is a different
    # exit. Paying a solver here would buy nothing, which is why `solve` is
    # False on a state whose name says blocked.
    "blocked":   {"retry": True,  "solve": False, "blocked": True,  "parse": False},
    # The Managed Challenge: HTTP 403 with `cf_chl_opt` and a Turnstile
    # widget. This one IS a test, and it is the state that pays for a solver.
    # A fresh browser from a different exit clears it too, which is why
    # `retry` is also True.
    "challenge": {"retry": True,  "solve": True,  "blocked": True,  "parse": False},
    # The address does not exist: BBB's own 404 (by status, or by its own
    # title where the driver gives no status). TERMINAL — retrying spends a
    # fetch on something that will never be there — and not blocked, because
    # exit 3 would send the reader after a proxy problem when the URL is
    # simply wrong. Not parsed: the page holds no rows.
    "not_found": {"retry": False, "solve": False, "blocked": False, "parse": False},
    # Served by BBB — its own assets are on the page — but not the listing
    # payload. A wait, not a spend.
    "unknown":   {"retry": True,  "solve": False, "blocked": False, "parse": False},
}


def should_retry(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["retry"]


def should_solve(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["solve"]


def counts_as_blocked(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["blocked"]


def should_parse(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["parse"]


# Whether a blocked page is worth re-fetching at all.
#
# True here, and CONSULTED rather than merely documented — the engines read
# it, so setting it False really does stop the retry loop. (A sibling repo
# carried this constant with a paragraph of justification and no reader,
# which is the same defect as dead code that looks load-bearing.)
#
# True because on BBB a re-fetch genuinely can change the answer. The
# Scraping Browser was measured serving a page normally that a datacenter
# address could not reach at all, and a profile that refuses once can serve
# the next request; what the site scores is the address and the session, both
# of which a retry can move.
RETRY_ON_BLOCKED = True

# How many times to re-fetch a blocked page when there is no proxy pool to
# rotate into.
#
# One, and only one: without a pool every retry leaves from the same address,
# and BBB's refusal is an address-level decision — a datacenter ASN was
# refused identically on six consecutive polls over 30 s, byte for byte. A
# second attempt from the same exit is a second identical refusal. WITH a
# pool, the engines retry once per remaining exit instead, because there the
# retry changes the one variable the refusal depends on.
BLOCK_RETRIES_WITHOUT_POOL = 1

# At most one solve per page. A challenge that survives a solved token is not
# a challenge this run can pass, and a second solve is a second charge for
# the same answer.
SOLVES_PER_PAGE = 1


class SolveBudget:
    """The solver purchases one page may make, spent at the purchase itself.

    The engines call `handle_captcha_if_present` TWICE per attempt: once
    before the page is classified, so a challenge is cleared before anything
    is judged, and once after, for the state that says the page really is
    gated. Only the second call used to be counted, and the first repeats on
    every block-retry, so `SOLVES_PER_PAGE` was advisory: three solver calls
    against a limit of one, reproduced with the solver stubbed.

    So the count lives here and is spent by whoever is about to BUY, not by
    whoever is about to ask. A call that finds no captcha spends nothing,
    which is why counting calls was the wrong unit. There is deliberately no
    reset on rotation: a fresh exit is a reason to re-fetch, not a fresh
    allowance. One budget per page, and no run-wide cap on top, because a
    run of N pages that each need a solve has bought what it needed.
    """

    def __init__(self, limit: Optional[int] = None):
        self.limit = SOLVES_PER_PAGE if limit is None else limit
        self.spent = 0

    @property
    def left(self) -> bool:
        return self.spent < self.limit

    def spend(self) -> bool:
        """Take one purchase. False, spending nothing, once none are left."""
        if not self.left:
            return False
        self.spent += 1
        return True


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def pagination_is_addressable(url: str) -> bool:
    """Whether page N of this listing can be fetched without walking to it.

    True for every BBB listing, and measured rather than assumed: `page=2` on
    a search returned a full second page of fifteen businesses sharing **0**
    ids with page 1, and the same holds for a category query. BBB paginates
    by an ordinary `?page=N` on an otherwise identical query.

    This question is asked PER URL rather than per
    site — a sibling repo has one page kind that paginates and one that does
    not, where a `page_url()` used unconditionally reported a complete run
    holding page 1. On BBB both kinds answer the same way, so this returns
    True for anything this repo accepts, and the function exists so that the
    engines ask the question rather than assuming the answer.
    """
    parsed = urlparse(url or "")
    return bool(parsed.scheme and parsed.netloc)


def pages_to_plan(pages_requested: int, pages_available: Optional[int]) -> int:
    """How many pages a run may ask for, given what page 1 reported.

    BBB states `totalPages` in every listing response, so the end of the
    listing is known from page 1 rather than discovered by walking off it —
    which matters here because walking off it is not free: `page=16` answers
    **HTTP 500**, not an empty page, so a run that overshoots manufactures a
    server error that reads like a fault in this code.
    """
    ceiling = pages_available if pages_available else MAX_PAGES
    return max(1, min(int(pages_requested), min(ceiling, MAX_PAGES)))


def concurrency_limit(cdp_endpoint: Optional[str]) -> Optional[int]:
    """1 when workers would collide, else None for "no limit imposed here".

    The Scraping Browser API allows ONE live connection per profile, so N
    workers sharing a `pid` collide with `profile_locked`. Several `pid`s,
    one run each, is the way to parallelise that path.
    """
    return 1 if cdp_endpoint else None


# ---------------------------------------------------------------------------
# --retries
# ---------------------------------------------------------------------------
import argparse as _argparse  # noqa: E402  (kept beside its only user)


def attempts(value) -> int:
    """argparse `type=` for --retries. Rejects anything below 1.

    WHY THIS IS A USAGE ERROR AND NOT A CLAMP. The page-load loop is
    `for attempt in range(1, retries + 1)`, so `--retries 0` makes its body
    unreachable: nothing is fetched, and the engine then judges whatever the
    browser holds — `about:blank`, 39 bytes of
    `<html><head></head><body></body></html>` — and reports a block or an
    empty result in a few dozen milliseconds. That is a confident, fast,
    plausible answer to a question nobody asked.

    Measured on 2026-10-13 against sleepnumber-scraper, from an address the
    site genuinely does refuse, so the conclusion even looked right:

        --retries 0   exit 3, 39 bytes, 36 ms    <- never left the process
        --retries 1   exit 3, 923 bytes, 116 ms  <- the real CloudFront 403

    A network round trip does not finish in 36 ms. Three third-party audits of
    the 2scraper scrapers have now run `--retries 0`, read that output, and
    reported a working scraper as broken or a site as unreachable.

    The flag counts ATTEMPTS, not re-tries, so a caller who writes 0 almost
    certainly means "do not retry", which is 1. Clamping would hide the
    mistake; saying so ends the ambiguity.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise _argparse.ArgumentTypeError(
            f"--retries must be an integer, not {value!r}")
    if number < 1:
        raise _argparse.ArgumentTypeError(
            f"--retries must be at least 1, not {number}. It counts ATTEMPTS, "
            f"not re-tries, so --retries 1 means 'try once and do not retry'. "
            f"With 0 the load loop never runs, nothing is fetched, and the "
            f"engine reports the browser's blank page as a result.")
    return number


def at_least(minimum, kind=int):
    """argparse `type=` factory: a number of `kind` no smaller than `minimum`.

    For the flags that count or wait: --pages, --concurrency, --delay,
    --retry-delay, --proxy-block-retries. A negative value there is never
    meaningful — `range()` quietly runs zero times and `time.sleep` raises
    halfway through a run — so it is refused up front, and argparse names
    the flag in the error ("argument --pages: must be at least 1, not 0").
    """
    def parse(value):
        try:
            number = kind(value)
        except (TypeError, ValueError):
            raise _argparse.ArgumentTypeError(
                f"must be {'an integer' if kind is int else 'a number'}, "
                f"not {value!r}")
        if number < minimum:
            raise _argparse.ArgumentTypeError(
                f"must be at least {minimum}, not {value}")
        return number
    parse.__name__ = f"at_least_{minimum}"
    return parse
