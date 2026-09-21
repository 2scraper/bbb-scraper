"""
output_writer.py
-----------------
Shared row models + JSON/CSV writers used by all three scrapers.

Three modes, one row shape
--------------------------
    --mode search     /search?find_text=…&find_loc=…   a keyword search
    --mode category   /us/category/{slug}              one category listing
    --mode profile    /{cc}/{st}/{city}/profile/…      one business profile

All three yield the SAME class, because they are three views of one thing.
The leaf BBB publishes is a BUSINESS AT A LOCATION: a search and a category
listing are two ways of selecting them, and a profile is one of them
described more fully. So there is one dataclass here (a sibling repo needs a
second for reviews; this one does not), and `diff_runs.py` can compare a
search run against a category run on the columns both populate.

The row is `Business` and not `Product`
---------------------------------------
Every shop repo in this family names its row `Product` and keeps the
commerce columns even where they are null, because on a shop a null price is
a fact worth recording. BBB is a business directory, not a shop: it has no
price, no currency, no discount, no stock and no product brand, and there is
nothing on the page to measure beside those names. Six columns null on every
row of every run of every mode is exactly what CLAUDE.md §9 says must not
exist, so they are not here. `quora-scraper` dropped them for the same
reason and is the precedent.

What IS kept, byte-identical and in order, is the family prefix — `source`,
`scraped_at`, `url`, `sku`, `title` — so one column name works across the
family and a consumer reading several of these repos reads the same first
five columns in the same order (§9).

`rating` is absent, and that one is a measurement rather than a definition
------------------------------------------------------------------------
BBB very much has a rating — it is the whole point of the site — but it is
not the family's `rating`. The rest of the family fills that column with a
0-to-5 float off a star widget. BBB publishes a LETTER GRADE (`"A+"`) beside
a 0-to-100 score, and the two are one rating in two notations. Measured over
105 rows on five fixtures (2026-09-16):

    A+  >= 97.0      A  94.0-95.9      A-  92.9-93.8      B-  80.0

Putting 100.0 into a column the family fills with 4.6 would make one column
name mean two different scales, so the grade and the score get their own
names — `rating_grade` and `rating_score` — and `rating` is absent rather
than misleading.

The zero in that column is the trap. An unrated business comes back as
`rating: ""` with `ratingScore: 0.0` — 7 of those 105 rows — so a score of
0.0 means "BBB has not graded this business", NOT "graded worst". Written
through as 0.0 it would drag every average a consumer computes. `parse_row`
therefore maps that pair to `rating_grade=None, rating_score=None`, and the
absence is the honest answer (§8: never present a guess as a fact).

Everything below the dataclass is row-class-agnostic: pass `row_cls` so an
empty CSV still gets the right header for the mode that produced it.
"""

import csv
import json
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timezone
from typing import Optional, List, Set, Sequence, Any, Type


# The hostname a row came from. BBB serves the United States and Canada from
# ONE host — `www.bbb.org` — with the country in the PATH (`/us/…`, `/ca/…`)
# rather than in a TLD, so this column is `bbb.org` on every row of every
# run. It is kept because the family's schema has it in this position and
# consumers read the columns by name across repos; `country` below is what
# actually varies.
SOURCE_DEFAULT = "bbb.org"


