# Releasing this iteration

**Status: built and tested locally. Nothing has been pushed, deployed or published.**
The live site is still running the previous version. Every claim below about behaviour comes
from local builds and the test suite; nothing here has been observed on mithqalprice.com.

**Where the suite has been run:** Linux (Python 3.11, Node 22), in a clean staging tree laid
out exactly as the repository will be — `.github/workflows/update-prices.yml` present, the
flat staging name absent. An independent review also ran it under Windows/Git Bash. The
workflow-shell tests need `git` and a `bash`; where there is no `bash` on PATH they skip and
say so rather than reporting a pass.

Publishing is Eslam's action. Claude did not touch the GitHub repository, the Cloudflare
project, any account, any secret or the production branch.

## What is in this release

14 pages (was 8), a rewritten data layer, and the founding principle carried through the copy.
The reasoning lives in `PAGE-MAP.md`, `DATA-SCHEMA.md`, `MEASUREMENT.md` and `WIDGET-MVP.md`.
Headlines:

- **No existing URL changed.** The eight indexed paths are untouched; a test fails if one moves.
- **Six new pages: five English** (`/sa/en/silver/`, `/sa/en/calculator/`, `/sa/en/sell-price/`,
  `/sa/en/up-or-down/`, `/sa/en/methodology/`) **and one Arabic** (`/sa/methodology/`).
- An English + Arabic silver calculator, a methodology page in both languages.
- Comparisons name the date they actually compared against; "yesterday" means yesterday.
- `history.json` holds finished days only; today is shown as "so far today" and never stored.
- We no longer call the provider's daily figure a "close" — its documentation does not say
  what the figure is, so the pages say "the recorded price for 10 Sep".
- The weekend exemption is bounded, and the market state is recomputed in the reader's browser,
  so a page left open from Saturday to Monday turns into a staleness warning instead of going
  on announcing a closed market.
- A live feed with no quote time stops the build instead of being shown as current.
- One versioned snapshot feeds the pages, the calculators and `/data/prices.json`.

## One upload, not batches

These files depend on each other: `build.py` supplies values the templates reference, the
templates reference values only the new `build.py` supplies, and the workflow reads an output
only the new `build.py` writes. **Upload all 38 files in a single
commit.** Uploading modules first breaks the old templates; uploading templates first breaks
the old builder, which is how a build failed on 2026-09-12. GitHub's "Add file → Upload files"
takes the whole set at once — drag the folders in so the structure is kept.

### New files (16)

```
DATA-SCHEMA.md  MEASUREMENT.md
PAGE-MAP.md  RELEASE.md
WIDGET-MVP.md  history.py
pages.py  snapshot.py
static/analytics.js  static/og.png
templates/sa_en_calculator.html  templates/sa_en_methodology.html
templates/sa_en_sell_price.html  templates/sa_en_silver.html
templates/sa_en_up_or_down.html  templates/sa_methodology.html
```

### Changed files (22)

```
DEPLOY.md  README.md
build.py  config.json
data/sample_prices.json  github-workflow-update-prices.yml
prices.py  providers.py
static/calc.js  static/style.css
templates/base.html  templates/footer_ar.html
templates/footer_en.html  templates/home.html
templates/sa_calculator.html  templates/sa_en.html
templates/sa_index.html  templates/sa_sell_price.html
templates/sa_silver.html  templates/sa_up_or_down.html
templates/sa_zakat.html  tests/test_prices.py
```

### Renamed on upload

One file lands under a different name than it has here. The local folder cannot hold a path
that starts with a dot-folder, so it is staged under a flat name and renamed in the repository.

| In this folder and in the manifest above | In the repository |
|---|---|
| `github-workflow-update-prices.yml` | `.github/workflows/update-prices.yml` |

There is exactly **one** workflow file in the repository, at the second path — the one GitHub
actually executes. Do not keep a copy under the staging name: a test fails if both exist, and
the workflow-shell tests load whichever path is present so they always exercise the real file.

### Unchanged — do not re-upload (11)

```
.gitignore  data/history.json
data/zakat_rules.json  static/TAJAWAL-OFL.txt
static/apple-touch-icon.png  static/icon-192.png
static/icon.svg  static/tajawal-ar-400.woff2
static/tajawal-ar-700.woff2  static/tajawal-lat-400.woff2
static/tajawal-lat-700.woff2
```

**`data/history.json` in particular.** The robot has been writing it since launch, so the copy
in the repository is ahead of the local one. The new code reads the old format correctly:
rows with no provenance are labelled `unknown`, used, and marked in the history table until
they age out. Overwriting it with the local copy would throw away real days.

### config.json — read this before uploading it

The local `config.json` carries the **live** values (`noindex: false`, `provider: metals_dev`)
plus the new validation limits and the analytics block, so it is safe to upload as-is. Local
previews must therefore pass `--provider sample` explicitly.

New keys: `plausible_usd_oz`, `max_daily_move_pct`, `sar_peg_tolerance`, `history_days`,
`public_snapshot_path`, `analytics`.

## Release steps

1. Upload the 38 files above in one commit, and rename the workflow
   file into `.github/workflows/update-prices.yml`.
