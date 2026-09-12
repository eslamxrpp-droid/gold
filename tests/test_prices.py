"""Run: python -m unittest discover tests   (from the site folder)"""
import json
import shutil
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import prices as P  # noqa: E402
import build  # noqa: E402


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
        below = P.zakat_gold([(97.0, 21)], 85, 500)
        above = P.zakat_gold([(97.2, 21)], 85, 500)
        self.assertFalse(below["reached"])
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
        js = ROOT / "static" / "calc.js"
        script = f"""
const M = require({json.dumps(str(js))});
console.log(JSON.stringify([
  M.karatPrice(525.9064, 21), M.purityPrice(7.76, 925), M.sellValue(20, 21, 525.6, 5),
  M.zakatGold([[60, 21], [40, 18]], 85, 525.9, 0.025), M.zakatSilver(650, 925, 595, 7.7, 0.025)
]));"""
        out = json.loads(subprocess.check_output(["node", "-e", script], text=True))
        self.assertAlmostEqual(out[0], P.gold_karat_price(525.9064, 21))
        self.assertAlmostEqual(out[1], P.silver_purity_price(7.76, 925))
        self.assertAlmostEqual(out[2], P.sell_value(20, 21, 525.6, 5))
        py = P.zakat_gold([(60, 21), (40, 18)], 85, 525.9)
        self.assertAlmostEqual(out[3]["zakat"], py["zakat"])
        self.assertEqual(out[3]["reached"], py["reached"])
        self.assertAlmostEqual(out[4]["zakat"], P.zakat_silver(650, 925, 595, 7.7)["zakat"])


class SafetyChecks(unittest.TestCase):
    cfg = {"max_price_age_minutes": 90, "sar_per_usd_peg": 3.75}

    def latest(self, **kw):
        d = {"gold_usd_oz": 4362, "silver_usd_oz": 64.4, "is_sample": True, "fx_per_usd": {"SAR": 3.75}}
        d.update(kw)
        return d

    def test_ok(self):
        self.assertEqual(build.safety_checks(self.cfg, self.latest(), []), [])

    def test_bad_price_and_jump(self):
        hist = [{"gold_usd_oz": 3000, "silver_usd_oz": 64}, {"gold_usd_oz": 4362, "silver_usd_oz": 64.4}]
        self.assertTrue(build.safety_checks(self.cfg, self.latest(), hist))  # +45% jump
        self.assertTrue(build.safety_checks(self.cfg, self.latest(gold_usd_oz=43.62), []))
        self.assertTrue(build.safety_checks(self.cfg, self.latest(fx_per_usd={"SAR": 1}), []))

    def test_stale_live_price(self):
        from unittest import mock
        stale = self.latest(is_sample=False, timestamp_utc="2020-01-01T00:00:00Z")
        with mock.patch.object(build.P, "market_closed", return_value=False):
            self.assertTrue(build.safety_checks(self.cfg, stale, []))
        with mock.patch.object(build.P, "market_closed", return_value=True):  # weekend: Friday's price is fine
            self.assertEqual(build.safety_checks(self.cfg, stale, []), [])


if __name__ == "__main__":
    unittest.main()


class MarketHours(unittest.TestCase):
    def test_weekend_window(self):
        from datetime import datetime, timezone
        utc = lambda *a: datetime(*a, tzinfo=timezone.utc)
        self.assertFalse(P.market_closed(utc(2026, 9, 11, 20, 0)))  # Friday 20:00 open
        self.assertTrue(P.market_closed(utc(2026, 9, 11, 21, 30)))  # Friday 21:30 closed
        self.assertTrue(P.market_closed(utc(2026, 9, 12, 12, 0)))   # Saturday
        self.assertTrue(P.market_closed(utc(2026, 9, 13, 21, 0)))   # Sunday before 22:00
        self.assertFalse(P.market_closed(utc(2026, 9, 13, 22, 30)))  # Sunday reopen
        self.assertFalse(P.market_closed(utc(2026, 9, 14, 9, 0)))   # Monday


class LiveHistory(unittest.TestCase):
    def test_first_live_run_without_history(self):
        """A live provider with no saved history and no backfill must still build (no 'yesterday' comparison)."""
        import tempfile
        from unittest import mock
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        cfg["provider"] = "gold_api"
        fake = {"is_sample": False, "source_name": "test", "timestamp_utc": None, "gold_usd_oz": 4362.0,
                "silver_usd_oz": 64.4, "gold_bid_usd_oz": None, "gold_ask_usd_oz": None,
                "silver_bid_usd_oz": None, "silver_ask_usd_oz": None, "fx_per_usd": {"SAR": 3.75}}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(build, "HISTORY_FILE", Path(tmp) / "history.json"), \
                 mock.patch.object(build, "DIST", Path(tmp) / "dist"), \
                 mock.patch.object(build.providers, "fetch", return_value=fake):
                build.build(cfg)
                self.assertTrue((Path(tmp) / "history.json").exists())
                html = (Path(tmp) / "dist" / "sa" / "up-or-down" / "index.html").read_text(encoding="utf-8")
                self.assertIn("لا توجد مقارنة بعد", html)