@dataclass
class Business:
    source: str = SOURCE_DEFAULT
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # The absolute profile URL, built from the row's own `reportUrl` path.
    url: str = ""
    # BBB's own `id`, which is `{bbbId}_{businessId}_{addressId}` —
    # "0121_134716_209415".
    #
    # It identifies a business AT A LOCATION, not a business, and that
    # distinction is load-bearing: "AV Brad Construction LLC" came back twice
    # on one page of fifteen with the same `businessId` (120583) and two
    # different address ids (128632, 225724). Deduping on `businessId` would
    # have silently dropped one of its two real locations; deduping on this
    # composite keeps both, which is what the site published.
    sku: Optional[str] = None
    # `businessName`, with BBB's search-term highlighting stripped.
    #
    # The site embeds `<em>` tags in the VALUE when the query matched the
    # name — "ADA <em>Restaurant</em>" — on 15 of 15 rows under
    # `--sort rating` and 3 of 15 under `--sort a-z`. `businessName` is the
    # only field affected, measured across all five fixtures. Writing the
    # tags through would put markup in the one column every consumer reads.
    title: Optional[str] = None

    # ---- who the business is -------------------------------------------
    # The three parts of `sku`, split out because each is independently
    # useful and none is recoverable from the others by a consumer who does
    # not know the format. `bbb_id` is the local BBB OFFICE that holds the
    # file (0121 = BBB Serving Metropolitan New York), not the business.
    business_id: Optional[str] = None
    address_id: Optional[str] = None
    bbb_id: Optional[str] = None
    bbb_office: Optional[str] = None
    # The letter grade and the 0-100 score behind it; both None when BBB has
    # not graded this business. See the module docstring.
    rating_grade: Optional[str] = None
    rating_score: Optional[float] = None
    # `bbbMember` — whether the business pays for BBB Accreditation. Named
    # for what the site calls it in public ("BBB Accredited Business")
    # rather than for the field name, because "member" reads like a
    # different thing.
    #
    # This is the column the default sort quietly filters on: `--sort
    # best-match` returned 15/15 accredited on every page measured. See
    # `product_parser.SORTS`.
    is_accredited: Optional[bool] = None

    # ---- what it does ---------------------------------------------------
    # `tobText`/`tobId` — BBB's primary "type of business" for this record,
    # and the id that `--mode category` filters on.
    category: Optional[str] = None
    category_id: Optional[str] = None
    # Every category BBB files it under, 1 to 4 of them over 105 rows. The
    # first is not always `category`, so this is not redundant with it.
    categories: Optional[List[str]] = None

    # ---- where it is ----------------------------------------------------
    # `address` is a single street line and is genuinely absent for some
    # businesses (13 of 15 populated on two fixtures) — a service-area
    # business with no storefront. Null rather than an empty string.
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    # Not a field BBB returns per row: it is the country the query was made
    # against (`find_country=USA|CAN`, or the `/us/` `/ca/` segment of a
    # profile URL). Recorded because a US and a Canadian row are otherwise
    # indistinguishable in a merged file, and because a two-letter state
    # code means different things in the two countries.
    country: Optional[str] = None
    # Split out of the `"40.75806427001953,-73.97885131835938"` STRING BBB
    # returns. 105 of 105 rows carried one, always with exactly one comma.
    # Two float columns are what a consumer can actually filter on.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # `serviceAreasSummary` — the places a business without a storefront
    # covers. Sparse by nature (6 to 9 of 15), and a LIST.
    service_areas: Optional[List[str]] = None
    has_service_area: Optional[bool] = None

    # ---- how to reach it ------------------------------------------------
    # A LIST, and not a single number: 78 of 105 rows carried one, 19 carried
    # two, and one business carried TWELVE. Taking `phone[0]` would have
    # thrown away real data on a quarter of every run.
    phone: Optional[List[str]] = None
    logo_url: Optional[str] = None

    # ---- BBB's own onward links ----------------------------------------
    # Kept because none is derivable from `url`, and each is the address a
    # consumer would otherwise have to go and find. `local_profile_url` is
    # sparse (1 to 4 of 15) and is BBB's alternate profile for the same
    # record under a different city.
    local_profile_url: Optional[str] = None
    leave_review_url: Optional[str] = None
    request_quote_url: Optional[str] = None

    # ---- charity and status flags --------------------------------------
    # BBB files charities alongside businesses and grades them differently
    # (the Wise Giving Alliance seal rather than a letter). A consumer
    # computing "share of A+ businesses" needs to be able to exclude them.
    is_charity: Optional[bool] = None
    charity_seal: Optional[bool] = None
    accredited_charity: Optional[bool] = None
    # Whether BBB has marked the record closed.
    #
    # Two sources, one column, and they disagree about TYPE: a listing row
    # carries `outOfBusinessStatus`, a string that was null on 105 of 105
    # rows measured, while a profile carries `orgDetails.isOutOfBusiness`, a
    # real boolean. Normalised to the boolean here, because a column that is
    # a string in one mode and a bool in the other is a column nobody can
    # filter on. The listing side is therefore UNVERIFIED in the True
    # direction — no captured listing row has ever set it — and that is
    # written down rather than assumed away.
    out_of_business: Optional[bool] = None

    # ---- --mode profile only; null on a listing row ---------------------
    # A listing response does not carry any of these. They come from the
    # profile page's own `__PRELOADED_STATE__.businessProfile`, which is the
    # richest thing BBB publishes and has no API of its own — every
    # `/api/businessprofile`-shaped endpoint tried returned BBB's 404 page.
    #
    # Deliberately NOT here: `contactInformation.contacts`, which names the
    # business's officers as individuals ("Mr. Wellington Deleon,
    # President"). BBB publishes them, and republishing a named person's
    # details is a separate act from the site showing them on its own page
    # (CLAUDE.md §10). A directory of businesses does not need a column of
    # people's names to be useful, so there is not one. Adding it later is a
    # product decision, not an oversight.
    website: Optional[str] = None
    description: Optional[str] = None
    fax: Optional[str] = None
    # `dates.*`, ISO-8601 as BBB states them. `accredited_since` is the one
    # that cannot be reconstructed from anything else and is the reason a
    # profile run is worth making at all for an accredited business.
    accredited_since: Optional[str] = None
    bbb_file_opened: Optional[str] = None
    business_started: Optional[str] = None
    years_in_business: Optional[int] = None
    # `typeOfEntity.name` — "Corporation", "Sole Proprietor", …
    entity_type: Optional[str] = None
    # The numbers a directory is actually consulted for. All four are
    # separate figures on BBB's own page and folding any two together would
    # lose the distinction the site draws: `complaints_total` is lifetime,
    # the other two are the windows BBB's rating formula uses.
    reviews_total: Optional[int] = None
    review_stars_avg: Optional[float] = None
    complaints_total: Optional[int] = None
    complaints_3y: Optional[int] = None
    complaints_12m: Optional[int] = None
    # Why BBB gave the grade it gave, where it says so (`rating.ratingReasons`
    # / `ratingReasonNotRated`). Empty on the captured A+ profile, which is
    # expected: BBB explains a grade it has marked DOWN.
    rating_reasons: Optional[List[str]] = None
    # `isMultiLocation` — True on the captured profile, and the profile-side
    # confirmation of what `sku` already implies: one business, several
    # address ids.
    is_multi_location: Optional[bool] = None

    # ---- about the RUN, not the business --------------------------------
    # Which listing page this row came from (1-based) and its position in
    # that page as BBB ordered it. Without `page`, `position` is ambiguous:
    # it restarts at 1 on every page.
    #
    # `position` is NOT stable between runs where BBB has a tie, and that is
    # measured rather than suspected: three engines fetching the identical
    # query a minute apart returned the identical 30 `sku`s, with rows 15 and
    # 16 SWAPPED — two locations of one business ("11400 Inc",
    # businessId 236021670, address ids 361754 and 264225). BBB's A-Z
    # ordering does not break a tie between two locations of the same
    # business deterministically.
    #
    # So `position` describes one fetch, not the directory, and
    # `diff_runs.py` keys on `sku` — which is unaffected, because the SET
    # came back identical both times.
    page: Optional[int] = None
    position: Optional[int] = None
    # Which mode produced the row. The repo no longer implies it — three
    # modes share this schema — and `diff_runs.py` refuses to compare two
    # runs whose modes it cannot line up (§9).
    mode: Optional[str] = None
    # WHICH ORDERING BBB was asked for. This belongs on the row rather than
    # only in the sidecar, because it changes which businesses are in the
    # file at all, not merely their order: `best-match` returned 15/15
    # accredited and zero query-matched results, `a-z` returned 0/15
    # accredited from the same query. Two runs that differ only here are not
    # comparable, and without the column nothing says so.
    sort: Optional[str] = None
    # Where the row's fields came from:
    #   "api"       BBB's own /api/search JSON — every listing row, whether
    #               an engine read it out of the page or fetched it directly.
    #   "profile"   a rendered profile page.
    # Provenance in a column, per §8, so `diff_runs.py` can tell a real
    # change from a change of source.
    data_source: Optional[str] = None


