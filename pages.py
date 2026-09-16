"""The page registry: the single place that knows which pages exist.

Navigation, canonicals, hreflang, breadcrumbs and sitemap.xml are all generated from this
list, so they cannot drift apart. Adding a page means adding one entry here; forgetting to
add it to the sitemap or to the language switch is no longer possible.

Field meanings
--------------
key             stable id, used by tests and by `alt_group` pairing
path            public URL path, always with a trailing slash (except "/")
lang            "ar" or "en"        hreflang  the full tag published in <link rel=alternate>
template        file in templates/
role            what this page is FOR, in one line. Two pages must never share a role
                within a language - that is the duplicate-content test.
alt_group       pages sharing a group are translations of each other. Only pages with the
                SAME role may share a group; a group with one member publishes no hreflang.
nav             show in the main header nav for its language
sitemap         include in sitemap.xml (indexable, canonical, returns 200)
price_driven    the visible content changes when the price data changes, so lastmod is the
                data's business date. False -> lastmod is `content_updated`, edited by hand.
content_updated the date the WORDING of the page last changed. Never stamped by a build.
title/desc      format strings; `{g21}` etc. come from the build's `common` dict.
"""

SITE_TITLE_MAX = 60      # characters, not bytes: Arabic counts the same as Latin here
SITE_DESC_MAX = 155


class Page:
    def __init__(self, key, path, lang, template, role, title, description,
                 alt_group=None, nav=None, sitemap=True, price_driven=True,
                 content_updated="2026-09-12", breadcrumb=None, noindex=False, status="live", hreflang=None):
        self.key = key
        self.path = path
        self.lang = lang
        self.hreflang = hreflang or ("ar-SA" if lang == "ar" else "en-SA")
        self.template = template
        self.role = role
        self.title = title
        self.description = description
        self.alt_group = alt_group
        self.nav = nav                  # short label for the header nav, or None
        self.sitemap = sitemap
        self.price_driven = price_driven
        self.content_updated = content_updated
        self.breadcrumb = breadcrumb or []   # list of (label, path) above this page
        self.noindex = noindex
        self.status = status            # live | pending  (pending pages are not built)

    def __repr__(self):
        return f"<Page {self.key} {self.path}>"


AR_HOME = ("الرئيسية", "/")
EN_HOME = ("Mithqal", "/sa/en/")