class HistorySaving(unittest.TestCase):
    def test_new_day_flag(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(build, "HISTORY_FILE", Path(tmp) / "h.json"):
                hist = [{"date": "2026-09-10", "gold_usd_oz": 4300, "silver_usd_oz": 64},
                        {"date": "2026-09-11", "gold_usd_oz": 4362, "silver_usd_oz": 64.4}]
                self.assertTrue(build.save_history({}, hist))          # first save: new day
                self.assertFalse(build.save_history({}, hist))         # same day again
                hist = hist + [{"date": "2026-09-12", "gold_usd_oz": 4370, "silver_usd_oz": 65}]
                self.assertTrue(build.save_history({}, hist))          # next day


class FrontPage(unittest.TestCase):
    """The domain root must be a real page, not a redirect stub."""

    @classmethod
    def setUpClass(cls):
        # Build into a throwaway folder: never touch dist/, which the scheduled job
        # has just built with REAL prices and is about to upload.
        cls.tmp = Path(tempfile.mkdtemp())
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        cfg["provider"] = "sample"
        real_dist = build.DIST
        build.DIST = cls.tmp
        try:
            build.build(cfg)
        finally:
            build.DIST = real_dist
        cls.html = (cls.tmp / "index.html").read_text(encoding="utf-8")
        cls.sitemap = (cls.tmp / "sitemap.xml").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_is_a_real_page(self):
        self.assertIn("<h1>", self.html)
        self.assertNotIn("http-equiv", self.html)  # no meta refresh stub

    def test_links_to_every_page(self):
        for path in ("/sa/", "/sa/up-or-down/", "/sa/sell-price/", "/sa/calculator/",
                     "/sa/zakat/", "/sa/silver/", "/sa/en/"):
            self.assertIn(f'href="{path}"', self.html, path)

    def test_canonical_and_sitemap(self):
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        base = cfg["base_url"].rstrip("/")
        self.assertIn(f'<link rel="canonical" href="{base}/">', self.html)
        self.assertIn(f"<loc>{base}/</loc>", self.sitemap)

    def test_no_placeholder_brand(self):
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertNotIn("example.com", cfg["base_url"])
        self.assertNotIn("مؤقت", cfg["site_name_ar"])
        self.assertNotIn("placeholder", cfg["site_name_en"].lower())


class RequestBudget(unittest.TestCase):
    """Every API call here is multiplied by ~2,900 builds a month, so the number of
    requests per build is a cost decision, not a detail. Metals.Dev meters per request."""

    CFG = {"provider_api_key_env": "METALS_API_KEY", "sar_per_usd_peg": 3.75}

    def _fake(self, calls):
        def _get(url, timeout=20):
            calls.append(url)
            if "/v1/latest" in url:
                return {"status": "success", "timestamps": {"metal": "2026-09-12T10:00:00Z"},
                        "metals": {"gold": 4362.0, "silver": 64.4},
                        "currencies": {"SAR": 1 / 3.75, "INR": 1 / 88.0, "PKR": 1 / 278.0}}
            return {"rate": {"bid": 4361.8, "ask": 4362.2}}
        return _get

    def _run(self, cfg):
        import providers
        from unittest import mock
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
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg.get("bid_ask_metals"), ["gold"])


class ErrorMessages(unittest.TestCase):
    """A bare 'HTTP Error 400' cost us a stale site for seven hours. Errors must say why."""

    def test_http_error_explains_and_redacts_the_key(self):
        import io, urllib.error, providers
        from unittest import mock
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


class StaleNotice(unittest.TestCase):
    """The build refuses to publish a bad price, so a broken feed leaves the last good
    page online saying 'last updated ...' as if it were current. On 2026-09-12 that
    served a 7-hour-old price for 7 hours. This check runs in the visitor's browser, so
    it still works while the build is down."""

    def js(self, expr):
        script = f'const M = require({json.dumps(str(ROOT / "static" / "calc.js"))});\nconsole.log(JSON.stringify({expr}));'
        return json.loads(subprocess.check_output(["node", "-e", script], text=True))

    def test_age_and_threshold(self):
        now = "Date.parse('2026-09-12T14:00:00Z')"
        self.assertEqual(self.js(f"M.ageMinutes('2026-09-12T11:00:00Z', {now})"), 180)
        self.assertTrue(self.js(f"M.isStale('2026-09-12T11:00:00Z', {now}, 120)"))
        self.assertFalse(self.js(f"M.isStale('2026-09-12T13:00:00Z', {now}, 120)"))

    def test_unparseable_timestamp_never_cries_wolf(self):
        now = "Date.parse('2026-09-12T14:00:00Z')"
        self.assertIsNone(self.js(f"M.ageMinutes('', {now})"))
        self.assertFalse(self.js(f"M.isStale('nonsense', {now}, 120)"))

    def test_arabic_counted_nouns(self):
        # Arabic counts: 2 is dual, 3-10 plural, 11+ back to singular. Getting this
        # wrong on an Arabic site reads as careless.
        cases = {60: "ساعة", 120: "ساعتين", 180: "3 ساعات", 660: "11 ساعة",
                 1440: "يوم", 2880: "يومين", 4320: "3 أيام", 20160: "14 يومًا"}
        for minutes, want in cases.items():
            self.assertEqual(self.js(f"M.humanAge({minutes}, 'ar')"), want, minutes)
        self.assertEqual(self.js("M.humanAge(60, 'en')"), "1 hour")
        self.assertEqual(self.js("M.humanAge(180, 'en')"), "3 hours")

    def test_pages_carry_what_the_browser_needs(self):
        import re
        for page in ("index.html", "sa/index.html", "sa/en/index.html", "sa/silver/index.html"):
            html = (ROOT / "dist" / page).read_text(encoding="utf-8")
            self.assertIn('id="stale-notice"', html, page)
            data = json.loads(re.search(r'id="prices">(.*?)</script>', html, re.S).group(1))
            self.assertRegex(data["updated_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertGreater(data["stale_after_minutes"], 0)
