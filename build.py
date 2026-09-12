"""Build the static site.

    python build.py            # uses the provider set in config.json (default: sample data)
    python build.py --provider gold_api

Steps: fetch prices -> safety checks -> update daily history -> render pages into dist/.
If a safety check fails, the build stops and the old pages stay online (never publish a wrong price).
Standard library only: no installs needed.
"""
import argparse
import html
import os
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import prices as P
import providers

HERE = Path(__file__).parent
DIST = HERE / "dist"
TEMPLATES = HERE / "templates"


# ---------- helpers ----------

def fmt(x, digits=2):
    return f"{x:,.{digits}f}"


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


# ---------- data ----------

HISTORY_FILE = HERE / "data" / "history.json"


def load_history(config, latest):
    """Daily closes (Riyadh date), oldest first, including today's latest price. Does not save:
    the caller saves only after the safety checks pass. Sample provider brings its own history."""
    if latest.get("daily_closes"):
        return latest["daily_closes"]
    hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else []
    tz0 = timezone(timedelta(hours=config["timezone_offset_hours"]))
    stale_days = bool(hist) and hist[-1]["date"] != datetime.now(tz0).date().isoformat()
    if len(hist) < 8 or stale_days:  # first live runs, and once a day: get real daily closes from the provider
        try:
            fetched = {h["date"]: h for h in providers.backfill(config)}
            hist = [fetched.pop(h["date"], h) for h in hist] + list(fetched.values())
        except Exception as e:  # backfill is optional; pages still build with fewer days
            print(f"note: history backfill failed ({e})")
    tz = timezone(timedelta(hours=config["timezone_offset_hours"]))
    today = datetime.now(tz).date().isoformat()
    hist = [h for h in hist if h["date"] != today]
    hist.append({"date": today, "gold_usd_oz": latest["gold_usd_oz"], "silver_usd_oz": latest["silver_usd_oz"]})
    return sorted(hist, key=lambda h: h["date"])[-400:]


def save_history(latest, history):
    """Save history and report whether a new day appeared (the scheduled job commits the file only then)."""
    if latest.get("daily_closes"):
        return False
    before = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else []
    new_day = not before or before[-1]["date"] != history[-1]["date"]
    HISTORY_FILE.write_text(json.dumps(history, indent=1), encoding="utf-8")
    gh_out = os.environ.get("GITHUB_OUTPUT")  # lets the scheduled job commit the file once a day
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"new_day={'true' if new_day else 'false'}\n")
    return new_day


def safety_checks(config, latest, history):
    problems = []
    if not (500 < latest["gold_usd_oz"] < 20000):
        problems.append(f"gold price out of range: {latest['gold_usd_oz']}")
    if not (5 < latest["silver_usd_oz"] < 500):
        problems.append(f"silver price out of range: {latest['silver_usd_oz']}")
    if len(history) >= 2:
        prev = history[-2]
        for m in ("gold", "silver"):
            ch = abs(P.pct_change(latest[f"{m}_usd_oz"], prev[f"{m}_usd_oz"]))
            if ch > 10:
                problems.append(f"{m} moved {ch:.1f}% vs previous close: check the feed")
    now = datetime.now(timezone.utc)
    if not latest["is_sample"] and latest.get("timestamp_utc") and not P.market_closed(now):
        ts = datetime.fromisoformat(latest["timestamp_utc"].replace("Z", "+00:00"))
        age = (now - ts).total_seconds() / 60
        if age > config["max_price_age_minutes"]:
            problems.append(f"price is {age:.0f} minutes old")
    sar = latest["fx_per_usd"].get("SAR")
    if not sar or abs(sar - config["sar_per_usd_peg"]) > 0.05:
        problems.append(f"SAR rate looks wrong: {sar}")
    return problems


