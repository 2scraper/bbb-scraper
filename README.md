# BBB Scraper

Free, open-source scraper for [Better Business Bureau (bbb.org)](https://www.bbb.org).
Extracts business listings including name, phone, address, BBB rating, accreditation
status, category, reviews, complaints, and more.

**GitHub:** [github.com/2scraper/bbb-scraper](https://github.com/2scraper/bbb-scraper)

---

## Cloudflare protection — important

BBB uses **Cloudflare Managed Challenge** which blocks all standard automation tools
(Playwright, Selenium, camoufox, curl-cffi, undetected-chromedriver alone).

**The only verified working approach:**

```
Chrome (undetected-chromedriver)
  → localhost:18080 (mitmproxy relay, adds proxy auth)
    → residential proxy (2captcha/2prx)
      → bbb.org
```

Cloudflare passes residential IPs automatically without any challenge.

---

## Quick start

### 1. Install

```bash
git clone https://github.com/2scraper/bbb-scraper.git
cd bbb-scraper
pip install undetected-chromedriver mitmproxy 2captcha-python
```

### 2. One-time: trust mitmproxy certificate

This prevents Chrome from showing a security warning on every run.

```bash
# Generate the certificate (run once, Ctrl-C after a second)
mitmdump --listen-port 18080 &
sleep 2 && kill %1

# Trust it in macOS keychain
sudo security add-trusted-cert -d -r trustRoot \
    -k /Library/Keychains/System.keychain \
    ~/.mitmproxy/mitmproxy-ca-cert.pem
```

### 3. Run

```bash
chmod +x run_with_proxy.sh

./run_with_proxy.sh \
  'http://USER:PASS@PROXY_HOST:PORT' \
  'restaurants' \
  'New York, NY'
```

Output is saved to `bbb_results.json` by default.

---

## Usage

### Search mode (keyword + location)

```bash
./run_with_proxy.sh 'http://user:pass@host:port' 'restaurants' 'New York, NY'
./run_with_proxy.sh 'http://user:pass@host:port' 'plumbers' 'Chicago, IL' \
    --output plumbers.csv --max-pages 10
./run_with_proxy.sh 'http://user:pass@host:port' 'lawyers' 'Los Angeles, CA' \
    --enrich --output lawyers.json
```

### Category mode (full category browse)

```bash
# Find category URLs at https://www.bbb.org/us/categories
python bbb_uc.py --mode category \
    --category-url https://www.bbb.org/us/category/restaurants \
    --proxy http://localhost:18080 \
    --output restaurants.json
```

### All flags (bbb_uc.py)

| Flag | Description |
|---|---|
| `--mode` | `search` or `category` |
| `--keyword` | Business type, e.g. `"restaurants"` (search mode) |
| `--location` | City/state, e.g. `"New York, NY"` (search mode) |
| `--category-url` | Category URL (category mode) |
| `--output` | Output file: `.json` or `.csv` (default: `bbb_results.json`) |
| `--max-pages` | Max pages to scrape; `0` = all |
| `--proxy` | `http://host:port` — use `run_with_proxy.sh` for auth |
| `--2captcha-key` | 2captcha.com API key (env: `APIKEY_2CAPTCHA`) |
| `--chrome-version` | Chrome major version if uc can't detect it |
| `--enrich` | Visit each profile page for full JSON-LD data |
| `--headless` | Run Chrome without a window |
| `--debug` | Save raw HTML snapshots to `debug/` |

### Extra flags via run_with_proxy.sh

Any flags after the location are passed to `bbb_uc.py`:

```bash
./run_with_proxy.sh 'http://user:pass@host:port' 'dentists' 'Houston, TX' \
    --output dentists.csv --max-pages 5 --enrich --debug
```

---

## Data fields

### Listing (all modes)

| Field | Source |
|---|---|
| `name` | `.result-business-name > a > span` |
| `profile_url` | BBB profile link |
| `phone` | `.result-business-info > div > div > a` |
| `location` | `.result-business-info > div > div > p` |
| `tagline` | Short description on card |
| `logo` | Business logo URL |

### With `--enrich` (visits each profile, reads JSON-LD)

| Field | Source |
|---|---|
| `street` | `address.streetAddress` |
| `city` | `address.addressLocality` |
| `state` | `address.addressRegion` |
| `zip` | `address.postalCode` |
| `country` | `address.addressCountry` |
| `latitude` / `longitude` | `geo.latitude` / `geo.longitude` |
| `phone` | `telephone` (overrides listing value) |
| `description` | Business description |
| `founded_at` | `foundingDate` |
| `site_url` | Business website (`a.dtm-url`) |
| `rating` | `aggregateRating.ratingValue` |
| `rating_count` | `aggregateRating.reviewCount` |

---

## Output example

```json
[
  {
    "name": "Joe's Pizza",
    "profile_url": "https://www.bbb.org/us/ny/new-york/profile/pizza/...",
    "phone": "(212) 555-0101",
    "location": "123 Main St, New York, NY 10001",
    "tagline": "Serving New York since 1985",
    "logo": "https://www.bbb.org/logos/...",
    "street": "123 Main St",
    "city": "New York",
    "state": "NY",
    "zip": "10001",
    "country": "US",
    "latitude": 40.7128,
    "longitude": -74.006,
    "description": "Family-owned pizzeria...",
    "founded_at": "1985",
    "site_url": "https://joespizza.com",
    "rating": "4.5",
    "rating_count": "123"
  }
]
```

---

## Proxy

BBB requires **residential proxies** to bypass Cloudflare. Datacenter IPs are blocked
immediately regardless of browser or tool.

Get residential proxies at [2captcha.com/proxy](https://2captcha.com/proxy)
(same service as [2prx.com](https://2prx.com)).

`run_with_proxy.sh` handles proxy authentication automatically via mitmproxy.

---

## CAPTCHA solving

If you encounter Turnstile challenges on other pages, pass your
[2captcha.com](https://2captcha.com) API key:

```bash
export APIKEY_2CAPTCHA="your_key"
./run_with_proxy.sh 'http://user:pass@host:port' 'lawyers' 'Seattle, WA'
```

---

## Anti-detect browser

For large-scale operations: [2captcha.com/anti-detect-browser](https://2captcha.com/anti-detect-browser)

---

## Known issues

| Issue | Fix |
|---|---|
| `ChromeDriver version mismatch` | Pass `--chrome-version 148` (your Chrome version) |
| `selenium-wire` incompatible with pyOpenSSL>=26 | Use `run_with_proxy.sh` + mitmproxy instead |
| Chrome shows certificate warning | Trust mitmproxy cert (see Quick Start step 2) |
| `blinker._saferef` error | `pip install blinker==1.7.0` |
| IP banned ("You have been blocked") | Switch proxy region or wait — the IP rotates |

---

## License

MIT

---

## Related

- [2captcha.com](https://2captcha.com) — CAPTCHA solving API
- [2captcha.com/proxy](https://2captcha.com/proxy) — Residential proxies
- [2captcha.com/anti-detect-browser](https://2captcha.com/anti-detect-browser) — Anti-detect browser
- [github.com/2scraper](https://github.com/2scraper) — More open-source scrapers
