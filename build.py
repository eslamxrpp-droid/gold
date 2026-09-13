"""Build the static site.

    python build.py --provider sample     # local preview, never publishable
    python build.py                       # uses config.json (live: metals_dev)

Order: fetch -> normalise into one versioned snapshot -> validate -> fill history gaps ->
render every page in the registry. If validation fails the build stops and the pages already
online stay exactly as they are: a stale page is recoverable, a wrong price is not.

Standard library only, no installs.
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
from datetime import date, datetime, timezone

from pathlib import Path

import history as H
import pages as REG
import prices as P
import providers
import snapshot as SNAP

HERE = Path(__file__).parent
DIST = HERE / "dist"
TEMPLATES = HERE / "templates"


# ---------- helpers ----------

def fmt(x, digits=2):
    return f"{x:,.{digits}f}"


def static_url(name):
    """Cache-bust /static/*. Cloudflare and browsers cache those URLs hard, so without a
    version a returning visitor keeps running the PREVIOUS deploy's CSS and JS. Found on
    2026-09-12: the live pages were still executing the old calc.js after a deploy, which
    silently disabled the stale-price notice - a safety feature must never be cached away."""
    digest = hashlib.md5((HERE / "static" / name).read_bytes()).hexdigest()[:8]
    return f"/static/{name}?v={digest}"


def render(template_name, ctx):
    text = (TEMPLATES / template_name).read_text(encoding="utf-8")

    def sub(m):
        key = m.group(1)
        if key not in ctx:
            raise KeyError(f"{template_name}: missing value for {{{{{key}}}}}")
        return str(ctx[key])

    return re.sub(r"{{\s*(\w+)\s*}}", sub, text)


def arrow(d):
    return {"up": "▲", "down": "▼", "flat": "●"}[d]


def signed(x, digits=2):
    return ("+" if x > 0 else "") + fmt(x, digits)


def ltr(text):
    """Keep numbers like +0.40% or 2026-09-11 readable inside Arabic (right-to-left) text."""
    return f"<bdi dir='ltr'>{text}</bdi>"


AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def human_date(iso, lang):
    d = date.fromisoformat(iso)
    if lang == "ar":
        return f"{d.day} {AR_MONTHS[d.month - 1]}"
    return d.strftime("%-d %b") if os.name != "nt" else d.strftime("%d %b")


# ---------- comparison labels ----------
# The old build took days[-2] as "yesterday" and days[-8] as "last week" and labelled them
# that way whatever the dates actually were. The history had no 2026-09-11 row, so on the
# 12th the page compared against the 10th under the word "أمس". A comparison is only as
# honest as its label, so the label is built from the row that was really used.

# The provider publishes one figure per finished date and documents nothing about what it is
# (metals.dev docs, read 2026-09-12: "daily historical exchange rates between two dates" - no
# close, no average, no cut-off timezone). So the wording is "the recorded price for 10 Sep",
# never "the 10 Sep close": we can stand behind the first and not the second.

def comparison_label(row, lang, kind="day"):
    if row is None:
        return "لا توجد مقارنة متاحة" if lang == "ar" else "no comparison available"
    when = human_date(row["date"], lang)
    if kind == "day":
        if row.get("is_yesterday"):
            return (f"مقارنة بسعر أمس المسجَّل ({when})" if lang == "ar"
                    else f"vs yesterday's recorded price ({when})")
        return (f"مقارنة بسعر {when} المسجَّل" if lang == "ar"
                else f"vs the recorded price for {when}")
    days = row.get("days_ago", 7)
    return (f"مقارنة بسعر {when} المسجَّل (قبل {days} أيام)" if lang == "ar"
            else f"vs the recorded price for {when} ({days} days earlier)")


# ---------- tables ----------

def scroll_wrap(table_html, lang, wide=False):
    """Wrap a table in its own horizontal scroller. `wide` adds a visible hint on narrow
    screens: an edge shadow alone is too easy to miss, and a reader who cannot see that a
    column exists will conclude it does not."""
    hint = ""
    if wide:
        hint = ("<p class='scrollhint'>اسحب الجدول أفقيًا لعرض بقية الأعمدة</p>" if lang == "ar"
                else "<p class='scrollhint'>Scroll the table sideways for the rest of the columns</p>")
    return f"<div class='scroll'>{table_html}</div>{hint}"


def _window(c):
    """The rows a history table shows, plus the row before them so the first visible row can
    still have a change figure. `history_days` rows are shown - the wording and the window
    are the same number, which they were not before (8 rows under a 7-day heading)."""
    n = c["history_days"]
    return c["days"][-(n + 1):], n


def history_table(c, lang, metal="gold", karat=21):
    rows, n = _window(c)
    out = []
    for i, d in enumerate(rows):
        if metal == "gold":
            price = P.gold_karat_price(d["gold_sar_g"], karat)
            basis = d["gold_sar_g"]
            prev_basis = rows[i - 1]["gold_sar_g"] if i else None
        else:
            price = P.silver_purity_price(d["silver_sar_g"], 999)
            basis = d["silver_sar_g"]
            prev_basis = rows[i - 1]["silver_sar_g"] if i else None
        ch = "—"
        if prev_basis:
            pc = P.pct_change(basis, prev_basis)
            dr = P.direction(pc)
            ch = f"<span class='{dr}'>{arrow(dr)} {ltr(signed(pc) + '%')}</span>"
        live = d.get("kind") == "intraday"
        tag = (" <span class='tag'>حتى الآن</span>" if lang == "ar" else " <span class='tag'>so far today</span>") if live else ""
        out.append((i, f"<tr{' class=live' if live else ''}><td>{ltr(d['date'])}{tag}</td>"
                       f"<td>{fmt(price)}</td><td>{ch}</td></tr>"))
    visible = [r for i, r in out][-n:]
    unknown = any(d.get("kind") == H.UNKNOWN for d in rows[-n:])
    if lang == "ar":
        head = (f"<th>التاريخ</th><th>{'عيار ' + str(karat) if metal == 'gold' else 'عيار 999'} (ريال/جرام)</th>"
                "<th>التغير عن اليوم المسجل السابق</th>")
    else:
        head = (f"<th>Date</th><th>{str(karat) + 'K' if metal == 'gold' else '999'} (SAR/g)</th>"
                "<th>Change vs previous recorded day</th>")
    note = ""
    if unknown:
        # Rows written before this project recorded where a figure came from. They are used,
        # because they are the best we have, but they are not certified as anything.
        note = ("<p class='note'>بعض الأيام الأقدم سُجّلت قبل أن نبدأ بتوثيق مصدر كل رقم، فهي معروضة كما هي.</p>"
                if lang == "ar" else
                "<p class='note'>Some older rows were stored before this site recorded where each figure came "
                "from; they are shown as they are.</p>")
    return scroll_wrap(f"<table class='prices'><thead><tr>{head}</tr></thead><tbody>"
                       + "".join(reversed(visible)) + "</tbody></table>", lang) + note


def karat_table_ar(c, config):
    rows = "".join(
        f"<tr><th>عيار {k}</th><td>{fmt(P.gold_karat_price(c['gold'], k))}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold'], k) * P.GRAMS_PER_TROY_OUNCE)}</td></tr>"
        for k in config["gold_karats"])
    return scroll_wrap("<table class='prices'><thead><tr><th>العيار</th><th>سعر الجرام (ريال)</th>"
                       f"<th>سعر الأونصة (ريال)</th></tr></thead><tbody>{rows}</tbody></table>", "ar")


def change_table(c, config, lang):
    rows = []
    for k in config["gold_karats"]:
        now = P.gold_karat_price(c["gold"], k)
        y = P.gold_karat_price(c["prev"]["gold_sar_g"], k) if c["prev"] else None
        w = P.gold_karat_price(c["week"]["gold_sar_g"], k) if c["week"] else None
        label = f"عيار {k}" if lang == "ar" else f"{k}K"
        rows.append(f"<tr><th>{label}</th><td>{fmt(now)}</td>"
                    f"<td>{ltr(signed(now - y)) if y else '—'}</td>"
                    f"<td>{ltr(signed(now - w)) if w else '—'}</td></tr>")
    head = ("<th>العيار</th><th>اليوم (ريال/جرام)</th><th>مقابل آخر يوم مسجَّل</th><th>مقابل الأسبوع الماضي</th>"
            if lang == "ar" else
            "<th>Karat</th><th>Today (SAR/g)</th><th>vs last recorded day</th><th>vs last week</th>")
    return scroll_wrap(f"<table class='prices'><thead><tr>{head}</tr></thead><tbody>"
                       + "".join(rows) + "</tbody></table>", lang, wide=True)


def bid_ask_table(c, config, lang):
    rows = "".join(
        f"<tr><th>{'عيار ' + str(k) if lang == 'ar' else str(k) + 'K'}</th>"
        f"<td>{fmt(P.gold_karat_price(c['gold_bid'], k))}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold_ask'], k))}</td></tr>" for k in config["gold_karats"])
    head = ("<th>العيار</th><th>سعر البيع: ما يدفعه السوق لك (ريال/جرام)</th>"
            "<th>سعر الشراء: ما تدفعه أنت (ريال/جرام)</th>" if lang == "ar" else
            "<th>Karat</th><th>Bid: what the market pays you (SAR/g)</th>"
            "<th>Ask: what you pay (SAR/g)</th>")
    return scroll_wrap(f"<table class='prices'><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>", lang)


def silver_table(c, config, lang):
    """One table covering gram, ounce, kilo and a 1 kg bar - the kilo/bar intent lives here
    rather than on a page of its own."""
    rows = "".join(
        f"<tr><th>{'عيار ' + str(p) if lang == 'ar' else str(p)}</th>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p))}</td>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p) * 10)}</td>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p) * 100)}</td>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p) * P.GRAMS_PER_TROY_OUNCE)}</td>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p) * 1000, 0)}</td></tr>"
        for p in config["silver_purities"])
    head = ("<th>العيار</th><th>جرام</th><th>10 جرام</th><th>100 جرام</th><th>أونصة</th><th>كيلو / سبيكة</th>"
            if lang == "ar" else
            "<th>Purity</th><th>1 g</th><th>10 g</th><th>100 g</th><th>1 oz</th><th>1 kg / bar</th>")
    unit = " (ريال)" if lang == "ar" else " (SAR)"
    cap = f"الأسعار{unit}" if lang == "ar" else f"Prices{unit}"
    return scroll_wrap(f"<table class='prices'><caption>{cap}</caption>"
                       f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>", lang, wide=True)


def en_gram_table(c, config):
    rows = "".join(
        f"<tr><th>{k}K</th><td>{fmt(P.gold_karat_price(c['gold'], k))}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold'], k) * 10)}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold'], k) * P.GRAMS_PER_TOLA)}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold'], k) * P.GRAMS_PER_TROY_OUNCE)}</td></tr>" for k in config["gold_karats"])
    return scroll_wrap("<table class='prices'><thead><tr><th>Karat</th><th>1 gram (SAR)</th>"
                       "<th>10 grams (SAR)</th><th>1 tola (SAR)</th><th>1 ounce (SAR)</th></tr></thead>"
                       f"<tbody>{rows}</tbody></table>", "en", wide=True)


def en_fx_section(c):
    """INR and PKR are CONVERTED SAR spot values, not Indian or Pakistani retail rates. The
    heading and the sentence both say so, because this is the single easiest number on the
    site to mistake for a local shop price."""
    inr, pkr = c["fx"].get("INR"), c["fx"].get("PKR")
    if not (inr and pkr):
        return ""
    sar = c["sar"]
    rows = []
    for k in (24, 22):
        g = P.gold_karat_price(c["gold"], k)
        rows.append(f"<tr><th>{k}K</th><td>₹{fmt(g / sar * inr * 10, 0)}</td>"
                    f"<td>Rs {fmt(g / sar * pkr * P.GRAMS_PER_TOLA, 0)}</td></tr>")
    return ("<section><h2>The same Saudi rate converted into rupees</h2>"
            f"<p>This is the <strong>Saudi</strong> gold value converted at 1 SAR = ₹{fmt(inr / sar)} "
            f"and Rs {fmt(pkr / sar)}, from the same data update. It is <strong>not</strong> the gold "
            "rate in India or Pakistan: those markets have their own duties, premiums and local rates.</p>"
            + scroll_wrap("<table class='prices'><thead><tr><th>Karat</th><th>10 grams, converted to INR</th>"
                          f"<th>1 tola, converted to PKR</th></tr></thead><tbody>{''.join(rows)}</tbody></table>", "en")
            + "</section>")


def karat_options(config, lang="ar", selected=21):
    label = (lambda k: f"عيار {k}") if lang == "ar" else (lambda k: f"{k}K")
    return "".join(f"<option value='{k}'{' selected' if k == selected else ''}>{label(k)}</option>"
                   for k in config["gold_karats"])


def purity_options(config, lang="ar"):
    label = (lambda p: f"عيار {p}") if lang == "ar" else (lambda p: f"{p}")
    return "".join(f"<option value='{p}'>{label(p)}</option>" for p in config["silver_purities"])


def sources_list(rules, ids=None):
    ids = ids or list(rules["sources"].keys())
    return "<ol class='sources'>" + "".join(
        f"<li><a href='{html.escape(rules['sources'][i]['url'])}' rel='nofollow noopener' target='_blank'>"
        f"{html.escape(rules['sources'][i]['title'])}</a></li>" for i in ids) + "</ol>"


def src_refs(rules, ids):
    order = list(rules["sources"].keys())
    return " ".join(f"<sup>[{order.index(i) + 1}]</sup>" for i in ids)


# ---------- page shell ----------

def nav_html(lang, current):
    cur = ' aria-current="page"'
    links = "".join(f"<a href='{u}'{cur if u == current else ''}>{t}</a>" for u, t in REG.nav_items(lang))
    return links


def breadcrumb_ld(page, base):
    if not page.breadcrumb:
        return None
    items = [{"@type": "ListItem", "position": i + 1, "name": name, "item": base + path}
             for i, (name, path) in enumerate(page.breadcrumb)]
    items.append({"@type": "ListItem", "position": len(items) + 1, "name": page.nav or page.title,
                  "item": base + page.path})
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}


def site_ld(config, base):
    """Organization and WebSite only, and only on the front page.

    Checked against Google's structured-data gallery on 2026-09-12: FAQPage is no longer
    listed at all, and the sitelinks searchbox was retired, so neither is marked up here.
    Nothing in this block claims a rating, a price, an offer, a review or a founding date -
    we do not have any of those to claim.
    """
    return [
        {"@context": "https://schema.org", "@type": "Organization",
         "name": "Mithqal", "alternateName": "مثقال", "url": base + "/",
         "logo": base + "/static/icon-192.png"},
        {"@context": "https://schema.org", "@type": "WebSite",
         "name": config["site_name_ar"], "url": base + "/", "inLanguage": ["ar-SA", "en-SA"]},
    ]


def page(config, c, reg_page, ctx_extra, built):
    p = reg_page
    base = config["base_url"].rstrip("/")
    out = DIST / p.path.strip("/") / "index.html" if p.path != "/" else DIST / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    title = p.title.format(**c["common"])
    description = p.description.format(**c["common"])
    alt = "".join(f"<link rel='alternate' hreflang='{hl}' href='{base}{u}'>" for hl, u in REG.alternates(p))
    switch_path, switch_label = REG.language_switch(p)

    ld = site_ld(config, base) if p.key == "home" else []
    bc = breadcrumb_ld(p, base)
    if bc:
        ld.append(bc)
    ld_html = "".join(f"<script type='application/ld+json'>{json.dumps(o, ensure_ascii=False)}</script>" for o in ld)

    sample_banner = ""
    if c["is_sample"]:
        sample_banner = ("<div class='sample'>نسخة تجريبية: الأسعار بيانات تجريبية وليست أسعارًا حقيقية</div>"
                         if p.lang == "ar" else "<div class='sample'>Prototype: SAMPLE data, not real prices</div>")

    body = render(p.template, dict(c["common"], **ctx_extra))
    full = render("base.html", {
        "lang": p.lang, "dir": "rtl" if p.lang == "ar" else "ltr",
        "title": html.escape(title), "description": html.escape(description),
        "canonical": base + p.path, "alternates": alt,
        "robots": "noindex, nofollow" if (config["noindex"] or p.noindex or c["is_sample"]) else "index, follow",
        "site_name": config["site_name_ar"] if p.lang == "ar" else config["site_name_en"],
        "home": "/" if p.lang == "ar" else "/sa/en/",
        "nav": nav_html(p.lang, p.path),
        "lang_switch": f"<a class='lang' href='{switch_path}' hreflang='{'en' if p.lang == 'ar' else 'ar'}'>{switch_label}</a>",
        "sample_banner": sample_banner, "content": body,
        "css_url": static_url("style.css"), "js_url": static_url("calc.js"),
        "og_title": html.escape(title), "og_description": html.escape(description),
        "og_url": base + p.path, "og_image": base + "/static/og.png",
        "og_locale": "ar_SA" if p.lang == "ar" else "en_US",
        "structured_data": ld_html,
        "page_key": p.key, "page_locale": p.hreflang,
        "snapshot_url": config.get("public_snapshot_path", "/data/prices.json"),
        "footer": render("footer_ar.html" if p.lang == "ar" else "footer_en.html", c["footer_ctx"][p.lang]),
        "year": c["snapshot"]["generated_local"][:4],
    })
    out.write_text(full, encoding="utf-8")
    built.append(p)
    return p.path


# ---------- compute ----------

def compute(config, snap, series, today):
    sar = snap["fx"]["per_usd"]["SAR"]
    g = snap["metals"]["gold"]["spot_sar_per_gram"]
    s = snap["metals"]["silver"]["spot_sar_per_gram"]
    two = snap["metals"]["gold"].get("two_way")
    g_bid = two["bid_sar_per_gram"] if two else g
    g_ask = two["ask_sar_per_gram"] if two else g

    days = [dict(h, gold_sar_g=P.per_gram(h["gold_usd_oz"], sar),
                 silver_sar_g=P.per_gram(h["silver_usd_oz"], sar)) for h in series]
    closes = [d for d in days if d.get("kind") != H.INTRADAY]
    prev = H.previous_daily(closes, today)
    week = H.week_daily(closes, today, target_days=config.get("history_days", 7))
    if prev:
        prev = dict(prev, gold_sar_g=P.per_gram(prev["gold_usd_oz"], sar),
                    silver_sar_g=P.per_gram(prev["silver_usd_oz"], sar))
    if week:
        week = dict(week, gold_sar_g=P.per_gram(week["gold_usd_oz"], sar),
                    silver_sar_g=P.per_gram(week["silver_usd_oz"], sar))

    return {
        "sar": sar, "gold": g, "silver": s, "gold_bid": g_bid, "gold_ask": g_ask,
        "has_bid_ask": bool(two), "days": days, "prev": prev, "week": week,
        "history_days": config.get("history_days", 7),
        "chg_day": P.pct_change(g, prev["gold_sar_g"]) if prev else None,
        "chg_week": P.pct_change(g, week["gold_sar_g"]) if week else None,
        "s_chg_day": P.pct_change(s, prev["silver_sar_g"]) if prev else None,
        "fx": snap["fx"]["per_usd"], "is_sample": snap["source"]["is_sample"],
        "source": snap["source"]["name"], "snapshot": snap,
    }


def freshness_line(snap, lang):
    """One sentence saying exactly what time the reader is looking at. When the feed gave no
    usable time we say that, rather than showing the build clock and calling it an update."""
    q, b = snap["quoted_local"], snap["generated_local"]
    if lang == "ar":
        if not q:
            return f"وقت التسعيرة غير متاح من المصدر · صُفحت الصفحة {ltr(b)} بتوقيت الرياض"
        line = f"سعر السوق بتاريخ {ltr(q)} بتوقيت الرياض"
        if snap["market"]["closed"]:
            line += " · السوق العالمية مغلقة في هذا الوقت"
        return line
    if not q:
        return f"Quote time not provided by the source · page built {b} Riyadh time"
    line = f"Market price as of {q} Riyadh time"
    if snap["market"]["closed"]:
        line += " · the global market is closed at this time"
    return line


# ---------- build ----------

def build(config):
    latest = providers.fetch(config)
    now = datetime.now(timezone.utc)
    snap = SNAP.build_snapshot(config, latest, now)
    today = H.today_in(config["timezone_offset_hours"])

    problems = REG.check_registry()
    if problems:
        print("BUILD STOPPED, the page registry is inconsistent:\n - " + "\n - ".join(problems))
        sys.exit(1)

    note = ""
    state_changed = False
    if latest.get("daily_closes"):          # the sample provider carries its own history
        rows = [dict(r, kind=H.PROVIDER_DAILY) for r in latest["daily_closes"]]
        rows = H.drop_unfinalised(rows, today)
        rows_changed = False
    else:
        rows = H.drop_unfinalised(H.load(), today)
        rows, changed, note, state_changed = H.refresh(
            config, rows, today, backfill=lambda days: providers.backfill(config, days))
        rows_changed = False
        if note:
            print(f"history: {note}")

    series = H.series(rows, today, latest["gold_usd_oz"], latest["silver_usd_oz"])
    problems = SNAP.validate(config, snap, latest, series, now)
    if problems:
        # The attempt counters that refresh() just wrote stay on disk but are NOT reported as
        # a change, so the scheduled job does not commit them: a build that produced no
        # publishable artifact must not leave a trail behind. The cost is that a date can be
        # retried more than MAX_ATTEMPTS_PER_DATE times across a run of failing builds - the
        # safe direction to err, since the cap exists to stop wasted requests, not to ration
        # a feed that is already broken.
        print("BUILD STOPPED, old pages kept:\n - " + "\n - ".join(problems))
        sys.exit(1)

    if not latest.get("daily_closes"):
        rows_changed = H.save(rows)
    # BOTH files decide the commit. Reporting only on history.json meant a build whose
    # backfill failed updated the retry counter on the runner and then threw the runner away,
    # so the three-attempt cap never advanced across fresh checkouts.
    history_changed = bool(rows_changed or state_changed)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"history_changed={'true' if history_changed else 'false'}\n")
            f.write(f"history_rows_changed={'true' if rows_changed else 'false'}\n")
            f.write(f"retry_state_changed={'true' if state_changed else 'false'}\n")

    c = compute(config, snap, series, today)
    rules = json.loads((HERE / "data" / "zakat_rules.json").read_text(encoding="utf-8"))

    # Everything the browser needs, in one object, so the tables, the change figures and the
    # calculators can never be reading three different snapshots.
    page_data = {
        "schema": SNAP.SCHEMA,
        "gold_sar_g": round(c["gold"], 4), "gold_bid_sar_g": round(c["gold_bid"], 4),
        "gold_ask_sar_g": round(c["gold_ask"], 4), "silver_sar_g": round(c["silver"], 4),
        "karats": config["gold_karats"], "purities": config["silver_purities"],
        "updated": snap["quoted_local"] or snap["generated_local"],
        "quoted_utc": snap["quoted_utc"], "generated_utc": snap["generated_utc"],
        "updated_utc": snap["quoted_utc"] or snap["generated_utc"],
        "quote_time_known": bool(snap["quoted_utc"]),
        # The browser recomputes the market state from `market_session` and its own clock.
        # `market_closed_at_build` is kept only so a page can say what was true when it was
        # made; it is never used to tell a reader what is true now.
        "market_closed_at_build": snap["market"]["closed"],
        "market_session": snap["market"]["session"],
        "status": snap["status"],
        "stale_after_minutes": snap["stale_after_minutes"],
        "snapshot_url": config.get("public_snapshot_path", "/data/prices.json"),
        "zakat_rate": rules["rate"]["value"],
        "gold_nisab": [n["grams"] for n in rules["gold_nisab_grams_pure"]],
        "silver_nisab": [n["grams"] for n in rules["silver_nisab_grams_pure"]],
    }

    hp = c["prev"] is not None
    d = P.direction(c["chg_day"]) if hp else "flat"
    sd = P.direction(c["s_chg_day"]) if hp else "flat"
    word_ar = {"up": "مرتفع", "down": "نازل", "flat": "مستقر"}[d] if hp else "لا توجد مقارنة بعد"
    word_en = {"up": "up", "down": "down", "flat": "flat"}[d] if hp else "no comparison yet"
    chg_ar = (lambda x: ltr(signed(x) + "%") if x is not None else "—")
    chg_en = (lambda x: signed(x) + "%" if x is not None else "—")

    common = {
        "updated": snap["quoted_local"] or snap["generated_local"],
        "prices_json": json.dumps(page_data, ensure_ascii=False),
        "karat_options": karat_options(config), "karat_options_en": karat_options(config, "en"),
        "purity_options": purity_options(config), "purity_options_en": purity_options(config, "en"),
        "g21": fmt(P.gold_karat_price(c["gold"], 21)), "g24": fmt(c["gold"]),
        "g22": fmt(P.gold_karat_price(c["gold"], 22)), "g18": fmt(P.gold_karat_price(c["gold"], 18)),
        "s999": fmt(P.silver_purity_price(c["silver"], 999)),
        "s925": fmt(P.silver_purity_price(c["silver"], 925)),
        "s999_kg": fmt(P.silver_purity_price(c["silver"], 999) * 1000, 0),
        "fresh_ar": freshness_line(snap, "ar"), "fresh_en": freshness_line(snap, "en"),
        "source": html.escape(c["source"]),
    }
    c["common"] = common
    c["footer_ctx"] = {
        "ar": {"fresh": common["fresh_ar"], "source": common["source"], "nav": footer_index("ar")},
        "en": {"fresh": common["fresh_en"], "source": common["source"], "nav": footer_index("en")},
    }

    cmp_ar, cmp_en = comparison_label(c["prev"], "ar"), comparison_label(c["prev"], "en")
    wk_ar, wk_en = comparison_label(c["week"], "ar", "week"), comparison_label(c["week"], "en", "week")
    built = []

    gold_ctx = dict(karat_table=karat_table_ar(c, config), history_table=history_table(c, "ar"),
                    direction_class=d, direction_arrow=arrow(d), direction_word=word_ar,
                    chg_day=chg_ar(c["chg_day"]), cmp_label=cmp_ar)

    page(config, c, REG.by_key("home"), dict(gold_ctx, silver_table=silver_table(c, config, "ar"),
         s_direction_class=sd, s_direction_arrow=arrow(sd), s_chg_day=chg_ar(c["s_chg_day"])), built)

    page(config, c, REG.by_key("sa_gold"), gold_ctx, built)

    page(config, c, REG.by_key("sa_updown"), dict(
        direction_class=d, direction_arrow=arrow(d), direction_word=word_ar,
        chg_day=chg_ar(c["chg_day"]), cmp_label=cmp_ar, week_label=wk_ar,
        chg_week=chg_ar(c["chg_week"]), week_class=P.direction(c["chg_week"]) if c["week"] else "flat",
        change_table=change_table(c, config, "ar"), history_table=history_table(c, "ar")), built)

    bid_note = "" if c["has_bid_ask"] else \
        "<p class='note'>مصدر البيانات الحالي لا يوفر سعري البيع والشراء، فالجدول يعرض السعر الفوري نفسه في العمودين.</p>"
    page(config, c, REG.by_key("sa_sell"), dict(bid_ask_table=bid_ask_table(c, config, "ar"), bid_note=bid_note), built)

    page(config, c, REG.by_key("sa_calculator"), {}, built)

    j = {o["id"]: o for o in rules["jewelry_opinions"]}
    gn = rules["gold_nisab_grams_pure"]
    page(config, c, REG.by_key("sa_zakat"), dict(
        nisab_gold_85=fmt(gn[0]["grams"] * c["gold"]), nisab_gold_92=fmt(gn[1]["grams"] * c["gold"]),
        nisab_gold_85_label=gn[0]["label_ar"], nisab_gold_92_label=gn[1]["label_ar"],
        nisab_gold_85_refs=src_refs(rules, gn[0]["sources"]), nisab_gold_92_refs=src_refs(rules, gn[1]["sources"]),
        nisab_silver=fmt(rules["silver_nisab_grams_pure"][0]["grams"] * c["silver"]),
        nisab_silver_label=rules["silver_nisab_grams_pure"][0]["label_ar"],
        nisab_silver_refs=src_refs(rules, rules["silver_nisab_grams_pure"][0]["sources"]),
        silver_note=rules["silver_nisab_note_ar"], silver_note_refs=src_refs(rules, ["ibnbaz_18047"]),
        rate_text=rules["rate"]["text_ar"], rate_refs=src_refs(rules, rules["rate"]["sources"]),
        rate_refs2=src_refs(rules, rules["rate"]["sources"]),
        karat_rule=rules["karat_rule_ar"], karat_rule_refs=src_refs(rules, rules["karat_rule_sources"]),
        opinion_must=j["must_pay"]["text_ar"], opinion_must_refs=src_refs(rules, j["must_pay"]["sources"]),
        opinion_exempt=j["exempt"]["text_ar"], opinion_exempt_refs=src_refs(rules, j["exempt"]["sources"]),
        sources=sources_list(rules), reviewed=rules["reviewed"]), built)

    page(config, c, REG.by_key("sa_silver"), dict(
        silver_table=silver_table(c, config, "ar"), silver_history_table=history_table(c, "ar", "silver"),
        s_direction_class=sd, s_direction_arrow=arrow(sd), s_chg_day=chg_ar(c["s_chg_day"]), cmp_label=cmp_ar,
        nisab_silver=fmt(rules["silver_nisab_grams_pure"][0]["grams"] * c["silver"])), built)

    page(config, c, REG.by_key("sa_method"), dict(methodology_table=methodology_table(config, "ar")), built)

    # ---- English
    page(config, c, REG.by_key("en_gold"), dict(
        gram_table=en_gram_table(c, config), fx_section=en_fx_section(c),
        history_table=history_table(c, "en"), chg_day=chg_en(c["chg_day"]), cmp_label=cmp_en), built)

    page(config, c, REG.by_key("en_silver"), dict(
        silver_table=silver_table(c, config, "en"), silver_history_table=history_table(c, "en", "silver"),
        s_direction_class=sd, s_direction_arrow=arrow(sd), s_chg_day=chg_en(c["s_chg_day"]), cmp_label=cmp_en,
        nisab_silver=fmt(rules["silver_nisab_grams_pure"][0]["grams"] * c["silver"])), built)

    page(config, c, REG.by_key("en_calculator"), {}, built)

    page(config, c, REG.by_key("en_sell"), dict(
        bid_ask_table=bid_ask_table(c, config, "en"),
        bid_note="" if c["has_bid_ask"] else
        "<p class='note'>The current data source does not publish bid and ask, so both columns show the same spot price.</p>"), built)

    page(config, c, REG.by_key("en_updown"), dict(
        direction_class=d, direction_arrow=arrow(d), direction_word=word_en,
        chg_day=chg_en(c["chg_day"]), cmp_label=cmp_en, week_label=wk_en,
        chg_week=chg_en(c["chg_week"]), week_class=P.direction(c["chg_week"]) if c["week"] else "flat",
        change_table=change_table(c, config, "en"), history_table=history_table(c, "en")), built)

    page(config, c, REG.by_key("en_method"), dict(methodology_table=methodology_table(config, "en")), built)

    write_assets(config, c, snap, built)
    print(f"Built {len(built)} pages from {c['source']} (quote {snap['quoted_local'] or 'time unknown'} Riyadh, "
          f"status {snap['status']}). Gold 24K {fmt(c['gold'])} SAR/g, 21K {common['g21']}, "
          f"silver 999 {common['s999']} SAR/g, day change {chg_en(c['chg_day'])}. "
          f"History {'changed' if history_changed else 'unchanged'}.")


def footer_index(lang):
    """Every page, in the footer of every page. This is what lets the header nav stay short
    on a phone without orphaning the pages that are not in it."""
    items = [p for p in REG.PAGES if p.lang == lang]
    label = {"home": "الرئيسية", "sa_gold": "سعر الذهب", "sa_silver": "سعر الفضة",
             "sa_calculator": "حاسبة الذهب", "sa_zakat": "زكاة الذهب والفضة",
             "sa_sell": "سعر البيع والشراء", "sa_updown": "مرتفع ولا نازل", "sa_method": "المنهجية",
             "en_gold": "Gold rate", "en_silver": "Silver price", "en_calculator": "Gold calculator",
             "en_sell": "Buy and sell price", "en_updown": "Up or down", "en_method": "Methodology"}
    return "<nav class='index'>" + "".join(
        f"<a href='{p.path}'>{label.get(p.key, p.key)}</a>" for p in items) + "</nav>"


def methodology_table(config, lang):
    rows = [
        ("المصدر" if lang == "ar" else "Source",
         "سعر السوق العالمية الفوري من مزوّد بيانات تجاري، ويُذكر اسمه في تذييل كل صفحة."
         if lang == "ar" else "Global spot market price from a commercial data provider, named in the footer of every page."),
        ("العملة" if lang == "ar" else "Currency",
         f"التحويل إلى الريال عند الربط الرسمي {config['sar_per_usd_peg']} ريال للدولار."
         if lang == "ar" else f"Converted to riyals at the official peg of {config['sar_per_usd_peg']} SAR per USD."),
        ("الوحدة" if lang == "ar" else "Unit",
         "الأونصة الترويّة = 31.1035 جرامًا. سعر الجرام = سعر الأونصة ÷ 31.1035."
         if lang == "ar" else "One troy ounce = 31.1035 g. Price per gram = price per ounce ÷ 31.1035."),
        ("النقاء" if lang == "ar" else "Purity",
         "الذهب: سعر العيار = سعر عيار 24 × العيار ÷ 24. الفضة: السعر × النقاء ÷ 1000."
         if lang == "ar" else "Gold: karat price = 24K price × karat ÷ 24. Silver: price × fineness ÷ 1000."),
        ("التحديث" if lang == "ar" else "Refresh",
         "تُبنى الصفحات كل 15 دقيقة تقريبًا. يُعرض وقت التسعيرة نفسه، لا وقت بناء الصفحة."
         if lang == "ar" else "Pages rebuild about every 15 minutes. The time shown is the quote time, not the build time."),
        ("متى نتوقف" if lang == "ar" else "When we stop",
         f"إذا كان السعر خارج النطاق المعقول، أو قفز أكثر من {config['max_daily_move_pct']}%، أو لا يحمل وقتًا، "
         f"أو تجاوز عمره الحد، يتوقف النشر وتبقى الصفحة السابقة كما هي."
         if lang == "ar" else
         f"If a price is outside its plausible range, jumps more than {config['max_daily_move_pct']}%, carries no "
         "quote time, or is older than the age limit, publishing stops and the previous page stays up."),
        ("ما لا ننشره" if lang == "ar" else "What we never publish",
         "أسعار المحلات أو التجار، توقعات الأسعار، تفسير حركة السوق، تقييمات أو شهادات."
         if lang == "ar" else "Shop or dealer prices, price forecasts, explanations of market moves, ratings or testimonials."),
    ]
    head = "<th>البند</th><th>التفصيل</th>" if lang == "ar" else "<th>Item</th><th>Detail</th>"
    body = "".join(f"<tr><th>{a}</th><td>{b}</td></tr>" for a, b in rows)
    return scroll_wrap(f"<table class='prices method'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>", lang)


def write_assets(config, c, snap, built):
    (DIST / "static").mkdir(parents=True, exist_ok=True)
    for f in (HERE / "static").iterdir():
        (DIST / "static" / f.name).write_bytes(f.read_bytes())

    # The public snapshot: the same object the pages embed, so a refresh can never disagree
    # with what is rendered. Excluded from the sitemap and marked noindex in _headers.
    (DIST / "data").mkdir(parents=True, exist_ok=True)
    (DIST / "data" / "prices.json").write_text(
        json.dumps(SNAP.public(snap), indent=1, ensure_ascii=False), encoding="utf-8")

    base = config["base_url"].rstrip("/")
    # lastmod is the date of the DATA the page shows, or the date its wording last changed.
    # It is never "now just because a build ran": a sitemap that claims every page changed
    # every fifteen minutes is telling a crawler something untrue about all of them.
    data_date = (snap["quoted_utc"] or snap["generated_utc"])[:10]
    urls = []
    for p in REG.sitemap_pages(config):
        lastmod = data_date if p.price_driven else p.content_updated
        urls.append(f"<url><loc>{base}{p.path}</loc><lastmod>{lastmod}</lastmod></url>")
    (DIST / "sitemap.xml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
        + "".join(urls) + "</urlset>\n", encoding="utf-8")

    if config["noindex"] or c["is_sample"]:
        robots = "User-agent: *\nDisallow: /\n"
    else:
        robots = f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"
    (DIST / "robots.txt").write_text(robots, encoding="utf-8")

    # Cloudflare Pages reads this file. The snapshot is for our own pages; it is not an API
    # and must not be indexed as a document.
    (DIST / "_headers").write_text(
        "/data/*\n  X-Robots-Tag: noindex\n  Cache-Control: public, max-age=60\n"
        "/static/*\n  Cache-Control: public, max-age=31536000, immutable\n", encoding="utf-8")

    # A sample build can never be mistaken for a release artifact.
    marker = DIST / "SAMPLE-BUILD-DO-NOT-PUBLISH.txt"
    if c["is_sample"]:
        marker.write_text("This dist/ was built from sample data. Do not deploy it.\n", encoding="utf-8")
    elif marker.exists():
        marker.unlink()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider")
    ap.add_argument("--out", help="write the site somewhere other than dist/ (used by tests)")
    args = ap.parse_args()
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    if args.provider:
        cfg["provider"] = args.provider
    if args.out:
        DIST = Path(args.out)
    build(cfg)
