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
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent


def _get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "metals-prototype/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def sample(config):
    data = json.loads((HERE / "data" / "sample_prices.json").read_text(encoding="utf-8"))
    out = dict(data["latest"])
    out.update(is_sample=True, source_name=data["source_name"], timestamp_utc=data["timestamp_utc"],
               fx_per_usd=data["fx_per_usd"], daily_closes=data["daily_closes"])
    return out


def metals_dev(config):
    """metals.dev: /v1/latest gives gold + silver (USD/toz) and currencies as USD per 1 unit.
    /v1/metal/spot gives bid/ask per metal (1 extra call each), so only call it when needed."""
    key = os.environ[config.get("provider_api_key_env", "METALS_API_KEY")]
    latest = _get_json(f"https://api.metals.dev/v1/latest?api_key={key}&currency=USD&unit=toz")
    if latest.get("status") != "success":
        raise RuntimeError(f"metals.dev error: {latest}")
    cur = latest["currencies"]  # USD per 1 unit of currency -> invert
    fx = {c: 1 / cur[c] for c in ("SAR", "AED", "INR", "PKR") if cur.get(c)}
    out = {
        "is_sample": False, "source_name": "Metals.Dev",
        "timestamp_utc": latest["timestamps"]["metal"],
        "gold_usd_oz": latest["metals"]["gold"], "silver_usd_oz": latest["metals"]["silver"],
        "fx_per_usd": fx,
    }
    for metal in ("gold", "silver"):
        spot = _get_json(f"https://api.metals.dev/v1/metal/spot?api_key={key}&metal={metal}&currency=USD")
        rate = spot.get("rate", spot)  # field nesting to confirm on first live run
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
