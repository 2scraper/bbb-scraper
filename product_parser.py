"""
product_parser.py
-----------------
Extracts business records from bbb.org. This module IS the site knowledge;
the engines around it carry a dozen constants and nothing else (CLAUDE.md §1).

Where the data comes from, and why it is not the DOM
----------------------------------------------------
BBB's own front end renders its listings out of a JSON endpoint:

    GET https://www.bbb.org/api/search
        ?find_country=USA&find_text=restaurants&find_loc=New%20York,%20NY&page=1

Measured 2026-09-16: HTTP 200, 57 KB of JSON, and a complete record per
business — name, address, phone list, letter grade, numeric score,
accreditation, categories, coordinates and BBB's own onward links. That is
strictly more than the rendered tile shows, and it is the site's own data
rather than an inference from markup, so it is the primary path — the same
call CLAUDE.md §4 records for zimmo-scraper, where the site's own embedded
JSON outranks even JSON-LD once confirmed live.

There is no JSON-LD fallback here because there is nothing to fall back to:
**zero** `application/ld+json` blocks were counted on every bbb.org response
captured, listing and interstitial alike.

The shapes this module parses
-----------------------------
    parse_listing(payload)   the /api/search response, as text or as a dict
    detect_page_state()      lives in page_flow.py, which imports from here

`parse_listing` accepts BOTH a JSON string and an already-decoded dict, so an
engine that read the payload out of a browser response and one that fetched
it over HTTP hand this module the same thing.

Six site-shaped traps, all measured, all handled here
-----------------------------------------------------
1.  **The default sort is not a relevance sort.** `Relevance` ("Best Match",
    the one BBB marks `isActive`) returned **15/15 accredited businesses** on
    every page measured, and for `find_text=restaurants` returned cleaning
    services, hotel management and home improvement — not one restaurant.
    The same query under `AToZ` returned **0/15 accredited** and real
    name-matched restaurants. So the default ordering is an accredited
    placement, and a scraper that ships it returns a biased sample while
    reporting success. See `SORTS` and `DEFAULT_SORT`.

2.  **`businessName` carries search-term highlighting.** `<em>` tags sit in
    the value: `"ADA <em>Restaurant</em>"`, 15/15 rows under `Rating`, 3/15
    under `AToZ`, 0/15 under the default (which does not match the query at
    all). It is the ONLY field affected. `strip_highlight` removes it.

3.  **An unrated business scores 0.0, not null.** `rating: ""` with
    `ratingScore: 0.0`, 7 of 105 rows. Written through, that zero drags every
    average a consumer computes, so `_rating` maps the pair to None/None.

4.  **`id` identifies a business AT A LOCATION.** Same `businessId`, two
    `addressId`s, twice in the fixtures. Dedupe on `id`; never on
    `businessId`.

5.  **Pagination stops at 15 pages and page 16 is an HTTP 500.**
    `totalResults: 19016` against `totalPages: 15` at `pageSize: 15` — one
    query reaches 225 rows at most, and `pageSize` is accepted and ignored.
    `pages_available` is the site's own number and is what an engine plans
    against, so no run ever asks for the page that 500s.

6.  **A category is addressed by tobId, not by its slug.** `/us/category/
    restaurants` is a human URL; the API filters on
    `find_type=Category&find_id=50544-000`. The mapping is published by the
    API itself, in `filters.byId.filter_category.filterOptions` of any
    response for that term, so `category_options` + `pick_category_option`
    resolve it from one extra request rather than from a hardcoded table
    that would rot.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from output_writer import Business

log = logging.getLogger("product_parser")


# ---------------------------------------------------------------------------
# The site
# ---------------------------------------------------------------------------

# BBB serves the United States and Canada from ONE host, with the country in
# the PATH (`/us/…`, `/ca/…`) rather than in a TLD. Taken from the site's own
# links rather than guessed, and checked: 105 of 105 captured `reportUrl`
# values began `/us/` or `/ca/`, and none of them named another host.
#
# `bbb.org` without `www.` redirects to `www.bbb.org`, so both are accepted
# and both normalise to the canonical one — CLAUDE.md §5's "check whether
# hosts answer on www.", from the other direction.
HOSTS = ("www.bbb.org", "bbb.org")
CANONICAL_HOST = "www.bbb.org"

API_PATH = "/api/search"

# The country codes BBB's own search takes, keyed by the path segment its
# profile URLs use. Nothing else is a BBB country: the site has exactly two.
COUNTRY_BY_SEGMENT = {"us": "USA", "ca": "CAN"}
SEGMENT_BY_COUNTRY = {v: k for k, v in COUNTRY_BY_SEGMENT.items()}

# BBB's five orderings, as the API spells them, keyed by the flag value this
# repo spells them with. The keys are lowercase-and-hyphens because that is
# what a CLI flag should look like; the values are what goes on the wire.
SORTS = {
    "best-match": "Relevance",
    "distance": "Distance",
    "rating": "Rating",
    "a-z": "AToZ",
    "z-a": "ZToA",
}

# NOT "best-match", which is what the site defaults to.
#
# This is the one place this repo deliberately disagrees with the site, and
# the measurement is in the module docstring: `Relevance` returned 15/15
# accredited businesses and nothing matching the query on every page tried,
# while `AToZ` returned 0/15 accredited and real matches from the identical
# query. "Best Match" is an accredited placement wearing a relevance label.
#
# A-Z is also the only ordering that is STABLE between runs, which is what a
# multi-page run needs: a relevance or distance ordering can reshuffle
# between page 1 and page 8 and silently skip businesses that moved across a
# page boundary. `--sort best-match` remains available and is what to pass to
# reproduce what a visitor sees.
DEFAULT_SORT = "a-z"

# BBB's hard ceiling, and the reason it is a constant rather than a surprise:
# `totalPages` came back as 15 for a query with `totalResults: 19016`, and
# `page=16` answered HTTP 500 rather than an empty page. An engine plans
# against `pages_available` (the site's own number) and this is only the
# backstop for a response that omits it.
MAX_PAGES = 15
PAGE_SIZE = 15

# `<em>` is what BBB wraps a matched search term in, inside the value of
# `businessName`. Kept narrow on purpose: this strips the highlighting the
# site adds, and is not a general HTML sanitiser for a field that should
# never contain markup in the first place.
_HIGHLIGHT_RE = re.compile(r"</?em>", re.I)

# `/{cc}/{state}/{city}/profile/{category-slug}/{name}-{bbbId}-{businessId}`
# — 105 of 105 captured profile paths had exactly these six segments.
_PROFILE_PATH_RE = re.compile(
    r"^/(?P<cc>us|ca)/(?P<state>[^/]+)/(?P<city>[^/]+)/profile/"
    r"(?P<category>[^/]+)/(?P<slug>[^/]+)/?$", re.I)

# `/us/category/{slug}`, the human-facing category listing.
_CATEGORY_PATH_RE = re.compile(r"^/(?P<cc>us|ca)/category/(?P<slug>[^/]+)/?$", re.I)


# ---------------------------------------------------------------------------
# Small, total helpers
# ---------------------------------------------------------------------------

def strip_highlight(value: Optional[str]) -> Optional[str]:
    """Remove BBB's `<em>` search-term highlighting from a business name.

    `"ADA <em>Restaurant</em>"` -> `"ADA Restaurant"`. Returns None for None
    so a missing name stays missing rather than becoming an empty string.
    """
    if value is None:
        return None
    cleaned = _HIGHLIGHT_RE.sub("", value).strip()
    return cleaned or None


def split_location(value: Any) -> Tuple[Optional[float], Optional[float]]:
    """Split BBB's `"40.758064,-73.978851"` STRING into two floats.

    105 of 105 rows carried one, always with exactly one comma. Anything else
    — a missing value, a shape that is not two numbers — yields (None, None)
    rather than half a coordinate, because a latitude with no longitude is
    worse than neither.
    """
    if not isinstance(value, str):
        return None, None
    parts = value.split(",")
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


def _rating(record: Dict[str, Any]) -> Tuple[Optional[str], Optional[float]]:
    """BBB's letter grade and 0-100 score, with "not graded" mapped to None.

    The trap is the zero: an ungraded business comes back as `rating: ""`
    with `ratingScore: 0.0` (7 of 105 rows), which is not a rating of zero.
    Both columns go None together — a score with no grade would be just as
    misleading as the zero.
    """
    grade = (record.get("rating") or "").strip() or None
    score = record.get("ratingScore")
    if grade is None:
        return None, None
    try:
        score = float(score)
    except (TypeError, ValueError):
        return grade, None
    return grade, score


def _text_list(values: Any) -> Optional[List[str]]:
    """A list of non-empty strings, or None — never an empty list.

    An empty list and a missing value mean the same thing to a consumer, and
    `[]` in a JSON column reads as "we looked and there were none" when in
    fact the field was absent. One business in 105 carried no phone number at
    all; that row gets None.
    """
    if not isinstance(values, (list, tuple)):
        return None
    out = [str(v).strip() for v in values if str(v or "").strip()]
    return out or None


def _absolute(path: Optional[str]) -> Optional[str]:
    """Turn one of BBB's root-relative links into a URL a consumer can open."""
    if not path:
        return None
    path = str(path)
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return "https://%s%s" % (CANONICAL_HOST, path)


