"""The price snapshot: one versioned object that every surface reads.

Why this exists. The build used to hand each template a loose bag of numbers and stamp a
single "last updated" on the page, taken from the provider if it had one and from the build
clock if it did not. That is the one thing a price site must never do - a build clock always
looks fresh, so a feed with no timestamp was displayed as a live quote.

So: one object, one version, every number labelled with what it is (metal, currency, unit,
purity basis, quote side), where it came from, when it was quoted, and whether we currently
believe it. `generated_utc` is the BUILD time and is never used as a quote time. A metal with
no usable timestamp gets `status: "unknown_age"` and the page says the quote time is
unavailable instead of implying it is now.

The same object is written to /data/prices.json for the site's own JavaScript to re-read, so
the tables, the deltas and the calculators can never be showing three different snapshots.
"""
from datetime import datetime, timedelta, timezone

import prices as P

SCHEMA = "mithqal.prices.v1"

# Metals.Dev: "bid = buying price, ask = selling price" (metals.dev/docs, read 2026-09-12),
# stated from the dealer's side. In our copy we always describe it from the READER's side,
# because that is the question they came with:
#   bid -> what the market pays you when you sell   (Arabic: سعر البيع)
#   ask -> what you pay when you buy                (Arabic: سعر الشراء)
QUOTE_SIDES = {"mid": "spot mid", "bid": "market buys from you", "ask": "market sells to you"}


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _metal_status(quoted, now, config):
    """ok / stale / unknown_age / future. Deliberately four states, not two: 'we do not know
    how old this is' and 'we know it is old' are different things to tell a reader."""
    if quoted is None:
        return "unknown_age", None
    age = (now - quoted).total_seconds() / 60
    if age < -5:
        return "future", age
    if age >= config.get("stale_after_minutes", 120):
        return "stale", age
    return "ok", age


def build_snapshot(config, latest, now_utc=None):
    now = now_utc or datetime.now(timezone.utc)
    stamps = latest.get("timestamps_utc") or {}
    fallback = latest.get("timestamp_utc")
    sar = (latest.get("fx_per_usd") or {}).get("SAR")
    tz = timezone(timedelta(hours=config["timezone_offset_hours"]))

    metals = {}
    for m in ("gold", "silver"):
        usd_oz = latest.get(f"{m}_usd_oz")
        quoted = _parse(stamps.get(m) or fallback)
        status, age = _metal_status(quoted, now, config)
        entry = {
            "metal": m,
            "unit": "gram",
            "currency": "SAR",
            "purity_basis": "24K, 999.9 fine" if m == "gold" else "999 fine",
            "quote_side": "mid",
            "spot_usd_per_troy_ounce": usd_oz,
            "spot_sar_per_gram": P.per_gram(usd_oz, sar) if (usd_oz and sar) else None,
            "quoted_utc": quoted.strftime("%Y-%m-%dT%H:%M:%SZ") if quoted else None,
            "age_minutes": round(age) if age is not None else None,
            "status": status,
        }
        bid, ask = latest.get(f"{m}_bid_usd_oz"), latest.get(f"{m}_ask_usd_oz")
        if bid and ask and sar:
            ba_q = _parse(stamps.get(f"{m}_bid_ask") or stamps.get(m) or fallback)
            entry["two_way"] = {
                "bid_sar_per_gram": P.per_gram(bid, sar),
                "ask_sar_per_gram": P.per_gram(ask, sar),
                "bid_means": QUOTE_SIDES["bid"],
                "ask_means": QUOTE_SIDES["ask"],
                "quoted_utc": ba_q.strftime("%Y-%m-%dT%H:%M:%SZ") if ba_q else None,
            }
        metals[m] = entry

    fx_quoted = _parse(stamps.get("fx"))
    snap = {
        "schema": SCHEMA,
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),   # BUILD time. Never a quote time.
        "timezone": config["timezone_name"],
        "source": {
            "name": latest.get("source_name"),
            "kind": "global spot market",
            "is_sample": bool(latest.get("is_sample")),
            "covers": "global spot metal value. NOT jewellery shop, dealer or city retail prices.",
        },
        "fx": {
            "base": "USD",
            "per_usd": latest.get("fx_per_usd") or {},
            "sar_basis": f"official peg {config['sar_per_usd_peg']} SAR per USD",
            "quoted_utc": fx_quoted.strftime("%Y-%m-%dT%H:%M:%SZ") if fx_quoted else None,
        },
        "market": {
            # `closed` is TRUE AT BUILD TIME and nowhere else. A page built on Saturday and
            # still open on Monday would go on announcing a closed market from this flag, and
            # during an outage no newer page arrives to correct it. The browser recomputes the
            # state from `session` against the reader's clock; this value is the build's own
            # record, useful for reading a published snapshot after the fact.
            "closed": P.market_closed(now),
            "closed_at_build": P.market_closed(now),
            "last_close_utc": P.last_session_close(now).strftime("%Y-%m-%dT%H:%M:%SZ"),
            # Weekdays follow Python's convention: Monday = 0 ... Sunday = 6.
            "session": {
                "tz": "UTC",
                "close_weekday": P.WEEK_CLOSE_WEEKDAY, "close_hour": P.WEEK_CLOSE_HOUR,
                "open_weekday": P.WEEK_OPEN_WEEKDAY, "open_hour": P.WEEK_OPEN_HOUR,
            },
            "session_basis": "assumed weekly pause Friday 21:00 UTC to Sunday 22:00 UTC; "
                             "not a validated exchange calendar, holidays not modelled",
        },
        "metals": metals,
        "stale_after_minutes": config.get("stale_after_minutes", 120),
    }
    worst = [m["status"] for m in metals.values()]
    snap["status"] = ("ok" if all(s == "ok" for s in worst)
                      else "stale" if "stale" in worst
                      else "unknown_age" if "unknown_age" in worst else "degraded")
    newest = max([_parse(m["quoted_utc"]) for m in metals.values() if m["quoted_utc"]], default=None)
    snap["quoted_utc"] = newest.strftime("%Y-%m-%dT%H:%M:%SZ") if newest else None
    snap["quoted_local"] = newest.astimezone(tz).strftime("%Y-%m-%d %H:%M") if newest else None
    snap["generated_local"] = now.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    return snap


