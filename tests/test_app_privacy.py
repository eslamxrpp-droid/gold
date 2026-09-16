"""App-policy regressions: static content, no price/analytics code or private config."""
import re
import unittest
from html.parser import HTMLParser

import build
import pages as REG
from test_prices import CONFIG, SampleSite


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class AppPrivacy(unittest.TestCase):
    paths = ("apps/gold-price-today/privacy/index.html",
             "ar/apps/gold-price-today/privacy/index.html")

    def test_identity_and_public_contacts_in_both_languages(self):
        for rel in self.paths:
            doc = SampleSite.html(rel)
            for expected in ("Gold Price Today", "Mithqal Labs", "com.gold.price.today",
                             "mailto:privacy@mithqalprice.com", "mailto:support@mithqalprice.com"):
                self.assertIn(expected, doc)

    def test_no_price_runtime_or_new_tracking(self):
        for rel in self.paths:
            doc = SampleSite.html(rel)
            for banned in ('id="prices"', "calc.js", 'id="stale-notice"',
                           'id="refresh-notice"', "<script", "<iframe", "<form",
                           "googletagmanager", "google-analytics.com", "beacon.min.js"):
                self.assertNotIn(banned, doc)
            self.assertIn('data-snapshot=""', doc)
            self.assertIn("/static/style.css?v=", doc)

    def test_canonical_and_global_language_pair(self):
        base = CONFIG["base_url"]
        for rel, key, lang in zip(self.paths, ("app_privacy_en", "app_privacy_ar"), ("en", "ar")):
            doc = SampleSite.html(rel)
            page = REG.by_key(key)
            self.assertIn(f'<link rel="canonical" href="{base}{page.path}">', doc)
            self.assertIn(f'<html lang="{lang}" dir="{"rtl" if lang == "ar" else "ltr"}">', doc)
            self.assertIn(f"hreflang='en' href='{base}/apps/gold-price-today/privacy/'", doc)
            self.assertIn(f"hreflang='ar' href='{base}/ar/apps/gold-price-today/privacy/'", doc)
            self.assertFalse(page.price_driven)
            self.assertIn(page.path, (SampleSite.path() / "sitemap.xml").read_text(encoding="utf-8"))

    def test_footer_links_and_no_misleading_price_footer(self):
        for lang in ("en", "ar"):
            self.assertIn(REG.by_key(f"app_privacy_{lang}").path, build.footer_index(lang))
        for rel in self.paths:
            self.assertNotIn('class="fresh"', SampleSite.html(rel))

    def test_current_firebase_and_price_states_are_explicit(self):
        doc = SampleSite.html(self.paths[0])
        for expected in ("Firebase Analytics collection and automatic Crashlytics crash-report collection are disabled",
                         "App analytics event logging is also disabled",
                         "There is currently no in-app switch", "maintain local technical state",
                         "Price fetching is not configured in the current release",
                         "does not currently make price-data requests", "requesting IP address",
                         "Calculator inputs are not needed for price requests"):
            self.assertIn(expected, doc)

    def test_no_visible_placeholders_or_private_configuration(self):
        for rel in self.paths:
            doc = SampleSite.html(rel)
            text = TextOnly()
            text.feed(doc)
            visible = " ".join(text.parts).lower()
            for banned in ("todo", "tbd", "placeholder", "example.com", "gmail.com",
                           "api_key", "google-services.json", "private key", "{{", "}}"):
                self.assertNotIn(banned, doc.lower())
            self.assertNotIn("sample", visible)
            emails = set(re.findall(r"mailto:([^\"']+)", doc))
            self.assertEqual(emails, {"privacy@mithqalprice.com", "support@mithqalprice.com"})

    def test_english_primary_content_is_not_arabic(self):
        main = SampleSite.html(self.paths[0]).split('<main id="main">')[1].split("</main>")[0]
        self.assertNotRegex(main, r"[\u0600-\u06ff]")

    def test_policy_date_matches_static_sitemap_date(self):
        for rel, key in zip(self.paths, ("app_privacy_en", "app_privacy_ar")):
            date = REG.by_key(key).content_updated
            self.assertIn(f'<time datetime="{date}">', SampleSite.html(rel))