def country_from_path(path: Optional[str]) -> Optional[str]:
    """`/us/ny/bronx/profile/…` -> "USA". A per-ROW fact, not a query echo.

    Deriving it from the row's own `reportUrl` rather than from the
    `find_country` the run asked for means a US row that turns up in a
    Canadian search is labelled for what it is. 105 of 105 captured rows
    carried a country segment.
    """
    if not path:
        return None
    parts = str(path).strip("/").split("/")
    return COUNTRY_BY_SEGMENT.get(parts[0].lower()) if parts else None


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def is_supported_url(url: str) -> Tuple[bool, str]:
    """(supported, reason). The reason is the point (CLAUDE.md §5).

    A refusal that says "is not a BBB site" about a host that plainly is one
    sends the reader looking for a typo, so each refusal names what is
    actually wrong.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "is not a URL"
    if parsed.scheme not in ("http", "https"):
        return False, "is not an http(s) URL"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "has no hostname"
    if host not in HOSTS:
        return False, ("is not a bbb.org address — BBB serves both the US and "
                       "Canada from %s, with the country in the path "
                       "(/us/…, /ca/…), so there is no country-specific "
                       "hostname to use" % CANONICAL_HOST)
    return True, ""


def category_from_url(url: str) -> Optional[str]:
    """The category slug of a `/us/category/{slug}` URL, or None.

    Used to label rows when `--category` was not given, so the column is
    never empty just because the flag was omitted.
    """
    try:
        path = urlparse(url).path
    except ValueError:
        return None
    m = _CATEGORY_PATH_RE.match(path or "")
    return m.group("slug").lower() if m else None


def profile_parts(url_or_path: str) -> Optional[Dict[str, str]]:
    """Split a BBB profile URL into its six parts, or None if it is not one."""
    try:
        path = urlparse(url_or_path).path or url_or_path
    except ValueError:
        return None
    m = _PROFILE_PATH_RE.match(path or "")
    if not m:
        return None
    parts = m.groupdict()
    parts["country"] = COUNTRY_BY_SEGMENT.get(parts["cc"].lower(), "")
    return parts


def page_url(url: str, page: int) -> str:
    """BBB's pagination convention: `?page=N`, REPLACED rather than appended.

    Preserves every other query parameter, which matters on a search URL
    where `find_text` and `find_loc` carry the query itself. Page 1 keeps the
    parameter rather than dropping it, so a run's first URL and its later
    ones differ in exactly one number — which is what makes a failure at page
    1 distinguishable from a malformed URL.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["page"] = [str(int(page))]
    flat = urlencode([(k, v) for k, vs in params.items() for v in vs])
    return urlunparse(parsed._replace(query=flat))


