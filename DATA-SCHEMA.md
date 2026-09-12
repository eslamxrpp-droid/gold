# Data and price integrity

How a number gets from the provider onto a page, and every place it is allowed to stop.

## The modules

| File | Job |
|---|---|
| `providers.py` | Talks to the feed. Normalises one provider's shape into ours. Knows nothing about pages. |
| `snapshot.py` | Builds the versioned snapshot, and validates it. The only place that decides whether a price may be published. |
| `history.py` | Daily rows for finished days: loading, provenance, validation, gap detection, bounded backfill, saving. |
| `prices.py` | Pure arithmetic and the market-session rules. No I/O. |
| `build.py` | Rendering only. It asks the other four and lays out the answer. |

## The snapshot: `mithqal.prices.v1`

One object per build, embedded in every page as `<script id="prices">` and published at
`/data/prices.json`. Every figure carries what it is:

```
schema            "mithqal.prices.v1"
generated_utc     when this BUILD ran. Never used as a quote time.
quoted_utc        the newest metal quote time, or null
timezone          "Asia/Riyadh"
source            {name, kind: "global spot market", is_sample, covers}
fx                {base: "USD", per_usd: {...}, sar_basis: "official peg 3.75", quoted_utc}
market            {closed_at_build: bool, last_close_utc, session_basis: "...assumed..., not
                   a validated calendar", session: {close_weekday, close_hour, open_weekday,
                   open_hour}}   <- the browser recomputes the state from `session`
metals.gold       {metal, unit: "gram", currency: "SAR", purity_basis: "24K, 999.9 fine",
                   quote_side: "mid", spot_usd_per_troy_ounce, spot_sar_per_gram,
                   quoted_utc, age_minutes, status,
                   two_way: {bid_sar_per_gram, ask_sar_per_gram, bid_means, ask_means, quoted_utc}}
metals.silver     same shape, purity_basis "999 fine", no two_way (not fetched: see below)
status            ok | stale | unknown_age | degraded
stale_after_minutes
```

**Per-metal timestamps.** `metals.dev` documents one `timestamps.metal` for the whole metals
block and a separate `timestamps.currency` for FX (read 2026-09-12), so on that feed gold and
silver share a time legitimately. `gold-api.com` stamps each metal separately. The snapshot
keeps them apart either way, so changing feeds cannot silently mislabel one metal with the
other's age.

**Quote side.** metals.dev states bid = buying price, ask = selling price, from the dealer's
side. Our copy always says it from the reader's side — bid is what the market pays you, ask
is what you pay — and the snapshot spells that out in `bid_means` / `ask_means`.

**Silver bid/ask is not fetched.** It is a request per build for a number no page shows; the
silver calculator values metal at spot. `config.json → bid_ask_metals` is the dial.

## The four metal states

| status | Means | What the page does |
|---|---|---|
| `ok` | Quoted within `stale_after_minutes` | Normal |
| `stale` | Quoted longer ago than that | Red banner, with the age in words |
| `unknown_age` | The feed gave no usable timestamp | Banner saying the age cannot be confirmed. **The build stops for a live provider** — there is no way to tell a live quote from a week-old one. |
| `future` | Timestamp ahead of now by more than 5 min | Build stops |

`market` is separate: a frozen weekend price is correct, and the page says so rather than
implying the number is moving. The build records `closed_at_build`, but what a reader is told
comes from the browser recomputing the session against their own clock — see below.

## What stops a build

All of these leave the pages already online untouched. Stale is recoverable; wrong is not.

- a price that is missing, non-finite, zero, negative, or outside `plausible_usd_oz`
- a live feed with no usable quote time, or a time in the future, or an unparseable one
- a quote older than the age limit (see the weekend rule below)
- bid above ask
- SAR off its peg by more than `sar_peg_tolerance`
- a move greater than `max_daily_move_pct` against the last recorded daily price
- an inconsistent page registry

## The weekend rule, bounded

The old rule skipped the age check entirely while the market was closed, so a feed that died
on Wednesday sailed through Saturday and Sunday unnoticed. Now the allowance is measured
**from the last weekly close**, so it grows exactly as fast as a correctly-frozen feed ages
and no faster: a Friday-close quote passes all weekend, a Wednesday quote fails immediately.

The session window (Friday 21:00 → Sunday 22:00 UTC) is an **assumption** about when our feed
stops printing, not a validated exchange calendar, and holidays are not modelled. A holiday
simply looks like a market that has not printed for a while, which the same rule handles.

## History: finished days only, and we do not call them closes

`data/history.json` holds the provider's figure for days that are **over**. Today is never
written to it. Today's live price is added to the in-memory series flagged `intraday`, and
the tables label that row "حتى الآن" / "so far today". Tomorrow the day's figure arrives from
the provider's timeseries.

This replaced a design where each build overwrote today's row with whatever intraday number
was current, which both mislabelled a snapshot as a settled day and threw away the previous
write.

**What the number is, the provider does not say.** metals.dev documents `/v1/timeseries` only
as "daily historical exchange rates between two dates" (read 2026-09-12): no statement of
close versus average versus snapshot, and no cut-off timezone. So a fetched row is stored as
`kind: "provider_daily"` and every page says "the recorded price for 10 Sep", never "the
10 Sep close".

