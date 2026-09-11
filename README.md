# Prototype site (gold + silver prices, Saudi Arabia)

Status: **prototype, sample data, not published** (built session 04, 2026-09-11).
Pages carry `noindex` and a yellow "sample data" banner until a real price feed and domain exist.

## What it builds

| URL | Language | Page |
|---|---|---|
| `/sa/` | Arabic | Gold price today by karat (24/22/21/18), 7-day history, silver box |
| `/sa/up-or-down/` | Arabic | Up or down vs yesterday and last week, per karat |
| `/sa/sell-price/` | Arabic | Market sell/buy (bid/ask) by karat + used-gold sell calculator |
| `/sa/calculator/` | Arabic | Gold value calculator (weight × karat, optional making charge) |
| `/sa/zakat/` | Arabic | Gold + silver nisab today in SAR + zakat calculator, both scholarly views, cited sources |
| `/sa/silver/` | Arabic | Silver price today by purity (999/925) per gram, ounce and kilo, 7-day history, nisab link |
| `/sa/en/` | English | Gold rate per gram / 10 g / tola / ounce, INR and PKR, Riyadh/Jeddah, silver section, FAQ |

Not built yet: UAE pages, ads, structured data (FAQ markup skipped: since 2023 Google shows FAQ rich results
only for government and health sites).

## How it works (plain English)

1. `build.py` asks a **price provider** for today's gold and silver price (USD per ounce).
2. **Safety checks** stop the build if the price looks wrong (out of range, jumped > 10% in a day,
   older than 90 minutes, bad riyal rate). The old pages then stay online, so a wrong price is never published.
3. It saves the day's price in `data/history.json` (for "up or down" and the 7-day table). Once a day it also
   asks the feed for the real daily closes of the past days. On weekends the metals market is closed, so the
   "price is too old" check is skipped from Friday 21:00 UTC to Sunday 22:00 UTC.
4. It fills the HTML templates in `templates/` and writes the finished site into `dist/`.
5. The calculators run in the visitor's browser (`static/calc.js`) using the prices embedded in each page.

Formulas: SAR per gram = USD/oz × 3.75 ÷ 31.1034768 · karat price = 24K × karat ÷ 24 · silver = pure × purity ÷ 1000.

## Run it on your computer (Windows)

1. Install Python 3 from python.org (tick "Add Python to PATH").
2. Open a terminal in this `site` folder and run:
   ```
   python build.py
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
| `config.json` | Site name (placeholder), domain (placeholder), provider, karats, noindex switch |
| `build.py` | Fetch → check → history → render |
| `prices.py` | Price and zakat math (pure functions) |
| `providers.py` | Price feed adapters |
| `data/zakat_rules.json` | Nisab, rate, jewelry opinions, each with its source |
| `data/sample_prices.json` | Sample prices for development |
| `templates/` | Page HTML (`{{name}}` placeholders) |
| `static/` | CSS and calculator JS |
| `tests/` | Unit tests |
| `dist/` | Generated site (rebuild any time; don't edit by hand) |
| `data/history.json` | Daily prices, created on the first live run |
| `github-workflow-update-prices.yml` | The scheduled rebuild + upload job (goes to `.github/workflows/` in the repository) |
| `DEPLOY.md` | How to put the site online |