def api_url(*, country: str = "USA", text: str = "", location: str = "",
            page: int = 1, sort: str = DEFAULT_SORT,
            category_id: Optional[str] = None,
            state: Optional[str] = None) -> str:
    """Build the `/api/search` URL for one page of one query.

    `sort` is this repo's spelling (`a-z`), translated here to BBB's
    (`AToZ`). An unknown value is a programming error rather than something
    to guess about, so it raises.
    """
    if sort not in SORTS:
        raise ValueError("unknown sort %r; expected one of %s"
                         % (sort, ", ".join(sorted(SORTS))))
    if country not in SEGMENT_BY_COUNTRY:
        raise ValueError("unknown country %r; BBB serves %s"
                         % (country, ", ".join(sorted(SEGMENT_BY_COUNTRY))))
    params = [
        ("find_country", country),
        ("find_text", text or ""),
        ("find_loc", location or ""),
        ("page", str(int(page))),
        ("sort", SORTS[sort]),
    ]
    if category_id:
        if not (text or "").strip():
            # Refused, not sent. Measured 2026-09-16: a category filter with
            # an EMPTY `find_text` answers HTTP 200 with `totalResults: 0`,
            # while the same filter with the category's label as the text
            # answers 234,844. A silent zero that looks like an empty
            # category is the worst answer available here (§8), so this
            # cannot be built at all.
            raise ValueError(
                "a category filter needs a non-empty `text` as well: BBB "
                "returns 0 results for find_type=Category with an empty "
                "find_text. Pass the category's own label.")
        # Both are needed: `find_type=Category` alone filters nothing, and
        # `find_entity` — the obvious-looking alternative — was measured to
        # return `totalResults: 0` for a tobId that works here.
        params.append(("find_type", "Category"))
        params.append(("find_id", category_id))
    if state:
        # The one documented way past the 225-row ceiling: slicing by state
        # cut a 19,016-result query to 6,693, and each slice gets its own
        # 15 pages.
        params.append(("filter_state", state))
    return "https://%s%s?%s" % (CANONICAL_HOST, API_PATH, urlencode(params))


def listing_url(*, country: str = "USA", text: str = "", location: str = "",
                page: int = 1) -> str:
    """The HUMAN listing URL for the same query — what a person would open.

    Recorded in the sidecar as the run's `start_url` so a reader can go and
    look at what was scraped, and used by the browser engines for navigation.
    """
    # Not country-segmented: `/search` is one route for both countries and
    # takes `find_country` as a parameter. Only PROFILE paths carry `/us/`
    # or `/ca/`.
    params = urlencode([("find_country", country), ("find_text", text or ""),
                        ("find_loc", location or ""), ("page", str(int(page)))])
    return "https://%s/search?%s" % (CANONICAL_HOST, params)


# ---------------------------------------------------------------------------
# Category resolution
# ---------------------------------------------------------------------------

def category_options(payload: Any) -> List[Dict[str, str]]:
    """BBB's own category ids for a query, from its own response.

    Every `/api/search` response carries
    `filters.byId.filter_category.filterOptions` — a list of
    `{"value": "50544-000", "label": "Restaurants"}`. Reading the mapping out
    of the site's answer is what keeps it from rotting: a hardcoded slug ->
    tobId table would be wrong the first time BBB renamed a category, and
    wrong silently.
    """
    data = _as_dict(payload)
    node = ((data.get("filters") or {}).get("byId") or {}).get("filter_category") or {}
    options = node.get("filterOptions") or []
    out = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        value, label = opt.get("value"), opt.get("label")
        if value and label:
            out.append({"value": str(value), "label": str(label)})
    return out


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def pick_category_option(options: Sequence[Dict[str, str]],
                        slug: str) -> Tuple[Optional[Dict[str, str]], bool]:
    """(option, was_exact) for a `/us/category/{slug}` slug.

    Exact first, on a normalised comparison, so `auto-repair` matches
    "Auto Repairs" only if nothing matches better. Otherwise BBB's own first
    option, which is its ranking for the term and was right on all four slugs
    measured — `restaurants` -> Restaurants, `plumbers` -> Plumber,
    `auto-repair` -> Auto Repairs, `roofing` -> Roofing Contractors.

    `was_exact` is returned rather than swallowed so the caller can WARN when
    it matched on the plural rather than exactly.

    Nothing plausible returns (None, False), and the caller REFUSES rather
    than scraping whatever BBB ranked first. That is the difference between
    a run that says "there is no such category, here is what BBB does have"
    and a run that quietly returns the wrong category's businesses while
    reporting success (§5, §8).
    """
    if not options:
        return None, False
    target = _normalise(slug)
    for opt in options:
        if _normalise(opt["label"]) == target:
            return opt, True
    # A slug is usually the plural of the label, or the label's first word:
    # `plumbers` / "Plumber", `roofing` / "Roofing Contractors",
    # `auto-repair` / "Auto Repairs" — all four slugs measured resolve here.
    for opt in options:
        label = _normalise(opt["label"])
        if label and (target.startswith(label) or label.startswith(target)):
            return opt, False
    return None, False


# ---------------------------------------------------------------------------
# The listing payload
# ---------------------------------------------------------------------------

