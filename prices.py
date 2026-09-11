"""Price math. Pure functions only, so they are easy to test.

All prices start as USD per troy ounce (the way price feeds publish them) and are
converted to SAR per gram. The same formulas are repeated in static/calc.js for the
calculators in the browser; tests/test_prices.py checks that both give the same numbers.
"""

GRAMS_PER_TROY_OUNCE = 31.1034768
GRAMS_PER_TOLA = 11.6638038  # Indian/Gulf tola used by South Asian buyers


def per_gram(usd_per_oz: float, sar_per_usd: float) -> float:
    """Pure metal (24K gold / 999.9 silver) price in SAR per gram."""
    return usd_per_oz * sar_per_usd / GRAMS_PER_TROY_OUNCE


def gold_karat_price(pure_per_gram: float, karat: int) -> float:
    """Gold price per gram for a karat: 24K price x karat/24 (21K = 87.5% gold)."""
    return pure_per_gram * karat / 24


def silver_purity_price(pure_per_gram: float, purity: int) -> float:
    """Silver price per gram for a fineness: 999 -> x0.999, 925 (sterling) -> x0.925."""
    return pure_per_gram * purity / 1000


def market_closed(now_utc) -> bool:
    """Global gold/silver trading pauses from Friday ~21:00 UTC to Sunday ~22:00 UTC.
    During that window a price feed's timestamp stays at Friday's close, so the build must not treat it as stale."""
    wd, h = now_utc.weekday(), now_utc.hour  # Monday = 0 ... Friday = 4, Saturday = 5, Sunday = 6
    return (wd == 4 and h >= 21) or wd == 5 or (wd == 6 and h < 22)


def pct_change(new: float, old: float) -> float:
    return (new - old) / old * 100 if old else 0.0


def direction(change_pct: float, flat_band: float = 0.05) -> str:
    """'up', 'down' or 'flat'. Moves smaller than +/-0.05% count as flat."""
    if change_pct > flat_band:
        return "up"
    if change_pct < -flat_band:
        return "down"
    return "flat"


def sell_value(weight_g: float, karat: int, bid_pure_per_gram: float, shop_deduction_pct: float = 0.0) -> float:
    """Raw gold value of an item at the bid price, minus an optional shop deduction the user enters."""
    raw = weight_g * gold_karat_price(bid_pure_per_gram, karat)
    return raw * (1 - shop_deduction_pct / 100)


def pure_gold_grams(weight_g: float, karat: int) -> float:
    """Pure gold inside an item: weight x karat/24 (nisab is counted on pure gold)."""
    return weight_g * karat / 24


def zakat_gold(items, nisab_grams_pure: float, pure_per_gram: float, rate: float = 0.025):
    """items: list of (weight_g, karat). Returns dict with pure grams, whether nisab is reached, value and zakat due."""
    pure = sum(pure_gold_grams(w, k) for w, k in items)
    value = pure * pure_per_gram
    reached = pure >= nisab_grams_pure
    return {"pure_grams": pure, "reached": reached, "value": value, "zakat": value * rate if reached else 0.0}


def zakat_silver(weight_g: float, purity: int, nisab_grams_pure: float, pure_per_gram: float, rate: float = 0.025):
    pure = weight_g * purity / 1000
    value = pure * pure_per_gram
    reached = pure >= nisab_grams_pure
    return {"pure_grams": pure, "reached": reached, "value": value, "zakat": value * rate if reached else 0.0}
