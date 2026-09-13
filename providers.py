"""Where prices come from. Each provider returns the same shape:

{
  "is_sample": bool, "source_name": str,
  "timestamp_utc": "YYYY-MM-DDTHH:MM:SSZ" | None,      # the metal quote time (newest)
  "timestamps_utc": {"gold": ..., "silver": ..., "fx": ..., "gold_bid_ask": ...},
  "gold_usd_oz": float, "silver_usd_oz": float,
  "gold_bid_usd_oz": float|None, "gold_ask_usd_oz": float|None,
  "silver_bid_usd_oz": float|None, "silver_ask_usd_oz": float|None,
  "fx_per_usd": {"SAR": 3.75, "INR": ..., "PKR": ...}
}

The API key is read from an environment variable (never written in files).
Live providers are NOT tested yet (the build sandbox has no access to these APIs):
check the response on the first real run.
"""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent


def _redact(url: str) -> str:
    """Never let an API key reach a log line: the Actions log is public on a public repo."""
    return re.sub(r"(api_key=)[^&]*", r"\1***", url)


def _get_json(url: str, timeout: int = 20):
    """Fetch JSON, and on an HTTP error raise something that says WHY.

    Without this the log shows a bare 'HTTP Error 400: Bad Request' and you have to guess.
    The provider puts the real reason (quota exhausted, bad key, bad parameter) in the
    response body, so read it and put it in the message.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "mithqalprice/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        hint = ""
        if e.code in (401, 403):
            hint = " -> the API key is missing, wrong, or not active"
        elif e.code in (400, 402, 429):
            hint = " -> most often the monthly request quota is used up, or a bad parameter"
        raise RuntimeError(f"{_redact(url)} returned HTTP {e.code}{hint}. Response: {body}") from None


def sample(config):
    """Local preview data. Never publishable: build.py forces noindex and writes a
    do-not-deploy marker whenever `is_sample` is set.

    The quote time is generated relative to now, so a preview shows the ordinary fresh
    state rather than permanently looking broken. MITHQAL_SAMPLE_AGE_MINUTES forces the
    unhappy states for screenshots and tests: 200 for stale, blank for "no timestamp".
    """
    from datetime import datetime, timedelta, timezone
    data = json.loads((HERE / "data" / "sample_prices.json").read_text(encoding="utf-8"))
    out = dict(data["latest"])
    age = os.environ.get("MITHQAL_SAMPLE_AGE_MINUTES")
    if age == "":
        ts = None
    else:
        minutes = float(age) if age else data.get("quote_age_minutes", 12)
        ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out.update(is_sample=True, source_name=data["source_name"], timestamp_utc=ts,
               timestamps_utc={"gold": ts, "silver": ts, "fx": ts, "gold_bid_ask": ts},
               fx_per_usd=data["fx_per_usd"], daily_closes=data["daily_closes"])
    return out


def metals_dev(config):
    """metals.dev. REQUEST BUDGET MATTERS: the plan is metered per request, so every call
    here is multiplied by the number of builds per month (see site/DEPLOY.md).

      /v1/latest      1 request  gold + silver (USD/toz) + currencies. Always needed.
      /v1/metal/spot  1 request per metal, bid/ask.

    Only GOLD bid/ask is displayed (the sell-price page); silver bid/ask was fetched and
    thrown away, which was a third of the budget. Metals in `bid_ask_metals` are fetched;
    default is gold only. Set it to [] to drop bid/ask entirely and halve the cost again.
    """
    key = os.environ[config.get("provider_api_key_env", "METALS_API_KEY")]
    latest = _get_json(f"https://api.metals.dev/v1/latest?api_key={key}&currency=USD&unit=toz")
    if latest.get("status") != "success":
        raise RuntimeError(f"metals.dev returned status={latest.get('status')!r}: {str(latest)[:400]}")
    cur = latest["currencies"]  # USD per 1 unit of currency -> invert
    fx = {c: 1 / cur[c] for c in ("SAR", "AED", "INR", "PKR") if cur.get(c)}
    # Documented semantics (metals.dev/docs, read 2026-09-12): `timestamps.metal` is when the
    # WHOLE metals block was collected and `timestamps.currency` when the FX block was - there
    # is no per-metal time on this endpoint. So gold and silver legitimately share one quote
    # time HERE; the snapshot still carries them separately, because a different feed (or a
    # different endpoint on this one) does not have to agree, and the day we switch we must
    # not have baked the assumption in. The bid/ask call has its own `timestamp`.
    stamps = latest.get("timestamps") or {}
    metal_ts, fx_ts = stamps.get("metal"), stamps.get("currency")
    out = {
        "is_sample": False, "source_name": "Metals.Dev",
        "timestamp_utc": metal_ts,
        "timestamps_utc": {"gold": metal_ts, "silver": metal_ts, "fx": fx_ts},
        "gold_usd_oz": latest["metals"]["gold"], "silver_usd_oz": latest["metals"]["silver"],
        "gold_bid_usd_oz": None, "gold_ask_usd_oz": None,
        "silver_bid_usd_oz": None, "silver_ask_usd_oz": None,
        "fx_per_usd": fx,
    }
    for metal in config.get("bid_ask_metals", ["gold"]):
        spot = _get_json(f"https://api.metals.dev/v1/metal/spot?api_key={key}&metal={metal}&currency=USD")
        rate = spot.get("rate", spot)
        out[f"{metal}_bid_usd_oz"] = rate.get("bid")
        out[f"{metal}_ask_usd_oz"] = rate.get("ask")
        out["timestamps_utc"][f"{metal}_bid_ask"] = spot.get("timestamp") or rate.get("timestamp")
    return out


def metals_dev_history(config, days=10):
    """Fill gaps in the daily history. One request (the endpoint allows up to 30 days).

    The range ENDS YESTERDAY, in the site's own timezone. Two reasons. `date.today()` is the
    build runner's UTC date, which is not the date the site calls today - at 01:30 Riyadh they
    are different days. And asking for today at all invites the provider to return a partial
    figure for a day that is not over, which the caller would then have to reject; the request
    is cheaper and clearer if it never asks.

    What the returned number IS, the provider does not say: the docs (read 2026-09-12)
    describe only "daily historical exchange rates between two dates" - no close, no average,
    no cut-off timezone. So these are stored as `provider_daily` and the pages call them the
    recorded price for that date, not the close.
    """
    from datetime import datetime, timedelta, timezone as _tz
    key = os.environ[config.get("provider_api_key_env", "METALS_API_KEY")]
    tz = _tz(timedelta(hours=config.get("timezone_offset_hours", 3)))
    end = datetime.now(tz).date() - timedelta(days=1)
    start = end - timedelta(days=max(1, days))
    data = _get_json(f"https://api.metals.dev/v1/timeseries?api_key={key}&start_date={start}&end_date={end}")
    out = []
    for d, rate in sorted((data.get("rates") or {}).items()):
        m = (rate or {}).get("metals", {}) or {}
        if m.get("gold") and m.get("silver"):
            out.append({"date": d, "gold_usd_oz": m["gold"], "silver_usd_oz": m["silver"]})
    return out


def gold_api(config):
    """gold-api.com free endpoint (no key). No bid/ask, no FX: SAR uses the official peg,
    INR/PKR conversion is hidden. Good as a backup / cross-check source."""
    g = _get_json("https://api.gold-api.com/price/XAU")
    s = _get_json("https://api.gold-api.com/price/XAG")
    return {
        "is_sample": False, "source_name": "gold-api.com",
        # This feed DOES timestamp each metal separately, which is exactly why the snapshot
        # keeps them apart rather than collapsing them to one site-wide "last updated".
        "timestamp_utc": g.get("updatedAt"),
        "timestamps_utc": {"gold": g.get("updatedAt"), "silver": s.get("updatedAt"), "fx": None},
        "gold_usd_oz": float(g["price"]), "silver_usd_oz": float(s["price"]),
        "gold_bid_usd_oz": None, "gold_ask_usd_oz": None, "silver_bid_usd_oz": None, "silver_ask_usd_oz": None,
        "fx_per_usd": {"SAR": config["sar_per_usd_peg"]},
    }


PROVIDERS = {"sample": sample, "metals_dev": metals_dev, "gold_api": gold_api}
HISTORY = {"metals_dev": metals_dev_history}  # providers that can backfill past days


def fetch(config):
    return PROVIDERS[config["provider"]](config)


def backfill(config, days=10):
    """Ask for `days` of daily closes. One request. The caller decides when it is worth
    spending (see history.refresh), and caps how often a stubborn gap is retried."""
    fn = HISTORY.get(config["provider"])
    return fn(config, days) if fn else []