@dataclass
class ListingPage:
    """One `/api/search` response, parsed.

    `total_results` and `pages_available` are BBB's OWN numbers, kept
    separate from how many rows this page held: they are what says a
    complete 15-page run is a 1.2% sample of the 19,016 businesses the site
    claims to have, which no count of rows can say.
    """
    rows: List[Business]
    page: int
    page_size: Optional[int]
    total_results: Optional[int]
    pages_available: Optional[int]
    sort: Optional[str]
    query_text: Optional[str]
    query_location: Optional[str]
    category_options: List[Dict[str, str]]

    @property
    def is_empty_result_set(self) -> bool:
        """A query BBB served and that matched nothing.

        `totalResults: 0`, `totalPages: 0`, `results: []`, HTTP 200 — which is
        EXIT_NO_PRODUCTS and emphatically not EXIT_BLOCKED. Reporting it as
        blocked sends a user hunting a proxy problem that is not there.
        """
        return not self.rows and not (self.total_results or 0)


def _as_dict(payload: Any) -> Dict[str, Any]:
    """Accept the response as text or as an already-decoded dict.

    An engine that read the payload out of a browser response and one that
    fetched it over HTTP then hand this module the same thing, which is what
    keeps the three engines from each growing their own decoding step.
    """
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def parse_row(record: Dict[str, Any], *, page: Optional[int] = None,
              position: Optional[int] = None, mode: Optional[str] = None,
              sort: Optional[str] = None,
              data_source: str = "api") -> Optional[Business]:
    """One `results[]` entry -> one `Business`, or None if it is not a record.

    Returns None rather than a half-filled row for an entry with no `id`:
    the id is what dedupe and diffing both key on, and a row without one is
    not comparable to anything. That has not been seen in 105 rows; it is
    handled because a silent partial row is this codebase's worst bug class
    (§8).
    """
    if not isinstance(record, dict):
        return None
    sku = record.get("id")
    if not sku:
        return None

    report_url = record.get("reportUrl")
    grade, score = _rating(record)
    lat, lon = split_location(record.get("location"))
    categories = record.get("categories")
    category_names = None
    if isinstance(categories, (list, tuple)):
        category_names = _text_list(
            [c.get("name") for c in categories if isinstance(c, dict)])

    return Business(
        url=_absolute(report_url) or "",
        sku=str(sku),
        title=strip_highlight(record.get("businessName")),
        business_id=_str_or_none(record.get("businessId")),
        address_id=_address_id(sku),
        bbb_id=_str_or_none(record.get("bbbId")),
        bbb_office=_str_or_none(record.get("bbbName")),
        rating_grade=grade,
        rating_score=score,
        is_accredited=_bool_or_none(record.get("bbbMember")),
        category=_str_or_none(record.get("tobText")),
        category_id=_str_or_none(record.get("tobId")),
        categories=category_names,
        address=_str_or_none(record.get("address")),
        city=_str_or_none(record.get("city")),
        state=_str_or_none(record.get("state")),
        postal_code=_str_or_none(record.get("postalcode")),
        country=country_from_path(report_url),
        latitude=lat,
        longitude=lon,
        service_areas=_text_list(record.get("serviceAreasSummary")),
        has_service_area=_bool_or_none(record.get("hasServiceArea")),
        phone=_text_list(record.get("phone")),
        logo_url=_str_or_none(record.get("logoUri")),
        local_profile_url=_absolute(record.get("localReportUrl")),
        leave_review_url=_absolute(record.get("leaveReviewUrl")),
        request_quote_url=_absolute(record.get("requestAQuoteUrl")),
        is_charity=_charity(record.get("isCharity")),
        charity_seal=_bool_or_none(record.get("charitySeal")),
        accredited_charity=_bool_or_none(record.get("accreditedCharity")),
        out_of_business=_out_of_business(record.get("outOfBusinessStatus")),
        page=page,
        position=position,
        mode=mode,
        sort=sort,
        data_source=data_source,
    )


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_or_none(value: Any) -> Optional[bool]:
    return bool(value) if isinstance(value, bool) else None


def _charity(value: Any) -> Optional[bool]:
    """`isCharity` is an INT (0/1) where its three neighbours are booleans.

    Measured 0 on all 105 rows. Normalised to a bool so the column does not
    hold 0 where `charitySeal` holds False — one schema, one type per
    column.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return None


def _out_of_business(value: Any) -> Optional[bool]:
    """`outOfBusinessStatus` -> a bool, so one column has one type.

    A listing row carries a STRING here and a profile row a real boolean
    (`orgDetails.isOutOfBusiness`). Null on 105 of 105 captured listing rows,
    so the True direction of this branch is unverified and says so rather
    than pretending otherwise.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    return bool(text) if text else None


def _address_id(sku: Any) -> Optional[str]:
    """The third part of `{bbbId}_{businessId}_{addressId}`.

    Split from the id rather than read from a field, because BBB publishes no
    address-id field of its own — and it is the part that distinguishes two
    locations of one business.
    """
    parts = str(sku or "").split("_")
    return parts[2] if len(parts) == 3 and parts[2] else None


def parse_listing(payload: Any, *, page: Optional[int] = None,
                  mode: Optional[str] = None, sort: Optional[str] = None,
                  data_source: str = "api") -> ListingPage:
    """Parse one `/api/search` response into rows plus the run's arithmetic.

    `page` is passed IN rather than trusted from the response, because the
    row's `page` column is what makes `position` meaningful and an engine
    knows which page it asked for. The response's own `page` is used only
    when the caller did not say.
    """
    data = listing_payload(payload)
    if data is None:
        # Loudly, and with the reason: "0 businesses" from a page that held
        # fifteen is indistinguishable from an empty category (§8).
        log.warning("page %s: no listing payload found — neither the "
                    "/api/search JSON nor a __PRELOADED_STATE__.searchResult "
                    "on the page", page)
        data = {}
    results = data.get("results")
    results = results if isinstance(results, list) else []

    page_number = page if page is not None else _int_or_none(data.get("page"))
    rows: List[Business] = []
    for index, record in enumerate(results, start=1):
        row = parse_row(record, page=page_number, position=index, mode=mode,
                        sort=sort, data_source=data_source)
        if row is not None:
            rows.append(row)

    if len(rows) != len(results):
        # Loudly, per §8: a row silently dropped is indistinguishable from a
        # business the site stopped listing.
        log.warning("page %s: %d of %d results had no id and were skipped",
                    page_number, len(results) - len(rows), len(results))

    heading = data.get("heading") if isinstance(data.get("heading"), dict) else {}
    return ListingPage(
        rows=rows,
        page=page_number or 1,
        page_size=_int_or_none(data.get("pageSize")),
        total_results=_int_or_none(data.get("totalResults")),
        pages_available=_int_or_none(data.get("totalPages")),
        sort=_active_sort(data),
        query_text=_str_or_none(heading.get("searchInputText")),
        query_location=_str_or_none(heading.get("searchLocationText")),
        category_options=category_options(data),
    )


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _active_sort(data: Dict[str, Any]) -> Optional[str]:
    """Which ordering BBB says it applied, translated back to our spelling.

    Read from the response rather than echoed from the request, so a sort the
    site silently ignored shows up as a disagreement rather than as a column
    that merely repeats what we asked for.
    """
    for entry in data.get("sortTypes") or []:
        if isinstance(entry, dict) and entry.get("isActive"):
            wire = entry.get("value")
            for flag, api_value in SORTS.items():
                if api_value == wire:
                    return flag
            return _str_or_none(wire)
    return None


