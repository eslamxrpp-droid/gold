# Mithqal — gold and silver prices, Saudi Arabia

Live at **https://mithqalprice.com**. Static site, built by a Python script, rebuilt every
15 minutes by a GitHub Action and uploaded to Cloudflare Pages.

**Founding principle: clarity & integrity > revenue.** Every number shows its source, quote
time, unit and purity basis; spot metal value is never presented as a shop price; the stale,
market-closed and time-unavailable states are designed, not hidden; and nothing is invented —
no dealer prices, no forecasts, no ratings, no explanations of why the market moved.

## The files

| File | What it does |
|---|---|
| `pages.py` | **The page registry.** Nav, canonicals, hreflang, breadcrumbs and the sitemap all come from here |
| `providers.py` | Talks to the price feed and normalises its shape |
| `snapshot.py` | Builds the one versioned snapshot, and decides whether it may be published |
| `history.py` | Finalised daily closes: gaps, bounded backfill, saving |
| `prices.py` | Pure arithmetic and the market-session rules |
| `build.py` | Renders the pages. Asks the four above; decides nothing about data itself |
| `PAGE-MAP.md` | The 14 pages, what each is for, and the one deliberately not built |
| `DATA-SCHEMA.md` | The snapshot, the four freshness states, what stops a build, how history works |
| `MEASUREMENT.md` | Search Console status and the analytics event contract (switched off) |
| `WIDGET-MVP.md` | Follow-on work, and the redistribution-rights question that gates it |
| `DEPLOY.md` / `RELEASE.md` | Putting it online / shipping a change and rolling one back |

## The pages

14 pages: `/` plus seven Arabic pages under `/sa/` and six English pages under `/sa/en/`.
The full table, with each page's job and its translation pair, is in `PAGE-MAP.md`.
An English zakat page is deliberately **not** built — the reason is there too.

## How it works (plain English)

1. `build.py` asks a **price provider** for gold and silver in USD per ounce.
2. `snapshot.py` turns that into **one versioned snapshot** where every number carries its
   unit, purity basis, currency, quote side, quote time and status — and then **validates**
   it. If anything fails (implausible price, no quote time, a jump over 10%, bid above ask,
   a bad riyal rate, a quote older than the limit) the build stops and the pages already
   online stay exactly as they are. `DATA-SCHEMA.md` lists every stopping condition.
3. `history.py` keeps **finalised daily closes** in `data/history.json`. Today is never
   written there: today's live price is shown flagged "so far today", and today's real close
   arrives tomorrow. Missing dates are detected and re-requested a bounded number of times,
   and never invented.
4. `build.py` fills the templates for every page in the registry and writes `dist/`, plus
   `sitemap.xml`, `robots.txt`, `_headers`, and `data/prices.json` (the same snapshot, for
   the site's own JavaScript).
5. The calculators run in the visitor's browser (`static/calc.js`) from the embedded
   snapshot. The page re-checks its own freshness on a timer and offers a reload when a newer
   build exists — it never calls the price provider, so traffic costs no API requests.

Formulas: SAR per gram = USD/oz × 3.75 ÷ 31.1034768 · karat price = 24K × karat ÷ 24 ·
silver = pure × purity ÷ 1000. The same formulas exist in `static/calc.js`, and the tests run
both on the same inputs and compare.

## Run it on your computer (Windows)

1. Install Python 3 from python.org (tick "Add Python to PATH").
2. Open a terminal in this `site` folder and run:
   ```
   python build.py --provider sample
   python -m http.server 8000 -d dist
   ```
3. Open http://localhost:8000/sa/ in a browser.

Tests (price math, zakat math, calculator JS = Python, safety checks; needs Node.js for the JS check):
```
python -m unittest discover tests
```

## Going online

`DEPLOY.md` has the step-by-step: GitHub Actions rebuilds every 5 minutes and uploads to Cloudflare Pages
(`github-workflow-update-prices.yml`).

## Price providers (`config.json` → `"provider"`)

| Provider | Key | Notes |
|---|---|---|
| `sample` | none | Default. Invented moves around approx. spot of 2026-09-11. **Not real prices.** |
| `metals_dev` | env var `METALS_API_KEY` | Recommended candidate (see `experiment-plan.md` §5). Gold + silver + SAR/INR/PKR + bid/ask. **Not tested live yet.** |
| `gold_api` | none | Free backup / cross-check. No bid/ask, no INR/PKR. **Not tested live yet.** |

Quick live test on your PC (no key needed): `python build.py --provider gold_api`.
With a Metals.Dev key, on Windows: `set METALS_API_KEY=your-key` then `python build.py --provider metals_dev`.

Never write the API key into a file: set it as an environment variable where the build runs.

## Files

| Path | What |
|---|---|
| `config.json` | Brand, domain, provider, karats, validation limits, analytics switch, noindex |
| `pages.py` | The page registry (see `PAGE-MAP.md`) |
| `build.py` | Rendering: templates in, `dist/` out |
| `snapshot.py` | The versioned snapshot and every rule that can stop a build |
| `history.py` | Finalised daily closes, gap detection, bounded backfill |
| `prices.py` | Price and zakat math, market-session rules (pure functions) |
| `providers.py` | Price feed adapters |
| `data/zakat_rules.json` | Nisab, rate, jewellery opinions, each with its source |
| `data/sample_prices.json` | Sample prices for local previews |
| `data/history.json` | Finalised daily closes. Today is never in here |
| `data/history_state.json` | Backfill attempt counters, so a permanent gap is not retried forever |
| `templates/` | Page HTML (`{{name}}` placeholders) |
| `static/` | CSS, calculator JS, self-hosted Tajawal, icons, `og.png` |
| `static/analytics.js` | Event adapter, **disabled** — see `MEASUREMENT.md` |
| `tests/` | 96 tests. They build into a temp folder and never touch `dist/` |
| `dist/` | Generated site (rebuild any time; don't edit by hand) |
| `github-workflow-update-prices.yml` | The scheduled rebuild + upload job (goes to `.github/workflows/` in the repository) |
| `DEPLOY.md`, `RELEASE.md` | Putting the site online; shipping and rolling back a change |

## Previewing the unhappy states

A local preview shows the ordinary fresh state. To see the others:

```
python build.py --provider sample                      # normal
MITHQAL_SAMPLE_AGE_MINUTES=420 python build.py --provider sample   # stale banner
MITHQAL_SAMPLE_AGE_MINUTES= python build.py --provider sample      # no quote time
```

A sample build always writes `robots.txt` as `Disallow: /`, forces `noindex` on every page,
shows the yellow banner and drops a `SAMPLE-BUILD-DO-NOT-PUBLISH.txt` marker in `dist/`, so a
preview can never be mistaken for a release artifact.