Three provenance labels, and no fourth:

| `kind` | Means |
|---|---|
| `provider_daily` | the provider's published figure for a finished date |
| `intraday` | today's live price. Display only; never written to the file |
| `unknown` | already on disk before provenance was recorded, including rows an older build labelled `close`. Used, because it is the best we have; not certified, and the history table says so while any such row is still on screen |

**Everything the provider sends is checked before it can reach the file.** A row dated today
or later is rejected outright — the timeseries range could include today, and merging it
would file an unfinished day as a settled one and then collide with the intraday row the
display adds for that date. Rows with missing, non-numeric, non-finite or implausible prices
are rejected with a reason in the build log. Duplicates collapse to one row per date. The
request itself now ends **yesterday in the site's timezone**, not `date.today()` on the build
runner, which is a different day at 01:30 Riyadh.

- **Gaps are found by date, not by length.** The 2026-09-12 bug: the file held 09-10 and
  09-12 but not 09-11, so "vs yesterday" compared against the day before yesterday. Missing
  calendar dates in the window are now listed explicitly.
- **Retries are bounded, and the count survives.** At most one backfill request per build,
  and at most `MAX_ATTEMPTS_PER_DATE` (3) attempts per missing date, counted in
  `data/history_state.json`. The build reports `history_changed` when **either** file
  changed, so the scheduled job commits the counters even on a build where no price moved —
  otherwise the runner is thrown away with the count on it and the cap never advances across
  checkouts. A build that fails validation reports nothing and commits nothing: no artifact,
  no trail. The cost is that a date may be retried more often across a run of failing builds,
  which errs toward more requests, never fewer.
- **Nothing is invented.** A gap that cannot be filled stays a gap, and the comparison label
  names the date actually used, or says no comparison is available.
- **Corrections survive.** A provider row replaces a stored row for the same date.
- **The file changes only when something really changed**, which is what the CI commit step
  keys on — roughly one commit a day instead of ~96, and no deploy race per build.

## Comparison labels

| Situation | Arabic | English |
|---|---|---|
| Yesterday is in the history | مقارنة بسعر أمس المسجَّل (11 سبتمبر) | vs yesterday's recorded price (11 Sep) |
| Yesterday is missing | مقارنة بسعر 10 سبتمبر المسجَّل | vs the recorded price for 10 Sep |
| No earlier close at all | لا توجد مقارنة متاحة | no comparison available |
| Week: nothing within ±2 days of 7 | لا توجد مقارنة متاحة | no comparison available |

## In the browser

`static/calc.js` reads the embedded snapshot and nothing else. It never calls the price
provider — visitor traffic must not cost requests — and its one network call is to our own
static `/data/prices.json`.

- Staleness is re-checked every 60 seconds and whenever the tab becomes visible, not once at
  load. A page left open overnight now notices.
- **The market session is recomputed from the reader's clock**, not read from a flag baked in
  at build time. That flag is true when the page is made and at no other moment: a Saturday
  page still open on Monday would go on announcing a closed market, and during an outage no
  newer page arrives to correct it. The snapshot ships the session window (`market_session`)
  so the browser applies the same rule as the build, and `market_closed_at_build` is kept only
  as a record of what was true then.
- The decision has five outcomes, and `M.staleState()` returns them as data so every one is
  testable: `hidden`, `unknown` (no quote time), `closed` (market shut, quote as recent as
  that allows), `stale` (market open, quote old), `outage` (older than a closed market can
  explain — the source stopped publishing). A frozen Friday price reads as `closed` all
  weekend and as `stale` on Monday morning; a feed that died on Wednesday reads as `outage`
  even on Saturday.
- The closed message never calls the quote "the last close". It says when it was quoted.
- When the static snapshot is newer, the page offers a **reload** rather than repainting
  parts of itself. Half-updated numbers — a fresh calculator against an old table — are worse
  than slightly old ones. Inputs are restored from `sessionStorage` across the reload.
- Input parsing accepts Arabic-Indic digits and Arabic separators. An ambiguous separator
  (`1,2345`) is refused rather than guessed, because guessing is wrong by a factor of a
  thousand.

## The published snapshot

`/data/prices.json` is a **public, same-origin static file**. Anyone who can open the site can
open it. `dist/_headers` sets `X-Robots-Tag: noindex` on `/data/*`, which keeps it out of
search results and **is not access control** — nothing here restricts who may fetch it.

Its only real protection is that nothing secret is put in it: no credentials, no provider
URLs, no request counts, no build or account logs. A test asserts that.

It exists so the site's own pages read one shared object instead of drifting apart. We do not
advertise it as an API or offer it to third parties, and it says so in its own `usage` block —
but a usage note is documentation, not a control, and it does not stop anyone.

Whether third parties *may* consume it is unresolved. Metals.Dev's terms (read 2026-09-12)
permit publishing rates on websites with an active subscription and forbid reselling or
building a competing API. An embeddable widget sits between those two sentences, and that gap
is a question for the provider, in writing, before anything is offered — see `WIDGET-MVP.md`.