def pages_to_fetch(pages_requested: int, pages_available: Optional[int]) -> int:
    """How many pages a run may actually ask for.

    BBB caps `totalPages` at 15 and answers `page=16` with **HTTP 500**, so
    asking past the end does not merely waste a fetch — it manufactures a
    server error that reads like a fault in this code. The site's own number
    wins; MAX_PAGES is the backstop for a response that omitted it.
    """
    ceiling = pages_available if pages_available else MAX_PAGES
    ceiling = min(ceiling, MAX_PAGES)
    return max(1, min(int(pages_requested), ceiling))


# ---------------------------------------------------------------------------
# What BBB answered with
# ---------------------------------------------------------------------------

# Markers that mean a CLOUDFLARE CHALLENGE was rendered, and nothing else.
#
# The negative measurement matters more than the positive one here, and this
# list has been WRONG once already — see the `cf-turnstile` note below, which
# is CLAUDE.md §18's rule biting for the third time in this family.
#
# Counted 2026-09-16 across every capture, where "served" means a page BBB
# really answered with (five of them fetched through the 2Captcha Scraping
# Browser, one through plain curl) and "refused" means one of its two 403s:
#
#                              served (6)   challenge (2)   hard block
#   cf_chl_opt                     0           7, 7              0
#   __cf_chl                       0           3, 3              0
#   cf-chl-                        0           1, 0              0
#   challenges.cloudflare.com      0           1, 0              0
#   cf-turnstile                 1 on 5!       1, 0              0     <- NOT a marker
#   /turnstile/v0/api.js           0           0, 0              0     <- never fires
#   challenge-platform          1 on the 404   2, 1              1     <- NOT a marker
#   cdn-cgi                     1 on the 404   2, 1              1     <- NOT a marker
#
# Four entries are therefore deliberately ABSENT:
#
#   `challenge-platform` / `cdn-cgi`  are facts about BBB sitting behind
#       Cloudflare at all. They appear on pages it serves normally, so either
#       would report every page as a challenge — the akamai-on-Tokopedia
#       mistake that repo shipped twice.
#
#   `cf-turnstile` is the obvious marker for a Turnstile and is MEASURED
#       USELESS here, for a reason that has nothing to do with BBB: 2Captcha's
#       own Scraping Browser auto-solve extension injects
#       `chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/
#       turnstile/hunter.js` with `data-ts-input="cf-turnstile-response"` into
#       EVERY page it loads. It fired on all five served pages fetched that
#       way and on only one of the two real challenges. A marker that fires on
#       good pages and misses half the bad ones is worse than no marker.
#
#       Note what is NOT done about it: the extension's script tags are not
#       stripped before matching. §19 says to add that guard "only if your
#       marker set can actually match one", and with `cf-turnstile` gone, none
#       of the entries below matches anything that extension injects. Adding
#       the strip would be dead code that looks load-bearing.
#
#   `/turnstile/v0/api.js` fires on nothing at all — 0 across every capture,
#       served and refused alike. Dead weight, removed.
#
# `smoke_test.py` pins this: no entry below may appear on a page BBB served,
# asserted against a listing fetched THROUGH the Scraping Browser, which is
# the page kind that exposed the mistake.
BOT_CHALLENGE_MARKERS = (
    "cf_chl_opt",
    "__cf_chl",
    "cf-chl-",
    "challenges.cloudflare.com",
)

# BBB HAS ITS OWN CAPTCHA, and it is not the one above.
#
# §18's "no challenge rendered is not no captcha configured": every page BBB
# serves carries a reCAPTCHA **Enterprise** configuration, for its own forms
# rather than for readers —
#
#     NEXT_PUBLIC_GOOGLE_RECAPTCHA_SITE_KEY
#     https://www.google.com/recaptcha/enterprise.js?render=6Lfm-HorAAAAA…
#
# `render=<sitekey>` rather than `render=explicit` means v3/Enterprise, so if
# it were ever rendered at a reader it would be scored invisibly rather than
# shown as a checkbox. It is NOT in the marker set, because it is present on
# every good page — it is recorded here so that the next person to meet a
# reCAPTCHA on this site knows which variant it is before paying for a task
# type. This scraper never touches the forms it guards.

# What the hard refusal says. BBB serves it under its OWN branding — the
# `<title>` is "You have been blocked | Better Business Bureau®" — so a title
# check calls it a real page, which is CLAUDE.md §18's inverted-detection
# case on a third site.
BLOCK_PAGE_MARKERS = (
    "You have been blocked",
    "Sorry, you have been blocked",
)