2. While you are there, delete the three stale paths flagged in the checkpoint: the root
   `test_prices.py`, `__pycache__/`, and `dist/`.
3. Watch the Actions run. It should build 14 pages and upload. If it fails, nothing is
   published and the live site is unaffected — read the log, it now says why.
4. Check on the live site, in this order:
   - `/sa/en/silver/` returns 200 (a page that did not exist before)
   - `/sitemap.xml` lists 14 URLs
   - `/data/prices.json` loads and its `quoted_utc` is recent
   - `/sa/` shows a comparison label with a real date
   - a phone-width check of `/` — the header should be two short rows
5. Search Console: submit the sitemap again and request indexing for the six new URLs.

## Rolling back

The site republishes itself every fifteen minutes, so a Cloudflare rollback on its own lasts
until the next run. **Stop the job first, then roll back** — and "stop" means two things, not
one.

1. **Disable the workflow.** GitHub → Actions → "Update prices" → ⋯ → *Disable workflow*.
   This stops new runs from starting. It does **not** stop a run that is already going.
2. **Cancel anything in flight.** Same Actions page: if a run is listed as in progress, open
   it and *Cancel workflow*, then wait for it to finish cancelling. A run that reaches its
   upload step after you roll back will publish the bad build straight over your rollback.
   Only when the list shows no running job is publishing actually stopped.
3. **Roll back the served version.** Cloudflare dashboard → Pages → `gold` → Deployments →
   the last good deployment → *Rollback*. Instant, and needs no code change.
4. **Revert the code** when you have decided what went wrong: revert the upload commit in
   GitHub. Do **not** revert `data/history.json` — its rows are real days, and the old code
   reads the new file correctly.
5. **Re-enable the workflow.** The next scheduled run rebuilds from whatever main now says and
   republishes. Confirm the live page matches that commit before walking away.

If only one thing misbehaves, two cheap switches avoid a full rollback: set `bid_ask_metals`
to `[]` (drops bid/ask, halves the request budget), and `analytics.provider` to `"none"`
(which it already is).

## What the workflow does about races, and what it does not

- **A bounded check against publishing stale code.** `dist/` is built and tested from the
  commit the job checked out. Immediately before uploading, the job compares `HEAD` with
  `origin/main`; if main has moved — you uploaded a fix, or another run pushed — it skips the
  upload with a warning rather than publishing the older build over the newer.

  Read that precisely: it is **one comparison at one moment**, not a lock. If main moves in
  the seconds between the check and the upload, the upload still goes ahead. It closes the
  wide window (a build and a test run, a minute or two) and not the narrow one. The cost of
  the narrow one is small — the next scheduled run republishes from the newer main — but it
  is not zero, so during a release watch the Actions tab rather than assuming.
- **It commits the data folder with `git add -A data`**, so a `history_state.json` that does
  not exist yet cannot fail the step, and it rebases before pushing so two runs cannot lose
  each other's rows.
- **It commits retry counters even when no price changed**, which keeps the three-attempt
  backfill cap from resetting on every fresh checkout. The step is marked `always()`, so a
  failed or skipped upload no longer discards them.

  Where that cap is **not** durable: when the build itself stops (a bad price, no quote time,
  a feed outage), `build.py` never writes the outputs, so nothing is committed — deliberately,
  because a build that produced no publishable artifact should leave no trail. Across a run of
  failing builds a missing date can therefore be requested more than three times. That errs
  toward spending requests on a feed that is already broken, which is the safe direction, but
  it is not an absolute cap and should not be described as one.

## Checking the release layout before you upload

The suite must pass in the layout the repository actually has, not the one this folder has.
Four commands build that layout in a scratch folder and run everything in it:

```bash
rm -rf /tmp/release-staging && mkdir -p /tmp/release-staging/.github/workflows
cd site && tar --exclude=dist --exclude=__pycache__ -cf - . | (cd /tmp/release-staging && tar xf -)
mv /tmp/release-staging/github-workflow-update-prices.yml /tmp/release-staging/.github/workflows/update-prices.yml
cd /tmp/release-staging && python build.py --provider sample && python -m unittest discover tests
```

Three things that tree must be true of, and the suite checks all three:

- `.github/workflows/update-prices.yml` exists and `github-workflow-update-prices.yml` does
  **not** — exactly one workflow file, and it is the one GitHub runs;
- the workflow-shell tests load that file, not a leftover copy;
- `data/history.json` is byte-identical before and after — the run uses sample data and
  fixtures in temporary folders, and never writes to the real history.

Last run, 2026-09-12: 14 pages built, **140 tests, all passing**, history untouched.

## Still outstanding for Eslam

| | |
|---|---|
| Search Console | **Not verified** — this project has no record either way. Open it and tell the next session; until then the 90-day experiment may have no search data. `MEASUREMENT.md` has the steps |
| English zakat page | Deliberately not built. Needs a competent Arabic-English review before it can exist. `PAGE-MAP.md` explains |
| Metals.Dev redistribution | Ask them in writing before any widget or offered endpoint. `WIDGET-MVP.md` has the quotes from their terms |
| Front page copy | The "ما هذا الموقع؟" section is still the one you have not read |
| Repo junk | Root `test_prices.py`, `__pycache__/`, `dist/` — deleting repo files is your job |
