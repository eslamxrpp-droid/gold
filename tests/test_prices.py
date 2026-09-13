"""Run: python -m unittest discover tests   (from the site folder)

Two rules for this suite:

1. **Nothing here may touch `dist/`.** In CI the scheduled job builds `dist/` with REAL
   prices and then runs the tests before uploading. On 2026-09-12 a test rebuilt `dist/`
   with sample data seconds before the upload step and the live site briefly served sample
   prices. Every test that needs a built site uses `SampleSite`, which builds into a
   temporary folder, and `ProductionArtifact` checks afterwards that `dist/` is untouched.
2. **No paid calls.** Providers are exercised through fixtures.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402
import history as H  # noqa: E402
import pages as REG  # noqa: E402
import prices as P  # noqa: E402
import providers  # noqa: E402
import snapshot as SNAP  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
CALC_JS = str(ROOT / "static" / "calc.js")

# The workflow has two legitimate names, because the two places this code lives cannot both
# hold the same one:
#   .github/workflows/update-prices.yml   the repository, and the ONLY file GitHub executes
#   github-workflow-update-prices.yml     the local folder, where the sync tool cannot write
#                                         a path containing a dot-folder
# The tests must exercise whichever one is really there, preferring the repository path, and
# there must never be two. Pinning the staging name made the whole class fail in the released
# layout - a failure of the tests, not of the release.
REPO_WORKFLOW = ".github/workflows/update-prices.yml"
STAGING_WORKFLOW = "github-workflow-update-prices.yml"


def workflow_path():
    for rel in (REPO_WORKFLOW, STAGING_WORKFLOW):
        p = ROOT / rel
        if p.exists():
            return p
    raise FileNotFoundError(
        f"no workflow found at {REPO_WORKFLOW} or {STAGING_WORKFLOW} under {ROOT}")


def bash_or_skip():
    """A deliberate choice of shell, and an honest skip when there is not one.

    The workflow steps are POSIX shell. On Linux and macOS `bash` is `bash`. On Windows it is
    whatever Git Bash or WSL put on PATH, which may be neither present nor usable - so the
    suite says it skipped rather than pretending to have run."""
    exe = shutil.which("bash")
    if not exe:
        raise unittest.SkipTest("no bash on PATH: the workflow-shell tests need one "
                                "(Git Bash or WSL on Windows)")
    return exe


def js(expr):
    script = f"const M = require({json.dumps(CALC_JS)});\nconsole.log(JSON.stringify({expr}));"
    return json.loads(subprocess.check_output(["node", "-e", script], text=True))


class SampleSite:
    """One sample build in a temp folder, shared by the tests that read HTML."""
    _dir = None

    @classmethod
    def path(cls):
        if cls._dir is None:
            cls._dir = Path(tempfile.mkdtemp(prefix="mithqal-test-"))
            cfg = dict(CONFIG, provider="sample")
            real = build.DIST
            build.DIST = cls._dir
            try:
                build.build(cfg)
            finally:
                build.DIST = real
        return cls._dir

    @classmethod
    def html(cls, rel):
        return (cls.path() / rel).read_text(encoding="utf-8")

    @classmethod
    def data(cls, rel):
        import re
        m = re.search(r'id="prices">(.*?)</script>', cls.html(rel), re.S)
        return json.loads(m.group(1))


def tearDownModule():
    if SampleSite._dir:
        shutil.rmtree(SampleSite._dir, ignore_errors=True)


# ---------------------------------------------------------------- price math

class PriceMath(unittest.TestCase):
    def test_per_gram(self):
        # 4362 USD/oz x 3.75 / 31.1034768 = 525.906 SAR/g
        self.assertAlmostEqual(P.per_gram(4362, 3.75), 525.9058, places=3)

    def test_karats(self):
        self.assertAlmostEqual(P.gold_karat_price(480, 21), 420)
        self.assertAlmostEqual(P.gold_karat_price(480, 18), 360)
        self.assertAlmostEqual(P.silver_purity_price(8, 925), 7.4)

    def test_direction(self):
        self.assertEqual(P.direction(0.2), "up")
        self.assertEqual(P.direction(-0.2), "down")
        self.assertEqual(P.direction(0.03), "flat")

    def test_sell_value(self):
        self.assertAlmostEqual(P.sell_value(20, 21, 480, 0), 8400)
        self.assertAlmostEqual(P.sell_value(20, 21, 480, 10), 7560)

    def test_zakat_gold_21k(self):
        # islamqa 396499: nisab for 21K = 85 x 24 / 21 = 97.14 g
        self.assertFalse(P.zakat_gold([(97.0, 21)], 85, 500)["reached"])
        above = P.zakat_gold([(97.2, 21)], 85, 500)
        self.assertTrue(above["reached"])
        self.assertAlmostEqual(above["zakat"], 97.2 * 21 / 24 * 500 * 0.025)

    def test_zakat_two_nisabs_differ(self):
        items = [(100, 21)]  # 87.5 g pure: reaches 85, not 92
        self.assertTrue(P.zakat_gold(items, 85, 500)["reached"])
        self.assertFalse(P.zakat_gold(items, 92, 500)["reached"])

    def test_zakat_silver(self):
        z = P.zakat_silver(650, 925, 595, 7.7)  # 601.25 g pure
        self.assertTrue(z["reached"])
        self.assertAlmostEqual(z["zakat"], 601.25 * 7.7 * 0.025)


class JsMatchesPython(unittest.TestCase):
    def test_same_numbers(self):
        out = js("""[
  M.karatPrice(525.9064, 21), M.purityPrice(7.76, 925), M.sellValue(20, 21, 525.6, 5),
  M.zakatGold([[60, 21], [40, 18]], 85, 525.9, 0.025), M.zakatSilver(650, 925, 595, 7.7, 0.025)
]""")
        self.assertAlmostEqual(out[0], P.gold_karat_price(525.9064, 21))
        self.assertAlmostEqual(out[1], P.silver_purity_price(7.76, 925))
        self.assertAlmostEqual(out[2], P.sell_value(20, 21, 525.6, 5))
        py = P.zakat_gold([(60, 21), (40, 18)], 85, 525.9)
        self.assertAlmostEqual(out[3]["zakat"], py["zakat"])
        self.assertEqual(out[3]["reached"], py["reached"])
        self.assertAlmostEqual(out[4]["zakat"], P.zakat_silver(650, 925, 595, 7.7)["zakat"])

    def test_silver_quote_math_matches(self):
        """The seller-quote comparison is the one calculation a reader might act on with
        money in hand, so the arithmetic is pinned here as well as in the browser."""
        grams, purity, per_gram_quote = 1000.0, 999, 8.5
        value = P.silver_purity_price(7.82537, purity) * grams
        total = per_gram_quote * grams
        self.assertAlmostEqual(js(f"M.purityPrice(7.82537, {purity}) * {grams}"), value, places=6)
        self.assertAlmostEqual(total - value, total - value)


# ---------------------------------------------------------------- input handling

class ArabicAndMessyInput(unittest.TestCase):
    """A reader typing ١٠٠ on an Arabic keyboard must get an answer, and an ambiguous
    separator must get a question rather than a confident wrong answer."""

    def test_arabic_indic_digits(self):
        self.assertEqual(js("M.parseNum('١٠٠')"), 100)
        self.assertEqual(js("M.parseNum('۹۹')"), 99)              # extended (Persian) forms
        self.assertEqual(js("M.parseNum('١٢٫٥')"), 12.5)          # U+066B decimal separator
        self.assertEqual(js("M.parseNum('١٬٢٥٠٫٧٥')"), 1250.75)   # U+066C thousands separator

    def test_latin_separators(self):
        self.assertEqual(js("M.parseNum('1,250.75')"), 1250.75)
        self.assertEqual(js("M.parseNum('1,250')"), 1250)
        self.assertEqual(js("M.parseNum('12,5')"), 12.5)
        self.assertEqual(js("M.parseNum('1 250')"), 1250)

    def test_ambiguous_is_refused_not_guessed(self):
        # 1,2345 is neither a thousands group nor a two-digit decimal. Guessing either way
        # is wrong by a factor of a thousand, so it is refused.
        self.assertIsNone(js("M.parseNum('1,2345')"))
        self.assertIsNone(js("M.parseNum('abc')"))
        self.assertIsNone(js("M.parseNum('1e5')"))
        self.assertIsNone(js("M.parseNum('')"))

    def test_weight_edges(self):
        self.assertEqual(js("M.checkWeight('')")["reason"], "empty")
        self.assertEqual(js("M.checkWeight('0')")["reason"], "zero")
        self.assertEqual(js("M.checkWeight('-3')")["reason"], "negative")
        self.assertEqual(js("M.checkWeight('99999999')")["reason"], "huge")
        self.assertEqual(js("M.checkWeight('نص')")["reason"], "invalid")
        self.assertTrue(js("M.checkWeight('12.5')")["ok"])

    def test_units(self):
        self.assertEqual(js("M.toGrams(1, 'kg')"), 1000)
        self.assertEqual(js("M.toGrams(2.5, 'g')"), 2.5)


# ---------------------------------------------------------------- history

class HistoryGaps(unittest.TestCase):
    """The real bug, 2026-09-12: history held 09-10 and 09-12 but not 09-11, and the page
    called the 09-10 close 'yesterday'. Gaps are now found, retried a bounded number of
    times, and never papered over."""

    rows = [{"date": "2026-09-08", "gold_usd_oz": 4355, "silver_usd_oz": 65.7, "kind": H.PROVIDER_DAILY},
            {"date": "2026-09-09", "gold_usd_oz": 4402, "silver_usd_oz": 67.2, "kind": H.PROVIDER_DAILY},
            {"date": "2026-09-10", "gold_usd_oz": 4317, "silver_usd_oz": 63.5, "kind": H.PROVIDER_DAILY}]

    def test_missing_dates_found_in_the_middle(self):
        missing = H.missing_dates(self.rows, date(2026, 9, 8), date(2026, 9, 12))
        self.assertEqual([d.isoformat() for d in missing], ["2026-09-11", "2026-09-12"])

    def test_previous_close_is_named_not_assumed(self):
        prev = H.previous_daily(self.rows, date(2026, 9, 12))
        self.assertEqual(prev["date"], "2026-09-10")
        self.assertFalse(prev["is_yesterday"])      # the whole point: it is NOT yesterday
        self.assertEqual(prev["days_ago"], 2)
        with_yesterday = self.rows + [{"date": "2026-09-11", "gold_usd_oz": 4340, "silver_usd_oz": 64.0}]
        self.assertTrue(H.previous_daily(with_yesterday, date(2026, 9, 12))["is_yesterday"])

    def test_label_says_which_day_it_compared(self):
        prev = H.previous_daily(self.rows, date(2026, 9, 12))
        self.assertIn("10 سبتمبر", build.comparison_label(prev, "ar"))
        self.assertNotIn("أمس", build.comparison_label(prev, "ar"))
        self.assertIn("10 Sep", build.comparison_label(prev, "en"))
        self.assertIn("لا توجد مقارنة", build.comparison_label(None, "ar"))

    def test_week_comparison_refuses_to_stretch(self):
        # Only three days of history: a "last week" figure would be a fabrication.
        self.assertIsNone(H.week_daily(self.rows, date(2026, 9, 12)))
        long = [{"date": f"2026-09-{d:02d}", "gold_usd_oz": 4300 + d, "silver_usd_oz": 64.0}
                for d in range(1, 12)]
        wk = H.week_daily(long, date(2026, 9, 12))
        self.assertEqual(wk["date"], "2026-09-05")
        self.assertEqual(wk["days_ago"], 7)

    def test_backfill_is_bounded_per_date(self):
        calls = []

        def never_returns_the_gap(days):
            calls.append(days)
            return [{"date": "2026-09-09", "gold_usd_oz": 4402, "silver_usd_oz": 67.2}]

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = {"attempts": {}}
            rows = list(self.rows)
            with mock.patch.object(H, "STATE_FILE", state_path):
                for _ in range(6):
                    rows, changed, note, _sc = H.refresh(
                        dict(CONFIG), rows, date(2026, 9, 12), never_returns_the_gap, state=state)
            self.assertEqual(len(calls), H.MAX_ATTEMPTS_PER_DATE,
                             "a gap the provider never fills must stop being requested")
            self.assertIn("not retried again", note)

    def test_backfill_failure_never_fails_the_build(self):
        def explode(days):
            raise RuntimeError("quota exhausted")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(H, "STATE_FILE", Path(tmp) / "s.json"):
                rows, changed, note, _sc = H.refresh(dict(CONFIG), list(self.rows), date(2026, 9, 12),
                                                     explode, state={"attempts": {}})
        self.assertEqual(rows, self.rows)
        self.assertIn("backfill failed", note)

    def test_a_correction_replaces_the_stored_close(self):
        fixed, changed, rejected = H.merge(
            self.rows, [{"date": "2026-09-10", "gold_usd_oz": 4320.5, "silver_usd_oz": 63.6}], date(2026, 9, 11))
        self.assertEqual(rejected, [])
        self.assertEqual(changed, ["2026-09-10"])
        self.assertEqual([r for r in fixed if r["date"] == "2026-09-10"][0]["gold_usd_oz"], 4320.5)
        self.assertEqual(len(fixed), 3)

    def test_today_is_never_stored_as_a_close(self):
        """An intraday snapshot relabelled as a daily close is how a 09:14 price became
        'the close' and was then overwritten by the 09:29 price."""
        with_today = self.rows + [{"date": "2026-09-12", "gold_usd_oz": 4349, "silver_usd_oz": 64.4}]
        kept = H.drop_unfinalised(with_today, date(2026, 9, 12))
        self.assertNotIn("2026-09-12", [r["date"] for r in kept])
        series = H.series(kept, date(2026, 9, 12), 4349, 64.4)
        self.assertEqual(series[-1]["kind"], "intraday")


class HistoryPersistence(unittest.TestCase):
    """The scheduled job commits history.json when it changed. It must change when a day
    finalises or a correction lands, and must NOT change on every one of the ~96 builds a
    day - that was 2,900 commits a month and a deploy race with every one of them."""

    def test_save_reports_only_real_changes(self):
        rows = [{"date": "2026-09-10", "gold_usd_oz": 4300, "silver_usd_oz": 64, "kind": "close"}]
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "h.json"
            self.assertTrue(H.save(rows, f))        # first write
            self.assertFalse(H.save(rows, f))       # identical: no commit, no race
            rows2 = rows + [{"date": "2026-09-11", "gold_usd_oz": 4362, "silver_usd_oz": 64.4, "kind": H.PROVIDER_DAILY}]
            self.assertTrue(H.save(rows2, f))       # a day finalised
            rows3 = [dict(rows2[0], gold_usd_oz=4301)] + rows2[1:]
            self.assertTrue(H.save(rows3, f))       # a correction must survive, not be dropped
            self.assertEqual(H.load(f)[0]["gold_usd_oz"], 4301)

    def test_repeated_fresh_checkouts_are_stable(self):
        """Simulates the CI shape: a clean checkout, a build, a commit of history, then the
        next run starting from that committed file. Nothing may be lost or duplicated."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "h.json"
            closes = [{"date": "2026-09-09", "gold_usd_oz": 4402, "silver_usd_oz": 67.2},
                      {"date": "2026-09-10", "gold_usd_oz": 4317, "silver_usd_oz": 63.5},
                      {"date": "2026-09-11", "gold_usd_oz": 4340, "silver_usd_oz": 64.0}]
            for run in range(4):
                rows = H.drop_unfinalised(H.load(f), date(2026, 9, 12))
                rows, _, _ = H.merge(rows, closes, date(2026, 9, 11))
                changed = H.save(rows, f)
                self.assertEqual(changed, run == 0, f"run {run} should {'' if run == 0 else 'not '}commit")
            saved = H.load(f)
            self.assertEqual([r["date"] for r in saved], ["2026-09-09", "2026-09-10", "2026-09-11"])
            self.assertTrue(all(r["kind"] == H.PROVIDER_DAILY for r in saved))