# The positive signal, and the one that decides the ambiguous cases: a page
# BBB actually served is BUILT OUT OF BBB'S OWN ASSETS. Both interstitials
# reference neither host; the served 404 references them three times.
#
# This is the `assets.mmsrg.com` trick from §8, and it is what catches the
# failure modes a marker list cannot: the hard block page (which carries no
# challenge marker at all), and Chromium's own network-error page (which
# carries the site's hostname in its `<title>` and would otherwise read as
# real).
SERVED_ASSET_HOSTS = ("assets.bbb.org", "m.bbb.org")


def detect_bot_challenge(html: str, url: str = "") -> Optional[str]:
    """Name the challenge vendor if this markup IS a challenge, else None.

    Returns the marker that matched, so a log line says WHICH signal fired
    rather than only that something did.
    """
    if not html:
        return None
    for marker in BOT_CHALLENGE_MARKERS:
        if marker in html:
            return "cloudflare (%s)" % marker
    return None


def references_own_assets(html: str) -> int:
    """How many times this markup references one of BBB's own asset hosts."""
    if not html:
        return 0
    return sum(html.count(host) for host in SERVED_ASSET_HOSTS)


def looks_like_api_payload(text: str) -> bool:
    """Whether this is the /api/search JSON rather than a rendered page."""
    if not text:
        return False
    head = text.lstrip()[:1]
    return head == "{"


def detect_page_state(html: str, status: Optional[int] = None,
                      url: str = "") -> str:
    """One of "content", "empty", "blocked", "challenge", "unknown".

    Ordered by how much each signal PROVES, not by how cheap it is to
    compute — CLAUDE.md §17's classification-order trap. An unambiguous
    positive ("this response IS the API payload", "this page says it is a
    challenge") is decided before the asset-reference heuristic, so a real
    answer that happens to be minimal is never reported as blocked.

    `status` is positional-friendly and second, matching the rest of the
    family: every engine calls `classify(html, status, url)` and a signature
    that disagreed crashed two of three engines on their first fetch in a
    sibling repo (§17).
    """
    text = html or ""

    # 1. Unambiguous: the listing payload is here — either as the raw
    #    /api/search JSON, or embedded in a page BBB rendered. Both carry
    #    the identical object, so both are decided in one place and neither
    #    can be mistaken for a block.
    payload = listing_payload(text)
    if payload is not None:
        page = parse_listing(payload)
        if page.rows:
            return "content"
        if page.is_empty_result_set:
            return "empty"
        return "unknown"

    # 1b. A profile page carries `businessProfile` instead, and a profile is
    #     content even though it holds no `results` list at all.
    if profile_payload(text):
        return "content"

    # 2. Unambiguous: the page says, in Cloudflare's own vocabulary, that it
    #    is a challenge.
    if detect_bot_challenge(text, url):
        return "challenge"

    # 3. Unambiguous: the hard refusal names itself.
    if any(marker in text for marker in BLOCK_PAGE_MARKERS):
        return "blocked"

    # 4. The status, which on this site is a real signal rather than a
    #    formality: BBB answers a refused request 403 and a served page 200
    #    or 404. A 403 that reached here carries neither vendor marker, which
    #    is exactly the shape of the hard block page.
    if status == 403:
        return "blocked"

    # 5. The heuristic, last: was this built out of BBB's own assets? An
    #    interstitial is not, and neither is the browser's own error page.
    if text and references_own_assets(text) == 0:
        return "blocked"

    # 6. Served by BBB, but not the payload this scraper reads. On this site
    #    that means the listing's JSON has not landed yet — a wait, not a
    #    retry and certainly not a solve.
    return "unknown"


def parse_products(payload: Any, url: str = "", page: int = 1,
                   mode: Optional[str] = None,
                   sort: Optional[str] = None) -> List[Business]:
    """The family's entry point, returning rows for one listing page.

    Kept under the family's name so the shared modules
    (`scraper_api_client.py`, the three engines) call the same function they
    call in every sibling repo.

    Takes either route to the same object: the `/api/search` payload (text
    or dict), or a rendered BBB listing page carrying
    `__PRELOADED_STATE__.searchResult`. It RAISES on input that is neither,
    rather than returning `[]` — a function that quietly returns nothing on
    input it does not understand is this codebase's most common historical
    bug class (§8), and "0 businesses" from a page that had 15 is
    indistinguishable from an empty category.
    """
    if listing_payload(payload) is None:
        raise ValueError(
            "parse_products found no listing payload: the input is neither "
            "the /api/search JSON nor a BBB page carrying "
            "__PRELOADED_STATE__.searchResult (%d bytes). Returning an empty "
            "list here would be indistinguishable from an empty category, so "
            "it raises instead." % len(payload or ""))
    return parse_listing(payload, page=page, mode=mode, sort=sort).rows


# ---------------------------------------------------------------------------
# The rendered page — same data, reached the way a visitor reaches it
# ---------------------------------------------------------------------------

# BBB's pages embed their own state, and on a listing page that state holds
# the /api/search response VERBATIM:
#
#     window.__PRELOADED_STATE__ = {"user": …, "page": …, "searchResult": {…}}
#
# `searchResult` carries the identical keys — `results`, `totalResults`,
# `totalPages`, `pageSize`, `sortTypes`, `filters`, `heading` — so ONE parser
# reads both paths and the two can never drift into disagreeing about a
# field. Verified 2026-09-16 on four live pages: US search (19,016 results),
# US category (171,978), CA search (1,290) and a no-results query (0), each
# reporting `totalPages` and 15 or 0 rows exactly as the endpoint does.
#
# A profile page embeds `businessProfile` in the same object, and that one
# has no endpoint at all — every `/api/businessprofile`-shaped URL tried
# returned BBB's 404 page — so for `--mode profile` this IS the only path.
_PRELOADED_RE = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*")