def compute(config, latest, history):
    sar = latest["fx_per_usd"]["SAR"]
    g = P.per_gram(latest["gold_usd_oz"], sar)
    s = P.per_gram(latest["silver_usd_oz"], sar)
    has_bid_ask = bool(latest.get("gold_bid_usd_oz") and latest.get("gold_ask_usd_oz"))
    g_bid = P.per_gram(latest["gold_bid_usd_oz"], sar) if has_bid_ask else g
    g_ask = P.per_gram(latest["gold_ask_usd_oz"], sar) if has_bid_ask else g

    days = [dict(h, gold_sar_g=P.per_gram(h["gold_usd_oz"], sar), silver_sar_g=P.per_gram(h["silver_usd_oz"], sar))
            for h in history]
    has_prev = len(days) >= 2
    prev = days[-2] if has_prev else days[-1]
    week = days[-8] if len(days) >= 8 else days[0]

    tz = timezone(timedelta(hours=config["timezone_offset_hours"]))
    raw_ts = latest.get("timestamp_utc") or datetime.now(timezone.utc).isoformat()
    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).astimezone(tz)
    return {
        "sar": sar, "gold": g, "silver": s, "gold_bid": g_bid, "gold_ask": g_ask, "has_bid_ask": has_bid_ask,
        "days": days, "prev": prev, "week": week, "has_prev": has_prev,
        "chg_day": P.pct_change(g, prev["gold_sar_g"]), "chg_week": P.pct_change(g, week["gold_sar_g"]),
        "s_chg_day": P.pct_change(s, prev["silver_sar_g"]),
        "updated": ts.strftime("%Y-%m-%d %H:%M"),  # wrapped with ltr() where shown in Arabic
        "fx": latest["fx_per_usd"], "is_sample": latest["is_sample"], "source": latest["source_name"],
    }


# ---------- HTML snippets ----------

def karat_table_ar(c, config):
    rows = "".join(
        f"<tr><th>عيار {k}</th><td>{fmt(P.gold_karat_price(c['gold'], k))}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold'], k) * P.GRAMS_PER_TROY_OUNCE)}</td></tr>"
        for k in config["gold_karats"])
    return ("<table class='prices'><thead><tr><th>العيار</th><th>سعر الجرام (ريال)</th><th>سعر الأونصة (ريال)</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")


def history_table_ar(c, karat=21):
    rows = []
    days = c["days"][-8:]
    for i, d in enumerate(days):
        price = P.gold_karat_price(d["gold_sar_g"], karat)
        if i == 0:
            ch = ""
        else:
            pc = P.pct_change(d["gold_sar_g"], days[i - 1]["gold_sar_g"])
            dr = P.direction(pc)
            ch = f"<span class='{dr}'>{arrow(dr)} {ltr(signed(pc) + '%')}</span>"
        rows.append(f"<tr><td>{ltr(d['date'])}</td><td>{fmt(price)}</td><td>{ch}</td></tr>")
    return ("<table class='prices'><thead><tr><th>التاريخ</th>"
            f"<th>عيار {karat} (ريال/جرام)</th><th>التغير اليومي</th></tr></thead><tbody>"
            + "".join(reversed(rows)) + "</tbody></table>")


