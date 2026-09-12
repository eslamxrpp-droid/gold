"""Where prices come from. Each provider returns the same shape:

{
  "is_sample": bool, "source_name": str, "timestamp_utc": "YYYY-MM-DDTHH:MM:SSZ",
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
    data = json.loads((HERE / "data" / "sample_prices.json").read_text(encoding="utf-8"))
    out = dict(data["latest"])
    out.update(is_sample=True, source_name=data["source_name"], timestamp_utc=data["timestamp_utc"],
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
    out = {
        "is_sample": False, "source_name": "Metals.Dev",
        "timestamp_utc": latest["timestamps"]["metal"],
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
    return out


def metals_dev_history(config, days=10):
    """Backfill daily prices on the first live run (timeseries allows up to 30 days per request). 1 request."""
    from datetime import date, timedelta
    key = os.environ[config.get("provider_api_key_env", "METALS_API_KEY")]
    end = date.today()
    start = end - timedelta(days=days)
    data = _get_json(f"https://api.metals.dev/v1/timeseries?api_key={key}&start_date={start}&end_date={end}")
    out = []
    for d, rate in sorted(data.get("rates", {}).items()):
        m = rate.get("metals", {})
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
        "timestamp_utc": g.get("updatedAt"),
        "gold_usd_oz": float(g["price"]), "silver_usd_oz": float(s["price"]),
        "gold_bid_usd_oz": None, "gold_ask_usd_oz": None, "silver_bid_usd_oz": None, "silver_ask_usd_oz": None,
        "fx_per_usd": {"SAR": config["sar_per_usd_peg"]},
    }


PROVIDERS = {"sample": sample, "metals_dev": metals_dev, "gold_api": gold_api}
HISTORY = {"metals_dev": metals_dev_history}  # providers that can backfill past days


def fetch(config):
    return PROVIDERS[config["provider"]](config)


def backfill(config):
    fn = HISTORY.get(config["provider"])
    return fn(config) if fn else []