# Row classes by --mode, so an engine maps its mode to a schema in one place.
# All three are Business here; the mapping exists so adding a mode later is a
# one-line change rather than a search for every place that assumed Business.
ROW_CLASS_BY_MODE = {"search": Business, "category": Business, "profile": Business}

# Modes whose rows are one-per-sku, and therefore safe to dedupe on `sku` and
# to hand to diff_runs.py. All three of this repo's modes qualify: a listing
# page names each business-at-a-location once, and a profile page IS one.
UNIQUE_BY_SKU_MODES = ("search", "category", "profile")


def dedupe_by_key(rows: Sequence[Any], seen: Set[str], key: str = "sku") -> List[Any]:
    """Drop rows whose key already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages —
    a repeated page then re-parses without duplicating its rows into the
    final output. On BBB this should fire RARELY on a healthy run, and that
    is measured: pages 1 and 2 of one search shared **0** of 30 `id`s, and
    every page's fifteen `id`s were distinct.

    What DOES repeat is `businessId` — once across those two pages, and once
    WITHIN page 1, where "AV Brad Construction LLC" appeared twice under two
    different address ids. Those are two real locations of one business, not
    a duplicate, which is why `sku` is the composite
    `{bbbId}_{businessId}_{addressId}` and why deduping on `businessId`
    would quietly delete data the site published. A non-zero drop count here
    means a page was genuinely re-fetched.

    A row with no key is always kept: there is nothing to check a duplicate
    against, and dropping it would be a silent data loss rather than a
    duplicate removal.

    All three of this repo's modes are one row per `sku`, so `key` is never
    overridden here — the parameter exists because the rest of the family
    shares this function and one of them needs it.
    """
    fresh = []
    for r in rows:
        val = getattr(r, key, None)
        if val is None or val not in seen:
            if val is not None:
                seen.add(val)
            fresh.append(r)
    return fresh