def change_table_ar(c, config):
    rows = []
    for k in config["gold_karats"]:
        now = P.gold_karat_price(c["gold"], k)
        y = P.gold_karat_price(c["prev"]["gold_sar_g"], k)
        w = P.gold_karat_price(c["week"]["gold_sar_g"], k)
        rows.append(f"<tr><th>عيار {k}</th><td>{fmt(now)}</td><td>{ltr(signed(now - y))}</td><td>{ltr(signed(now - w))}</td></tr>")
    return ("<table class='prices'><thead><tr><th>العيار</th><th>اليوم (ريال/جرام)</th><th>مقارنة بأمس</th>"
            "<th>مقارنة بالأسبوع الماضي</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def bid_ask_table_ar(c, config):
    rows = "".join(
        f"<tr><th>عيار {k}</th><td>{fmt(P.gold_karat_price(c['gold_bid'], k))}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold_ask'], k))}</td></tr>" for k in config["gold_karats"])
    return ("<table class='prices'><thead><tr><th>العيار</th><th>سعر البيع (ريال/جرام)</th><th>سعر الشراء (ريال/جرام)</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")


def silver_table_ar(c, config):
    rows = "".join(
        f"<tr><th>عيار {p}</th><td>{fmt(P.silver_purity_price(c['silver'], p))}</td>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p) * P.GRAMS_PER_TROY_OUNCE)}</td>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p) * 1000, 0)}</td></tr>" for p in config["silver_purities"])
    return ("<table class='prices'><thead><tr><th>عيار الفضة</th><th>سعر الجرام (ريال)</th><th>سعر الأونصة (ريال)</th>"
            f"<th>سعر الكيلو (ريال)</th></tr></thead><tbody>{rows}</tbody></table>")


def silver_history_table_ar(c):
    rows = []
    days = c["days"][-8:]
    for i, d in enumerate(days):
        ch = ""
        if i:
            pc = P.pct_change(d["silver_sar_g"], days[i - 1]["silver_sar_g"])
            dr = P.direction(pc)
            ch = f"<span class='{dr}'>{arrow(dr)} {ltr(signed(pc) + '%')}</span>"
        rows.append(f"<tr><td>{ltr(d['date'])}</td><td>{fmt(P.silver_purity_price(d['silver_sar_g'], 999))}</td><td>{ch}</td></tr>")
    return ("<table class='prices'><thead><tr><th>التاريخ</th><th>عيار 999 (ريال/جرام)</th><th>التغير اليومي</th></tr></thead><tbody>"
            + "".join(reversed(rows)) + "</tbody></table>")


def karat_options(config, selected=21):
    return "".join(f"<option value='{k}'{' selected' if k == selected else ''}>عيار {k}</option>" for k in config["gold_karats"])


def sources_list(rules, ids=None):
    ids = ids or list(rules["sources"].keys())
    return "<ol class='sources'>" + "".join(
        f"<li><a href='{html.escape(rules['sources'][i]['url'])}' rel='nofollow noopener' target='_blank'>"
        f"{html.escape(rules['sources'][i]['title'])}</a></li>" for i in ids) + "</ol>"


def src_refs(rules, ids):
    order = list(rules["sources"].keys())
    return " ".join(f"<sup>[{order.index(i) + 1}]</sup>" for i in ids)


def en_gram_table(c, config):
    rows = "".join(
        f"<tr><th>{k}K</th><td>{fmt(P.gold_karat_price(c['gold'], k))}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold'], k) * 10)}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold'], k) * P.GRAMS_PER_TOLA)}</td>"
        f"<td>{fmt(P.gold_karat_price(c['gold'], k) * P.GRAMS_PER_TROY_OUNCE)}</td></tr>" for k in config["gold_karats"])
    return ("<table class='prices'><thead><tr><th>Karat</th><th>1 gram (SAR)</th><th>10 grams (SAR)</th>"
            f"<th>1 tola (SAR)</th><th>1 ounce (SAR)</th></tr></thead><tbody>{rows}</tbody></table>")


def en_fx_section(c):
    inr, pkr = c["fx"].get("INR"), c["fx"].get("PKR")
    if not (inr and pkr):
        return ""
    sar = c["sar"]
    rows = []
    for k in (24, 22):
        g = P.gold_karat_price(c["gold"], k)
        rows.append(f"<tr><th>{k}K</th><td>₹{fmt(g / sar * inr * 10, 0)}</td><td>Rs {fmt(g / sar * pkr * P.GRAMS_PER_TOLA, 0)}</td></tr>")
    return ("<section><h2>Saudi gold rate in Indian and Pakistani rupees</h2>"
            f"<p>Converted at 1 SAR = ₹{fmt(inr / sar)} and Rs {fmt(pkr / sar)} (rates from the same data update). "
            "Indian buyers usually compare 10 grams, Pakistani buyers compare 1 tola.</p>"
            "<table class='prices'><thead><tr><th>Karat</th><th>10 grams in INR</th><th>1 tola in PKR</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>")


def en_silver_table(c, config):
    rows = "".join(
        f"<tr><th>{p}</th><td>{fmt(P.silver_purity_price(c['silver'], p))}</td>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p) * 10)}</td>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p) * 100)}</td>"
        f"<td>{fmt(P.silver_purity_price(c['silver'], p) * 1000, 0)}</td></tr>" for p in config["silver_purities"])
    return ("<table class='prices'><thead><tr><th>Purity</th><th>1 gram (SAR)</th><th>10 grams (SAR)</th>"
            f"<th>100 grams (SAR)</th><th>1 kg (SAR)</th></tr></thead><tbody>{rows}</tbody></table>")


def en_history_table(c):
    days = c["days"][-8:]
    rows = [f"<tr><td>{d['date']}</td><td>{fmt(P.gold_karat_price(d['gold_sar_g'], 24))}</td>"
            f"<td>{fmt(P.gold_karat_price(d['gold_sar_g'], 22))}</td></tr>" for d in days]
    return ("<table class='prices'><thead><tr><th>Date</th><th>24K (SAR/g)</th><th>22K (SAR/g)</th></tr></thead><tbody>"
            + "".join(reversed(rows)) + "</tbody></table>")


# ---------- pages ----------

NAV_AR = [("/sa/", "سعر الذهب اليوم"), ("/sa/up-or-down/", "مرتفع ولا نازل"), ("/sa/sell-price/", "سعر البيع"),
          ("/sa/calculator/", "حاسبة الذهب"), ("/sa/zakat/", "زكاة الذهب والفضة"), ("/sa/silver/", "سعر الفضة"),
          ("/sa/en/", "English")]
NAV_EN = [("/sa/en/", "Gold rate"), ("/sa/en/#silver", "Silver"), ("/sa/", "العربية")]


def nav(items, current):
    cur = ' aria-current="page"'
    return "".join(f"<a href='{u}'{cur if u == current else ''}>{t}</a>" for u, t in items)


def page(config, c, path, lang, title, description, body_template, ctx, alternates=None):
    out = DIST / path.strip("/") / "index.html" if path != "/" else DIST / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    base = config["base_url"].rstrip("/")
    alt = "".join(f"<link rel='alternate' hreflang='{hl}' href='{base}{u}'>" for hl, u in (alternates or []))
    sample_banner = ""
    if c["is_sample"]:
        sample_banner = ("<div class='sample'>نسخة تجريبية: الأسعار بيانات تجريبية وليست أسعارًا حقيقية</div>" if lang == "ar"
                         else "<div class='sample'>Prototype: SAMPLE data, not real prices</div>")
    body = render(body_template, ctx)
    full = render("base.html", {
        "lang": lang, "dir": "rtl" if lang == "ar" else "ltr", "title": html.escape(title),
        "description": html.escape(description), "canonical": base + path, "alternates": alt,
        "robots": "noindex, nofollow" if config["noindex"] else "index, follow",
        "site_name": config["site_name_ar"] if lang == "ar" else config["site_name_en"],
        "home": "/" if lang == "ar" else "/sa/en/",
        "nav": nav(NAV_AR if lang == "ar" else NAV_EN, path), "sample_banner": sample_banner, "content": body,
        "footer": render("footer_ar.html" if lang == "ar" else "footer_en.html",
                         {"updated": c["updated"], "source": html.escape(c["source"])}),
        "year": c["updated"][:4],
    })
    out.write_text(full, encoding="utf-8")
    return path


def build(config):
    latest = providers.fetch(config)
    history = load_history(config, latest)
    problems = safety_checks(config, latest, history)
    if problems:
        print("BUILD STOPPED, old pages kept:\n - " + "\n - ".join(problems))
        sys.exit(1)
    save_history(latest, history)
    c = compute(config, latest, history)
    rules = json.loads((HERE / "data" / "zakat_rules.json").read_text(encoding="utf-8"))

    prices_json = json.dumps({
        "gold_sar_g": round(c["gold"], 4), "gold_bid_sar_g": round(c["gold_bid"], 4),
        "silver_sar_g": round(c["silver"], 4), "karats": config["gold_karats"], "updated": c["updated"],
        "zakat_rate": rules["rate"]["value"],
        "gold_nisab": [n["grams"] for n in rules["gold_nisab_grams_pure"]],
        "silver_nisab": [n["grams"] for n in rules["silver_nisab_grams_pure"]],
    })
    hp = c["has_prev"]
    d = P.direction(c["chg_day"]) if hp else "flat"
    word_ar = {"up": "مرتفع", "down": "نازل", "flat": "مستقر"}[d] if hp else "لا توجد مقارنة بعد"
    chg_ar = (lambda x: ltr(signed(x) + "%") if hp else "—")
    chg_en = (lambda x: signed(x) + "%" if hp else "—")
    sd = P.direction(c["s_chg_day"]) if hp else "flat"
    common = {"updated": c["updated"], "prices_json": prices_json, "karat_options": karat_options(config),
              "g21": fmt(P.gold_karat_price(c["gold"], 21)), "g24": fmt(c["gold"]),
              "g22": fmt(P.gold_karat_price(c["gold"], 22)), "g18": fmt(P.gold_karat_price(c["gold"], 18)),
              "s999": fmt(P.silver_purity_price(c["silver"], 999))}
    alt_sa = [("ar-SA", "/sa/"), ("en-SA", "/sa/en/")]
    built = []

    built.append(page(config, c, "/sa/", "ar", f"سعر الذهب اليوم في السعودية: عيار 21 = {common['g21']} ريال",
        "سعر جرام الذهب اليوم في السعودية لعيار 24 و22 و21 و18 بالريال السعودي، مع تغير الأسعار آخر 7 أيام.",
        "sa_index.html", dict(common, karat_table=karat_table_ar(c, config), history_table=history_table_ar(c),
        direction_class=d, direction_arrow=arrow(d), direction_word=word_ar, chg_day=chg_ar(c["chg_day"])), alt_sa))

    built.append(page(config, c, "/sa/up-or-down/", "ar", "أسعار الذهب اليوم مرتفع ولا نازل؟",
        "هل الذهب مرتفع ولا نازل اليوم في السعودية؟ التغير مقارنة بأمس والأسبوع الماضي لكل عيار.",
        "sa_up_or_down.html", dict(common, direction_class=d, direction_arrow=arrow(d),
        direction_word=word_ar, chg_day=chg_ar(c["chg_day"]), chg_week=chg_ar(c["chg_week"]), week_class=P.direction(c["chg_week"]), change_table=change_table_ar(c, config),
        history_table=history_table_ar(c), prev_date=ltr(c["prev"]["date"]), week_date=ltr(c["week"]["date"]))))

    bid_note = "" if c["has_bid_ask"] else "<p class='note'>مصدر البيانات الحالي لا يوفر سعري البيع والشراء، فالجدول يعرض السعر الفوري نفسه في العمودين.</p>"
    built.append(page(config, c, "/sa/sell-price/", "ar", "سعر بيع الذهب اليوم في السعودية وحاسبة بيع الذهب المستعمل",
        "سعر بيع وشراء الذهب اليوم في السعودية لكل عيار، وحاسبة تقدّر قيمة ذهبك المستعمل قبل البيع.",
        "sa_sell_price.html", dict(common, bid_ask_table=bid_ask_table_ar(c, config), bid_note=bid_note)))

    built.append(page(config, c, "/sa/calculator/", "ar", "حاسبة الذهب: احسب سعر الذهب اليوم بالوزن والعيار",
        "حاسبة سعر الذهب اليوم في السعودية: أدخل الوزن بالجرام واختر العيار لتعرف القيمة بالريال.",
        "sa_calculator.html", dict(common)))

    j = {o["id"]: o for o in rules["jewelry_opinions"]}
    gn = rules["gold_nisab_grams_pure"]
    built.append(page(config, c, "/sa/zakat/", "ar", "حاسبة زكاة الذهب والفضة ونصاب الذهب اليوم بالريال",
        "احسب زكاة الذهب والفضة: نصاب الذهب والفضة اليوم بالريال السعودي، مع ذكر أقوال العلماء ومصادرها.",
        "sa_zakat.html", dict(common,
        nisab_gold_85=fmt(gn[0]["grams"] * c["gold"]), nisab_gold_92=fmt(gn[1]["grams"] * c["gold"]),
        nisab_gold_85_label=gn[0]["label_ar"], nisab_gold_92_label=gn[1]["label_ar"],
        nisab_gold_85_refs=src_refs(rules, gn[0]["sources"]), nisab_gold_92_refs=src_refs(rules, gn[1]["sources"]),
        nisab_silver=fmt(rules["silver_nisab_grams_pure"][0]["grams"] * c["silver"]),
        nisab_silver_label=rules["silver_nisab_grams_pure"][0]["label_ar"],
        nisab_silver_refs=src_refs(rules, rules["silver_nisab_grams_pure"][0]["sources"]),
        silver_note=rules["silver_nisab_note_ar"], silver_note_refs=src_refs(rules, ["ibnbaz_18047"]),
        rate_text=rules["rate"]["text_ar"], rate_refs=src_refs(rules, rules["rate"]["sources"]),
        karat_rule=rules["karat_rule_ar"], karat_rule_refs=src_refs(rules, rules["karat_rule_sources"]),
        opinion_must=j["must_pay"]["text_ar"], opinion_must_refs=src_refs(rules, j["must_pay"]["sources"]),
        opinion_exempt=j["exempt"]["text_ar"], opinion_exempt_refs=src_refs(rules, j["exempt"]["sources"]),
        sources=sources_list(rules), reviewed=rules["reviewed"])))

    built.append(page(config, c, "/sa/en/", "en", f"Gold Rate in Saudi Arabia Today: 22K = SAR {common['g22']}/g",
        "Today's gold rate in Saudi Arabia per gram for 24K, 22K, 21K and 18K, 1 tola and 10 grams, in SAR, INR and PKR, plus the silver price.",
        "sa_en.html", dict(common, gram_table=en_gram_table(c, config), fx_section=en_fx_section(c),
        silver_table=en_silver_table(c, config), history_table=en_history_table(c),
        s_chg_day=chg_en(c["s_chg_day"]), chg_day=chg_en(c["chg_day"])), alt_sa))

    built.append(page(config, c, "/sa/silver/", "ar", f"سعر الفضة اليوم في السعودية: الجرام عيار 999 = {common['s999']} ريال",
        "سعر جرام الفضة اليوم في السعودية لعيار 999 و925، وسعر أونصة وكيلو الفضة بالريال، مع تغير السعر آخر 7 أيام.",
        "sa_silver.html", dict(common, silver_table=silver_table_ar(c, config), silver_history_table=silver_history_table_ar(c),
        s925=fmt(P.silver_purity_price(c["silver"], 925)), s_direction_class=sd, s_direction_arrow=arrow(sd),
        s_chg_day=chg_ar(c["s_chg_day"]),
        nisab_silver=fmt(rules["silver_nisab_grams_pure"][0]["grams"] * c["silver"]))))

    built.insert(0, page(config, c, "/", "ar",
        f"أسعار الذهب والفضة اليوم في السعودية: جرام الذهب عيار 21 = {common['g21']} ريال",
        "أسعار الذهب والفضة اليوم في السعودية بالريال: سعر جرام الذهب لكل عيار، سعر الفضة، حاسبة الذهب وحاسبة الزكاة. تحديث تلقائي.",
        "home.html", dict(common, karat_table=karat_table_ar(c, config), silver_table=silver_table_ar(c, config),
        direction_class=d, direction_arrow=arrow(d), direction_word=word_ar, chg_day=chg_ar(c["chg_day"]),
        s_direction_class=sd, s_direction_arrow=arrow(sd), s_chg_day=chg_ar(c["s_chg_day"]))))

    (DIST / "static").mkdir(parents=True, exist_ok=True)
    for f in (HERE / "static").iterdir():
        (DIST / "static" / f.name).write_bytes(f.read_bytes())
    base = config["base_url"].rstrip("/")
    (DIST / "sitemap.xml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
        + "".join(f"<url><loc>{base}{p}</loc></url>" for p in built) + "</urlset>\n", encoding="utf-8")
    (DIST / "robots.txt").write_text(
        ("User-agent: *\nDisallow: /\n" if config["noindex"] else f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"),
        encoding="utf-8")
    print(f"Built {len(built)} pages from {c['source']} (updated {c['updated']} Riyadh). Gold 24K {fmt(c['gold'])} SAR/g, "
          f"21K {common['g21']}, silver 999 {common['s999']} SAR/g, day change {chg_en(c['chg_day'])}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider")
    args = ap.parse_args()
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    if args.provider:
        cfg["provider"] = args.provider
    build(cfg)