def extract_preloaded_state(html: str) -> Optional[Dict[str, Any]]:
    """Pull `window.__PRELOADED_STATE__` out of a rendered BBB page.

    Bracket-matched rather than regex-captured: the object is 80 KB on a
    search page and contains every bracket character inside strings, so a
    fixed pattern would either stop early or swallow the rest of the
    document. Returns None — never a partial object — when the assignment is
    absent or does not decode.
    """
    if not html:
        return None
    match = _PRELOADED_RE.search(html)
    if not match:
        return None
    try:
        start = html.index("{", match.end())
    except ValueError:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(html[start:index + 1])
                except ValueError as exc:
                    log.warning("__PRELOADED_STATE__ found but did not decode: %s", exc)
                    return None
                return data if isinstance(data, dict) else None
    log.warning("__PRELOADED_STATE__ found but its object never closed "
                "(%d bytes scanned) — the page was probably truncated",
                len(html) - start)
    return None


def listing_payload(source: Any) -> Optional[Dict[str, Any]]:
    """The listing payload, whether `source` is the API response or a page.

    This is the single place that knows there are two routes to the same
    object, so nothing downstream has to care which one a run used.
    """
    if isinstance(source, dict):
        return _listing_from_object(source)
    text = source.decode("utf-8", "replace") if isinstance(source, (bytes, bytearray)) else source
    if not isinstance(text, str):
        return None
    if looks_like_api_payload(text):
        return _listing_from_object(_as_dict(text))
    state = extract_preloaded_state(text)
    if state is None:
        return None
    return _listing_from_object(state)


def _listing_from_object(data: Any) -> Optional[Dict[str, Any]]:
    """The listing payload inside a decoded object, or None if it is not one.

    Three shapes reach here and only two of them are listings:

      * the endpoint's own response         -> itself
      * a listing page's `__PRELOADED_STATE__` -> its `searchResult`
      * a PROFILE page's state              -> None

    That last one matters more than it looks. A profile state has no
    `results` key, so returning it unchanged made `detect_page_state` call a
    perfectly good profile page an EMPTY RESULT SET — a real answer reported
    as "this query matched nothing". Recognising a listing POSITIVELY, by the
    key that makes it one, is what keeps the two apart.
    """
    if not isinstance(data, dict):
        return None
    if "searchResult" in data:
        result = data.get("searchResult")
        return result if isinstance(result, dict) else None
    if "businessProfile" in data:
        return None
    return data if "results" in data else None


def profile_payload(source: Any) -> Optional[Dict[str, Any]]:
    """The `businessProfile` object out of a rendered profile page."""
    if isinstance(source, dict):
        return _profile_from_object(source)
    text = source.decode("utf-8", "replace") if isinstance(source, (bytes, bytearray)) else source
    if not isinstance(text, str):
        return None
    # The state object can arrive as JSON in its own right — that is how a
    # fixture carries it, and how a caller that has already extracted it
    # hands it over — or embedded in the page BBB rendered.
    state = _as_dict(text) if looks_like_api_payload(text) else extract_preloaded_state(text)
    if state is None:
        return None
    return _profile_from_object(state)