def validate(config, snap, latest, series, now_utc=None):
    """Everything that must be true before a price is allowed onto a public page.

    Returns a list of problems; a non-empty list stops the build and leaves the pages that
    are already online exactly as they are. Stale beats wrong, every time.
    """
    now = now_utc or datetime.now(timezone.utc)
    problems = []
    sample = snap["source"]["is_sample"]

    for m, entry in snap["metals"].items():
        v = entry["spot_usd_per_troy_ounce"]
        if v is None or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
            problems.append(f"{m}: price is missing or not a finite number ({v!r})")
            continue
        if v <= 0:
            problems.append(f"{m}: price is not positive ({v})")
        lo, hi = config["plausible_usd_oz"][m]
        if not (lo < v < hi):
            problems.append(f"{m}: {v} USD/oz is outside the plausible range {lo}-{hi}")
        if entry["status"] == "future":
            problems.append(f"{m}: quote time {entry['quoted_utc']} is in the future")
        if entry["status"] == "unknown_age" and not sample:
            # No timestamp means no way to tell a live quote from a week-old one. The old
            # build silently substituted the build clock here, which is how a seven-hour-old
            # price was presented as current.
            problems.append(f"{m}: the feed gave no usable quote time, so freshness cannot be checked")
        two = entry.get("two_way")
        if two and two["bid_sar_per_gram"] > two["ask_sar_per_gram"]:
            problems.append(f"{m}: bid {two['bid_sar_per_gram']:.2f} is above ask {two['ask_sar_per_gram']:.2f}")

    if not sample:
        limit = P.quote_age_limit_minutes(now, config)
        for m, entry in snap["metals"].items():
            age = entry["age_minutes"]
            if age is not None and age > limit:
                closed = " (market closed; measured from the last weekly close)" if snap["market"]["closed"] else ""
                problems.append(f"{m}: quote is {age:.0f} minutes old, limit is {limit:.0f}{closed}")

    sar = snap["fx"]["per_usd"].get("SAR")
    if not sar or abs(sar - config["sar_per_usd_peg"]) > config.get("sar_peg_tolerance", 0.05):
        problems.append(f"SAR rate looks wrong: {sar}")

    closes = [r for r in series if r.get("kind") != "intraday"]
    if closes:
        prev = closes[-1]
        for m in ("gold", "silver"):
            new, old = latest.get(f"{m}_usd_oz"), prev.get(f"{m}_usd_oz")
            if new and old:
                ch = abs(P.pct_change(new, old))
                if ch > config.get("max_daily_move_pct", 10):
                    problems.append(f"{m} moved {ch:.1f}% vs the recorded {prev['date']} price: check the feed")
    return problems


def public(snap):
    """The copy published at /data/prices.json.

    Be clear about what this is: a **public, same-origin static file**. Anyone who can open
    the site can open it, and the `X-Robots-Tag: noindex` on /data/* keeps it out of a search
    index - that is not access control and is not described as any. It exists so the site's
    own pages can re-read one shared object instead of drifting apart.

    Nothing secret is put in it, which is the only real protection it has: no credentials, no
    provider URLs, no request counts, no build or account logs.

    Whether the same file may be consumed by OTHER people's sites is a separate question, and
    an open one - see WIDGET-MVP.md. Publishing rates on our own website is covered by the
    provider's terms; a third-party embed is not something we are going to decide by reading
    those terms hopefully.
    """
    return dict(snap, usage={
        "what": "public static file, served same-origin for mithqalprice.com's own pages",
        "note": "Spot metal value converted at the SAR peg. Not a shop price, not advice.",
        "third_party_use": "not offered: rights for third-party redistribution are unresolved "
                           "with the data provider (see WIDGET-MVP.md)",
    })