PAGES = [
    Page(
        key="app_privacy_en", path="/apps/gold-price-today/privacy/", lang="en",
        template="app_privacy_en.html", hreflang="en",
        role="Privacy policy for the Gold Price Today Android app by Mithqal Labs.",
        title="Gold Price Today Privacy Policy | Mithqal Labs",
        description="Privacy policy for the Gold Price Today Android app by Mithqal Labs: on-device data, disabled Firebase collection and privacy contact.",
        alt_group="app_privacy", price_driven=False, content_updated="2026-09-16",
    ),
    Page(
        key="app_privacy_ar", path="/ar/apps/gold-price-today/privacy/", lang="ar",
        template="app_privacy_ar.html", hreflang="ar",
        role="Privacy policy for the Gold Price Today Android app by Mithqal Labs.",
        title="سياسة خصوصية Gold Price Today | Mithqal Labs",
        description="سياسة خصوصية تطبيق Gold Price Today من Mithqal Labs: البيانات المحلية، وتعطيل جمع بيانات Firebase، والتواصل بشأن الخصوصية.",
        alt_group="app_privacy", price_driven=False, content_updated="2026-09-16",
    ),
    # ---------------------------------------------------------------- Arabic
    Page(
        key="home", path="/", lang="ar", template="home.html",
        role="Brand entry: what Mithqal is, today's gold and silver headline, and the way in to every tool.",
        title="أسعار الذهب والفضة اليوم في السعودية | مثقال",
        description="أسعار الذهب والفضة اليوم في السعودية بالريال: سعر الجرام لكل عيار، حاسبة الذهب، حاسبة الفضة وحاسبة الزكاة. مصدر السعر ووقته معلنان.",
        alt_group=None, nav=None,
    ),
    Page(
        key="sa_gold", path="/sa/", lang="ar", template="sa_index.html",
        role="The detailed Saudi gold price page: every karat, the recorded daily history, how the price is derived.",
        title="سعر الذهب في السعودية اليوم: عيار 21 = {g21} ريال",
        description="كم سعر الذهب اليوم في السعودية؟ سعر جرام الذهب لعيار 24 و22 و21 و18 بالريال السعودي، مع حركة السعر في آخر الأيام المسجلة.",
        alt_group="sa_gold", nav="الذهب", breadcrumb=[AR_HOME],
    ),
    Page(
        key="sa_silver", path="/sa/silver/", lang="ar", template="sa_silver.html",
        role="The Saudi silver page: 999 and 925 per gram, ounce, kilo and bar, plus the silver value calculator.",
        title="سعر الفضة اليوم في السعودية: جرام 999 = {s999} ريال",
        description="كم سعر الفضة اليوم؟ سعر جرام الفضة عيار 999 و925 في السعودية، وسعر الأونصة والكيلو والسبيكة بالريال، مع حاسبة قيمة الفضة.",
        alt_group="sa_silver", nav="الفضة", breadcrumb=[AR_HOME],
    ),
    Page(
        key="sa_calculator", path="/sa/calculator/", lang="ar", template="sa_calculator.html",
        role="Gold value calculator: weight and karat to riyals, with an optional making charge.",
        title="حاسبة الذهب: احسب قيمة ذهبك اليوم بالوزن والعيار",
        description="حاسبة سعر الذهب اليوم في السعودية: أدخل الوزن بالجرام واختر العيار لتعرف قيمة الذهب بالريال، مع المصنعية إن أردت.",
        alt_group="sa_calculator", nav="الحاسبة", breadcrumb=[AR_HOME],
    ),
    Page(
        key="sa_zakat", path="/sa/zakat/", lang="ar", template="sa_zakat.html",
        role="Zakat on gold and silver: today's nisab in riyals, the calculator, and the cited scholarly positions.",
        title="حاسبة زكاة الذهب: كم نصاب زكاة الذهب اليوم بالريال؟",
        description="حساب زكاة الذهب والفضة: النصاب اليوم بالريال السعودي، ومتى تجب الزكاة، وهل الذهب الملبوس عليه زكاة — بأقوال أهل العلم ومصادرها.",
        alt_group=None,  # the English counterpart is deliberately not published yet
        nav="الزكاة", breadcrumb=[AR_HOME], content_updated="2026-09-12",
    ),
    Page(
        key="sa_sell", path="/sa/sell-price/", lang="ar", template="sa_sell_price.html",
        role="Bid and ask per karat, and what a used-gold seller can expect before the shop's deduction.",
        title="سعر الذهب اليوم في السعودية بيع وشراء لكل عيار",
        description="كم سعر جرام الذهب اليوم في السعودية بيع وشراء؟ سعر البيع والشراء لكل عيار، وحاسبة تقدّر قيمة ذهبك المستعمل قبل البيع.",
        alt_group="sa_sell", nav=None, breadcrumb=[AR_HOME],
    ),
    Page(
        key="sa_updown", path="/sa/up-or-down/", lang="ar", template="sa_up_or_down.html",
        role="Direction only: is gold up or down against the last recorded close, and against the week.",
        title="أسعار الذهب اليوم: مرتفع ولا نازل؟",
        description="هل الذهب مرتفع ولا نازل اليوم في السعودية؟ التغير مقارنة بآخر إغلاق مسجّل وبالأسبوع الماضي لكل عيار، بالتاريخ الفعلي للمقارنة.",
        alt_group="sa_updown", nav=None, breadcrumb=[AR_HOME],
    ),
    Page(
        key="sa_method", path="/sa/methodology/", lang="ar", template="sa_methodology.html",
        role="Methodology and about: source, conversion, freshness rules, what we do not publish, corrections.",
        title="كيف نحسب الأسعار؟ منهجية مثقال ومصادرها",
        description="من أين يأتي سعر الذهب والفضة في مثقال، وكيف يُحوَّل إلى الريال، وكم يتأخر، ومتى نتوقف عن النشر — ولماذا لا نعرض أسعار المحلات.",
        alt_group="sa_method", nav="المنهجية", breadcrumb=[AR_HOME],
        price_driven=False, content_updated="2026-09-12",
    ),

    # --------------------------------------------------------------- English
    Page(
        key="en_gold", path="/sa/en/", lang="en", template="sa_en.html",
        role="The detailed Saudi gold price page: every karat, the recorded daily history, how the price is derived.",
        title="Today Gold Rate in Saudi Arabia (KSA): 24K SAR {g24}/g",
        description="Today gold rate in Saudi Arabia (KSA) per gram for 24k, 22k, 21k and 18k, plus 10 grams, tola and ounce in Saudi riyals. Source and quote time shown.",
        alt_group="sa_gold", nav="Gold", breadcrumb=[EN_HOME],
    ),
    Page(
        key="en_silver", path="/sa/en/silver/", lang="en", template="sa_en_silver.html",
        role="The Saudi silver page: 999 and 925 per gram, ounce, kilo and bar, plus the silver value calculator.",
        title="Silver Price Today in Saudi Arabia: 999 = SAR {s999}/g",
        description="Silver price today in Saudi Arabia per gram, 10 g, 100 g and 1 kg for 999 and 925 silver, in riyals, with a calculator that compares a seller's quote to metal value.",
        alt_group="sa_silver", nav="Silver", breadcrumb=[EN_HOME],
    ),
    Page(
        key="en_calculator", path="/sa/en/calculator/", lang="en", template="sa_en_calculator.html",
        role="Gold value calculator: weight and karat to riyals, with an optional making charge.",
        title="Gold Calculator: Value of Your Gold in Saudi Riyals",
        description="Enter the weight in grams and pick the karat to get today's gold value in Saudi riyals, with an optional making charge per gram.",
        alt_group="sa_calculator", nav="Calculator", breadcrumb=[EN_HOME],
    ),
    Page(
        key="en_sell", path="/sa/en/sell-price/", lang="en", template="sa_en_sell_price.html",
        role="Bid and ask per karat, and what a used-gold seller can expect before the shop's deduction.",
        title="Gold Buying and Selling Price in Saudi Arabia Today",
        description="Today's gold bid and ask per karat in Saudi riyals, and a calculator that estimates what used gold is worth before a shop's deduction.",
        alt_group="sa_sell", nav=None, breadcrumb=[EN_HOME],
    ),
    Page(
        key="en_updown", path="/sa/en/up-or-down/", lang="en", template="sa_en_up_or_down.html",
        role="Direction only: is gold up or down against the last recorded close, and against the week.",
        title="Is Gold Up or Down Today in Saudi Arabia?",
        description="Is the gold rate up or down today in Saudi Arabia? The change against the last recorded close and against last week, per karat, with the dates compared.",
        alt_group="sa_updown", nav=None, breadcrumb=[EN_HOME],
    ),
    Page(
        key="en_method", path="/sa/en/methodology/", lang="en", template="sa_en_methodology.html",
        role="Methodology and about: source, conversion, freshness rules, what we do not publish, corrections.",
        title="How Mithqal Calculates Gold and Silver Prices",
        description="Where Mithqal's gold and silver prices come from, how they are converted to riyals, how fresh they are, when we stop publishing, and what we never publish.",
        alt_group="sa_method", nav="Methodology", breadcrumb=[EN_HOME],
        price_driven=False, content_updated="2026-09-12",
    ),
]