def _profile_from_object(data: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    if "businessProfile" in data:
        profile = data.get("businessProfile")
        return profile if isinstance(profile, dict) else None
    # A bare profile object, recognised by keys only a profile has.
    if "accreditationInformation" in data or "reviewsComplaintsSummary" in data:
        return data
    return None


def _nested(node: Any, *path: str) -> Any:
    """Walk a chain of dict keys, stopping at the first thing that is not one.

    BBB's profile object nests four deep in places
    (`orgDetails.typeOfEntity.name`), and every level is legitimately absent
    on some profiles. A chain of `.get()` calls with `or {}` reads worse and
    hides which level was missing.
    """
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def parse_profile(source: Any, url: str = "", *,
                  mode: str = "profile") -> Optional[Business]:
    """One business profile page -> one `Business` with the profile columns.

    Returns None rather than an empty row when the page carries no
    `businessProfile`: "the profile had no data" and "this was not a profile
    page" are different things, and only the caller knows which it asked
    for (§8).

    `sku` is rebuilt as `{bbbId}_{businessId}_{addressId}` so a profile row
    JOINS a listing row on the family's own key. It has to be rebuilt
    because BBB's profile object states its id as `"0_209366"` — the bbbId
    slot is a literal zero there, which would never match the listing row
    for the same business at the same address.
    """
    profile = profile_payload(source)
    if not profile:
        return None

    bbb_id = _str_or_none(profile.get("bbbId"))
    business_id = _str_or_none(profile.get("businessId"))
    address_id = _address_id_from_profile(profile)
    sku = "_".join(x for x in (bbb_id, business_id, address_id) if x) or None

    urls = profile.get("urls") if isinstance(profile.get("urls"), dict) else {}
    location = profile.get("location") if isinstance(profile.get("location"), dict) else {}
    postal = location.get("postalAddress") if isinstance(location.get("postalAddress"), dict) else {}
    dates = profile.get("dates") if isinstance(profile.get("dates"), dict) else {}
    org = profile.get("orgDetails") if isinstance(profile.get("orgDetails"), dict) else {}
    summary = profile.get("reviewsComplaintsSummary")
    summary = summary if isinstance(summary, dict) else {}
    contact = profile.get("contactInformation")
    contact = contact if isinstance(contact, dict) else {}

    profile_path = _str_or_none(urls.get("profile"))
    category_links = _nested(profile, "categories", "links")
    category_names = None
    if isinstance(category_links, list):
        category_names = _text_list(
            [c.get("title") for c in category_links if isinstance(c, dict)])

    return Business(
        url=_absolute(profile_path) or url or "",
        sku=sku,
        title=_str_or_none(_nested(profile, "names", "primary")),
        business_id=business_id,
        address_id=address_id,
        bbb_id=bbb_id,
        bbb_office=_str_or_none(_nested(profile, "localBbbData", "name")),
        rating_grade=_str_or_none(_nested(profile, "rating", "bbbRating")),
        # A profile states the GRADE and not the 0-100 score behind it. Left
        # null rather than reconstructed from the grade: the listing
        # measurement shows several distinct scores map to one letter
        # (A+ spans 97.0 to 100.0), so inverting the mapping would invent a
        # precision the page does not have (§8).
        rating_score=None,
        is_accredited=_bool_or_none(
            _nested(profile, "accreditationInformation", "isAccredited")),
        category=(category_names[0] if category_names else None),
        categories=category_names,
        address=_str_or_none(postal.get("addressLine1")),
        city=_str_or_none(postal.get("city")),
        state=_str_or_none(postal.get("stateCode")),
        postal_code=_str_or_none(postal.get("zipCode")),
        country=country_from_path(profile_path),
        latitude=_float_or_none(location.get("latitude")),
        longitude=_float_or_none(location.get("longitude")),
        service_areas=_text_list(location.get("servingAreas")),
        has_service_area=_bool_or_none(location.get("servingArea")),
        phone=_text_list(_profile_phones(contact)),
        logo_url=_str_or_none(_nested(profile, "media", "logo")),
        leave_review_url=_absolute(urls.get("submitReview")),
        request_quote_url=_absolute(urls.get("requestQuote")),
        is_charity=None,
        out_of_business=_bool_or_none(org.get("isOutOfBusiness")),
        website=_str_or_none(urls.get("primary")),
        description=_str_or_none(org.get("organizationDescription")),
        fax=_str_or_none(_first_value(contact.get("additionalFaxNumbers"))),
        accredited_since=_str_or_none(dates.get("accredited")),
        bbb_file_opened=_str_or_none(dates.get("bbbFileOpened")),
        business_started=_str_or_none(dates.get("businessStart")),
        years_in_business=_int_or_none(org.get("yearsInBusiness")),
        entity_type=_str_or_none(_nested(org, "typeOfEntity", "name")),
        reviews_total=_int_or_none(summary.get("reviewsTotal")),
        review_stars_avg=_review_stars(summary),
        complaints_total=_int_or_none(summary.get("complaintsTotal")),
        complaints_3y=_int_or_none(summary.get("totalClosedComplaintsPastThreeYears")),
        complaints_12m=_int_or_none(summary.get("totalClosedComplaintsPastTwelveMonths")),
        rating_reasons=_text_list(_nested(profile, "rating", "ratingReasons")),
        is_multi_location=_bool_or_none(profile.get("isMultiLocation")),
        mode=mode,
        data_source="profile",
    )


def _address_id_from_profile(profile: Dict[str, Any]) -> Optional[str]:
    """The address id, out of the profile's own `"0_209366"`-shaped id.

    BBB writes a literal `0` where the listing writes the bbbId, so only the
    trailing part is usable — and it is the part that says WHICH location
    this profile describes.
    """
    parts = str(profile.get("id") or "").split("_")
    return parts[-1] if len(parts) >= 2 and parts[-1] else None


def _profile_phones(contact: Dict[str, Any]) -> List[str]:
    """Every number on the profile, primary first, without duplicates."""
    numbers = []
    primary = _str_or_none(contact.get("phoneNumber"))
    if primary:
        numbers.append(primary)
    extra = contact.get("additionalPhoneNumbers")
    if isinstance(extra, list):
        for entry in extra:
            value = _str_or_none(entry.get("value")) if isinstance(entry, dict) else _str_or_none(entry)
            if value and value not in numbers:
                numbers.append(value)
    return numbers


def _first_value(entries: Any) -> Optional[str]:
    if not isinstance(entries, list) or not entries:
        return None
    first = entries[0]
    return _str_or_none(first.get("value")) if isinstance(first, dict) else _str_or_none(first)


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _review_stars(summary: Dict[str, Any]) -> Optional[float]:
    """The average star rating, but only where BBB says it is displayable.

    `averageOfReviewStarRatings` is **0** on a business with `reviewsTotal:
    0`, and a zero there is "no reviews", not "rated zero stars" — the same
    trap as the 0.0 rating score, one object deeper. BBB carries its own flag
    for it (`displayAverageOfReviewStarRatings`, False on the captured
    profile), so the site's own answer is used rather than a guess.
    """
    if not summary.get("displayAverageOfReviewStarRatings"):
        return None
    return _float_or_none(summary.get("averageOfReviewStarRatings"))


def api_url_from_listing_url(url: str, *, sort: str = DEFAULT_SORT,
                             state: Optional[str] = None,
                             category_id: Optional[str] = None,
                             category_label: Optional[str] = None,
                             page: Optional[int] = None) -> str:
    """Translate a human BBB listing URL into the endpoint that backs it.

    A `/search?find_text=…&find_loc=…` URL already carries the whole query,
    so this copies it across rather than asking the caller to restate it —
    which is what keeps `--url` and `--text/--location` from being two
    different code paths that can disagree.

    A `/{cc}/category/{slug}` URL carries no query at all, so the caller must
    supply the resolved `category_id` and its label; the slug alone cannot
    address the endpoint (see `api_url`).
    """
    parsed = urlparse(url or "")
    params = {k: v for k, v in parse_qsl(parsed.query, keep_blank_values=True)}
    slug = category_from_url(url)
    if slug is not None:
        if not category_id or not category_label:
            raise ValueError(
                "a /%s/category/%s URL needs its tobId resolved first — the "
                "slug is a human address and the endpoint filters on an id. "
                "See category_options() and pick_category_option()."
                % (parsed.path.strip("/").split("/")[0], slug))
        country = COUNTRY_BY_SEGMENT.get(
            parsed.path.strip("/").split("/")[0].lower(), "USA")
        return api_url(country=country, text=category_label, location="",
                       page=page or 1, sort=sort, category_id=category_id,
                       state=state)
    return api_url(country=params.get("find_country") or "USA",
                   text=params.get("find_text") or "",
                   location=params.get("find_loc") or "",
                   page=page or _int_or_none(params.get("page")) or 1,
                   sort=sort, category_id=category_id, state=state)