# Kept under its old name: the engines and smoke tests in this family all
# call it, and a listing run does dedupe by sku.
def dedupe_by_sku(rows: Sequence[Any], seen: Set[str]) -> List[Any]:
    return dedupe_by_key(rows, seen, key="sku")


# CSV cannot hold a list. Joining with " | " keeps the cell readable in a
# spreadsheet and round-trippable by splitting on the same separator; the
# JSON output keeps the real list, so nothing is lost for a consumer that
# wants structure. `repr()` of a Python list (the default if this is not
# handled) is neither readable nor parseable by anything but Python.
LIST_CSV_SEPARATOR = " | "


def _csv_value(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return LIST_CSV_SEPARATOR.join(str(x) for x in v)
    return v


def write_json(rows: Sequence[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in rows], f, ensure_ascii=False, indent=2)


def write_csv(rows: Sequence[Any], path: str, row_cls: Type = Business) -> None:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows — and "an empty result is still a well-formed
    # result" is the same principle as `save` refusing to overwrite good data.
    #
    # The header comes from `row_cls`, not from the first row, so an empty
    # run still writes the columns of the mode that produced it.
    fieldnames = [f.name for f in fields(row_cls)]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_value(v) for k, v in asdict(r).items()})


# Exit code used when a run completes but produced nothing. Distinct from 1
# (crash) so a caller can tell "ran, found nothing" from "blew up".
EXIT_NO_PRODUCTS = 4

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started — distinct from EXIT_NO_PRODUCTS so a caller can tell "the
# search genuinely matched nothing" from "something stood between us and the
# content". See product_parser.detect_bot_challenge.
#
# On BBB this code does NOT cover a query that simply matched nothing. That
# answers HTTP 200 with `totalResults: 0`, `totalPages: 0` and an empty
# `results` list, and it is EXIT_NO_PRODUCTS: the request was served exactly
# as asked and has no businesses in it. Reporting it as blocked would send a
# user hunting for a proxy problem that does not exist.
#
# What EXIT_BLOCKED means here is Cloudflare, and BBB refuses in TWO shapes,
# both HTTP 403 and both wearing BBB's own branding in the `<title>` so that
# a title check calls them real pages (measured 2026-09-16):
#
#   "Just a moment..."               a Managed Challenge: ~15 KB, carrying
#                                    `challenge-platform`, `__cf_chl` and
#                                    `turnstile`.
#   "You have been blocked | Better  a hard refusal: ~12.5 KB, carrying
#    Business Bureau(R)"             `challenge-platform` and NO turnstile.
#
# Neither carries a single `application/ld+json` block, and from a datacenter
# address the challenge never self-clears — six polls over 30 s returned
# byte-identical markup. See page_flow.detect_page_state.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME rows and then stopped early — a
# page-load timeout, a 503 throttle, or a challenge on page 3 of 10. The
# output file is still written (throwing away three good pages would be
# worse), but it is not a complete picture, and a consumer that cannot tell
# the difference will read the pages that were never fetched as products that
# disappeared from the catalogue. See write_run_meta.
# A REMOTE service failed — the Scraping Browser refusing the connection
# (`profile_locked` is the common one: a profile allows a single live
# connection), or the Scraper API answering an error. Distinct from 1 (a
# crash in this code) and from 2 (bad usage) because it means "try again, or
# use a different profile", not "there is a bug here". Defined once, here,
# because the browser engines and scraper_api_client.py both return it and
# two definitions of the same code is exactly how a family's exit contract
# drifts.
EXIT_API_ERROR = 5

