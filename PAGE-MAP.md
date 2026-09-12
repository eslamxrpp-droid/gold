# Page map

Generated from `pages.py`, which is the single source of truth. Navigation, canonicals,
hreflang, breadcrumbs and `sitemap.xml` all come from that one list, so they cannot drift
apart. Adding a page means adding one entry there.

**Definitive as of 2026-09-12. Built locally; not yet published.**

## Live pages (14)

| Path | Lang | Role — what this page is for | Translation of | In header nav |
|---|---|---|---|---|
| `/` | ar | Brand entry: what Mithqal is, today's gold and silver headline, the way in to every tool | — (no English home; the English gold page is the English entry) | no (brand link) |
| `/sa/` | ar | The detailed Saudi gold page: every karat, recorded daily closes, how the price is derived | `/sa/en/` | الذهب |
| `/sa/silver/` | ar | Silver: 999 and 925 per gram, ounce, kilo and bar, plus the value calculator | `/sa/en/silver/` | الفضة |
| `/sa/calculator/` | ar | Gold value calculator: weight and karat to riyals, optional making charge | `/sa/en/calculator/` | الحاسبة |
| `/sa/zakat/` | ar | Zakat: today's nisab in riyals, the calculator, the cited scholarly positions | **none — see "Pending"** | الزكاة |
| `/sa/sell-price/` | ar | Bid and ask per karat; what a used-gold seller gets before the shop's deduction | `/sa/en/sell-price/` | no |
| `/sa/up-or-down/` | ar | Direction only: up or down against the last recorded close, and against the week | `/sa/en/up-or-down/` | no |
| `/sa/methodology/` | ar | Methodology: source, conversion, freshness rules, what we never publish, corrections | `/sa/en/methodology/` | المنهجية |
| `/sa/en/` | en | (same role as `/sa/`) | `/sa/` | Gold |
| `/sa/en/silver/` | en | (same role as `/sa/silver/`) | `/sa/silver/` | Silver |
| `/sa/en/calculator/` | en | (same role as `/sa/calculator/`) | `/sa/calculator/` | Calculator |
| `/sa/en/sell-price/` | en | (same role as `/sa/sell-price/`) | `/sa/sell-price/` | no |
| `/sa/en/up-or-down/` | en | (same role as `/sa/up-or-down/`) | `/sa/up-or-down/` | no |
| `/sa/en/methodology/` | en | (same role as `/sa/methodology/`) | `/sa/methodology/` | Methodology |

New in this iteration: `/sa/silver/`'s calculator, and the six pages under `/sa/en/` beyond
the existing gold page. **No existing URL changed.** The eight already-indexed paths are
unchanged, and a test (`Registry.test_established_urls_are_preserved`) fails if one moves.

## Pending, deliberately not built

| Path | Why not | What would unblock it |
|---|---|---|
| `/sa/en/zakat/` | The Arabic page quotes named scholars' positions on zakat, sourced to islamqa, islamweb and Ibn Baz. Rendering those positions in English is an act of translation on religious rulings; a mistranslated ruling is worse than no English page, and nobody here can verify the English wording against the Arabic sources. | A competent Arabic-English reviewer confirms that each cited position still says what its source says. Until then the English pages link to the Arabic page and say why, and the registry carries the page in `PENDING` so the gap stays visible. |

The English pages do link to `/sa/zakat/` with `hreflang="ar"`, and `/sa/en/silver/` shows
today's silver nisab in riyals — a number, not a ruling.

## Rules this map follows

- **No page exists twice.** Two pages in the same language may not share a role;
  `pages.check_registry()` fails the build if they do. That is the guard against building a
  page per spelling variant, per gram weight or per word order — `سعر الفضه` with a ه, "1
  gram silver price", "silver rate in ksa" are all the same page, addressed in its copy.
- **A separate Arabic kilo page was considered and not built.** The kilo and bar intent is
  covered by the silver table (gram / 10 g / 100 g / ounce / kilo-bar) and by the `1 kg`
  preset in the calculator. A page of its own would repeat the same number under a different
  heading.
- **hreflang is published only between equivalent pages**, and always reciprocally. A page
  whose counterpart does not exist publishes none — the zakat page is the live example.
- **The language switch never dead-ends**: where there is no counterpart it goes to the other
  language's entry page, and a test asserts the target exists.
- **The header nav carries at most five links per language.** The previous seven-link Arabic
  nav wrapped to three rows on a phone and took 23% of the screen before the reader saw a
  price. The other pages are reachable from the cards and from the footer index, which lists
  every page in that language on every page.

## Sitemap

`sitemap.xml` is generated from the registry: every live, indexable page and nothing else.
`/data/prices.json` is excluded and carries `X-Robots-Tag: noindex` via `dist/_headers`.

`lastmod` is the date of the data for price-driven pages, and the hand-maintained
`content_updated` date for the methodology pages. It is never "now because a build ran" —
a sitemap claiming all fourteen pages changed every fifteen minutes teaches a crawler to
ignore the field.