# Deliberately NOT built. Kept here so the gap is visible in the registry rather than
# forgotten. See PAGE-MAP.md for the reason and what would unblock it.
PENDING = [
    Page(
        key="en_zakat", path="/sa/en/zakat/", lang="en", template="sa_en_zakat.html",
        role="Zakat on gold and silver: today's nisab in riyals, the calculator, and the cited scholarly positions.",
        title="", description="", alt_group=None, sitemap=False, status="pending",
    ),
]

LANG_SWITCH_LABEL = {"ar": "English", "en": "العربية"}


def by_key(key):
    for p in PAGES:
        if p.key == key:
            return p
    raise KeyError(key)


def nav_items(lang):
    """Header nav for a language: the primary pages only. Everything else is reachable from
    the page cards and the footer index, which is how the header stays one line on a phone."""
    return [(p.path, p.nav) for p in PAGES if p.lang == lang and p.nav]


def alternates(page):
    """Reciprocal hreflang pairs. Only equivalent pages (same role, same alt_group) pair up,
    and a group of one publishes nothing - an unpaired page must not claim a translation."""
    if not page.alt_group:
        return []
    group = [p for p in PAGES if p.alt_group == page.alt_group]
    if len(group) < 2:
        return []
    return [(p.hreflang, p.path) for p in group]


def language_switch(page):
    """(path, label) of this page's counterpart in the other language, or the other
    language's home when this page has no counterpart. Never a dead end."""
    other = "en" if page.lang == "ar" else "ar"
    for hl, path in alternates(page):
        if hl.startswith(other):
            return path, LANG_SWITCH_LABEL[page.lang]
    return ("/sa/en/" if other == "en" else "/"), LANG_SWITCH_LABEL[page.lang]


def sitemap_pages(config):
    """Only pages that are canonical, indexable and actually built."""
    if config.get("noindex"):
        return []
    return [p for p in PAGES if p.sitemap and not p.noindex and p.status == "live"]


def check_registry():
    """Self-check run by the build and by the tests. Returns a list of problems."""
    problems = []
    seen_paths, seen_keys = set(), set()
    for p in PAGES:
        if p.path in seen_paths:
            problems.append(f"duplicate path {p.path}")
        if p.key in seen_keys:
            problems.append(f"duplicate key {p.key}")
        seen_paths.add(p.path)
        seen_keys.add(p.key)
        if p.path != "/" and not p.path.endswith("/"):
            problems.append(f"{p.key}: path must end with a slash")
        if not p.title or not p.description:
            problems.append(f"{p.key}: missing title or description")
    # two pages in the same language must not claim the same job
    for lang in ("ar", "en"):
        roles = {}
        for p in PAGES:
            if p.lang != lang:
                continue
            if p.role in roles:
                problems.append(f"{p.key} and {roles[p.role]} share a role in {lang}")
            roles[p.role] = p.key
    # a translation pair must be the same page in two languages, not two different pages
    groups = {}
    for p in PAGES:
        if p.alt_group:
            groups.setdefault(p.alt_group, []).append(p)
    for g, members in groups.items():
        if len({m.lang for m in members}) != len(members):
            problems.append(f"alt_group {g} has two pages in the same language")
        if len({m.role for m in members}) != 1:
            problems.append(f"alt_group {g} pairs pages with different roles")
    return problems