EXIT_PARTIAL = 6


# Exit code for a run that never GOT its pages: a navigation timeout, a dead
# or unauthenticated proxy, a DNS failure, or an edge answering with
# something that is not the page that was asked for.
#
# Distinct from EXIT_NO_PRODUCTS because those are opposite facts. Exit 4 is
# a statement about the CATALOGUE — "we asked, and the answer was nothing" —
# so handing it to a run that never reached the site tells a pipeline the
# listing is empty when nothing was read at all.
#
# 5 rather than a new number, and 5 rather than EXIT_PARTIAL:
#
#   * this family's contract already reserves 5 for a transport failure
#     (scraper_api_client has used it for a remote API error since it was
#     written), so this needs no new code and no per-repo table for a caller
#     driving more than one of these scrapers;
#   * EXIT_PARTIAL (6) means "some rows were gathered and the output is
#     incomplete". A run holding nothing writes no output at all, so a
#     consumer that reads the file on a 6 finds either nothing or the
#     PREVIOUS run's good data, which `save` deliberately does not
#     overwrite. Exit 5 promises no file.
#
# Deliberately NOT applied when rows WERE gathered: a timeout on page 7 of
# 10 is a partial run (exit 6, output written), which is already right. This
# decides only what a run holding nothing reports.
EXIT_FETCH_FAILED = 5


