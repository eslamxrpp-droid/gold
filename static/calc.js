// Calculators and freshness. The price formulas here are the same ones in prices.py, and
// tests/test_prices.py runs both on the same inputs and compares the answers.
//
// Everything on a page reads ONE snapshot - the <script id="prices"> block the build wrote.
// Nothing here fetches a price from the provider; the only network call is for the site's
// own static /data/prices.json, and even that never repaints half the page. If a newer
// snapshot exists the visitor is offered a reload, so the tables, the change figures and the
// calculator always move together instead of disagreeing with each other.
(function (root) {
  "use strict";

  var AR_DIGITS = /[٠-٩۰-۹]/g;

  var M = {
    karatPrice: function (pure, karat) { return pure * karat / 24; },
    purityPrice: function (pure, purity) { return pure * purity / 1000; },
    sellValue: function (weight, karat, bidPure, deductionPct) {
      return weight * M.karatPrice(bidPure, karat) * (1 - (deductionPct || 0) / 100);
    },
    itemValue: function (weight, karat, pure, makingPerGram) {
      return weight * (M.karatPrice(pure, karat) + (makingPerGram || 0));
    },
    zakatGold: function (items, nisab, pure, rate) {
      var g = 0;
      items.forEach(function (it) { g += it[0] * it[1] / 24; });
      var value = g * pure, reached = g >= nisab;
      return { pure_grams: g, reached: reached, value: value, zakat: reached ? value * rate : 0 };
    },
    zakatSilver: function (weight, purity, nisab, pure, rate) {
      var g = weight * purity / 1000, value = g * pure, reached = g >= nisab;
      return { pure_grams: g, reached: reached, value: value, zakat: reached ? value * rate : 0 };
    },

    // --- input parsing --------------------------------------------------
    // Readers type ٢٥٠ and ١٠٫٥ as readily as 250 and 10.5. A calculator that silently
    // returns zero for a number the reader can see on their own keyboard is broken, so
    // Arabic-Indic digits, the Arabic decimal separator and the Arabic thousands separator
    // are all accepted. Anything still unparseable returns null - never a silent 0.
    parseNum: function (raw) {
      if (raw === null || raw === undefined) return null;
      var s = String(raw).trim();
      if (!s) return null;
      s = s.replace(AR_DIGITS, function (d) {
        var c = d.charCodeAt(0);
        return String(c >= 0x06F0 ? c - 0x06F0 : c - 0x0660);
      });
      s = s.replace(/[\u00A0\s']/g, "");
      s = s.replace(/\u066C/g, "");        // U+066C Arabic thousands separator: always thousands
      s = s.replace(/\u066B/g, ".");       // U+066B Arabic decimal separator: always decimal
      // "," and the Arabic comma are genuinely ambiguous - 1,250 is a thousand in one
      // convention and one-and-a-quarter in another. Guessing wrong is a factor of a
      // thousand, so we do not guess: exactly three trailing digits reads as thousands
      // (1,250), one or two reads as a decimal (12,5), anything else is refused outright
      // and the reader is asked to retype it.
      if (/[,\u060C]/.test(s)) {
        var norm = s.replace(/\u060C/g, ",");
        if (/^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$/.test(norm)) {
          s = norm.replace(/,/g, "");            // 1,250 and 1,250.75 -> thousands
        } else if (/^[+-]?\d+,\d{1,2}$/.test(norm)) {
          s = norm.replace(",", ".");            // 12,5 -> decimal
        } else {
          return null;                           // 1,2345 and friends: ask, do not guess
        }
      }
      if (!/^[+-]?\d*\.?\d+$/.test(s)) return null;
      var v = parseFloat(s);
      return isFinite(v) ? v : null;
    },
    // A weight has to be a positive, finite, sane number. 0 is not an error the reader made,
    // it is just nothing to calculate; a negative weight or 10^9 grams is a typo worth saying
    // out loud rather than rendering a confident answer to.
    checkWeight: function (raw, maxGrams) {
      var v = M.parseNum(raw);
      if (v === null) return { ok: false, reason: raw && String(raw).trim() ? "invalid" : "empty" };
      if (v < 0) return { ok: false, reason: "negative" };
      if (v === 0) return { ok: false, reason: "zero" };
      if (v > (maxGrams || 1e7)) return { ok: false, reason: "huge" };
      return { ok: true, value: v };
    },
    toGrams: function (value, unit) { return unit === "kg" ? value * 1000 : value; },

    // --- market session, computed from the READER'S clock -----------------
    // The build's "market closed" flag is true when the page was made and at no other
    // moment. A page built on Saturday and still open on Monday would keep announcing a
    // closed market from that flag - and during an outage no newer page arrives to correct
    // it. So the session is recomputed here from the same window the build uses, which the
    // snapshot ships in `market_session` (weekdays in Python's convention, Monday = 0).
    DEFAULT_SESSION: { close_weekday: 4, close_hour: 21, open_weekday: 6, open_hour: 22 },
    pyWeekday: function (d) { return (d.getUTCDay() + 6) % 7; },   // JS Sunday=0 -> Python Monday=0
    marketClosedAt: function (nowMs, session) {
      var s = session || M.DEFAULT_SESSION;
      var d = new Date(nowMs), wd = M.pyWeekday(d), h = d.getUTCHours();
      return (wd === s.close_weekday && h >= s.close_hour) ||
             wd === 5 ||
             (wd === s.open_weekday && h < s.open_hour);
    },
    lastSessionCloseMs: function (nowMs, session) {
      var s = session || M.DEFAULT_SESSION;
      var d = new Date(nowMs);
      for (var i = 0; i < 14; i++) {
        if (M.pyWeekday(d) === s.close_weekday) {
          var close = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), s.close_hour, 0, 0, 0);
          if (close <= nowMs) return close;
        }
        d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - 1, 23, 59, 59));
      }
      return nowMs;                                   // unreachable in practice
    },
    // How old a quote may be before the page calls it stale. While the market is closed the
    // allowance is measured FROM THE LAST CLOSE, so a correctly frozen weekend price is not
    // flagged, and a feed that died before the close still is.
    staleLimitMinutes: function (nowMs, staleAfterMinutes, session) {
      if (!M.marketClosedAt(nowMs, session)) return staleAfterMinutes;
      var since = (nowMs - M.lastSessionCloseMs(nowMs, session)) / 60000;
      return staleAfterMinutes + Math.max(0, since);
    },

    // --- freshness ------------------------------------------------------
    ageMinutes: function (updatedUtc, nowMs) {
      var t = Date.parse(updatedUtc);
      if (!isFinite(t)) return null;              // unparseable: say nothing rather than cry wolf
      return Math.floor((nowMs - t) / 60000);
    },
    isStale: function (updatedUtc, nowMs, staleAfterMinutes) {
      var m = M.ageMinutes(updatedUtc, nowMs);
      return m === null ? false : m >= staleAfterMinutes;
    },
    // Arabic counted nouns are not "n + singular": 2 is dual, 3-10 takes the plural,
    // 11+ goes back to the singular. Getting this wrong looks careless to a reader.
    humanAge: function (minutes, lang) {
      var ar = lang !== "en";
      var n, unit;
      if (minutes < 1440) { n = Math.max(1, Math.floor(minutes / 60)); unit = "hour"; }
      else { n = Math.floor(minutes / 1440); unit = "day"; }
      if (!ar) return n + " " + unit + (n === 1 ? "" : "s");
      var forms = unit === "hour"
        ? { one: "ساعة", two: "ساعتين", few: "ساعات", many: "ساعة" }
        : { one: "يوم", two: "يومين", few: "أيام", many: "يومًا" };
      if (n === 1) return forms.one;
      if (n === 2) return forms.two;
      if (n <= 10) return n + " " + forms.few;
      return n + " " + forms.many;
    },
    // The whole freshness decision, as data. It lives here rather than inside the DOM code
    // so a test can walk every case - including the one that matters most, a page left open
    // from Saturday into Monday while the feed is down.
    //   hidden  nothing to say
    //   unknown the source gave no quote time
    //   closed  the market is shut and this quote is as recent as that allows
    //   stale   the market is open and the quote is old
    //   outage  older than a closed market can explain: the source stopped publishing
    staleState: function (P, nowMs, lang) {
      if (!P || !P.updated_utc) return { kind: "hidden", text: "" };
      var ar = lang !== "en";
      if (!P.quote_time_known) {
        return { kind: "unknown", text: ar
          ? "وقت هذا السعر غير معروف من المصدر، فلا يمكننا تأكيد أنه محدَّث."
          : "The source did not give a time for this price, so we cannot confirm it is current." };
      }
      var mins = M.ageMinutes(P.updated_utc, nowMs);
      if (mins === null) return { kind: "hidden", text: "" };
      var session = P.market_session;
      var closedNow = M.marketClosedAt(nowMs, session);
      var stale = mins >= M.staleLimitMinutes(nowMs, P.stale_after_minutes, session);
      if (!stale && !closedNow) return { kind: "hidden", text: "" };
      if (!stale && closedNow) {
        // Note what this does NOT say: it never calls the quote "the last close". We know
        // when it was quoted, and that is what the reader is told.
        return { kind: "closed", text: ar
          ? "السوق العالمية مغلقة في هذا الوقت. هذا السعر مسجَّل في " + P.updated + " ولن يتغير حتى تفتح السوق."
          : "The global market is closed at this time. This price was quoted at " + P.updated +
            " and will not move until it reopens." };
      }
      if (closedNow) {
        return { kind: "outage", text: ar
          ? "لم يتم تحديث هذا السعر منذ " + M.humanAge(mins, "ar") +
            "، وهو أقدم مما يفسّره إغلاق السوق. قد يكون المصدر متوقفًا."
          : "This price has not updated for " + M.humanAge(mins, "en") +
            ", which is older than the market closure explains. The source may be down." };
      }
      return { kind: "stale", text: M.staleMessage(mins, lang) };
    },
    staleMessage: function (minutes, lang) {
      var age = M.humanAge(minutes, lang);
      return lang === "en"
        ? "This price has not updated for " + age + ". It may no longer match the market."
        : "لم يتم تحديث هذا السعر منذ " + age + "، وقد لا يطابق السوق الآن.";
    }
  };
  if (typeof module !== "undefined") { module.exports = M; return; }

  // ---------------------------------------------------------------- page code
  function fmt(x, dp) {
    return x.toLocaleString("en-US", {
      minimumFractionDigits: dp === undefined ? 2 : dp,
      maximumFractionDigits: dp === undefined ? 2 : dp
    });
  }
  function $(id) { return document.getElementById(id); }
  var LANG = (document.documentElement.getAttribute("lang") || "ar").slice(0, 2);
  var AR = LANG !== "en";
  function t(ar, en) { return AR ? ar : en; }

  // Analytics is opt-in and off by default (config.json -> analytics.provider). When no
  // adapter is loaded this is a no-op. It never receives an amount, a weight or a quote.
  function track(name, params) {
    try { if (root.mithqalTrack) root.mithqalTrack(name, params || {}); } catch (e) { /* never break a page for a metric */ }
  }

  var PAGE = document.body.getAttribute("data-page") || "page";
  var LOCALE = document.body.getAttribute("data-locale") || "";

  // Inputs survive a price refresh, a reload and the back button. Per page, per tab only -
  // nothing here is sent anywhere, and sessionStorage dies with the tab.
  var STORE_KEY = "mithqal:" + PAGE;
  function saveInputs(scope) {
    try {
      var data = {};
      scope.querySelectorAll("input,select").forEach(function (el) {
        if (el.id && el.type !== "radio") data[el.id] = el.value;
      });
      sessionStorage.setItem(STORE_KEY, JSON.stringify(data));
    } catch (e) { /* private mode, blocked storage: the calculator still works */ }
  }
  function restoreInputs(scope) {
    try {
      var data = JSON.parse(sessionStorage.getItem(STORE_KEY) || "{}");
      Object.keys(data).forEach(function (id) {
        var el = scope.querySelector("#" + CSS.escape(id));
        if (el) el.value = data[id];
      });
      return Object.keys(data).length > 0;
    } catch (e) { return false; }
  }

  function snapshotNote(P) {
    if (!P.quote_time_known) {
      return t("المصدر لم يعطِ وقتًا للتسعيرة، فلا يمكن التأكد من حداثة هذا الحساب.",
               "The source gave no quote time, so this calculation's freshness cannot be confirmed.");
    }
    var base = t("محسوب على سعر ", "Calculated on the ") + P.updated + t(" بتوقيت الرياض.", " Riyadh price.");
    if (M.marketClosedAt(Date.now(), P.market_session)) {
      base += t(" السوق مغلقة في هذا الوقت.", " The market is closed at this time.");
    }
    return base;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var pEl = $("prices");
    if (!pEl) return;
    var P;
    try { P = JSON.parse(pEl.textContent); } catch (e) { return; }

    ["calc-snapshot", "sell-snapshot", "sv-snapshot"].forEach(function (id) {
      if ($(id)) $(id).textContent = snapshotNote(P);
    });

    // ---- gold value calculator
    if ($("value-calc")) {
      var vc = $("value-calc");
      var upd = function () {
        var w = M.checkWeight($("calc-weight").value);
        var out = $("calc-result");
        if (!w.ok) { out.textContent = weightMessage(w.reason); return; }
        var k = +$("calc-karat").value;
        var mk = M.parseNum($("calc-making").value) || 0;
        if (mk < 0) mk = 0;
        var raw = M.itemValue(w.value, k, P.gold_sar_g, 0);
        var total = M.itemValue(w.value, k, P.gold_sar_g, mk);
        out.innerHTML = t("قيمة الذهب: <strong>", "Gold value: <strong>") + fmt(raw) + t(" ريال</strong>", " SAR</strong>") +
          (mk ? t("<br>مع المصنعية: <strong>", "<br>With making charge: <strong>") + fmt(total) + t(" ريال</strong>", " SAR</strong>") : "");
        saveInputs(vc);
      };
      vc.addEventListener("input", upd);
      vc.addEventListener("change", function (e) {
        upd();
        if (e.target.id === "calc-karat") track("karat_selected", { page: PAGE, locale: LOCALE, metal: "gold", karat: e.target.value });
      });
      if (restoreInputs(vc)) upd();
      $("calc-weight").addEventListener("change", function () { track("calculator_used", { page: PAGE, locale: LOCALE, metal: "gold", tool: "value" }); });
    }

    // ---- used-gold sell calculator
    if ($("sell-calc")) {
      var sc = $("sell-calc");
      var updSell = function () {
        var w = M.checkWeight($("sell-weight").value);
        var out = $("sell-result");
        if (!w.ok) { out.textContent = weightMessage(w.reason); return; }
        var k = +$("sell-karat").value;
        var d = M.parseNum($("sell-deduction").value) || 0;
        d = Math.min(Math.max(d, 0), 100);
        var raw = M.sellValue(w.value, k, P.gold_bid_sar_g, 0);
        var net = M.sellValue(w.value, k, P.gold_bid_sar_g, d);
        out.innerHTML = t("القيمة بسعر البيع في السوق: <strong>", "Value at the market bid: <strong>") + fmt(raw) +
          t(" ريال</strong>", " SAR</strong>") +
          (d ? t("<br>بعد خصم المحل ", "<br>After a " ) + d + t("%: <strong>", "% shop deduction: <strong>") + fmt(net) +
               t(" ريال</strong>", " SAR</strong>") : "");
        saveInputs(sc);
      };
      sc.addEventListener("input", updSell);
      sc.addEventListener("change", updSell);
      if (restoreInputs(sc)) updSell();
      $("sell-weight").addEventListener("change", function () { track("calculator_used", { page: PAGE, locale: LOCALE, metal: "gold", tool: "sell" }); });
    }

    // ---- silver bullion calculator (Arabic and English share this code)
    if ($("silver-calc")) {
      var sv = $("silver-calc");
      var updSilver = function () {
        var out = $("sv-result");
        var unit = $("sv-unit").value;
        var w = M.checkWeight($("sv-weight").value, unit === "kg" ? 10000 : 1e7);
        if (!w.ok) { out.textContent = weightMessage(w.reason, "silver"); return; }
        var grams = M.toGrams(w.value, unit);
        var purity = +$("sv-purity").value;
        var perGram = M.purityPrice(P.silver_sar_g, purity);
        var value = grams * perGram;

        var html = t("قيمة المعدن: <strong>", "Metal value: <strong>") + fmt(value) + t(" ريال</strong>", " SAR</strong>") +
          t("<br>الوزن: ", "<br>Weight: ") + fmt(grams, grams < 10 ? 2 : 0) + t(" جرام · عيار ", " g · purity ") + purity +
          t(" · سعر الجرام ", " · per gram ") + fmt(perGram) + t(" ريال", " SAR");

        // A seller's quote. The basis is a choice the reader makes: reading "850" as a
        // per-gram price when it was the total for the bar is how a comparison tool
        // becomes a lie with a nice layout.
        var qRaw = $("sv-quote").value;
        var q = M.parseNum(qRaw);
        if (qRaw && qRaw.trim() && q === null) {
          html += t("<br><span class='warn'>لم نفهم مبلغ العرض.</span>", "<br><span class='warn'>The quote amount could not be read.</span>");
        } else if (q !== null && q > 0) {
          var basis = $("sv-quote-basis").value;
          var quoteTotal = basis === "per_gram" ? q * grams : q;
          var diff = quoteTotal - value;
          var pct = value ? (diff / value) * 100 : 0;
          var word = diff > 0 ? t("أعلى من", "above") : diff < 0 ? t("أقل من", "below") : t("مطابق ل", "equal to");
          html += "<hr>" + t("عرض البائع: <strong>", "Seller's quote: <strong>") + fmt(quoteTotal) +
            t(" ريال</strong> إجمالًا", " SAR</strong> in total") +
            (basis === "per_gram" ? t(" (" + fmt(q) + " ريال × " + fmt(grams, grams < 10 ? 2 : 0) + " جرام)",
                                      " (" + fmt(q) + " SAR × " + fmt(grams, grams < 10 ? 2 : 0) + " g)") : "") +
            "<br><span class='" + (diff > 0 ? "down" : diff < 0 ? "up" : "flat") + "'>" +
            fmt(Math.abs(diff)) + t(" ريال ", " SAR ") + word + t(" قيمة المعدن", " the metal value") +
            " (" + (pct > 0 ? "+" : "") + fmt(pct) + "%)</span>" +
            t("<p class='note'>الفرق طبيعي: يشمل المصنعية وهامش البائع والضريبة إن وُجدت. هذه مقارنة بقيمة المعدن فقط، وليست حكمًا على البائع.</p>",
              "<p class='note'>A difference is normal: it covers fabrication, the seller's margin and any tax. This compares against metal value only; it is not a judgement of the seller.</p>");
          track("quote_compared", { page: PAGE, locale: LOCALE, metal: "silver", basis: basis });
        }
        out.innerHTML = html;
        saveInputs(sv);
      };
      sv.addEventListener("input", updSilver);
      sv.addEventListener("change", function (e) {
        updSilver();
        if (e.target.id === "sv-purity") track("purity_selected", { page: PAGE, locale: LOCALE, metal: "silver", purity: e.target.value });
        if (e.target.id === "sv-unit") track("unit_selected", { page: PAGE, locale: LOCALE, metal: "silver", unit: e.target.value });
      });
      sv.querySelectorAll(".presets button").forEach(function (b) {
        b.addEventListener("click", function () {
          $("sv-weight").value = b.getAttribute("data-weight");
          $("sv-unit").value = b.getAttribute("data-unit");
          updSilver();
          track("preset_used", { page: PAGE, locale: LOCALE, metal: "silver", preset: b.getAttribute("data-weight") + b.getAttribute("data-unit") });
        });
      });
      if (restoreInputs(sv)) updSilver();
      $("sv-weight").addEventListener("change", function () { track("calculator_used", { page: PAGE, locale: LOCALE, metal: "silver", tool: "value" }); });
    }

    function weightMessage(reason, metal) {
      var what = metal === "silver" ? t("قيمة الفضة", "the metal value") : t("القيمة", "the value");
      if (reason === "empty") return t("أدخل الوزن لعرض ", "Enter a weight to see ") + what;
      if (reason === "negative") return t("الوزن لا يكون بالسالب.", "A weight cannot be negative.");
      if (reason === "zero") return t("أدخل وزنًا أكبر من صفر.", "Enter a weight greater than zero.");
      if (reason === "huge") return t("هذا الوزن كبير جدًا — تأكد من الرقم والوحدة.", "That weight looks too large — check the number and the unit.");
      return t("لم نفهم الرقم. اكتب الوزن بالأرقام، مثل 12.5", "That number could not be read. Enter digits, for example 12.5");
    }

    // ---- zakat calculator
    if ($("zakat-calc")) {
      var zc = $("zakat-calc");
      var rows = $("zakat-gold-rows");
      $("zakat-add-row").addEventListener("click", function () {
        var r = rows.querySelector(".row").cloneNode(true);
        r.querySelector("input").value = "";
        rows.appendChild(r);
      });
      var updZakat = function () {
        var items = [];
        rows.querySelectorAll(".row").forEach(function (r) {
          var w = M.parseNum(r.querySelector(".zg-weight").value);
          if (w && w > 0) items.push([w, +r.querySelector(".zg-karat").value]);
        });
        var sw = M.parseNum($("zs-weight").value) || 0;
        var sp = +$("zs-purity").value;
        var jewel = document.querySelector("input[name=zjewel]:checked").value === "yes";
        if (!items.length && sw <= 0) { $("zakat-result").textContent = "أدخل الوزن لعرض النتيجة"; return; }
        var html = "";
        if (items.length) {
          html += "<strong>الذهب</strong>";
          var first = M.zakatGold(items, P.gold_nisab[0], P.gold_sar_g, P.zakat_rate);
          html += "<br>الذهب الخالص: " + fmt(first.pure_grams) + " جرام · القيمة اليوم: " + fmt(first.value) + " ريال";
          html += "<table><tr><th>على نصاب</th><th>بلغ النصاب؟</th><th>الزكاة (ريال)</th></tr>";
          P.gold_nisab.forEach(function (n) {
            var z = M.zakatGold(items, n, P.gold_sar_g, P.zakat_rate);
            html += "<tr><td>" + n + " جرام</td><td>" + (z.reached ? "نعم" : "لا") + "</td><td>" + fmt(z.zakat) + "</td></tr>";
          });
          html += "</table>";
        }
        if (sw > 0) {
          html += (html ? "<br>" : "") + "<strong>الفضة</strong>";
          P.silver_nisab.forEach(function (n) {
            var z = M.zakatSilver(sw, sp, n, P.silver_sar_g, P.zakat_rate);
            html += "<br>الفضة الخالصة: " + fmt(z.pure_grams) + " جرام · نصاب " + n + " جرام: " + (z.reached ? "بلغ" : "لم يبلغ") +
              " · الزكاة: " + fmt(z.zakat) + " ريال";
          });
        }
        if (jewel) {
          html += "<p class='note'>بما أنه حُليّ معدّ للاستعمال: الأرقام أعلاه على قول من يوجب الزكاة فيه، وعلى قول الجمهور لا زكاة فيه. انظر القولين ومصادرهما أسفل الصفحة.</p>";
        }
        html += "<p class='note'>تجب الزكاة إذا حال على المال حول هجري كامل. هذه النتيجة تقديرية وليست فتوى.</p>";
        $("zakat-result").innerHTML = html;
        saveInputs(zc);
      };
      zc.addEventListener("input", updZakat);
      zc.addEventListener("change", updZakat);
      if (restoreInputs(zc)) updZakat();
    }

    // ---- language switch is worth measuring: it says the reader landed in the wrong one
    var ls = document.querySelector("a.lang");
    if (ls) ls.addEventListener("click", function () { track("language_switch", { page: PAGE, locale: LOCALE }); });

    // ---- freshness, checked continuously ------------------------------
    // The old build checked once at DOMContentLoaded. A page left open on a phone overnight
    // kept presenting a stale price as current, and a page open through a feed outage never
    // learned about it. Now it re-checks on a timer and whenever the tab comes back.
    var box = $("stale-notice");
    var refreshBox = $("refresh-notice");
    var CHECK_MS = 60000;

    function renderStale() {
      if (!box) return;
      var s = M.staleState(P, Date.now(), LANG);
      if (s.kind === "hidden") { box.hidden = true; return; }
      box.className = "banner " + (s.kind === "closed" ? "closed" : "stale");
      box.textContent = s.text;
      box.hidden = false;
    }

    // Ask the site's own static snapshot - not the price provider - whether a newer build
    // exists. Offering a reload rather than repainting pieces of the page is deliberate:
    // half-updated numbers are worse than slightly old ones.
    var offered = false;
    function checkForNewer() {
      if (offered || !refreshBox || !P.snapshot_url || !root.fetch) return;
      fetch(P.snapshot_url, { cache: "no-store" }).then(function (r) {
        return r.ok ? r.json() : null;
      }).then(function (s) {
        if (!s || !s.quoted_utc || !P.quoted_utc) return;
        if (Date.parse(s.quoted_utc) <= Date.parse(P.quoted_utc)) return;
        offered = true;
        refreshBox.innerHTML = t("يتوفر سعر أحدث. ", "A newer price is available. ") +
          "<button type='button' id='do-refresh'>" + t("حدّث الصفحة", "Refresh") + "</button>";
        refreshBox.hidden = false;
        $("do-refresh").addEventListener("click", function () {
          track("refresh_clicked", { page: PAGE, locale: LOCALE });
          root.location.reload();          // inputs come back from sessionStorage
        });
      }).catch(function () { /* offline or blocked: the page stays exactly as it is */ });
    }

    renderStale();
    setInterval(function () { renderStale(); checkForNewer(); }, CHECK_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { renderStale(); checkForNewer(); }
    });
  });
})(this);
