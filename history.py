"""The daily price history: what we store, what we are willing to call it, how gaps are
found, and how retries stay bounded.

Three rules decide everything in this file.

**history.json holds days that are OVER.** It never holds today. The old build appended
whatever intraday price happened to be on screen and wrote it in as that day's row, so a
number captured at 09:14 was filed under the same label as a settled daily figure - and on
the next build it was silently overwritten by the 09:29 number. Today's live price is added
to the *series* for display at build time, flagged `intraday`, and is never written to the
file. Today's daily figure arrives tomorrow, from the provider's timeseries.

**We do not call a number a "close" unless someone documents that it is one.** metals.dev's
timeseries endpoint is documented (read 2026-09-12) only as "daily historical exchange
rates between two dates": no statement of whether the figure is a close, an average, or a
snapshot, and no statement of the cut-off timezone. So a row fetched from it is a
`provider_daily` - the provider's figure for that date - and the pages say "recorded price
for 10 Sep", not "the 10 Sep close". Rows already on disk when this rule arrived have no
provenance at all and are labelled `unknown`; they are used, because they are the best we
have, but they are not certified.

**A gap is a missing date, not a short file.** The old check looked at the length of the
list and at its last entry, so a hole in the middle - 2026-09-11 really was missing - was
invisible, and the yesterday comparison silently compared against the day before yesterday.
Gaps are searched for explicitly, retried a bounded number of times per date, and never
filled with an invented number.
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
HISTORY_FILE = HERE / "data" / "history.json"
STATE_FILE = HERE / "data" / "history_state.json"

MAX_ATTEMPTS_PER_DATE = 3          # after this we stop asking the provider for that date
MAX_BACKFILL_CALLS_PER_BUILD = 1   # refresh() makes at most this many provider calls

# What a row's number actually is. Never "close": see the module docstring.
PROVIDER_DAILY = "provider_daily"  # the provider's published figure for a finished date
INTRADAY = "intraday"              # today's live price, display only, never saved
UNKNOWN = "unknown"                # on disk before provenance was recorded; used, not certified

PLAUSIBLE = {"gold_usd_oz": (100, 50000), "silver_usd_oz": (1, 2000)}


def _d(s):
    return date.fromisoformat(s)


def today_in(tz_offset_hours):
    return datetime.now(timezone(timedelta(hours=tz_offset_hours))).date()


def load(path=None):
    """Rows for finished days, oldest first.

    A row dated today or later is dropped on read: it can only be an intraday snapshot
    written by an older build, and we will not present it as a settled daily figure. A row
    with no `kind`, or with the old `close` label that this project used before it checked
    what the provider actually publishes, becomes `unknown`.
    """
    path = Path(path) if path else HISTORY_FILE
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ok, _ = check_row(r)
        if not ok:
            continue
        kind = r.get("kind")
        if kind not in (PROVIDER_DAILY, INTRADAY):
            kind = UNKNOWN                     # includes the legacy "close" label
        out.append({"date": r["date"], "gold_usd_oz": r["gold_usd_oz"],
                    "silver_usd_oz": r["silver_usd_oz"], "kind": kind})
    return sorted(out, key=lambda r: r["date"])


def check_row(r, max_date=None):
    """(ok, reason) for one candidate row. Used on read and, more importantly, on every row
    a provider hands us before it can reach the file."""
    try:
        d = _d(str(r.get("date")))
    except (ValueError, TypeError):
        return False, f"unparseable date {r.get('date')!r}"
    if max_date is not None and d > max_date:
        return False, f"{d} is not a finished day (latest allowed: {max_date})"
    for field, (lo, hi) in PLAUSIBLE.items():
        v = r.get(field)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return False, f"{field} is {v!r}, not a number"
        if v != v or v in (float("inf"), float("-inf")):
            return False, f"{field} is not finite"
        if not (lo < v < hi):
            return False, f"{field}={v} is outside {lo}-{hi}"
    return True, ""


def drop_unfinalised(rows, today):
    return [r for r in rows if _d(r["date"]) < today]


def missing_dates(rows, start, end):
    """Calendar dates in [start, end] with no row. Weekend dates count: the provider does
    publish a weekend row (it repeats Friday's figure), so a missing Saturday is still a hole
    in what we were given, and it is better to ask for it than to guess."""
    have = {r["date"] for r in rows}
    out, d = [], start
    while d <= end:
        if d.isoformat() not in have:
            out.append(d)
        d += timedelta(days=1)
    return out


def load_state(path=None):
    path = Path(path) if path else STATE_FILE
    if not path.exists():
        return {"attempts": {}}
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"attempts": {}}
    s.setdefault("attempts", {})
    return s


def save_state(state, path=None):
    """Write only on a real change, and say whether it changed.

    This return value is load-bearing. The scheduled job commits the data folder when the
    build reports a change; if that report came only from history.json, a build whose
    backfill failed would update the attempt counter on the runner and then throw the runner
    away, and the three-attempt cap would never advance across checkouts.
    """
    path = Path(path) if path else STATE_FILE
    new = json.dumps(state, indent=1, sort_keys=True)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == new:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def dates_worth_retrying(missing, state):
    return [d for d in missing if state["attempts"].get(d.isoformat(), 0) < MAX_ATTEMPTS_PER_DATE]


def record_attempts(state, attempted):
    for d in attempted:
        k = d.isoformat()
        state["attempts"][k] = state["attempts"].get(k, 0) + 1
    return state


def merge(rows, fetched, max_date):
    """Merge provider rows into stored rows. Returns (merged, changed_dates, rejected).

    Everything the provider sends is checked first. A row for today or a future date is
    rejected outright - the provider's range may include today, and merging it would file an
    unfinished day as a settled one and then collide with the intraday row the display adds
    for the same date. A row with a missing, non-numeric or implausible price is rejected
    with a reason, so a bad response cannot quietly rewrite history.
    """
    by_date = {r["date"]: r for r in rows}
    changed, rejected = [], []
    for f in fetched or []:
        if not isinstance(f, dict):
            rejected.append((repr(f)[:40], "not an object"))
            continue
        ok, why = check_row(f, max_date)
        if not ok:
            rejected.append((str(f.get("date")), why))
            continue
        d = f["date"]
        new = {"date": d, "gold_usd_oz": f["gold_usd_oz"], "silver_usd_oz": f["silver_usd_oz"],
               "kind": PROVIDER_DAILY}
        if by_date.get(d) != new:
            if d not in changed:
                changed.append(d)
            by_date[d] = new                      # a later duplicate wins; both are the provider's
    return sorted(by_date.values(), key=lambda r: r["date"]), sorted(changed), rejected


def refresh(config, rows, today, backfill, state=None, keep_days=400):
    """Fill gaps in the stored history, within a fixed request budget.

    `backfill(days)` is the provider call; it is made at most MAX_BACKFILL_CALLS_PER_BUILD
    times per build, only when a wanted date is actually missing and has not already been
    asked for MAX_ATTEMPTS_PER_DATE times.

    Returns (rows, changed_dates, note, state_changed).
    """
    state = state if state is not None else load_state()
    want_days = max(config.get("history_days", 7) + 1, 8)
    start = today - timedelta(days=want_days)
    end = today - timedelta(days=1)          # today is never a finished day
    missing = missing_dates(rows, start, end)
    if not missing:
        return rows, [], "history complete", False
    retryable = dates_worth_retrying(missing, state)
    if not retryable:
        stuck = ", ".join(d.isoformat() for d in missing)
        return rows, [], f"gaps the provider never returned, not retried again: {stuck}", False
    span = (end - min(retryable)).days + 1
    try:
        fetched = backfill(span)
    except Exception as e:                    # a failed backfill must never fail the build
        record_attempts(state, retryable)
        return rows, [], f"backfill failed ({e})", save_state(state)
    rows, changed, rejected = merge(rows, fetched, end)
    record_attempts(state, [d for d in retryable if d.isoformat() not in changed])
    state_changed = save_state(state)
    still = [d.isoformat() for d in missing if d.isoformat() not in {r["date"] for r in rows}]
    note = f"filled {len(changed)} day(s)"
    if rejected:
        note += "; rejected " + ", ".join(f"{d} ({why})" for d, why in rejected[:4])
    if still:
        note += f"; still missing {', '.join(still)}"
    return rows[-keep_days:], changed, note, state_changed


def save(rows, path=None):
    """Write only when something actually changed, and say whether it did. The scheduled job
    commits on that answer, so an unchanged file produces no commit and no deploy race."""
    path = Path(path) if path else HISTORY_FILE
    new = json.dumps(rows, indent=1, ensure_ascii=False)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == new:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def series(rows, today, latest_gold, latest_silver):
    """History plus today's live point, flagged for what it is. Nothing here is written to
    disk; `kind` travels with the row so a table can label today's row honestly. Any stored
    row dated today is dropped first - there must be exactly one row per date."""
    past = [r for r in rows if _d(r["date"]) < today]
    return past + [{"date": today.isoformat(), "gold_usd_oz": latest_gold,
                    "silver_usd_oz": latest_silver, "kind": INTRADAY}]


def previous_daily(rows, today):
    """The most recent stored day before today, or None. Also says whether it really is
    yesterday - the difference between 'vs yesterday' and 'vs Thursday' is the whole point."""
    past = [r for r in rows if _d(r["date"]) < today and r.get("kind") != INTRADAY]
    if not past:
        return None
    row = past[-1]
    return {**row, "is_yesterday": _d(row["date"]) == today - timedelta(days=1),
            "days_ago": (today - _d(row["date"])).days}


def week_daily(rows, today, target_days=7, tolerance_days=2):
    """The stored day nearest to `target_days` ago, preferring the newest row at or before
    the target. Returns None when the history does not reach back far enough - in which case
    the page says the comparison is unavailable instead of quietly comparing against whatever
    the oldest row happens to be."""
    target = today - timedelta(days=target_days)
    past = [r for r in rows if _d(r["date"]) < today and r.get("kind") != INTRADAY]
    if not past:
        return None
    at_or_before = [r for r in past if _d(r["date"]) <= target]
    row = at_or_before[-1] if at_or_before else past[0]
    days_ago = (today - _d(row["date"])).days
    if abs(days_ago - target_days) > tolerance_days:
        return None
    return {**row, "days_ago": days_ago}


# Old names kept so nothing silently breaks if a caller is missed.
previous_close = previous_daily
week_close = week_daily
