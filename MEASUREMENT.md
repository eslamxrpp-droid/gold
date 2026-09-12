# Measurement

Two different questions, measured separately, and neither of them has a target attached.

| Question | Where it is answered | Status |
|---|---|---|
| Did anyone find us? impressions, clicks, CTR, position, per page and per query | Google Search Console | **Not verified.** No record in this project either way — Eslam to confirm. See below. |
| Did the people who arrived use the thing? calculator use, unit and purity choices, quote comparisons, language switches | The event contract below | **Adapter written, switched off.** |

Search performance and tool usage never get mixed into one number. A page can rank well and
be useless, or be useful and invisible, and only separate measurements tell you which.

## Search Console — not verified

As of 2026-09-12 this project holds **no record either way**. The checkpoint lists it as a
launch-day task and nothing records it being done — which is evidence about our notes, not
about the account. It stays *not verified* until Eslam looks: a planned integration described
as a working one is how a 90-day experiment ends with no data, and an absence asserted from
silence is the same error pointed the other way.

**Eslam: open Search Console and tell the next session which of these is true.** If the
property is already there and the sitemap is submitted, this section becomes one line. If not,
the steps are: 

1. Search Console → Add property → **Domain** (not URL prefix): `mithqalprice.com`.
   A domain property covers www/non-www and http/https in one, and DNS is already at
   Cloudflare, so verification is a TXT record.
2. Submit `https://mithqalprice.com/sitemap.xml`.
3. URL Inspection → Request indexing, for the 14 URLs in `PAGE-MAP.md`.
4. Tell the next session it is done, so the checkpoint can stop calling it planned.

What to export once data exists (this is the H7 test in `hypotheses.md`): per-query position
for `/sa/` and for `/sa/zakat/`, over the same window. A flat band across a page's whole query
family points to Google treating the intent as one cluster; a persistent gradient points to
keyword-level difficulty. That decides how every future page is chosen.

## The event contract

`static/analytics.js` is committed **disabled** (`config.json → analytics.provider: "none"`).
Turning it on is a deliberate act: a measurement tool is a third party reading our visitors.

Events say **which tool was used** and **which fixed-list choice was made**. They never carry
a weight, an amount, a quote, a computed value, or anything a reader typed. The adapter drops
any event not on this list, and any value longer than 32 characters.

| Event | Fields | Fires when |
|---|---|---|
| `calculator_used` | page, locale, metal, tool (`value` \| `sell`) | a weight input is committed |
| `karat_selected` | page, locale, metal, karat | the karat select changes |
| `purity_selected` | page, locale, metal, purity | the silver purity select changes |
| `unit_selected` | page, locale, metal, unit (`g` \| `kg`) | the unit select changes |
| `preset_used` | page, locale, metal, preset (`1g`, `10g`, `100g`, `1kg`) | a preset chip is clicked |
| `quote_compared` | page, locale, metal, basis (`total` \| `per_gram`) | a seller's quote is compared |
| `language_switch` | page, locale | the language chip is clicked |
| `refresh_clicked` | page, locale | the newer-price banner is used |

`page` is the registry key (`sa_silver`, `en_gold`, …) and `locale` is the hreflang
(`ar-SA`, `en-SA`), so everything groups by page, language and metal without a separate
dimension table.

Two tests hold this down: `test_events_carry_no_amounts` fails if a `track()` call so much as
mentions an input the reader types into, and `test_analytics_is_off_until_configured` fails
if a tracker script appears in a page while the config says analytics is off.

## Turning it on later

1. Decide the provider. Plausible (no cookies, no personal data, paid) and GA4 (free, cookies,
   a consent question in the EU; most of our readers are in Saudi Arabia) are both wired.
2. Set `analytics.provider` and its `domain` / `measurement_id` in `config.json`.
3. Add the provider's own script tag to `templates/base.html`, then `static/analytics.js`
   after it, and pass the config through as `window.MITHQAL_ANALYTICS`.
4. Say so on `/sa/methodology/`. A site whose principle is clarity says who is watching.

`provider: "console"` logs events to the browser console and sends nothing anywhere — useful
for checking the contract locally.

## What is not measured

No revenue targets, no conversion goals, no A/B tests against readers, and no attempt to
identify anyone. There is no monetisation on the site, so there is nothing to optimise
towards, and inventing a number to chase is how the principle gets quietly traded away.