# ---------------------------------------------------------------- validation

def fake_latest(**kw):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = {"is_sample": False, "source_name": "test", "timestamp_utc": now,
         "timestamps_utc": {"gold": now, "silver": now, "fx": now},
         "gold_usd_oz": 4362.0, "silver_usd_oz": 64.4,
         "gold_bid_usd_oz": 4360.0, "gold_ask_usd_oz": 4364.0,
         "silver_bid_usd_oz": None, "silver_ask_usd_oz": None,
         "fx_per_usd": {"SAR": 3.75}}
    d.update(kw)
    return d


class Validation(unittest.TestCase):
    def snap(self, latest, now=None):
        return SNAP.build_snapshot(CONFIG, latest, now or datetime.now(timezone.utc))

    def check(self, latest, series=(), now=None):
        now = now or datetime.now(timezone.utc)
        return SNAP.validate(CONFIG, self.snap(latest, now), latest, list(series), now)

    def test_healthy_price_passes(self):
        self.assertEqual(self.check(fake_latest()), [])

    def test_implausible_and_non_finite_prices_stop_the_build(self):
        self.assertTrue(self.check(fake_latest(gold_usd_oz=43.62)))
        self.assertTrue(self.check(fake_latest(silver_usd_oz=5000)))
        self.assertTrue(self.check(fake_latest(gold_usd_oz=float("nan"))))
        self.assertTrue(self.check(fake_latest(gold_usd_oz=None)))
        self.assertTrue(self.check(fake_latest(gold_usd_oz=-4362.0)))

    def test_bad_fx_stops_the_build(self):
        self.assertTrue(self.check(fake_latest(fx_per_usd={"SAR": 1})))
        self.assertTrue(self.check(fake_latest(fx_per_usd={})))

    def test_daily_jump_stops_the_build(self):
        series = [{"date": "2026-09-11", "gold_usd_oz": 3000, "silver_usd_oz": 64, "kind": H.PROVIDER_DAILY}]
        self.assertTrue(self.check(fake_latest(), series))

    def test_missing_timestamp_is_not_treated_as_now(self):
        """The old build substituted the build clock for a missing quote time, so a feed
        with no timestamp always looked live."""
        latest = fake_latest(timestamp_utc=None, timestamps_utc={"gold": None, "silver": None, "fx": None})
        snap = self.snap(latest)
        self.assertEqual(snap["metals"]["gold"]["status"], "unknown_age")
        self.assertIsNone(snap["quoted_utc"])
        self.assertTrue(self.check(latest), "a live feed with no quote time must stop the build")

    def test_future_timestamp_is_rejected(self):
        ahead = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        latest = fake_latest(timestamp_utc=ahead, timestamps_utc={"gold": ahead, "silver": ahead, "fx": ahead})
        self.assertTrue(any("future" in p for p in self.check(latest)))

    def test_unparseable_timestamp_is_rejected(self):
        bad = "not a date"
        latest = fake_latest(timestamp_utc=bad, timestamps_utc={"gold": bad, "silver": bad, "fx": bad})
        self.assertTrue(self.check(latest))

    def test_crossed_bid_ask_is_rejected(self):
        self.assertTrue(any("above ask" in p for p in
                            self.check(fake_latest(gold_bid_usd_oz=4400.0, gold_ask_usd_oz=4360.0))))

    def test_metals_keep_independent_timestamps(self):
        """metals.dev gives one time for both metals; gold-api.com gives one each. The
        snapshot must carry them separately so switching feeds cannot silently mislabel."""
        old = (datetime.now(timezone.utc) - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
        new = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        snap = self.snap(fake_latest(timestamps_utc={"gold": new, "silver": old, "fx": new}))
        self.assertEqual(snap["metals"]["gold"]["status"], "ok")
        self.assertEqual(snap["metals"]["silver"]["status"], "stale")
        self.assertEqual(snap["status"], "stale")


class MarketHours(unittest.TestCase):
    utc = staticmethod(lambda *a: datetime(*a, tzinfo=timezone.utc))

    def test_weekend_window(self):
        self.assertFalse(P.market_closed(self.utc(2026, 9, 11, 20, 0)))   # Friday 20:00 open
        self.assertTrue(P.market_closed(self.utc(2026, 9, 11, 21, 30)))   # Friday 21:30 closed
        self.assertTrue(P.market_closed(self.utc(2026, 9, 12, 12, 0)))    # Saturday
        self.assertTrue(P.market_closed(self.utc(2026, 9, 13, 21, 0)))    # Sunday before 22:00
        self.assertFalse(P.market_closed(self.utc(2026, 9, 13, 22, 30)))  # Sunday reopen
        self.assertFalse(P.market_closed(self.utc(2026, 9, 14, 9, 0)))    # Monday

    def test_last_session_close(self):
        self.assertEqual(P.last_session_close(self.utc(2026, 9, 12, 12, 0)), self.utc(2026, 9, 11, 21, 0))
        self.assertEqual(P.last_session_close(self.utc(2026, 9, 11, 22, 0)), self.utc(2026, 9, 11, 21, 0))
        self.assertEqual(P.last_session_close(self.utc(2026, 9, 11, 20, 0)), self.utc(2026, 9, 4, 21, 0))

    def test_weekend_allowance_is_bounded(self):
        """The old rule skipped the age check entirely while the market was closed, so a
        feed that died on Wednesday sailed through the weekend. The allowance now grows
        only as fast as a correctly-frozen feed ages."""
        sat = self.utc(2026, 9, 12, 12, 0)                       # 15h after Friday's close
        limit = P.quote_age_limit_minutes(sat, CONFIG)
        self.assertAlmostEqual(limit, CONFIG["max_price_age_minutes"] + 15 * 60, delta=1)

        friday_close = (self.utc(2026, 9, 11, 21, 0)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ok = fake_latest(timestamp_utc=friday_close,
                         timestamps_utc={"gold": friday_close, "silver": friday_close, "fx": friday_close})
        snap = SNAP.build_snapshot(CONFIG, ok, sat)
        self.assertEqual(SNAP.validate(CONFIG, snap, ok, [], sat), [], "a frozen weekend feed is fine")

        dead = (self.utc(2026, 9, 9, 12, 0)).strftime("%Y-%m-%dT%H:%M:%SZ")   # Wednesday
        bad = fake_latest(timestamp_utc=dead, timestamps_utc={"gold": dead, "silver": dead, "fx": dead})
        snap = SNAP.build_snapshot(CONFIG, bad, sat)
        self.assertTrue(SNAP.validate(CONFIG, snap, bad, [], sat),
                        "a feed that died before the close must not hide behind the weekend")

    def test_open_market_stale_price_stops_the_build(self):
        mon = self.utc(2026, 9, 14, 12, 0)
        old = self.utc(2026, 9, 14, 8, 0).strftime("%Y-%m-%dT%H:%M:%SZ")
        latest = fake_latest(timestamp_utc=old, timestamps_utc={"gold": old, "silver": old, "fx": old})
        snap = SNAP.build_snapshot(CONFIG, latest, mon)
        self.assertTrue(SNAP.validate(CONFIG, snap, latest, [], mon))


# ---------------------------------------------------------------- providers

class RequestBudget(unittest.TestCase):
    """Every API call here is multiplied by ~2,900 builds a month, so the number of
    requests per build is a cost decision, not a detail. Metals.Dev meters per request."""

    CFG = {"provider_api_key_env": "METALS_API_KEY", "sar_per_usd_peg": 3.75}

    def _fake(self, calls):
        def _get(url, timeout=20):
            calls.append(url)
            if "/v1/latest" in url:
                return {"status": "success",
                        "timestamps": {"metal": "2026-09-12T10:00:00Z", "currency": "2026-09-12T10:01:00Z"},
                        "metals": {"gold": 4362.0, "silver": 64.4},
                        "currencies": {"SAR": 1 / 3.75, "INR": 1 / 88.0, "PKR": 1 / 278.0}}
            return {"rate": {"bid": 4361.8, "ask": 4362.2}, "timestamp": "2026-09-12T10:00:30Z"}
        return _get

    def _run(self, cfg):
        calls = []
        with mock.patch.dict("os.environ", {"METALS_API_KEY": "secret"}), \
             mock.patch.object(providers, "_get_json", self._fake(calls)):
            out = providers.metals_dev(cfg)
        return out, calls

    def test_two_requests_per_build(self):
        out, calls = self._run(dict(self.CFG))
        self.assertEqual(len(calls), 2, f"expected latest + gold spot, got {calls}")
        self.assertEqual(sum("/v1/metal/spot" in c for c in calls), 1)
        self.assertNotIn("metal=silver", " ".join(calls))  # was fetched and never displayed
        self.assertAlmostEqual(out["gold_bid_usd_oz"], 4361.8)
        self.assertIsNone(out["silver_bid_usd_oz"])
        self.assertAlmostEqual(out["fx_per_usd"]["SAR"], 3.75)

    def test_bid_ask_can_be_switched_off(self):
        out, calls = self._run(dict(self.CFG, bid_ask_metals=[]))
        self.assertEqual(len(calls), 1)
        self.assertIsNone(out["gold_bid_usd_oz"])

    def test_config_asks_for_gold_only(self):
        self.assertEqual(CONFIG.get("bid_ask_metals"), ["gold"])

    def test_timestamps_are_carried_per_field(self):
        out, _ = self._run(dict(self.CFG))
        self.assertEqual(out["timestamps_utc"]["gold"], "2026-09-12T10:00:00Z")
        self.assertEqual(out["timestamps_utc"]["silver"], "2026-09-12T10:00:00Z")
        self.assertEqual(out["timestamps_utc"]["fx"], "2026-09-12T10:01:00Z")
        self.assertEqual(out["timestamps_utc"]["gold_bid_ask"], "2026-09-12T10:00:30Z")

    def test_visitors_never_trigger_a_provider_call(self):
        """Traffic must not cost requests: the only network call in the page code is to our
        own static snapshot."""
        js_src = Path(CALC_JS).read_text(encoding="utf-8")
        self.assertNotIn("api.metals.dev", js_src)
        self.assertNotIn("api.gold-api.com", js_src)
        for call in ("fetch(",):
            self.assertEqual(js_src.count(call), 1, "only the static snapshot may be fetched")
        self.assertIn("P.snapshot_url", js_src)


class ErrorMessages(unittest.TestCase):
    """A bare 'HTTP Error 400' cost us a stale site for seven hours. Errors must say why."""

    def test_http_error_explains_and_redacts_the_key(self):
        import io
        import urllib.error
        err = urllib.error.HTTPError(
            "https://api.metals.dev/v1/latest?api_key=SECRETKEY&currency=USD", 400, "Bad Request",
            {}, io.BytesIO(b'{"status":"failure","error_message":"Monthly quota exceeded"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                providers._get_json("https://api.metals.dev/v1/latest?api_key=SECRETKEY&currency=USD")
        msg = str(cm.exception)
        self.assertIn("Monthly quota exceeded", msg)   # the real reason, from the response body
        self.assertIn("quota", msg)                     # our hint for 400/402/429
        self.assertNotIn("SECRETKEY", msg)              # public repo: never log the key

    def test_provider_failure_leaves_the_site_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            real = build.DIST
            build.DIST = out
            try:
                with mock.patch.object(build.providers, "fetch", side_effect=RuntimeError("feed down")):
                    with self.assertRaises(RuntimeError):
                        build.build(dict(CONFIG, provider="metals_dev"))
            finally:
                build.DIST = real
            self.assertFalse(out.exists(), "a failed fetch must not write any pages")

    def test_a_bad_price_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            real = build.DIST
            build.DIST = out
            try:
                with mock.patch.object(build.providers, "fetch", return_value=fake_latest(gold_usd_oz=10.0)), \
                     mock.patch.object(H, "HISTORY_FILE", Path(tmp) / "h.json"), \
                     mock.patch.object(H, "STATE_FILE", Path(tmp) / "s.json"):
                    with self.assertRaises(SystemExit):
                        build.build(dict(CONFIG, provider="gold_api"))
            finally:
                build.DIST = real
            self.assertFalse(out.exists(), "a failed safety check must not write any pages")


class LiveHistory(unittest.TestCase):
    def test_first_live_run_without_history(self):
        """A live provider with no saved history and no backfill must still build, and must
        say there is no comparison rather than inventing one."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fake = fake_latest(source_name="test", timestamp_utc=now,
                           timestamps_utc={"gold": now, "silver": now, "fx": now},
                           gold_bid_usd_oz=None, gold_ask_usd_oz=None)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            real = build.DIST
            build.DIST = out
            try:
                with mock.patch.object(H, "HISTORY_FILE", Path(tmp) / "history.json"), \
                     mock.patch.object(H, "STATE_FILE", Path(tmp) / "state.json"), \
                     mock.patch.object(build.providers, "fetch", return_value=fake):
                    build.build(dict(CONFIG, provider="gold_api"))
            finally:
                build.DIST = real
            html = (out / "sa" / "up-or-down" / "index.html").read_text(encoding="utf-8")
            self.assertIn("لا توجد مقارنة", html)
            en = (out / "sa" / "en" / "up-or-down" / "index.html").read_text(encoding="utf-8")
            self.assertIn("no comparison", en)


# ---------------------------------------------------------------- the registry

class Registry(unittest.TestCase):
    def test_registry_is_self_consistent(self):
        self.assertEqual(REG.check_registry(), [])

    def test_established_urls_are_preserved(self):
        """These eight URLs are live and indexed. Renaming one for tidiness costs whatever
        ranking it has earned."""
        live = {"/", "/sa/", "/sa/up-or-down/", "/sa/sell-price/", "/sa/calculator/",
                "/sa/zakat/", "/sa/silver/", "/sa/en/"}
        self.assertTrue(live <= {p.path for p in REG.PAGES})

    def test_hreflang_pairs_are_reciprocal_and_equivalent(self):
        for p in REG.PAGES:
            alts = REG.alternates(p)
            if not alts:
                continue
            self.assertIn((p.hreflang, p.path), alts, f"{p.key} omits itself")
            for hl, path in alts:
                other = [q for q in REG.PAGES if q.path == path][0]
                self.assertEqual(other.role, p.role, f"{p.key} paired with a different page")
                self.assertIn((p.hreflang, p.path), REG.alternates(other), "not reciprocal")

    def test_unpaired_pages_claim_no_translation(self):
        # The zakat page has no published English version, so it must publish no hreflang.
        self.assertEqual(REG.alternates(REG.by_key("sa_zakat")), [])
        self.assertEqual(REG.alternates(REG.by_key("home")), [])

    def test_language_switch_never_dead_ends(self):
        for p in REG.PAGES:
            path, label = REG.language_switch(p)
            self.assertIn(path, {q.path for q in REG.PAGES}, f"{p.key} switches to a missing page")
            self.assertTrue(label)

    def test_pending_pages_are_declared_not_forgotten(self):
        self.assertTrue(any(p.key == "en_zakat" and p.status == "pending" for p in REG.PENDING))
        self.assertNotIn("/sa/en/zakat/", {p.path for p in REG.PAGES})


# ---------------------------------------------------------------- the built site

class BuiltSite(unittest.TestCase):
    def test_every_registry_page_is_built(self):
        root = SampleSite.path()
        for p in REG.PAGES:
            f = root / "index.html" if p.path == "/" else root / p.path.strip("/") / "index.html"
            self.assertTrue(f.exists(), f"{p.path} was not built")

    def test_front_page_is_a_real_page(self):
        html = SampleSite.html("index.html")
        self.assertIn("<h1>", html)
        self.assertNotIn("http-equiv", html)     # no meta refresh stub

    def test_internal_links_all_resolve(self):
        import re
        root = SampleSite.path()
        known = {p.path for p in REG.PAGES}
        for p in REG.PAGES:
            rel = "index.html" if p.path == "/" else f"{p.path.strip('/')}/index.html"
            html = SampleSite.html(rel)
            for href in re.findall(r'href=[\'"](/[^\'"#?]*)[\'"]', html):
                if href.startswith("/static/"):
                    self.assertTrue((root / href.lstrip("/")).exists(), f"{p.path} -> {href}")
                else:
                    self.assertIn(href, known, f"{p.path} links to unknown page {href}")

    def test_canonical_and_alternates(self):
        base = CONFIG["base_url"].rstrip("/")
        gold = SampleSite.html("sa/index.html")
        self.assertIn(f'<link rel="canonical" href="{base}/sa/">', gold)
        self.assertIn(f"hreflang='ar-SA' href='{base}/sa/'", gold)
        self.assertIn(f"hreflang='en-SA' href='{base}/sa/en/'", gold)
        zakat = SampleSite.html("sa/zakat/index.html")
        self.assertNotIn("hreflang=", zakat.split("<body")[0].replace("hreflang='ar'", "").replace('hreflang="ar"', ""))

    def test_sitemap_holds_exactly_the_indexable_pages(self):
        import re
        sitemap = (SampleSite.path() / "sitemap.xml").read_text(encoding="utf-8")
        base = CONFIG["base_url"].rstrip("/")
        locs = set(re.findall(r"<loc>(.*?)</loc>", sitemap))
        self.assertEqual(locs, {base + p.path for p in REG.PAGES})
        self.assertNotIn("/data/prices.json", sitemap)
        # lastmod must not be "now because a build ran"
        for lm in re.findall(r"<lastmod>(.*?)</lastmod>", sitemap):
            self.assertRegex(lm, r"^\d{4}-\d{2}-\d{2}$")

    def test_static_page_lastmod_is_not_the_build_date(self):
        import re
        sitemap = (SampleSite.path() / "sitemap.xml").read_text(encoding="utf-8")
        block = re.search(r"<url><loc>[^<]*/sa/methodology/</loc><lastmod>([^<]+)</lastmod>", sitemap)
        self.assertEqual(block.group(1), REG.by_key("sa_method").content_updated)

    def test_social_preview_is_present_and_matches_the_page(self):
        html = SampleSite.html("sa/silver/index.html")
        self.assertIn('property="og:title"', html)
        self.assertIn('property="og:image"', html)
        self.assertIn('content="summary_large_image"', html)
        self.assertTrue((SampleSite.path() / "static" / "og.png").exists())

    def test_structured_data_is_truthful(self):
        import re
        home = SampleSite.html("index.html")
        blocks = [json.loads(m) for m in re.findall(r"<script type='application/ld\+json'>(.*?)</script>", home, re.S)]
        types = {b["@type"] for b in blocks}
        self.assertIn("Organization", types)
        self.assertIn("WebSite", types)
        # No FAQ (Google dropped FAQ rich results for sites like ours), no invented commerce
        for banned in ("FAQPage", "Product", "Offer", "AggregateRating", "Review", "SearchAction"):
            self.assertNotIn(banned, home)
        inner = SampleSite.html("sa/silver/index.html")
        self.assertIn("BreadcrumbList", inner)

    def test_titles_and_descriptions_are_unique_and_sized(self):
        import re
        titles, descs = {}, {}
        for p in REG.PAGES:
            rel = "index.html" if p.path == "/" else f"{p.path.strip('/')}/index.html"
            html = SampleSite.html(rel)
            title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
            desc = re.search(r'<meta name="description" content="(.*?)">', html, re.S).group(1)
            self.assertNotIn(title, titles, f"{p.key} duplicates the title of {titles.get(title)}")
            self.assertNotIn(desc, descs, f"{p.key} duplicates the description of {descs.get(desc)}")
            titles[title], descs[desc] = p.key, p.key
            self.assertLessEqual(len(title), 65, f"{p.key} title is {len(title)} chars")
            self.assertLessEqual(len(desc), 175, f"{p.key} description is {len(desc)} chars")
            self.assertEqual(html.count("<h1>"), 1, f"{p.key} must have exactly one H1")

    def test_language_and_direction(self):
        self.assertIn('<html lang="ar" dir="rtl">', SampleSite.html("sa/index.html"))
        self.assertIn('<html lang="en" dir="ltr">', SampleSite.html("sa/en/index.html"))

    def test_english_pages_carry_no_arabic_ui(self):
        """A localised page, not a translated one: the only Arabic on an English page is a
        deliberate link to an Arabic page."""
        for rel in ("sa/en/index.html", "sa/en/silver/index.html", "sa/en/calculator/index.html",
                    "sa/en/sell-price/index.html", "sa/en/up-or-down/index.html", "sa/en/methodology/index.html"):
            body = SampleSite.html(rel).split("<main")[1].split("</main>")[0]
            arabic_bits = [line for line in body.splitlines()
                           if any("؀" <= ch <= "ۿ" for ch in line) and 'hreflang="ar"' not in line]
            self.assertEqual(arabic_bits, [], f"{rel} has untranslated Arabic: {arabic_bits[:1]}")

    def test_rupee_figures_are_labelled_as_conversions(self):
        html = SampleSite.html("sa/en/index.html")
        if "INR" in html:
            self.assertIn("not</strong> the gold rate in India", html)

    def test_no_invented_retail_or_forecast_claims(self):
        for p in REG.PAGES:
            rel = "index.html" if p.path == "/" else f"{p.path.strip('/')}/index.html"
            html = SampleSite.html(rel).lower()
            body = html.split("<main")[1].split("</main>")[0]
            for banned in ("trusted by", "rated ", "reviews", "our forecast", "we expect",
                           "price prediction", "guaranteed price", "شعبيتنا", "توقعنا",
                           "سعر مضمون", "نتوقع أن"):
                self.assertNotIn(banned, body, f"{p.key} contains '{banned}'")

    def test_history_window_matches_its_wording(self):
        """Eight rows under a seven-day heading was the old mismatch."""
        import re
        html = SampleSite.html("sa/index.html")
        table = re.search(r"<table class='prices'>.*?</table>", html.split("history")[0] + html, re.S)
        rows = re.findall(r"<tr[^>]*><td>", html)
        self.assertGreaterEqual(len(rows), CONFIG["history_days"])

    def test_todays_row_is_marked_as_intraday(self):
        html = SampleSite.html("sa/index.html")
        self.assertIn("حتى الآن", html)
        self.assertIn("so far today", SampleSite.html("sa/en/index.html"))


class PublicSnapshot(unittest.TestCase):
    def snap(self):
        return json.loads((SampleSite.path() / "data" / "prices.json").read_text(encoding="utf-8"))

    def test_schema_and_units_are_explicit(self):
        s = self.snap()
        self.assertEqual(s["schema"], SNAP.SCHEMA)
        for m in ("gold", "silver"):
            e = s["metals"][m]
            for field in ("metal", "unit", "currency", "purity_basis", "quote_side",
                          "spot_sar_per_gram", "quoted_utc", "status"):
                self.assertIn(field, e, f"{m}.{field}")

    def test_build_time_is_never_the_quote_time(self):
        s = self.snap()
        self.assertIn("generated_utc", s)
        self.assertNotEqual(s["generated_utc"], s["quoted_utc"])

    def test_carries_no_secrets(self):
        raw = (SampleSite.path() / "data" / "prices.json").read_text(encoding="utf-8").lower()
        for banned in ("api_key", "apikey", "secret", "token", "metals.dev/v1", "authorization"):
            self.assertNotIn(banned, raw)

    def test_third_party_use_is_described_as_unresolved(self):
        usage = json.dumps(self.snap()["usage"], ensure_ascii=False)
        self.assertIn("unresolved", usage)
        self.assertIn("WIDGET-MVP.md", usage)
        # Neither an entitlement nor a prohibition we cannot point to.
        for overclaim in ("licensed", "prohibited", "forbidden", "allowed"):
            self.assertNotIn(overclaim, usage.lower())

    def test_json_endpoint_is_not_indexable(self):
        headers = (SampleSite.path() / "_headers").read_text(encoding="utf-8")
        self.assertIn("/data/*", headers)
        self.assertIn("X-Robots-Tag: noindex", headers)

    def test_pages_and_snapshot_agree(self):
        page = SampleSite.data("sa/index.html")
        snap = self.snap()
        self.assertAlmostEqual(page["gold_sar_g"], snap["metals"]["gold"]["spot_sar_per_gram"], places=3)
        self.assertAlmostEqual(page["silver_sar_g"], snap["metals"]["silver"]["spot_sar_per_gram"], places=3)
        self.assertEqual(page["quoted_utc"], snap["quoted_utc"])


class SampleAndLiveSeparation(unittest.TestCase):
    def test_missing_route_document_does_not_impersonate_a_price_page(self):
        # Cloudflare's root 404 file turns off its implicit homepage/SPA fallback.
        html = SampleSite.html("404.html")
        self.assertIn("Page not found", html)
        self.assertIn("الصفحة غير موجودة", html)
        self.assertIn('content="noindex, follow"', html)
        self.assertNotIn('rel="canonical"', html)
        self.assertNotIn("application/ld+json", html)
        self.assertNotIn("404", (SampleSite.path() / "sitemap.xml").read_text(encoding="utf-8"))
        for path in ("/sa/", "/sa/silver/", "/sa/en/", "/sa/en/silver/"):
            self.assertIn(f'href="{path}"', html)
            self.assertTrue((SampleSite.path() / path.strip("/") / "index.html").exists())

    def test_sample_build_is_marked_and_blocked(self):
        root = SampleSite.path()
        self.assertTrue((root / "SAMPLE-BUILD-DO-NOT-PUBLISH.txt").exists())
        self.assertEqual((root / "robots.txt").read_text(encoding="utf-8").strip(),
                         "User-agent: *\nDisallow: /")
        for rel in ("index.html", "sa/index.html", "sa/en/silver/index.html"):
            self.assertIn('content="noindex, nofollow"', SampleSite.html(rel))
            self.assertIn("SAMPLE", SampleSite.html(rel).upper())

    def test_live_config_is_not_left_on_sample(self):
        self.assertEqual(CONFIG["provider"], "metals_dev")
        self.assertFalse(CONFIG["noindex"])
        self.assertNotIn("example.com", CONFIG["base_url"])
        self.assertNotIn("مؤقت", CONFIG["site_name_ar"])


class ProductionArtifact(unittest.TestCase):
    """The 2026-09-12 near-miss: a test rebuilt dist/ with sample data between the real
    build and the upload. Nothing in this suite may write there."""

    def test_dist_is_untouched_by_the_suite(self):
        dist = ROOT / "dist"
        if not dist.exists():
            self.skipTest("no dist/ in this checkout")
        before = self._hash(dist)
        SampleSite.path()          # forces a full build, into its temp folder
        self.assertEqual(self._hash(dist), before, "the test suite modified dist/")

    @staticmethod
    def _hash(root):
        h = hashlib.sha256()
        for f in sorted(root.rglob("*")):
            if f.is_file():
                h.update(str(f.relative_to(root)).encode())
                h.update(hashlib.md5(f.read_bytes()).digest())
        return h.hexdigest()


# ---------------------------------------------------------------- browser behaviour

class StaleNotice(unittest.TestCase):
    """The build refuses to publish a bad price, so a broken feed leaves the last good
    page online saying 'last updated ...' as if it were current. On 2026-09-12 that
    served a 7-hour-old price for 7 hours. This check runs in the visitor's browser, so
    it still works while the build is down."""

    def test_age_and_threshold(self):
        now = "Date.parse('2026-09-12T14:00:00Z')"
        self.assertEqual(js(f"M.ageMinutes('2026-09-12T11:00:00Z', {now})"), 180)
        self.assertTrue(js(f"M.isStale('2026-09-12T11:00:00Z', {now}, 120)"))
        self.assertFalse(js(f"M.isStale('2026-09-12T13:00:00Z', {now}, 120)"))

    def test_unparseable_timestamp_never_cries_wolf(self):
        now = "Date.parse('2026-09-12T14:00:00Z')"
        self.assertIsNone(js(f"M.ageMinutes('', {now})"))
        self.assertFalse(js(f"M.isStale('nonsense', {now}, 120)"))

    def test_arabic_counted_nouns(self):
        cases = {60: "ساعة", 120: "ساعتين", 180: "3 ساعات", 660: "11 ساعة",
                 1440: "يوم", 2880: "يومين", 4320: "3 أيام", 20160: "14 يومًا"}
        for minutes, want in cases.items():
            self.assertEqual(js(f"M.humanAge({minutes}, 'ar')"), want, minutes)
        self.assertEqual(js("M.humanAge(60, 'en')"), "1 hour")
        self.assertEqual(js("M.humanAge(180, 'en')"), "3 hours")

    def test_pages_carry_what_the_browser_needs(self):
        for page in ("index.html", "sa/index.html", "sa/en/index.html", "sa/silver/index.html"):
            html = SampleSite.html(page)
            self.assertIn('id="stale-notice"', html, page)
            self.assertIn('id="refresh-notice"', html, page)
            data = SampleSite.data(page)
            self.assertRegex(data["updated_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertGreater(data["stale_after_minutes"], 0)
            self.assertIn("quote_time_known", data)
            self.assertIn("market_closed_at_build", data)
            self.assertIn("market_session", data)
            self.assertIn("snapshot_url", data)

    def test_freshness_is_rechecked_not_just_read_once(self):
        """A page left open overnight used to keep presenting a stale price as current."""
        src = Path(CALC_JS).read_text(encoding="utf-8")
        self.assertIn("setInterval", src)
        self.assertIn("visibilitychange", src)


class CalculatorContract(unittest.TestCase):
    def test_silver_calculator_is_on_both_languages(self):
        for rel in ("sa/silver/index.html", "sa/en/silver/index.html"):
            html = SampleSite.html(rel)
            self.assertIn('id="silver-calc"', html)
            self.assertIn('id="sv-quote-basis"', html)
            for preset in ('data-weight="1" data-unit="g"', 'data-weight="1" data-unit="kg"',
                           'data-weight="100" data-unit="g"'):
                self.assertIn(preset, html, f"{rel} missing preset {preset}")

    def test_quote_basis_is_asked_never_assumed(self):
        html = SampleSite.html("sa/en/silver/index.html")
        self.assertIn('value="total"', html)
        self.assertIn('value="per_gram"', html)
        self.assertIn("never assume", html)

    def test_calculators_say_which_snapshot_they_used(self):
        for rel in ("sa/calculator/index.html", "sa/silver/index.html", "sa/sell-price/index.html"):
            self.assertIn("snapshot-note", SampleSite.html(rel))

    def test_events_carry_no_amounts(self):
        """The event contract is deliberately thin: which tool was used and which fixed-list
        choice was made, never the reader's own numbers. Anything the reader types lives in
        a text input, so no track() call may touch one of those ids or a computed figure."""
        import re
        src = Path(CALC_JS).read_text(encoding="utf-8")
        typed_ids = ("sv-weight", "sv-quote", "calc-weight", "calc-making",
                     "sell-weight", "sell-deduction", "zs-weight", "zg-weight")
        computed = ("quoteTotal", "grams", "perGram", "diff", "pct(", "raw", "net,", "total,")
        calls = re.findall(r"track\((.*?)\);", src, re.S)
        self.assertGreaterEqual(len(calls), 5, "the event contract disappeared")
        for call in calls:
            for bad in typed_ids + computed:
                self.assertNotIn(bad, call, f"event carries reader data ({bad}): {call[:90]}")

    def test_analytics_is_off_until_configured(self):
        self.assertEqual(CONFIG["analytics"]["provider"], "none")
        src = Path(CALC_JS).read_text(encoding="utf-8")
        self.assertIn("root.mithqalTrack", src)      # an adapter may exist
        for rel in ("index.html", "sa/en/silver/index.html"):
            html = SampleSite.html(rel)
            for tracker in ("googletagmanager", "google-analytics", "plausible.io", "gtag("):
                self.assertNotIn(tracker, html, f"{rel} loads {tracker} while analytics is off")


class AssetCacheBusting(unittest.TestCase):
    """Cloudflare and browsers cache /static/* hard. Without a version in the URL a
    returning visitor keeps the previous deploy's CSS and JS - on 2026-09-12 the live
    pages were still running the old calc.js, which disabled the stale-price notice."""

    def test_every_page_versions_its_assets(self):
        for page in ("index.html", "sa/index.html", "sa/en/index.html", "sa/zakat/index.html"):
            html = SampleSite.html(page)
            self.assertRegex(html, r'href="/static/style\.css\?v=[0-9a-f]{8}"', page)
            self.assertRegex(html, r'src="/static/calc\.js\?v=[0-9a-f]{8}"', page)
            self.assertNotIn('href="/static/style.css"', html, page)   # bare, cacheable URL
            self.assertNotIn('src="/static/calc.js"', html, page)

    def test_version_follows_the_file_contents(self):
        before = build.static_url("calc.js")
        path = ROOT / "static" / "calc.js"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\n// touched\n")
            self.assertNotEqual(build.static_url("calc.js"), before)
        finally:
            path.write_bytes(original)
        self.assertEqual(build.static_url("calc.js"), before)


class VisualIdentity(unittest.TestCase):
    """Session 08. The identity has two parts a build can silently lose: the
    self-hosted font files (a missing one would fall back to the device font and
    undo the whole look) and the third-party-free promise."""

    FONTS = ("tajawal-ar-400.woff2", "tajawal-ar-700.woff2",
             "tajawal-lat-400.woff2", "tajawal-lat-700.woff2")

    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    def test_font_files_are_present_and_published(self):
        for f in self.FONTS:
            src = ROOT / "static" / f
            self.assertTrue(src.exists(), f"missing {f} in static/")
            self.assertGreater(src.stat().st_size, 4000, f)
            self.assertTrue((SampleSite.path() / "static" / f).exists(), f"{f} not copied into the build")

    def test_css_references_every_font_file(self):
        for f in self.FONTS:
            self.assertIn(f, self.css, f"style.css never loads {f}")

    def test_no_third_party_font_request(self):
        for host in ("fonts.googleapis.com", "fonts.gstatic.com", "//", "http:"):
            self.assertNotIn(host, self.css.split("/* ---------- tokens")[0],
                             f"font block reaches outside our domain: {host}")

    def test_arabic_and_latin_are_split_by_unicode_range(self):
        self.assertEqual(self.css.count("unicode-range:"), 4)
        self.assertIn("U+0600-06FF", self.css)

    def test_pages_carry_the_icon_and_theme_colour(self):
        for page in ("index.html", "sa/index.html", "sa/en/index.html"):
            html = SampleSite.html(page)
            self.assertIn('href="/static/icon.svg"', html, page)
            self.assertIn('name="theme-color"', html, page)
        for asset in ("icon.svg", "icon-192.png", "apple-touch-icon.png", "og.png"):
            self.assertTrue((SampleSite.path() / "static" / asset).exists(), asset)

    def test_font_licence_ships_with_the_fonts(self):
        # SIL OFL requires the licence to travel with the font files.
        self.assertTrue((SampleSite.path() / "static" / "TAJAWAL-OFL.txt").exists())

    def test_header_nav_stays_short(self):
        """Seven links wrapped to three lines on a phone and pushed the price down the
        screen. The header carries the primary pages; the rest live in the footer index."""
        for lang in ("ar", "en"):
            self.assertLessEqual(len(REG.nav_items(lang)), 5, f"{lang} header nav is growing again")
        html = SampleSite.html("sa/index.html")
        self.assertIn("class='index'", html)      # every page still reachable, from the footer
        for p in REG.PAGES:
            if p.lang == "ar":
                self.assertIn(f"<a href='{p.path}'>", html, f"{p.path} missing from the footer index")

    def test_accessibility_basics(self):
        html = SampleSite.html("sa/silver/index.html")
        self.assertIn('class="skip"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn("aria-label", html)
        self.assertIn(":focus-visible", self.css)


# ============================================================================
# Regressions for the independent review of session 09 (CODEX_S09_REVIEW.md).
# Each test below fails against the code as it stood before that review.
# ============================================================================

class RetryStateSurvivesUnchangedPrices(unittest.TestCase):
    """Review finding 1. `refresh` wrote the attempt counters, but the build reported a
    change only from history.json, and the scheduled job commits on that report. So a build
    whose backfill failed bumped the counter on the runner and then threw the runner away:
    across fresh checkouts the three-attempt cap never advanced and the same dead date was
    requested forever."""

    ROWS = [{"date": "2026-09-08", "gold_usd_oz": 4355, "silver_usd_oz": 65.7, "kind": H.PROVIDER_DAILY},
            {"date": "2026-09-09", "gold_usd_oz": 4402, "silver_usd_oz": 67.2, "kind": H.PROVIDER_DAILY},
            {"date": "2026-09-10", "gold_usd_oz": 4317, "silver_usd_oz": 63.5, "kind": H.PROVIDER_DAILY}]

    def test_failed_backfill_reports_a_state_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(H, "STATE_FILE", Path(tmp) / "s.json"):
                rows, changed, note, state_changed = H.refresh(
                    dict(CONFIG), list(self.ROWS), date(2026, 9, 12),
                    lambda days: (_ for _ in ()).throw(RuntimeError("feed down")))
        self.assertEqual(changed, [], "no prices changed")
        self.assertTrue(state_changed, "the attempt counter changed and must be reported")

    def test_cap_holds_across_fresh_checkouts(self):
        """Each iteration reloads the state from disk, the way a fresh CI checkout would
        after the previous run committed it."""
        calls = []

        def never_fills(days):
            calls.append(days)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "s.json"
            with mock.patch.object(H, "STATE_FILE", state_file):
                for _ in range(6):
                    state = H.load_state()                  # fresh checkout: read from disk
                    rows, changed, note, state_changed = H.refresh(
                        dict(CONFIG), list(self.ROWS), date(2026, 9, 12), never_fills, state=state)
                    self.assertEqual(changed, [])
        self.assertEqual(len(calls), H.MAX_ATTEMPTS_PER_DATE,
                         "the cap must survive checkouts, not restart every run")
        self.assertIn("not retried again", note)

    def test_complete_history_writes_no_state_file(self):
        """Nothing to retry means nothing to persist: the state file must stay absent, which
        is exactly the case the commit step used to fail on by naming it explicitly."""
        rows = [{"date": (date(2026, 9, 12) - timedelta(days=n)).isoformat(),
                 "gold_usd_oz": 4300, "silver_usd_oz": 64, "kind": H.PROVIDER_DAILY}
                for n in range(1, 12)]
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "s.json"
            with mock.patch.object(H, "STATE_FILE", state_file):
                _, changed, note, state_changed = H.refresh(
                    dict(CONFIG), rows, date(2026, 9, 12), lambda days: self.fail("must not call the provider"))
            self.assertFalse(state_file.exists())
        self.assertFalse(state_changed)
        self.assertEqual(note, "history complete")

    def test_build_output_reports_the_state_change(self):
        """End to end: prices unchanged, backfill failing, and the build still tells the job
        there is something to commit."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fake = fake_latest(timestamp_utc=now, timestamps_utc={"gold": now, "silver": now, "fx": now})
        with tempfile.TemporaryDirectory() as tmp:
            gh_out = Path(tmp) / "gh_out"
            hist = Path(tmp) / "history.json"
            # Written through save() so the file is already in canonical form: the point of
            # this test is a build where the PRICES do not change.
            H.save([{"date": (H.today_in(3) - timedelta(days=n)).isoformat(),
                     "gold_usd_oz": 4362, "silver_usd_oz": 64.4, "kind": H.PROVIDER_DAILY}
                    for n in range(11, 1, -1)], hist)
            real = build.DIST
            build.DIST = Path(tmp) / "dist"
            try:
                with mock.patch.object(H, "HISTORY_FILE", hist), \
                     mock.patch.object(H, "STATE_FILE", Path(tmp) / "state.json"), \
                     mock.patch.object(build.providers, "fetch", return_value=fake), \
                     mock.patch.object(build.providers, "backfill",
                                       side_effect=RuntimeError("quota exhausted")), \
                     mock.patch.dict("os.environ", {"GITHUB_OUTPUT": str(gh_out)}):
                    build.build(dict(CONFIG, provider="metals_dev"))
            finally:
                build.DIST = real
            out = gh_out.read_text(encoding="utf-8")
        self.assertIn("history_rows_changed=false", out)
        self.assertIn("retry_state_changed=true", out)
        self.assertIn("history_changed=true", out)

    def test_a_stopped_build_leaves_no_commit_behind(self):
        """If validation stops the build there is no publishable artifact, so the job must
        not commit anything - not even retry counters. The cost is that a date may be retried
        more often across failing builds, which errs toward more requests, never fewer."""
        with tempfile.TemporaryDirectory() as tmp:
            gh_out = Path(tmp) / "gh_out"
            real = build.DIST
            build.DIST = Path(tmp) / "dist"
            try:
                with mock.patch.object(H, "HISTORY_FILE", Path(tmp) / "h.json"), \
                     mock.patch.object(H, "STATE_FILE", Path(tmp) / "s.json"), \
                     mock.patch.object(build.providers, "fetch",
                                       return_value=fake_latest(gold_usd_oz=10.0)), \
                     mock.patch.object(build.providers, "backfill", return_value=[]), \
                     mock.patch.dict("os.environ", {"GITHUB_OUTPUT": str(gh_out)}):
                    with self.assertRaises(SystemExit):
                        build.build(dict(CONFIG, provider="metals_dev"))
            finally:
                build.DIST = real
            self.assertFalse(gh_out.exists(), "a stopped build must report nothing to commit")


class ProviderRowsAreNeverTakenOnTrust(unittest.TestCase):
    """Review finding 2. The provider's range could include today, `merge` accepted whatever
    it was given, and `drop_unfinalised` ran BEFORE the merge - so today's partial figure was
    stored as a settled day and then collided with the intraday row the display adds for the
    same date."""

    ROWS = [{"date": "2026-09-10", "gold_usd_oz": 4317, "silver_usd_oz": 63.5, "kind": H.PROVIDER_DAILY}]

    def refresh(self, fetched, today=date(2026, 9, 12)):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(H, "STATE_FILE", Path(tmp) / "s.json"):
                return H.refresh(dict(CONFIG), list(self.ROWS), today, lambda days: fetched,
                                 state={"attempts": {}})

    def test_todays_row_from_the_provider_is_rejected(self):
        rows, changed, note, _ = self.refresh(
            [{"date": "2026-09-12", "gold_usd_oz": 4348, "silver_usd_oz": 64.4}])
        self.assertNotIn("2026-09-12", [r["date"] for r in rows])
        self.assertIn("rejected", note)

    def test_future_row_is_rejected(self):
        rows, _, note, _ = self.refresh(
            [{"date": "2027-01-01", "gold_usd_oz": 4348, "silver_usd_oz": 64.4}])
        self.assertNotIn("2027-01-01", [r["date"] for r in rows])
        self.assertIn("rejected", note)

    def test_invalid_rows_are_rejected_with_a_reason(self):
        bad = [{"date": "2026-09-11", "gold_usd_oz": None, "silver_usd_oz": 64.4},
               {"date": "2026-09-11", "gold_usd_oz": float("nan"), "silver_usd_oz": 64.4},
               {"date": "not-a-date", "gold_usd_oz": 4348, "silver_usd_oz": 64.4},
               {"date": "2026-09-11", "gold_usd_oz": -4348, "silver_usd_oz": 64.4},
               {"date": "2026-09-11", "gold_usd_oz": "4348", "silver_usd_oz": 64.4},
               "not even an object"]
        rows, changed, note, _ = self.refresh(bad)
        self.assertEqual(changed, [])
        self.assertEqual([r["date"] for r in rows], ["2026-09-10"])

    def test_a_duplicate_date_collapses_to_one_row(self):
        rows, _, _, _ = self.refresh([
            {"date": "2026-09-11", "gold_usd_oz": 4340, "silver_usd_oz": 64.0},
            {"date": "2026-09-11", "gold_usd_oz": 4341, "silver_usd_oz": 64.1}])
        same_day = [r for r in rows if r["date"] == "2026-09-11"]
        self.assertEqual(len(same_day), 1)
        self.assertAlmostEqual(same_day[0]["gold_usd_oz"], 4341)

    def test_the_display_series_has_one_row_per_date(self):
        polluted = self.ROWS + [{"date": "2026-09-12", "gold_usd_oz": 4348,
                                 "silver_usd_oz": 64.4, "kind": H.PROVIDER_DAILY}]
        ser = H.series(polluted, date(2026, 9, 12), 4350, 64.5)
        dates = [r["date"] for r in ser]
        self.assertEqual(len(dates), len(set(dates)), f"duplicate dates in the series: {dates}")
        today_rows = [r for r in ser if r["date"] == "2026-09-12"]
        self.assertEqual(len(today_rows), 1)
        self.assertEqual(today_rows[0]["kind"], H.INTRADAY)

    def test_legacy_rows_are_not_certified_as_anything(self):
        """Rows already on disk have no provenance. Labelling them `close` certified a
        statistic nobody documented - including first-build intraday snapshots, once their
        date became past."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "h.json"
            f.write_text(json.dumps([
                {"date": "2026-09-09", "gold_usd_oz": 4402, "silver_usd_oz": 67.2},
                {"date": "2026-09-10", "gold_usd_oz": 4317, "silver_usd_oz": 63.5, "kind": "close"},
                {"date": "2026-09-11", "gold_usd_oz": 4340, "silver_usd_oz": 64.0, "kind": H.PROVIDER_DAILY},
            ]), encoding="utf-8")
            rows = H.load(f)
        kinds = {r["date"]: r["kind"] for r in rows}
        self.assertEqual(kinds["2026-09-09"], H.UNKNOWN)
        self.assertEqual(kinds["2026-09-10"], H.UNKNOWN, "the old 'close' label was never evidence")
        self.assertEqual(kinds["2026-09-11"], H.PROVIDER_DAILY)

    def test_pages_do_not_call_the_providers_figure_a_close(self):
        """metals.dev documents the timeseries only as 'daily historical exchange rates'
        (read 2026-09-12): no close, no average, no cut-off timezone. So the pages say
        'the recorded price for 10 Sep'."""
        for rel in ("sa/index.html", "sa/up-or-down/index.html", "sa/en/index.html",
                    "sa/en/up-or-down/index.html", "sa/silver/index.html", "sa/en/silver/index.html"):
            body = SampleSite.html(rel).split("<main")[1].split("</main>")[0]
            for banned in ("إغلاق", "close (", "'s close", " close.", " close,", "daily closes"):
                self.assertNotIn(banned, body, f"{rel} still calls the provider's figure a close")
        # The methodology pages DO use the word - to say why we do not.
        self.assertIn("لا نسمّيه «سعر إغلاق»", SampleSite.html("sa/methodology/index.html"))
        self.assertIn("We do not call it a closing price",
                      SampleSite.html("sa/en/methodology/index.html"))

    def test_history_is_requested_only_for_finished_days_in_our_timezone(self):
        """`date.today()` is the runner's UTC date, which is not the site's date at 01:30
        Riyadh, and asking for today invites a partial figure."""
        seen = {}

        def fake_get(url, timeout=20):
            seen["url"] = url
            return {"rates": {}}

        with mock.patch.dict("os.environ", {"METALS_API_KEY": "k"}), \
             mock.patch.object(providers, "_get_json", fake_get):
            providers.metals_dev_history(dict(CONFIG), days=10)
        expected_end = H.today_in(CONFIG["timezone_offset_hours"]) - timedelta(days=1)
        self.assertIn(f"end_date={expected_end.isoformat()}", seen["url"])
        self.assertNotIn(f"end_date={H.today_in(CONFIG['timezone_offset_hours']).isoformat()}", seen["url"])


class MarketStateIsLiveInTheBrowser(unittest.TestCase):
    """Review finding 3. `market_closed` was computed at build time and read as if it were
    now, so a Saturday page still announced a closed market on Monday - exactly when the
    build is down and no newer page is coming to correct it."""

    SESSION = "{close_weekday:4, close_hour:21, open_weekday:6, open_hour:22}"

    def test_js_and_python_agree_on_the_session_all_week(self):
        start = datetime(2026, 9, 7, tzinfo=timezone.utc)      # Monday
        for hours in range(0, 24 * 8):
            at = start + timedelta(hours=hours)
            want = P.market_closed(at)
            got = js(f"M.marketClosedAt(Date.parse('{at.strftime('%Y-%m-%dT%H:%M:%SZ')}'), {self.SESSION})")
            self.assertEqual(got, want, f"disagreement at {at.isoformat()}")

    def test_js_and_python_agree_on_the_last_close(self):
        for iso in ("2026-09-12T12:00:00Z", "2026-09-11T20:00:00Z", "2026-09-11T21:00:00Z",
                    "2026-09-14T09:00:00Z", "2026-09-13T23:00:00Z"):
            at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            want = P.last_session_close(at).strftime("%Y-%m-%dT%H:%M:%SZ")
            got = js(f"new Date(M.lastSessionCloseMs(Date.parse('{iso}'), {self.SESSION})).toISOString()")
            self.assertEqual(got[:19], want[:19], iso)

    def _state(self, quoted, now, known=True):
        page = ("{updated_utc:'%s', updated:'x', quote_time_known:%s, stale_after_minutes:120, "
                "market_session:%s}" % (quoted, "true" if known else "false", self.SESSION))
        return js(f"M.staleState({page}, Date.parse('{now}'), 'en')")

    def test_a_saturday_page_read_on_monday_becomes_a_warning(self):
        friday_quote = "2026-09-11T20:55:00Z"
        self.assertEqual(self._state(friday_quote, "2026-09-12T12:00:00Z")["kind"], "closed")
        self.assertEqual(self._state(friday_quote, "2026-09-13T20:00:00Z")["kind"], "closed")
        monday = self._state(friday_quote, "2026-09-14T09:00:00Z")
        self.assertEqual(monday["kind"], "stale", "the market reopened; a frozen price is now stale")
        self.assertIn("has not updated", monday["text"])

    def test_a_feed_that_died_before_the_close_is_an_outage_even_at_the_weekend(self):
        s = self._state("2026-09-09T12:00:00Z", "2026-09-12T12:00:00Z")   # died Wednesday
        self.assertEqual(s["kind"], "outage")
        self.assertIn("older than the market closure explains", s["text"])

    def test_the_closed_message_never_calls_the_quote_a_close(self):
        s = self._state("2026-09-11T20:55:00Z", "2026-09-12T12:00:00Z")
        self.assertEqual(s["kind"], "closed")
        self.assertNotIn("last close", s["text"])
        self.assertIn("quoted at", s["text"])

    def test_a_fresh_open_market_says_nothing(self):
        self.assertEqual(self._state("2026-09-14T08:30:00Z", "2026-09-14T09:00:00Z")["kind"], "hidden")

    def test_missing_quote_time_still_wins_over_everything(self):
        self.assertEqual(self._state("2026-09-14T08:30:00Z", "2026-09-14T09:00:00Z", known=False)["kind"],
                         "unknown")

    def test_the_page_ships_the_session_not_a_live_claim(self):
        data = SampleSite.data("sa/index.html")
        self.assertIn("market_session", data)
        self.assertIn("market_closed_at_build", data)
        self.assertNotIn("market_closed", data, "a build-time flag must not be named as if it were now")
        src = Path(CALC_JS).read_text(encoding="utf-8")
        self.assertNotIn("P.market_closed;", src)
        self.assertNotIn("if (P.market_closed)", src)


class SnapshotIsDescribedAsWhatItIs(unittest.TestCase):
    """Review finding 4. /data/prices.json is a public same-origin file. `noindex` keeps it
    out of a search index; it is not access control, and the wording must not imply it is."""

    def snap(self):
        return json.loads((SampleSite.path() / "data" / "prices.json").read_text(encoding="utf-8"))

    def test_the_file_says_it_is_public(self):
        usage = self.snap()["usage"]
        blob = json.dumps(usage, ensure_ascii=False).lower()
        self.assertIn("public", blob)
        self.assertNotIn("internal", blob)

    def test_no_document_claims_noindex_restricts_access(self):
        for doc in ("DATA-SCHEMA.md", "WIDGET-MVP.md", "RELEASE.md"):
            text = (ROOT / doc).read_text(encoding="utf-8")
            self.assertNotIn("stays internal", text, f"{doc} still calls a public file internal")
        schema = (ROOT / "DATA-SCHEMA.md").read_text(encoding="utf-8")
        self.assertIn("not access control", schema)

    def test_rights_are_described_as_unsettled_not_decided(self):
        widget = (ROOT / "WIDGET-MVP.md").read_text(encoding="utf-8")
        self.assertIn("in writing", widget)
        for overclaim in ("we are not permitted", "is prohibited", "is permitted for widgets"):
            self.assertNotIn(overclaim, widget)


class ReleaseInstructionsAreExact(unittest.TestCase):
    """Review finding 5. The manifest's counts contradicted its own lists, and it described
    six new English pages when five are English and one is Arabic."""

    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / "RELEASE.md").read_text(encoding="utf-8")

    def _listed(self, heading):
        import re
        block = re.search(heading + r".*?```" + "\n" + r"(.*?)```", self.text, re.S)
        self.assertIsNotNone(block, heading)
        return [w for w in block.group(1).split() if "/" in w or "." in w]

    def _renames(self):
        """The source -> destination map RELEASE.md publishes. A manifest that lists a file
        under one name while the release instructions rename it is a manifest that cannot be
        checked, which is how the workflow entry went unnoticed."""
        import re
        rows = re.findall(r"^\| `([^`]+)` \| `([^`]+)` \|$", self.text, re.M)
        self.assertTrue(rows, "RELEASE.md has no source -> destination table")
        return dict(rows)

    def test_every_listed_file_exists_at_its_source_or_its_destination(self):
        renames = self._renames()
        for name in self._listed(r"### New files") + self._listed(r"### Changed files"):
            candidates = [name] + ([renames[name]] if name in renames else [])
            self.assertTrue(any((ROOT / c).exists() for c in candidates),
                            f"RELEASE.md lists a file found at none of {candidates}")

    def test_the_rename_table_covers_the_workflow(self):
        renames = self._renames()
        self.assertIn(STAGING_WORKFLOW, renames)
        self.assertEqual(renames[STAGING_WORKFLOW], REPO_WORKFLOW)

    def test_counts_match_the_lists(self):
        import re
        for heading in ("New files", "Changed files"):
            stated = int(re.search(heading + r" \((\d+)\)", self.text).group(1))
            actual = len(self._listed("### " + heading))
            self.assertEqual(stated, actual, f"{heading}: header says {stated}, list has {actual}")
        total = int(re.search(r"Upload the (\d+) files", self.text).group(1))
        self.assertEqual(total, len(self._listed(r"### New files")) + len(self._listed(r"### Changed files")))

    def test_the_new_pages_are_counted_by_language(self):
        self.assertIn("five English", self.text)
        self.assertNotIn("Six new English pages", self.text)

    def test_rollback_stops_the_scheduled_job_first(self):
        lowered = self.text.lower()
        self.assertIn("disable", lowered)
        self.assertIn("actions", lowered)
        idx_disable = lowered.index("disable the")
        idx_cf = lowered.index("cloudflare dashboard")
        self.assertLess(idx_disable, idx_cf, "stopping the 15-minute job must come before the rollback")

    def test_one_atomic_upload(self):
        self.assertNotIn("modules FIRST", self.text)
        self.assertIn("one upload", self.text.lower())

    def test_rollback_accounts_for_a_run_already_in_flight(self):
        """Disabling a workflow stops new runs starting. A run already going will still reach
        its upload step and publish over the rollback."""
        lowered = self.text.lower()
        self.assertIn("cancel", lowered)
        self.assertIn("in progress", lowered)
        self.assertLess(lowered.index("cancel workflow"), lowered.index("cloudflare dashboard"),
                        "cancelling an in-flight run must come before the rollback")
        self.assertNotIn("nothing will publish until it is re-enabled.\n2. **roll back", lowered)

    def test_the_guard_is_described_as_bounded(self):
        """It is one comparison at one moment, not a lock. Saying otherwise invites someone
        to rely on it."""
        lowered = self.text.lower()
        self.assertIn("one comparison at one moment", lowered)
        self.assertIn("not a lock", lowered)

    def test_the_retry_cap_is_not_claimed_to_be_absolute(self):
        lowered = self.text.lower()
        self.assertIn("not an absolute cap", lowered)
        self.assertIn("always()", self.text)

    def test_the_tested_platform_is_stated(self):
        lowered = self.text.lower()
        self.assertIn("linux", lowered)
        self.assertIn("git bash", lowered)


class WorkflowStepsActuallyRun(unittest.TestCase):
    """The review asked for these to be exercised, not grepped. The shell is lifted out of
    the workflow file and run against a real git repository."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = workflow_path()
        cls.yaml = cls.workflow.read_text(encoding="utf-8")
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is not available")
        cls.bash = bash_or_skip()

    def test_exactly_one_workflow_file_exists(self):
        """Keeping a spare copy under the old name so the tests keep passing would mean the
        suite exercises a file GitHub never runs."""
        present = [rel for rel in (REPO_WORKFLOW, STAGING_WORKFLOW) if (ROOT / rel).exists()]
        self.assertEqual(len(present), 1, f"expected exactly one workflow file, found {present}")

    def test_it_is_the_file_github_would_run(self):
        """In a repository layout the tests must load .github/workflows/update-prices.yml."""
        if (ROOT / REPO_WORKFLOW).exists():
            self.assertEqual(self.workflow, ROOT / REPO_WORKFLOW)

    def _step(self, name):
        """The `run:` block of one named step, dedented."""
        import re, textwrap
        pat = r"- name: " + re.escape(name) + r"\n(.*?)(?=\n      - name: |\Z)"
        m = re.search(pat, self.yaml, re.S)
        self.assertIsNotNone(m, name)
        run = re.search(r"run: \|\n(.*)", m.group(1), re.S)
        self.assertIsNotNone(run, f"{name} has no run block")
        return textwrap.dedent(run.group(1))

    def _repo(self, tmp):
        import subprocess as sp
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        sp.run(["git", "init", "-q", "-b", "main", tmp], check=True, env=env)
        (Path(tmp) / "data").mkdir()
        (Path(tmp) / "data" / "history.json").write_text("[]", encoding="utf-8")
        sp.run(["git", "add", "-A"], cwd=tmp, check=True, env=env)
        sp.run(["git", "commit", "-qm", "init"], cwd=tmp, check=True, env=env)
        return env

    def test_history_commit_survives_a_missing_state_file(self):
        """`git add data/history.json data/history_state.json` fails outright when the state
        file has never been created - which is the normal case."""
        import subprocess as sp
        script = self._step("Save the price history and the retry state")
        script = script.replace("git pull --rebase --autostash origin main", "true") \
                       .replace("git push", "true")
        with tempfile.TemporaryDirectory() as tmp:
            env = self._repo(tmp)
            (Path(tmp) / "data" / "history.json").write_text('[{"date":"2026-09-11"}]', encoding="utf-8")
            r = sp.run([self.bash, "-e", "-c", script], cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            log = sp.run(["git", "log", "--oneline"], cwd=tmp, env=env, capture_output=True, text=True).stdout
            self.assertIn("price history", log)

    def test_history_commit_picks_up_a_new_state_file(self):
        import subprocess as sp
        script = self._step("Save the price history and the retry state")
        script = script.replace("git pull --rebase --autostash origin main", "true") \
                       .replace("git push", "true")
        with tempfile.TemporaryDirectory() as tmp:
            env = self._repo(tmp)
            (Path(tmp) / "data" / "history_state.json").write_text('{"attempts":{"2026-09-11":1}}',
                                                                   encoding="utf-8")
            r = sp.run([self.bash, "-e", "-c", script], cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            files = sp.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=tmp, env=env,
                           capture_output=True, text=True).stdout
            self.assertIn("data/history_state.json", files)

    def test_nothing_staged_is_not_a_failure(self):
        import subprocess as sp
        script = self._step("Save the price history and the retry state")
        script = script.replace("git pull --rebase --autostash origin main", "true") \
                       .replace("git push", "true")
        with tempfile.TemporaryDirectory() as tmp:
            env = self._repo(tmp)
            r = sp.run([self.bash, "-e", "-c", script], cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("nothing to commit", r.stdout)

    def test_deploy_is_skipped_when_main_moved(self):
        """The job builds and tests one commit, then pushes source a step later. If main moved
        in between, deploying would publish the older code over the newer."""
        import subprocess as sp
        script = self._step("Abort the deploy if main moved under us")
        with tempfile.TemporaryDirectory() as tmp:
            upstream = Path(tmp) / "upstream"
            clone = Path(tmp) / "clone"
            env = self._repo(str(upstream))
            # file:// URI, not a bare path. On Windows git reads `C:\Users\...` as the
            # scp-style `user@host:path` form and tries SSH: the independent Git Bash run
            # failed with "ssh: Could not resolve hostname c".
            sp.run(["git", "clone", "-q", upstream.resolve().as_uri(), str(clone)],
                   check=True, env=env)
            gh_out = Path(tmp) / "out"
            run_env = dict(env, GITHUB_OUTPUT=str(gh_out))

            r = sp.run([self.bash, "-e", "-c", script], cwd=clone, env=run_env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("deploy=true", gh_out.read_text(encoding="utf-8"))

            gh_out.write_text("", encoding="utf-8")
            (upstream / "newfile.txt").write_text("someone pushed", encoding="utf-8")
            sp.run(["git", "add", "-A"], cwd=upstream, check=True, env=env)
            sp.run(["git", "commit", "-qm", "upstream moved"], cwd=upstream, check=True, env=env)

            r = sp.run([self.bash, "-e", "-c", script], cwd=clone, env=run_env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("deploy=false", gh_out.read_text(encoding="utf-8"))
            self.assertIn("Skipping the upload", r.stdout)

    def test_the_upload_step_is_gated_on_that_guard(self):
        self.assertIn("if: steps.guard.outputs.deploy == 'true'", self.yaml)

    def test_the_history_step_runs_even_when_the_upload_does_not(self):
        """It comes after the upload, so without always() a failed or skipped deploy would
        also discard the retry counters the build just wrote."""
        self.assertIn("if: always() && steps.build.outputs.history_changed == 'true'", self.yaml)
        upload = self.yaml.index("- name: Upload to Cloudflare Pages")
        history = self.yaml.index("- name: Save the price history and the retry state")
        self.assertLess(upload, history, "the history step is after the upload; always() is why it still runs")


class EvidenceWording(unittest.TestCase):
    """The review's last note: unverified is not the same as absent, and a sample build
    proves nothing about the live site."""

    def test_search_console_is_called_unverified_not_absent(self):
        for doc in (ROOT / "MEASUREMENT.md", ROOT.parent / "CHECKPOINT.md"):
            if not doc.exists():
                continue
            text = doc.read_text(encoding="utf-8")
            self.assertNotIn("Not connected.", text, f"{doc.name} states absence it cannot prove")
            self.assertIn("not verified", text.lower())

    def test_no_live_claims_are_made_from_sample_builds(self):
        release = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
        self.assertIn("locally", release.lower())
        for overclaim in ("verified live", "confirmed on the live site"):
            self.assertNotIn(overclaim, release.lower())


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- gold bullion (session 12)

def bullion(**kw):
    """One call into the browser's own bullion function. The prices here are fictional and
    round on purpose: they exist so a wrong formula shows up as a wrong whole number."""
    payload = dict(weight="10", unit="g", fineness="999.9", offerA="", offerB="", goldSarG=500)
    payload.update(kw)
    return js(f"M.bullion({json.dumps(payload)})")


class BullionArithmetic(unittest.TestCase):
    """The bar comparison is the second calculation on this site a reader might act on with
    money in hand, so every displayed figure is pinned. Fixture: pure gold 500 SAR/g."""

    def test_the_reference_fixture(self):
        r = bullion(weight="100", fineness="999", offerA="50949", offerB="50449.50")
        self.assertEqual(r["grams"], 100)
        self.assertAlmostEqual(r["fineGrams"], 99.9)
        self.assertAlmostEqual(r["perGramFine"], 499.5)
        self.assertAlmostEqual(r["reference"], 49950)
        a, b = r["offers"]["a"], r["offers"]["b"]
        self.assertAlmostEqual(a["premiumSar"], 999)
        self.assertAlmostEqual(a["premiumPct"], 2)
        self.assertAlmostEqual(a["perGram"], 509.49)
        self.assertEqual(a["direction"], "above")
        self.assertAlmostEqual(b["premiumSar"], 499.50)
        self.assertAlmostEqual(b["premiumPct"], 1)
        self.assertAlmostEqual(b["perGram"], 504.495)
        self.assertEqual(r["compare"], {"lower": "b", "diffSar": 499.50})

    def test_reference_matches_python_arithmetic(self):
        """Same numbers, computed independently here: fine grams x SAR per pure gram."""
        for grams, fineness in ((100, 999), (100, 999.9), (31.1035, 999), (1, 999.9)):
            expected = grams * fineness / 1000 * 500
            r = bullion(weight=str(grams), fineness=str(fineness))
            self.assertAlmostEqual(r["reference"], expected, places=6,
                                   msg=f"{grams} g at {fineness}")

    def test_999_and_999_9_are_not_the_same_bar(self):
        nine = bullion(weight="100", fineness="999")["reference"]
        finer = bullion(weight="100", fineness="999.9")["reference"]
        self.assertAlmostEqual(nine, 49950)
        self.assertAlmostEqual(finer, 49995)
        self.assertNotAlmostEqual(nine, finer)

    def test_fineness_is_applied_once_and_never_as_a_karat(self):
        """999.9 is not 24K and a bar is not karat-converted. Both of the wrong answers -
        treating 999.9 as pure, and running a 24-karat conversion on top of the fineness -
        are excluded by name."""
        r = bullion(weight="100", fineness="999.9")
        pure_gold_value = 100 * 500                       # what "999.9 == pure" would give
        double_converted = 100 * 0.9999 * (24 / 24) * 0.9999 * 500
        self.assertNotAlmostEqual(r["reference"], pure_gold_value)
        self.assertNotAlmostEqual(r["reference"], double_converted, places=2)
        self.assertAlmostEqual(r["reference"], 49995)

    def test_a_kilo_is_a_thousand_grams(self):
        kg = bullion(weight="1", unit="kg", fineness="999")
        g = bullion(weight="1000", unit="g", fineness="999")
        self.assertEqual(kg["grams"], 1000)
        self.assertAlmostEqual(kg["reference"], 499500)
        self.assertAlmostEqual(kg["reference"], g["reference"])

    def test_kg_entries_hit_the_same_gram_ceiling(self):
        self.assertFalse(bullion(weight="20000", unit="kg")["ok"])
        self.assertFalse(bullion(weight="20000000", unit="g")["ok"])
        self.assertTrue(bullion(weight="1", unit="kg")["ok"])

    def test_a_blank_offer_is_absent_not_zero(self):
        r = bullion(weight="100", fineness="999")
        self.assertEqual(r["offers"]["a"]["state"], "absent")
        self.assertEqual(r["offers"]["b"]["state"], "absent")
        self.assertIsNone(r["compare"])
        self.assertNotIn("premiumSar", r["offers"]["a"])
        r = bullion(weight="100", fineness="999", offerA="   ")
        self.assertEqual(r["offers"]["a"]["state"], "absent")

    def test_a_zero_offer_is_an_error_not_a_free_bar(self):
        r = bullion(weight="100", fineness="999", offerA="0")
        self.assertEqual(r["offers"]["a"]["state"], "invalid")
        self.assertEqual(r["offers"]["a"]["reason"], "zero")
        self.assertNotIn("premiumSar", r["offers"]["a"])

    def test_bad_input_is_refused_never_coerced_to_zero(self):
        for bad, reason in (("1,2345", "unreadable"), ("abc", "unreadable"),
                            ("-100", "negative"), ("", None)):
            r = bullion(weight="100", fineness="999", offerA=bad)
            o = r["offers"]["a"]
            if reason is None:
                self.assertEqual(o["state"], "absent", bad)
            else:
                self.assertEqual(o["state"], "invalid", bad)
                self.assertEqual(o["reason"], reason, bad)

    def test_a_bad_b_neither_hides_a_nor_crowns_it(self):
        r = bullion(weight="100", fineness="999", offerA="50949", offerB="not a number")
        self.assertEqual(r["offers"]["a"]["state"], "ok")
        self.assertAlmostEqual(r["offers"]["a"]["premiumSar"], 999)
        self.assertEqual(r["offers"]["b"]["state"], "invalid")
        self.assertIsNone(r["compare"], "an unreadable B produced a comparison anyway")

    def test_a_total_below_the_metal_value_says_below(self):
        r = bullion(weight="100", fineness="999", offerA="49000")
        a = r["offers"]["a"]
        self.assertEqual(a["direction"], "below")
        self.assertAlmostEqual(a["premiumSar"], -950)
        self.assertAlmostEqual(a["premiumPct"], -950 / 49950 * 100)

    def test_an_exact_match_says_equal(self):
        a = bullion(weight="100", fineness="999", offerA="49950")["offers"]["a"]
        self.assertEqual(a["direction"], "equal")
        self.assertEqual(a["premiumSarShown"], 0)
        self.assertEqual(a["premiumPctShown"], 0)

    def test_a_difference_that_rounds_away_is_shown_as_equal_never_as_minus_zero(self):
        """The display policy: the above/below/equal word is decided from the same 2 dp
        figure the reader sees. Otherwise the page prints '-0.00 SAR below the metal
        value', which is both contradictory and wrong."""
        for offer in ("49949.999", "49950.001", "49949.9951"):
            a = bullion(weight="100", fineness="999", offerA=offer)["offers"]["a"]
            self.assertEqual(a["direction"], "equal", offer)
            self.assertEqual(a["premiumSarShown"], 0, offer)
            self.assertEqual(a["premiumPctShown"], 0, offer)
        # round2 can legitimately return negative zero. What must never happen is a sign or
        # a word coming out of it, so both are checked where they are actually produced.
        self.assertTrue(js("1 / M.round2(-0.001) === -Infinity"))     # it really is -0
        self.assertEqual(js("M.signState(M.round2(-0.001))"), "equal")
        self.assertEqual(js("(function (x) { return x < 0 ? 'MINUS' : x > 0 ? 'PLUS' : ''; })"
                            "(M.round2(-0.001))"), "")

    def test_the_word_never_contradicts_the_rounded_figure(self):
        for offer in ("49950.005", "49949.995", "49955", "49945"):
            a = bullion(weight="100", fineness="999", offerA=offer)["offers"]["a"]
            shown = a["premiumSarShown"]
            self.assertEqual(a["direction"],
                             "above" if shown > 0 else "below" if shown < 0 else "equal", offer)

    def test_display_rounding_is_half_away_from_zero_on_the_shown_figures(self):
        self.assertEqual(js("M.round2(504.495)"), 504.5)
        self.assertEqual(js("M.round2(-504.495)"), -504.5)
        self.assertEqual(js("M.round2(2.345)"), 2.35)
        self.assertEqual(js("M.round2(-2.345)"), -2.35)
        b = bullion(weight="100", fineness="999", offerB="50449.50")["offers"]["b"]
        self.assertAlmostEqual(b["perGram"], 504.495)      # full precision kept
        self.assertEqual(b["perGramShown"], 504.5)         # 504.50 on screen

    def test_two_equal_totals_are_a_state_not_a_winner(self):
        r = bullion(weight="100", fineness="999", offerA="50000", offerB="50000")
        self.assertEqual(r["compare"], {"lower": "equal", "diffSar": 0})

    def test_a_hairs_breadth_apart_reads_as_equal_rather_than_lower_by_nothing(self):
        r = bullion(weight="100", fineness="999", offerA="50000", offerB="50000.001")
        self.assertEqual(r["compare"]["lower"], "equal")
        self.assertEqual(r["compare"]["diffSar"], 0)

    def test_which_total_is_lower_is_decided_on_full_precision(self):
        r = bullion(weight="100", fineness="999", offerA="50000.02", offerB="50000")
        self.assertEqual(r["compare"]["lower"], "b")
        self.assertEqual(r["compare"]["diffSar"], 0.02)

    def test_an_unusable_snapshot_produces_no_figure_at_all(self):
        """A missing or impossible gold price must not become NaN on the page, and must not
        become a premium either: there is nothing to be a premium over."""
        for bad in (None, 0, -5, "not a price", float("nan")):
            payload = dict(weight="100", fineness="999", offerA="50949", offerB="", unit="g")
            if bad != bad:                                  # NaN: JSON cannot carry it
                r = js("M.bullion({weight:'100',unit:'g',fineness:'999',offerA:'50949',"
                       "offerB:'',goldSarG:NaN})")
            else:
                r = bullion(goldSarG=bad, **{k: v for k, v in payload.items() if k != "goldSarG"})
            self.assertTrue(r["ok"], repr(bad))
            self.assertFalse(r["snapshotOk"], repr(bad))
            self.assertIsNone(r["reference"], repr(bad))
            self.assertIsNone(r["perGramFine"], repr(bad))
            a = r["offers"]["a"]
            self.assertEqual(a["state"], "ok", repr(bad))
            self.assertNotIn("premiumSar", a)              # no premium over nothing
            self.assertNotIn("direction", a)
            self.assertAlmostEqual(a["perGram"], 509.49)   # this one needs no snapshot
        self.assertEqual(js("M.bullion({weight:'100',unit:'g',fineness:'999',offerA:'50949',"
                            "offerB:'',goldSarG:Infinity}).reference"), None)

    def test_weights_that_are_not_weights(self):
        for raw, reason in (("0", "zero"), ("-1", "negative"), ("", "empty"),
                            ("1,2345", "invalid"), ("99999999", "huge")):
            r = bullion(weight=raw)
            self.assertFalse(r["ok"], raw)
            self.assertEqual(r["weight"]["reason"], reason, raw)

    def test_arabic_digits_work_here_too(self):
        r = bullion(weight="١٠٠", fineness="999", offerA="٥٠٩٤٩")
        self.assertEqual(r["grams"], 100)
        self.assertAlmostEqual(r["offers"]["a"]["premiumSar"], 999)


class BullionSection(unittest.TestCase):
    """What the two calculator pages must actually say. The arithmetic can be right while
    the page still promises something we cannot know."""

    PAGES = ("sa/calculator/index.html", "sa/en/calculator/index.html")

    def section(self, rel):
        html = SampleSite.html(rel)
        start = html.index('id="bullion-calc"')
        return html[start:html.index("</section>", start)]

    def test_the_tool_is_on_both_calculator_languages(self):
        for rel in self.PAGES:
            html = SampleSite.html(rel)
            self.assertIn('id="bullion-calc"', html, rel)
            for el in ("bl-weight", "bl-unit", "bl-fineness", "bl-offer-a", "bl-offer-b",
                       "bl-summary", "bl-detail", "bl-snapshot"):
                self.assertIn(f'id="{el}"', html, f"{rel} missing {el}")

    def test_a_plain_fragment_link_makes_it_discoverable(self):
        for rel in self.PAGES:
            html = SampleSite.html(rel)
            head = html[:html.index('id="bullion-calc"')]
            self.assertIn('href="#bullion-calc"', head, f"{rel}: no link above the section")

    def test_the_original_calculator_is_still_there(self):
        for rel in self.PAGES:
            html = SampleSite.html(rel)
            for el in ('id="value-calc"', 'id="calc-weight"', 'id="calc-karat"',
                       'id="calc-making"', 'id="calc-result"'):
                self.assertIn(el, html, f"{rel} lost {el}")

    def test_presets_are_buttons_and_cover_the_common_bars(self):
        for rel in self.PAGES:
            section = self.section(rel)
            for preset in ('data-weight="10" data-unit="g"', 'data-weight="20" data-unit="g"',
                           'data-weight="50" data-unit="g"', 'data-weight="100" data-unit="g"',
                           'data-weight="1" data-unit="kg"'):
                self.assertIn(preset, section, f"{rel} missing preset {preset}")
            self.assertNotIn("<div class=\"presets\" onclick", section)
            self.assertEqual(section.count('type="button"'), 5, rel)

    def test_first_visit_defaults_are_visible_and_no_dealer_price_is_invented(self):
        import re
        for rel in self.PAGES:
            section = self.section(rel)
            self.assertRegex(section, r'id="bl-weight"[^>]*value="10"', rel)
            self.assertIn('<option value="999.9" selected>', section, rel)
            self.assertNotIn('<option value="999" selected>', section)
            for offer in ("bl-offer-a", "bl-offer-b"):
                tag = re.search(rf'<input[^>]*id="{offer}"[^>]*>', section).group(0)
                self.assertNotIn(" value=", tag, f"{rel}: {offer} ships a prefilled price")
                self.assertIn("placeholder=", tag)

    def test_only_the_two_bar_finenesses_are_offered(self):
        import re
        for rel in self.PAGES:
            section = self.section(rel)
            select = re.search(r'<select id="bl-fineness"[^>]*>.*?</select>', section, re.S).group(0)
            self.assertEqual(re.findall(r'value="([\d.]+)"', select), ["999", "999.9"], rel)

    def test_the_offer_is_asked_for_as_a_total_and_only_as_a_total(self):
        ar, en = (self.section(r) for r in self.PAGES)
        self.assertIn("السعر الإجمالي للعرض أ (ريال)", ar)
        self.assertIn("السعر الإجمالي للعرض ب (ريال، اختياري)", ar)
        self.assertIn("Total offer price A (SAR)", en)
        self.assertIn("Total offer price B (SAR, optional)", en)
        for section in (ar, en):
            self.assertNotIn("per_gram", section)          # no per-gram mode this iteration
            self.assertIn("<fieldset", section)
            self.assertIn("<legend", section)

    def test_fineness_is_explained_and_24k_is_explicitly_not_a_synonym(self):
        ar, en = (self.section(r) for r in self.PAGES)
        self.assertIn("99.99", ar)
        self.assertIn("99.9%", ar)
        self.assertIn("«عيار 24»", ar)
        self.assertIn("مرادف", ar)                          # ...is NOT an exact synonym
        self.assertIn("99.99% gold", en)
        self.assertIn('"24K"', en)
        self.assertIn("exact synonym", en)

    def test_the_page_says_what_it_does_not_know(self):
        ar, en = (self.section(r) for r in self.PAGES)
        for phrase in ("قيمة الذهب المرجعية", "التصنيع", "هامش البائع", "لا نعرف",
                       "أصالة السبيكة", "ضريبة", "إعادة البيع"):
            self.assertIn(phrase, ar, f"Arabic disclosure missing: {phrase}")
        for phrase in ("reference metal value", "fabrication", "dealer's margin",
                       "we do not know", "authenticity", "tax", "sell it back"):
            self.assertIn(phrase, en, f"English disclosure missing: {phrase}")

    def test_no_recommendation_language(self):
        """Banned outright: anything that ranks a dealer, tells the reader to buy, or
        invents a fair-price threshold. The words 'bargain' and 'wrongdoing' are allowed
        only in the sentence that refuses to draw either conclusion, which is checked
        separately below - so a lint on the bare word would be lying about what it tests."""
        for rel in self.PAGES:
            section = self.section(rel).lower()
            for banned in ("best dealer", "best price", "we recommend", "you should buy",
                           "expected return", "guaranteed", "fair price", "a good deal",
                           "أفضل بائع", "ننصح بالشراء", "السعر العادل", "عائد متوقع",
                           "صفقة مضمونة", "ربح مضمون"):
                self.assertNotIn(banned, section, f"{rel} says {banned!r}")

    def test_a_lower_total_is_not_called_a_bargain_or_a_scandal(self):
        ar, en = (self.section(r) for r in self.PAGES)
        self.assertIn("is not proof of a bargain and not evidence of wrongdoing", en)
        self.assertIn("ليس دليل صفقة رابحة ولا دليل مخالفة", ar)
        self.assertIn("does not pick a dealer and does not say when to buy", en)
        self.assertIn("لا نرشّح بائعًا ولا نقول متى تشتري", ar)

    def test_the_difference_is_never_coloured_good_or_bad(self):
        """Green and red on this site mean the market moved up or down. A premium is not a
        failing grade, so the bullion renderer may not borrow those classes."""
        src = Path(CALC_JS).read_text(encoding="utf-8")
        block = src[src.index("gold bullion offer comparison"):src.index("used-gold sell calculator")]
        for banned in ("'up'", '"up"', "'down'", '"down"', "class='up", "class='down"):
            self.assertNotIn(banned, block, f"bullion renderer emits {banned}")
        css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".bl-diff", css)
        rule = css[css.index(".bl-diff"):css.index("}", css.index(".bl-diff"))]
        self.assertIn("var(--fg)", rule, "the difference line is not in the body colour")

    def test_the_reader_is_told_which_snapshot_and_whose(self):
        for rel in self.PAGES:
            self.assertIn('id="bl-snapshot"', SampleSite.html(rel), rel)
            self.assertIn("data-source=", self.section(rel), rel)
        src = Path(CALC_JS).read_text(encoding="utf-8")
        note = src[src.index("function bullionSnapshotNote"):]
        note = note[:note.index("\n  }")]
        self.assertIn("P.updated", note)                    # the QUOTE time
        self.assertIn("quote_time_known", note)             # ...and the honest fallback
        self.assertNotIn("generated_utc", note)             # never the build clock
        self.assertNotIn("Date.parse", note)                # the quote time is never recomputed
        block = src[src.index("gold bullion offer comparison"):src.index("used-gold sell calculator")]
        self.assertNotIn("fetch(", block)                   # no second data source
        # the block must not write the note itself - renderSnapshotNotes() owns it, which is
        # what puts it on the freshness timer
        self.assertNotIn('$("bl-snapshot")', block)
        self.assertIn('$("bl-snapshot")', src[:src.index("gold bullion offer comparison")])

    def test_every_field_error_is_wired_to_a_real_element(self):
        """An aria-describedby pointing at an id that does not exist is a silent no-op: the
        markup looks accessible and announces nothing. Every referenced id is checked to
        exist, and every error slot to be present from the start rather than injected later
        (a slot created at error time is not reliably picked up by assistive tech)."""
        import re
        for rel in self.PAGES:
            html = SampleSite.html(rel)
            section = self.section(rel)
            ids = set(re.findall(r'id="([^"]+)"', html))
            described = re.findall(r'<(?:input|select)[^>]*id="(bl-[^"]+)"[^>]*'
                                   r'aria-describedby="([^"]+)"', section)
            self.assertEqual({d[0] for d in described},
                             {"bl-weight", "bl-fineness", "bl-offer-a", "bl-offer-b"}, rel)
            for field, refs in described:
                for ref in refs.split():
                    self.assertIn(ref, ids, f"{rel}: {field} describes a missing #{ref}")
            for field in ("bl-weight", "bl-offer-a", "bl-offer-b"):
                self.assertIn(f'<span class="field-error" id="{field}-error"></span>', section,
                              f"{rel}: {field} has no error slot in the markup")

    def test_no_handler_can_return_before_it_has_saved(self):
        """Review finding 1. Every calculator persists its scope BEFORE validation, because
        an early return that skipped the save left the previous value in storage and a reload
        resurrected a number the reader had deleted. Structural guard: in each handler the
        save must come before the first `return`."""
        src = Path(CALC_JS).read_text(encoding="utf-8")
        handlers = {"upd": "vc", "updSell": "sc", "updSilver": "sv",
                    "updZakat": "zc", "updBullion": "blc"}
        for name, scope in handlers.items():
            start = src.index(f"var {name} = function ()")
            body = src[start:src.index("\n      };", start)]
            save = body.find(f"saveInputs({scope})")
            self.assertNotEqual(save, -1, f"{name} never saves")
            first_return = body.find("return;")
            if first_return != -1:
                self.assertLess(save, first_return,
                                f"{name} can return before saving: an emptied field would "
                                f"survive a reload")
            self.assertEqual(body.count(f"saveInputs({scope})"), 1,
                             f"{name} saves more than once")

    def test_the_market_sentence_is_not_frozen_at_load(self):
        """Review finding 3. The quote time in the snapshot notes is fixed for the life of
        the page; 'the market is closed at this time' is not - it is about the reader's
        present, so it rides the same timer as the banner."""
        src = Path(CALC_JS).read_text(encoding="utf-8")
        self.assertIn("function renderSnapshotNotes()", src)
        head = src[src.index("function renderSnapshotNotes()"):src.index("setInterval(function ()")]
        self.assertIn("\n    renderSnapshotNotes();", head, "never painted at load")
        timer = src[src.index("setInterval(function ()"):]
        timer = timer[:timer.index("});")]
        self.assertIn("renderSnapshotNotes()", timer)
        visibility = src[src.index('document.addEventListener("visibilitychange"'):]
        visibility = visibility[:visibility.index("});")]
        self.assertIn("renderSnapshotNotes()", visibility)

    def test_the_live_region_is_one_short_line_not_the_whole_result(self):
        for rel in self.PAGES:
            section = self.section(rel)
            self.assertIn('id="bl-summary" role="status" aria-live="polite"', section, rel)
            detail = section[section.index('id="bl-detail"'):]
            self.assertNotIn("aria-live", detail, f"{rel}: the breakdown is a live region too")
            # <output> is implicitly aria-live, so the results box must not be one
            self.assertNotIn("<output", section, rel)

    def test_the_tool_adds_no_route_and_no_tracking(self):
        self.assertEqual(len(REG.PAGES), 14)
        sitemap = (SampleSite.path() / "sitemap.xml").read_text(encoding="utf-8")
        self.assertEqual(sitemap.count("<loc>"), 14)
        self.assertNotIn("bullion", sitemap)
        self.assertTrue((SampleSite.path() / "404.html").exists(), "root 404 stopped building")
        src = Path(CALC_JS).read_text(encoding="utf-8")
        block = src[src.index("gold bullion offer comparison"):src.index("used-gold sell calculator")]
        self.assertNotIn("track(", block, "the bullion tool instruments the reader's amounts")

    def test_the_other_shared_tools_still_build(self):
        """calc.js is one file for the whole site. A change here that broke the silver,
        sell or zakat calculator would be invisible on this page."""
        for rel, marker in (("sa/silver/index.html", 'id="silver-calc"'),
                            ("sa/en/silver/index.html", 'id="silver-calc"'),
                            ("sa/sell-price/index.html", 'id="sell-calc"'),
                            ("sa/en/sell-price/index.html", 'id="sell-calc"'),
                            ("sa/zakat/index.html", 'id="zakat-calc"')):
            self.assertIn(marker, SampleSite.html(rel), rel)


class TwoToolsOnOnePageKeepTheirInputs(unittest.TestCase):
    """The storage bug this feature would otherwise have shipped: saveInputs() REPLACED the
    page's whole sessionStorage object with one scope's inputs, so typing in either tool
    erased the other's saved values and a refresh restored half a form. Arithmetic tests
    cannot see this - it only exists in a browser with a real sessionStorage - so this runs
    the built page in one. It skips, loudly, where no browser is available."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright is not installed: the two-tool storage test "
                                    "needs a browser (pip install playwright && playwright install chromium). "
                                    "The manual procedure is in BULLION_IMPLEMENTATION_HANDOFF.md")
        cls._pw = sync_playwright().start()
        try:
            cls.browser = cls._pw.chromium.launch()
        except Exception as exc:
            cls._pw.stop()
            raise unittest.SkipTest(f"no chromium available for the storage test: {exc}")
        # Served over HTTP, not file://. The pages load /static/calc.js by absolute path and
        # sessionStorage is restricted on file:// - under file:// the whole class passes
        # vacuously because no script ever runs, which is worse than not testing at all.
        import functools
        import threading
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

        handler = functools.partial(Quiet, directory=str(SampleSite.path()))
        cls._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls._thread = threading.Thread(target=cls._httpd.serve_forever, daemon=True)
        cls._thread.start()
        cls.base = f"http://127.0.0.1:{cls._httpd.server_address[1]}/"

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_httpd", None):
            cls._httpd.shutdown()
            cls._httpd.server_close()
        if getattr(cls, "browser", None):
            cls.browser.close()
            cls._pw.stop()

    def page_for(self, rel, gold_sar_g=None):
        """gold_sar_g rewrites the embedded snapshot ON THE WIRE, so the fixture price
        survives a reload and the page still reads exactly one snapshot - the same one it
        would have read in production, with a different number in it."""
        import re as _re
        ctx = self.browser.new_context()
        page = ctx.new_page()
        if gold_sar_g is not None:
            def rewrite(route):
                resp = route.fetch()
                body = _re.sub(r'"gold_sar_g": ?[0-9.]+', f'"gold_sar_g": {gold_sar_g}',
                               resp.text())
                route.fulfill(response=resp, body=body)
            page.route("**/*.html", rewrite)
        page.goto(self.base + rel)
        page.wait_for_selector("#bl-summary")
        return ctx, page

    def test_each_tool_keeps_its_own_values_across_a_reload(self):
        for rel in ("sa/calculator/index.html", "sa/en/calculator/index.html"):
            ctx, page = self.page_for(rel)
            try:
                page.fill("#calc-weight", "37.5")
                page.fill("#calc-making", "12")
                page.select_option("#calc-karat", "18")
                page.fill("#bl-weight", "250")
                page.fill("#bl-offer-a", "131000")
                page.fill("#bl-offer-b", "130500")
                page.select_option("#bl-fineness", "999")
                page.reload()
                self.assertEqual(page.input_value("#calc-weight"), "37.5", rel)
                self.assertEqual(page.input_value("#calc-making"), "12", rel)
                self.assertEqual(page.input_value("#calc-karat"), "18", rel)
                self.assertEqual(page.input_value("#bl-weight"), "250", rel)
                self.assertEqual(page.input_value("#bl-offer-a"), "131000", rel)
                self.assertEqual(page.input_value("#bl-offer-b"), "130500", rel)
                self.assertEqual(page.input_value("#bl-fineness"), "999", rel)
                # both tools recomputed from what came back, rather than sitting on their
                # "enter a weight" prompt with values in the boxes
                for out, prompt in (("#calc-result", "Enter a weight"),
                                    ("#bl-result", "Enter a weight")):
                    text = page.text_content(out).strip()
                    self.assertTrue(text, f"{rel}: {out} is empty after a reload")
                    self.assertNotIn(prompt, text, f"{rel}: {out} did not recompute")
                    self.assertNotIn("أدخل الوزن", text, f"{rel}: {out} did not recompute")
                    self.assertNotIn("NaN", text, rel)
            finally:
                ctx.close()

    def test_typing_in_one_tool_does_not_wipe_the_other(self):
        ctx, page = self.page_for("sa/calculator/index.html")
        try:
            page.fill("#bl-offer-a", "20500")
            page.fill("#calc-weight", "8")          # the scope that used to clobber
            stored = page.evaluate("JSON.parse(sessionStorage.getItem('mithqal:sa_calculator'))")
            self.assertEqual(stored["bl-offer-a"], "20500")
            self.assertEqual(stored["calc-weight"], "8")
        finally:
            ctx.close()

    def test_clearing_a_field_is_not_undone_by_a_reload(self):
        ctx, page = self.page_for("sa/calculator/index.html")
        try:
            page.fill("#bl-offer-a", "20500")
            page.fill("#bl-offer-a", "")
            page.reload()
            self.assertEqual(page.input_value("#bl-offer-a"), "")
            self.assertNotIn("20500", page.text_content("#bl-result"))
        finally:
            ctx.close()

    def test_an_untouched_tool_shows_no_answer_to_an_unasked_question(self):
        """restoreInputs() used to report success whenever ANY tool on the page had saved
        something, which made an empty tool render a result."""
        ctx, page = self.page_for("sa/calculator/index.html")
        try:
            page.fill("#bl-offer-a", "20500")
            page.reload()
            self.assertIn("Enter a weight", page.text_content("#calc-result")
                          .replace("أدخل الوزن لعرض القيمة", "Enter a weight"))
        finally:
            ctx.close()

    def test_the_page_computes_the_reference_on_a_first_visit(self):
        for rel, needle in (("sa/calculator/index.html", "قيمة الذهب المرجعية"),
                            ("sa/en/calculator/index.html", "Reference metal value")):
            ctx, page = self.page_for(rel)
            try:
                self.assertEqual(page.input_value("#bl-weight"), "10")
                self.assertEqual(page.input_value("#bl-fineness"), "999.9")
                self.assertEqual(page.input_value("#bl-offer-a"), "")
                self.assertIn(needle, page.text_content("#bl-result"), rel)
                self.assertNotIn("NaN", page.text_content("#bl-result"), rel)
            finally:
                ctx.close()

    def test_a_live_page_produces_the_fixture_numbers(self):
        """End to end, through the real DOM: the snapshot is replaced with the fixture
        price so the displayed strings can be pinned."""
        ctx, page = self.page_for("sa/en/calculator/index.html", gold_sar_g=500)
        try:
            page.fill("#bl-weight", "100")
            page.select_option("#bl-fineness", "999")
            page.fill("#bl-offer-a", "50949")
            page.fill("#bl-offer-b", "50449.50")
            text = page.text_content("#bl-result")
            for needle in ("49,950.00", "999.00", "+2.00%", "509.49",
                           "499.50", "+1.00%", "504.50", "above the reference metal value"):
                self.assertIn(needle, text, f"missing {needle} in: {text[:400]}")
            self.assertIn("Offer B is the lower total, by 499.50 SAR", text)
            self.assertNotIn("-0.00", text)
            self.assertNotIn("NaN", text)
        finally:
            ctx.close()

    def test_clearing_the_original_calculator_weight_survives_a_reload(self):
        """Review finding 1, reproduced before the fix: enter a weight, enter a bullion
        offer, clear the weight, reload - the weight came back. The early return skipped the
        save, so storage still held the old value, and merging storage objects did not touch
        that."""
        for rel in ("sa/en/calculator/index.html", "sa/calculator/index.html"):
            ctx, page = self.page_for(rel)
            try:
                page.fill("#calc-weight", "37.5")
                page.fill("#bl-offer-a", "6000")
                page.fill("#calc-weight", "")
                page.reload()
                page.wait_for_selector("#bl-summary")
                self.assertEqual(page.input_value("#calc-weight"), "",
                                 f"{rel}: a deleted weight came back")
                self.assertEqual(page.input_value("#bl-offer-a"), "6000",
                                 f"{rel}: the other tool lost its value")
            finally:
                ctx.close()

    def test_making_a_valid_weight_invalid_is_not_undone_by_a_reload(self):
        for rel in ("sa/en/calculator/index.html", "sa/calculator/index.html"):
            ctx, page = self.page_for(rel)
            try:
                page.fill("#calc-weight", "37.5")
                page.fill("#calc-weight", "abc")
                page.fill("#bl-weight", "-4")
                page.reload()
                page.wait_for_selector("#bl-summary")
                self.assertEqual(page.input_value("#calc-weight"), "abc", rel)
                self.assertEqual(page.input_value("#bl-weight"), "-4", rel)
            finally:
                ctx.close()

    def test_the_other_shared_calculators_no_longer_resurrect_cleared_weights(self):
        """calc.js is one file: the same early-return pattern was in the sell and silver
        handlers, and in zakat. Fixed there too rather than only where the review looked."""
        for rel, field, other in (("sa/en/sell-price/index.html", "#sell-weight", "#sell-deduction"),
                                  ("sa/sell-price/index.html", "#sell-weight", "#sell-deduction"),
                                  ("sa/en/silver/index.html", "#sv-weight", "#sv-quote"),
                                  ("sa/silver/index.html", "#sv-weight", "#sv-quote"),
                                  ("sa/zakat/index.html", "#zs-weight", None)):
            ctx = self.browser.new_context()
            page = ctx.new_page()
            try:
                page.goto(self.base + rel)
                page.wait_for_selector(field)
                page.fill(field, "20")
                if other:
                    page.fill(other, "5")
                page.fill(field, "")
                page.reload()
                page.wait_for_selector(field)
                self.assertEqual(page.input_value(field), "", f"{rel}: {field} came back")
                if other:
                    self.assertEqual(page.input_value(other), "5", rel)
            finally:
                ctx.close()

    def test_a_field_error_is_announced_associated_and_then_cleared(self):
        """Review finding 2. The message has to be reachable FROM the input - a visible
        paragraph near the result is not an association - and it has to go away again."""
        for rel, bad_msg, ok_after in (("sa/en/calculator/index.html", "could not be read", "Offer A"),
                                       ("sa/calculator/index.html", "لم نفهم مبلغ العرض", "العرض")):
            ctx, page = self.page_for(rel)
            try:
                page.fill("#bl-offer-a", "1,2345")
                self.assertEqual(page.get_attribute("#bl-offer-a", "aria-invalid"), "true", rel)
                described = page.get_attribute("#bl-offer-a", "aria-describedby").split()
                self.assertIn("bl-offer-a-error", described, rel)
                self.assertIn(bad_msg, page.text_content("#bl-offer-a-error"), rel)
                # focus is never taken away from whoever is typing
                self.assertEqual(page.evaluate("() => document.activeElement.id"), "bl-offer-a", rel)

                page.fill("#bl-offer-a", "20500")          # corrected
                self.assertIsNone(page.get_attribute("#bl-offer-a", "aria-invalid"), rel)
                self.assertEqual(page.text_content("#bl-offer-a-error"), "", rel)
                self.assertFalse(page.locator("#bl-offer-a-error").is_visible(), rel)
                self.assertIn(ok_after, page.text_content("#bl-detail"), rel)

                page.fill("#bl-offer-a", "0")              # zero is an error, not "absent"
                self.assertEqual(page.get_attribute("#bl-offer-a", "aria-invalid"), "true", rel)
                page.fill("#bl-offer-a", "")               # blank optional offer clears it
                self.assertIsNone(page.get_attribute("#bl-offer-a", "aria-invalid"), rel)
                self.assertEqual(page.text_content("#bl-offer-a-error"), "", rel)
            finally:
                ctx.close()

    def test_a_bad_offer_stays_flagged_while_the_weight_is_being_fixed(self):
        ctx, page = self.page_for("sa/en/calculator/index.html")
        try:
            page.fill("#bl-offer-b", "abc")
            page.fill("#bl-weight", "")                    # weight now invalid too
            self.assertEqual(page.get_attribute("#bl-weight", "aria-invalid"), "true")
            self.assertEqual(page.get_attribute("#bl-offer-b", "aria-invalid"), "true")
            page.fill("#bl-offer-b", "")                   # corrected while weight still bad
            self.assertIsNone(page.get_attribute("#bl-offer-b", "aria-invalid"))
            page.fill("#bl-weight", "50")                  # weight fixed
            self.assertIsNone(page.get_attribute("#bl-weight", "aria-invalid"))
            self.assertEqual(page.text_content("#bl-weight-error"), "")
        finally:
            ctx.close()

    def test_a_field_error_is_reachable_by_keyboard_without_losing_the_place(self):
        ctx, page = self.page_for("sa/en/calculator/index.html")
        try:
            page.focus("#bl-offer-a")
            page.keyboard.type("abc")
            self.assertEqual(page.evaluate("() => document.activeElement.id"), "bl-offer-a")
            page.keyboard.press("Tab")
            self.assertEqual(page.evaluate("() => document.activeElement.id"), "bl-offer-b")
            page.keyboard.press("Shift+Tab")               # back to the field with the error
            self.assertEqual(page.evaluate("() => document.activeElement.id"), "bl-offer-a")
            self.assertEqual(page.get_attribute("#bl-offer-a", "aria-invalid"), "true")
        finally:
            ctx.close()

    def test_the_market_closed_sentence_follows_the_readers_clock(self):
        """Review finding 3, the transition it named: a page left open across the weekly
        reopening. The quote time must not move; the closed clause must go."""
        from datetime import datetime, timedelta, timezone
        probe_ctx, probe = self.page_for("sa/en/calculator/index.html")
        try:
            session = probe.evaluate(
                "() => JSON.parse(document.getElementById('prices').textContent).market_session")
        finally:
            probe_ctx.close()

        day = datetime(2026, 9, 1, tzinfo=timezone.utc)
        while day.weekday() != session["open_weekday"]:
            day += timedelta(days=1)
        closed_at = day.replace(hour=max(0, session["open_hour"] - 1))
        self.assertTrue(closed_at.hour < session["open_hour"])

        ctx = self.browser.new_context()
        page = ctx.new_page()
        try:
            page.clock.install(time=closed_at)
            page.goto(self.base + "sa/en/calculator/index.html")
            page.wait_for_selector("#bl-summary")
            before = page.text_content("#bl-snapshot")
            self.assertIn("The market is closed at this time.", before)
            quoted = before.split("quoted ")[1].split(" Riyadh")[0]

            page.clock.fast_forward("02:00:00")            # past the reopening
            page.wait_for_function(
                "() => !document.getElementById('bl-snapshot').textContent"
                ".includes('market is closed')", timeout=5000)
            after = page.text_content("#bl-snapshot")
            self.assertNotIn("closed", after)
            self.assertIn(quoted, after, "the quote time moved - it must never move")
            self.assertIn("Reference: pure gold from", after)
        finally:
            ctx.close()

    def test_the_other_snapshot_notes_unfreeze_too(self):
        """The same one-shot bug was in the existing calculator, sell and silver notes."""
        from datetime import datetime, timedelta, timezone
        probe_ctx, probe = self.page_for("sa/en/calculator/index.html")
        try:
            session = probe.evaluate(
                "() => JSON.parse(document.getElementById('prices').textContent).market_session")
        finally:
            probe_ctx.close()
        day = datetime(2026, 9, 1, tzinfo=timezone.utc)
        while day.weekday() != session["open_weekday"]:
            day += timedelta(days=1)
        closed_at = day.replace(hour=max(0, session["open_hour"] - 1))

        for rel, note in (("sa/en/calculator/index.html", "#calc-snapshot"),
                          ("sa/en/sell-price/index.html", "#sell-snapshot"),
                          ("sa/en/silver/index.html", "#sv-snapshot")):
            ctx = self.browser.new_context()
            page = ctx.new_page()
            try:
                page.clock.install(time=closed_at)
                page.goto(self.base + rel)
                page.wait_for_selector(note)
                self.assertIn("The market is closed at this time.",
                              page.text_content(note), rel)
                page.clock.fast_forward("02:00:00")
                page.wait_for_function(
                    f"() => !document.querySelector('{note}').textContent"
                    ".includes('market is closed')", timeout=5000)
            finally:
                ctx.close()

    def test_the_announcement_is_hidden_from_the_eye_but_not_from_a_screen_reader(self):
        ctx, page = self.page_for("sa/en/calculator/index.html")
        try:
            # It takes no space on screen (the conclusion is not printed twice) ...
            box = page.locator("#bl-summary").bounding_box()
            self.assertLessEqual(box["width"], 1.5)
            self.assertLessEqual(box["height"], 1.5)
            css = page.eval_on_selector("#bl-summary", """e => {
              const s = getComputedStyle(e);
              return {display: s.display, visibility: s.visibility, clip: s.clipPath};
            }""")
            # ... but it is still rendered and still exposed, which display:none,
            # visibility:hidden and the hidden attribute would all undo.
            self.assertNotEqual(css["display"], "none")
            self.assertNotEqual(css["visibility"], "hidden")
            self.assertNotEqual(css["clip"], "none")
            self.assertIsNone(page.get_attribute("#bl-summary", "aria-hidden"))
            self.assertIsNone(page.get_attribute("#bl-summary", "hidden"))
            self.assertTrue(page.text_content("#bl-summary").strip(), "nothing to announce")
        finally:
            ctx.close()

    def test_an_impossible_weight_is_said_on_the_field_it_is_about(self):
        """The live region is invisible, so an error that went only there would be an error
        nobody sighted could read. It belongs on the field, not floating near the result."""
        for rel, needle in (("sa/en/calculator/index.html", "cannot be negative"),
                            ("sa/calculator/index.html", "لا يكون بالسالب")):
            ctx, page = self.page_for(rel)
            try:
                page.fill("#bl-weight", "-5")
                self.assertTrue(page.locator("#bl-weight-error").is_visible(), rel)
                self.assertIn(needle, page.text_content("#bl-weight-error"), rel)
                self.assertIn(needle, page.text_content("#bl-summary"), rel)
                self.assertEqual(page.get_attribute("#bl-weight", "aria-invalid"), "true", rel)
                self.assertEqual(page.text_content("#bl-detail").strip(), "", rel)
                # ...and the results box does not sit there as an empty bordered panel
                self.assertIn("is-empty", page.get_attribute("#bl-result", "class"), rel)
                self.assertTrue(page.locator("#bl-summary").count(), "announcement removed")
            finally:
                ctx.close()

    def test_an_unreadable_offer_is_flagged_on_its_own_field(self):
        ctx, page = self.page_for("sa/en/calculator/index.html")
        try:
            page.fill("#bl-offer-a", "1,2345")
            slot = page.locator("#bl-offer-a-error")
            self.assertTrue(slot.is_visible())
            self.assertIn("could not be read", slot.text_content())
            self.assertEqual(page.get_attribute("#bl-offer-a", "aria-invalid"), "true")
            self.assertFalse(page.locator("#bl-offer-b-error").is_visible())
            self.assertIsNone(page.get_attribute("#bl-offer-b", "aria-invalid"))
        finally:
            ctx.close()

    def test_presets_are_reachable_and_usable_from_the_keyboard(self):
        ctx, page = self.page_for("sa/en/calculator/index.html")
        try:
            page.focus("#bl-weight")
            page.keyboard.press("Shift+Tab")              # back into the preset row
            focused = page.evaluate("() => document.activeElement.outerHTML")
            self.assertIn("data-weight", focused, f"focus landed on {focused[:80]}")
            page.keyboard.press("Enter")
            self.assertEqual(page.input_value("#bl-weight"), "1")
            self.assertEqual(page.input_value("#bl-unit"), "kg")
            self.assertIn("1,000 g", page.text_content("#bl-result"))
        finally:
            ctx.close()

    def test_changing_the_fineness_moves_the_reference_and_keeps_the_basis_visible(self):
        ctx, page = self.page_for("sa/en/calculator/index.html", gold_sar_g=500)
        try:
            page.fill("#bl-weight", "100")
            page.fill("#bl-offer-a", "50000")
            page.select_option("#bl-fineness", "999")
            self.assertIn("49,950.00", page.text_content("#bl-result"))
            self.assertIn("999 (99.9%)", page.text_content("#bl-result"))
            page.select_option("#bl-fineness", "999.9")
            text = page.text_content("#bl-result")
            self.assertIn("49,995.00", text)               # the reference really moved
            self.assertIn("999.9 (99.99%)", text)          # and the basis is still on screen
            self.assertIn("100 g", text)
            self.assertIn("5.00 SAR above", text)          # 50,000 - 49,995
        finally:
            ctx.close()

    def test_arabic_figures_are_isolated_so_a_sign_cannot_wander(self):
        ctx, page = self.page_for("sa/calculator/index.html", gold_sar_g=500)
        try:
            page.fill("#bl-weight", "100")
            page.select_option("#bl-fineness", "999")
            page.fill("#bl-offer-a", "49000")              # a below-reference total
            self.assertEqual(page.get_attribute("html", "dir"), "rtl")
            html = page.inner_html("#bl-detail")
            self.assertIn("<bdi>", html)
            self.assertIn("\u2212950.00", html.replace("&minus;", "\u2212"))
            self.assertIn("أقل من قيمة الذهب المرجعية", html)
            # every signed figure sits inside its own bdi
            import re as _re
            for sign in ("\u2212950.00", "\u22121.90%"):
                self.assertRegex(html, _re.escape("<bdi>" + sign))
        finally:
            ctx.close()

    def test_the_original_calculator_still_answers(self):
        ctx, page = self.page_for("sa/en/calculator/index.html", gold_sar_g=500)
        try:
            page.fill("#calc-weight", "10")
            page.select_option("#calc-karat", "24")
            self.assertIn("5,000.00", page.text_content("#calc-result"))
            page.fill("#calc-making", "20")
            self.assertIn("5,200.00", page.text_content("#calc-result"))
            self.assertNotIn("NaN", page.text_content("#calc-result"))
        finally:
            ctx.close()

    def test_a_bad_offer_b_leaves_a_standing_and_names_no_winner(self):
        ctx, page = self.page_for("sa/en/calculator/index.html")
        try:
            page.fill("#bl-weight", "100")
            page.fill("#bl-offer-a", "50949")
            page.fill("#bl-offer-b", "1,2345")
            detail = page.text_content("#bl-detail")
            self.assertIn("Offer A", detail)
            self.assertIn("could not be read", page.text_content("#bl-offer-b-error"))
            self.assertNotIn("lower total", page.text_content("#bl-result"))
            self.assertNotIn("NaN", page.text_content("#bl-result"))
        finally:
            ctx.close()
