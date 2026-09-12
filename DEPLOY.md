# Putting the site online (GitHub + Cloudflare Pages)

Chosen 2026-09-11. Cost: $0 for hosting, plus the price feed ($9.99/month Metals.Dev; its free plan
is fine for testing). Everything here uses Eslam's own accounts.

**How it works:** GitHub keeps the code. A free GitHub robot ("Actions") runs `build.py` every 15 minutes,
which fetches the price and rebuilds the pages, then uploads the finished `dist` folder to Cloudflare Pages,
which serves it to visitors. If a price looks wrong the build fails, nothing is uploaded, and the pages
already online stay unchanged.

## One-time setup

1. **Price feed:** sign up at metals.dev, copy the API key. The free plan is 100 requests/month — about 33 updates,
   i.e. a few hours, so it is for testing only. **Plan in use: Silver, $9.99/month, 10,000 requests.**
2. **GitHub:** create an account, then a repository (e.g. `metals-site`). Upload **only this `site` folder's
   contents** — not the research documents. Public repository = free unlimited robot minutes.
   Then rename `github-workflow-update-prices.yml` to `.github/workflows/update-prices.yml` inside the repository
   (GitHub only runs the robot from that path; Claude cannot write a folder starting with a dot on your PC).
3. **Cloudflare:** create an account → Workers & Pages → Create → Pages → "Direct upload" → name the project
   (e.g. `metals-site`) → upload the `dist` folder once by hand so the project exists.
4. **API token:** Cloudflare → My Profile → API Tokens → Create Token → template "Edit Cloudflare Workers"
   (or a custom token with `Cloudflare Pages: Edit`). Copy it. Also copy your Account ID from the Workers & Pages page.
5. **Put the keys into GitHub** (repository → Settings → Secrets and variables → Actions):
   - Secrets: `METALS_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`
   - Variable: `CF_PAGES_PROJECT` = the Cloudflare Pages project name
   Keys live only here — never inside the files.
6. **Domain:** buy it, add it to Cloudflare (free plan), then Pages project → Custom domains → add the domain.
7. **Go live:** in `config.json` set `"noindex": false` and `"base_url"` to the real domain, and set
   `"provider": "metals_dev"`. Until then the site tells Google not to index it.
8. **Search Console:** add the domain, submit `/sitemap.xml`.

## After setup

- The robot runs on its own. To run it now: repository → Actions → "Update prices" → "Run workflow".
- A failed run emails you. Usual causes: expired key, feed down, or a price that failed a safety check.
- The price history file is committed once a day, which also keeps the schedule alive (GitHub switches off
  schedules in public repositories after 60 days with no activity).

## Known limits (checked 2026-09-11)

| Thing | Limit | Our use |
|---|---|---|
| GitHub Actions, public repository | free, unlimited minutes | ~288 runs/day, ~1 min each |
| GitHub schedule | 5 min minimum, **can run 5–30 min late** at busy hours | we run every 15 min; pages always show the real last-updated time |
| Cloudflare Pages free | 500 *builds*/month; direct uploads (what we use) reported by Cloudflare staff as not counted | ~8,760 uploads/month — **watch this**, it is a forum answer (2022/2023), not documentation |
| Cloudflare Pages free | 20,000 files, 25 MiB per file, unlimited bandwidth | tiny site |
| Metals.Dev Silver plan | 10,000 requests/month (quota exceeded = API switched off, no overage charge) | **2 per build × ~2,920 builds ≈ 5,870**, plus ~31 backfill = **59% of quota** |

If Cloudflare ever starts counting direct uploads, move to every 15 or 30 minutes (~2,880 / ~1,440 uploads a month) or switch to the
Cloudflare Workers option (renders pages on request, $5/month).

## Request budget (read this before changing the schedule)

Metals.Dev charges per request, so **cost = requests per build × builds per month**. Both halves are tunable.

**Requests per build** — set by `bid_ask_metals` in `config.json`:

| `bid_ask_metals` | Requests/build | What it costs you |
|---|---|---|
| `["gold"]` *(current)* | 2 | — |
| `[]` | 1 | `/sa/sell-price/` loses its buy/sell split and shows one price |
| `["gold", "silver"]` | 3 | nothing gained: silver bid/ask is not displayed anywhere |

**Builds per month** — set by the `cron` line in `.github/workflows/update-prices.yml`:

| Schedule | Builds/month | Requests/month (at 2 per build) | Spare on a 10K plan |
|---|---|---|---|
| `*/5 * * * *` | 8,760 | 17,550 | **over budget** |
| `*/10 * * * *` | 4,380 | 8,790 | 12% |
| `*/15 * * * *` *(current)* | 2,920 | 5,870 | 41% |
| `*/30 * * * *` | 1,460 | 2,950 | 70% |

Leave real headroom: manual re-runs, failed jobs that retry, and local testing all spend from the same pot.

**If the quota runs out**, the build fails at "Build the site", nothing is uploaded, and the live pages keep the
last good prices — stale but never wrong. Since 2026-09-12 the log says so in words, e.g.
`… returned HTTP 400 -> most often the monthly request quota is used up … Response: {"error_message":"..."}`.
API keys are redacted in that message, because the Actions log is public on a public repo.