def write_run_meta(out_prefix: str, meta: dict) -> str:
    """Write a run-metadata sidecar next to the output, return its path.

    Deliberately a separate `<out>.meta.json` rather than columns on every
    row: this describes the RUN, not the product, and repeating it across
    every row would both bloat the output and change the schema every
    consumer of this project already parses.

    diff_runs.py reads it to refuse a comparison between runs that are not
    both complete, and between runs of different `mode`.
    """
    path = f"{out_prefix}.meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             mode: str = "listing", source: str = SOURCE_DEFAULT,
             extra: Optional[dict] = None) -> dict:
    """Build the metadata dict for a finished run.

    `status` is the field a consumer branches on:
      complete — every requested page was fetched, or the site's own
                 pagination genuinely ran out (nothing more existed to get)
      partial  — rows were gathered, then the run stopped early
      failed   — nothing was gathered at all

    `mode` and `source` are recorded because `mode` is not implied by the
    repo: the same output prefix can hold a search run, a category run or a
    profile run, and those populate different columns. diff_runs.py refuses
    a pair whose modes or sources differ. `source` is `bbb.org` on every row
    of every run here, since the US and Canadian directories share one host;
    it is kept because consumers read these columns by name across the
    family.

    `extra` carries facts about the run that are not about any single row.
    A listing run uses it for BBB's OWN result counts — `total_results` and
    `total_pages` as the site reported them — and those belong to the run
    rather than repeated down a column. They are also the only honest way to
    say how much of a listing a run actually holds: BBB caps `totalPages` at
    **15** at a `pageSize` of 15, so one query reaches at most 225 rows of a
    `totalResults` that was 19,016 on the search measured. A file of 225 rows
    is complete as a REQUEST and a 1.2% sample as a CATALOGUE, and only the
    sidecar can say so.

    `pages_failed` lists the pages that did not yield data, by number.
    `pages_completed` alone was enough only while pages were fetched strictly
    in order, where "3 of 10 completed" could only mean 1-2-3: a count is not
    a description once pages can be fetched independently and page 3 can fail
    while 4 and 5 succeed. Recording the numbers keeps the sidecar honest
    about WHICH part of the catalogue is missing, not just how much.
    """
    meta = {
        "source": source,
        "mode": mode,
        "status": status,
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        # Named "products" even though these are businesses, and kept that
        # way deliberately: every repo in this family writes this key, and a
        # consumer reading several of them reads one sidecar shape.
        # quora-scraper made the same call for answers. The row TYPE is
        # `mode` plus `source`, which are right beside it.
        "products": products,
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        # Merged rather than nested under a key, so a consumer reads
        # `shop_rating` at the top level beside `products`. Run fields win a
        # name collision: a caller cannot accidentally overwrite `status`.
        meta.update({k: v for k, v in extra.items() if k not in meta})
    return meta


def save(rows: Sequence[Any], out_prefix: str, fmt: str,
         allow_empty: bool = False, row_cls: Type = Business) -> int:
    """Write JSON/CSV and return a process exit code.

    Returns 0 when rows were written, EXIT_NO_PRODUCTS when there were none.
    Callers are expected to exit with it.

    On zero rows, nothing is written at all unless `allow_empty`. Two reasons,
    and a live run demonstrated both. A page-load timeout produced
    `Saved 0 businesses -> out.json` and exit 0: a two-byte `[]` that a
    consuming pipeline reads as a successful run with no stock. Worse, if the
    file already held a good result from an earlier run, that result is now
    gone — the failure destroyed the last known good data. So an empty result
    leaves the previous file intact and says why.

    `allow_empty=True` is for the legitimate case: a filter that genuinely
    matches nothing, where an empty file is the answer.
    """
    if not rows and not allow_empty:
        print(f"[!] 0 businesses — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(rows, f"{out_prefix}.json")
        print(f"[+] Saved {len(rows)} businesses -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        write_csv(rows, f"{out_prefix}.csv", row_cls=row_cls)
        print(f"[+] Saved {len(rows)} businesses -> {out_prefix}.csv")
    return 0 if rows else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see. Anything
# else ended the page loop early, so the result is only a partial view.
#
# "no_new_products" belongs here and "pagination_exhausted" is kept for the
# engines that still stop on a missing next-link: the first is a property of
# the DATA (a page contributed nothing not already seen, so the listing is
# over), while the second is a property of a CSS SELECTOR and is therefore
# the weaker signal — a renamed attribute looks identical to a short
# catalogue.
#
# On BBB there is a THIRD, stronger signal, and it is the site's own
# arithmetic: every listing response states `totalPages`, so the end of the
# listing is known from page 1 rather than discovered by walking off it.
# "page_cap_reached" is that stop reason, and it is a COMPLETE run: BBB caps
# `totalPages` at 15 however large `totalResults` is, so a run that fetched
# all 15 fetched everything the site will serve for that query. Asking for
# page 16 answers **HTTP 500**, not an empty page, so walking off the end is
# not merely wasteful here — it manufactures an error that looks like a
# fault in the scraper.
#
# "single_page_mode" is complete by construction: --mode profile reads one
# page because one page is all there is.
COMPLETE_STOP_REASONS = ("completed", "pagination_exhausted", "no_new_products",
                         "page_cap_reached", "single_page_mode")


def finish_run(rows: Sequence[Any], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               mode: str = "listing", source: str = SOURCE_DEFAULT,
               extra: Optional[dict] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all three browser engines so the status/exit-code mapping
    cannot drift between them.

    The metadata sidecar is written ONLY when the row file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other, and
    diff_runs.py would refuse to compare data that is in fact fine.
    """
    complete = stop_reason in COMPLETE_STOP_REASONS
    row_cls = ROW_CLASS_BY_MODE.get(mode, Business)
    rc = save(rows, out_prefix, fmt, allow_empty=allow_empty, row_cls=row_cls)
    wrote_output = bool(rows) or allow_empty

    if wrote_output:
        status = "complete" if (rows and complete) else (
            "partial" if rows else "failed")
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, mode=mode, source=source,
            start_url=start_url, final_url=final_url, products=len(rows),
            extra=extra))

    if not rows:
        # Nothing gathered at all, and WHY decides the code. The three
        # outcomes are different facts and a pipeline branches on them
        # (blocked is not empty is not "never reached"):
        #
        #   blocked            something stood between the run and the content
        #   did not complete   we never got the pages — a dead proxy, a load
        #                      timeout, an edge serving something else
        #   completed          we asked, and the answer was nothing
        #
        # Keyed on `not complete` rather than on a list of stop reasons, on
        # purpose: a list cannot cover a reason nobody has added to it yet,
        # so a new one falls silently through to "the catalogue is empty" —
        # which is the defect this branch exists to prevent.
        if blocked:
            return EXIT_BLOCKED
        if not complete:
            print(f"[!] Nothing was gathered and the run did not finish "
                  f"({stop_reason}) — exit {EXIT_FETCH_FAILED}, NOT an empty "
                  f"result (exit {EXIT_NO_PRODUCTS}). Nothing can be "
                  f"concluded about the catalogue from this run.")
            return EXIT_FETCH_FAILED
        return rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
