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

    // --- gold bullion (bars) ---------------------------------------------
    // A bar is sold by FINENESS in parts per thousand, not by karat. 999.9 means 99.99%
    // gold; "24K" is the rounded jeweller's word for roughly the same idea and is NOT an
    // exact synonym, so it is never used here as one. A bar's fineness is applied ONCE,
    // to the pure-gold reference the page already carries - never on top of a karat
    // conversion, which would shrink the metal twice.
    BULLION_FINENESS: [999, 999.9],

    // Display policy, in one place because two figures have to agree with one word.
    // Every SAR and percent figure the reader sees is rounded to 2 dp HERE, and the
    // above/below/equal word is then decided from that SAME rounded number. Deciding the
    // word from the full-precision value instead is how a page ends up printing "-0.00"
    // or saying "below the metal value" next to "0.00 SAR".
    // Rounding is half-away-from-zero applied to the binary double, so a value stored as
    // 504.49499999999995 (typed as 504.495) displays as 504.49, and the formatter is
    // always handed the already-rounded number so the two can never disagree.
    round2: function (x) {
      if (!isFinite(x)) return x;
      var s = x < 0 ? -1 : 1;
      return s * Math.round(Math.abs(x) * 100) / 100;
    },
    signState: function (rounded) { return rounded > 0 ? "above" : rounded < 0 ? "below" : "equal"; },

    // An offer is a TOTAL the reader was quoted. Blank is "no offer", which is not the
    // same thing as zero: zero is a number nobody was ever quoted, so it is an error to
    // say out loud rather than a premium of minus the whole bar.
    checkOffer: function (raw) {
      if (raw === null || raw === undefined || !String(raw).trim()) return { state: "absent" };
      var v = M.parseNum(raw);
      if (v === null) return { state: "invalid", reason: "unreadable" };
      if (v < 0) return { state: "invalid", reason: "negative" };
      if (v === 0) return { state: "invalid", reason: "zero" };
      return { state: "ok", value: v };
    },

    // The whole bullion calculation as data, so the tests can walk every case without a
    // browser. Nothing here reaches the network or the DOM.
    bullion: function (input) {
      input = input || {};
      var unit = input.unit === "kg" ? "kg" : "g";
      // Same bounds the silver tool uses: 10,000 kg and 10,000,000 g are the same ceiling
      // in grams, so a kg entry cannot slip past a limit a gram entry would hit.
      var w = M.checkWeight(input.weight, unit === "kg" ? 10000 : 1e7);
      if (!w.ok) return { ok: false, unit: unit, weight: w };

      var grams = M.toGrams(w.value, unit);
      var fineness = M.parseNum(input.fineness);
      if (fineness === null || !(fineness > 0) || fineness > 1000) fineness = 999.9;

      var pure = input.goldSarG;
      // A snapshot that is missing, NaN, Infinity or non-positive must not become a
      // reference value, a per-gram price or a premium. It produces no figure at all.
      var snapshotOk = typeof pure === "number" && isFinite(pure) && pure > 0;
      var fineGrams = grams * fineness / 1000;
      var perGramFine = snapshotOk ? M.purityPrice(pure, fineness) : null;
      var reference = snapshotOk ? fineGrams * pure : null;

      var offers = {};
      ["a", "b"].forEach(function (key) {
        var o = M.checkOffer(input[key === "a" ? "offerA" : "offerB"]);
        if (o.state === "ok") {
          o.perGram = o.value / grams;              // GROSS bar grams, labelled as such
          o.perGramShown = M.round2(o.perGram);
          o.totalShown = M.round2(o.value);
          if (reference !== null) {
            o.premiumSar = o.value - reference;
            o.premiumPct = o.premiumSar / reference * 100;
            o.premiumSarShown = M.round2(o.premiumSar);
            o.direction = M.signState(o.premiumSarShown);
            // When the SAR difference rounds to nothing, the percent says nothing either,
            // so the reader never sees "equal" beside "+0.04%".
            o.premiumPctShown = o.direction === "equal" ? 0 : M.round2(o.premiumPct);
          }
        }
        offers[key] = o;
      });

      // Which total is lower is decided on the FULL-PRECISION numbers; only the gap the
      // reader is shown is rounded. If that gap rounds to zero the two totals are
      // presented as equal at the precision shown, rather than "A is lower by 0.00".
      var compare = null;
      if (offers.a.state === "ok" && offers.b.state === "ok") {
        var rawDiff = offers.a.value - offers.b.value;
        var shown = M.round2(Math.abs(rawDiff));
        compare = shown === 0
          ? { lower: "equal", diffSar: 0 }
          : { lower: rawDiff < 0 ? "a" : "b", diffSar: shown };
      }

      return {
        ok: true, unit: unit, weight: w.value, grams: grams, fineness: fineness,
        finenessPercent: fineness / 10, fineGrams: fineGrams, perGramFine: perGramFine,
        reference: reference, snapshotOk: snapshotOk, offers: offers, compare: compare
      };
    },

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

  // One page can carry more than one tool. The stored shape is unchanged - a flat
  // {inputId: value} object per page, so values saved by an earlier build still restore -
  // but a save now MERGES its own scope's ids into that object instead of replacing it.
  // It used to replace it: the moment the gold calculator page grew a second tool, typing
  // in either one silently erased the other's saved values, and a refresh brought back
  // half a form. A save still writes every one of its own inputs every time, including
  // the empty ones, so clearing a field really clears it and a reload cannot resurrect it.
  function readStore() {
    try {
      var data = JSON.parse(sessionStorage.getItem(STORE_KEY) || "{}");
      return (data && typeof data === "object" && !Array.isArray(data)) ? data : {};
    } catch (e) { return {}; }
  }
  function saveInputs(scope) {
    try {
      var data = readStore();
      scope.querySelectorAll("input,select").forEach(function (el) {
        if (el.id && el.type !== "radio") data[el.id] = el.value;
      });
      sessionStorage.setItem(STORE_KEY, JSON.stringify(data));
    } catch (e) { /* private mode, blocked storage: the calculator still works */ }
  }
  // Reports whether THIS scope got anything back. Counting the whole object instead made
  // an empty tool render a result the moment a different tool on the page had saved
  // something - an answer to a question the reader never asked.
  function restoreInputs(scope) {
    try {
      var data = readStore(), restored = 0;
      Object.keys(data).forEach(function (id) {
        var el = scope.querySelector("#" + CSS.escape(id));
        if (el) { el.value = data[id]; restored++; }
      });
      return restored > 0;
    } catch (e) { return false; }
  }

  // Two different kinds of fact live in these notes, and only one of them is fixed.
  // The QUOTE TIME is a property of the snapshot: it is decided when the page is built and
  // must never move afterwards. "The market is closed at this time" is a statement about the
  // READER'S present, computed from their clock - so it goes stale exactly the way the old
  // once-at-load banner did. A page left open across the Sunday reopening sat there saying
  // the market was closed while the banner above it had already stopped saying so. These
  // notes are therefore re-rendered on the same timer and tab-return that drive the banner.
  // Re-rendering never re-reads a price and never advances the quote time.
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

  // The bullion note names the provider as well, because that tool's whole claim is "this is
  // what the metal in the bar is worth, according to someone, at a stated moment".
  function bullionSnapshotNote(P, source) {
    if (!P.quote_time_known) {
      return t("المرجع: سعر الذهب الخالص من " + source + "؛ المصدر لم يعطِ وقتًا للتسعيرة، فلا يمكن تأكيد حداثة هذا الحساب.",
               "Reference: pure gold from " + source + "; the source gave no quote time, so this calculation's freshness cannot be confirmed.");
    }
    var note = t("المرجع: سعر الذهب الخالص من " + source + "، تسعيرة " + P.updated + " بتوقيت الرياض.",
                 "Reference: pure gold from " + source + ", quoted " + P.updated + " Riyadh time.");
    if (M.marketClosedAt(Date.now(), P.market_session)) {
      note += t(" السوق مغلقة في هذا الوقت.", " The market is closed at this time.");
    }
    return note;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var pEl = $("prices");
    if (!pEl) return;
    var P;
    try { P = JSON.parse(pEl.textContent); } catch (e) { return; }

    function renderSnapshotNotes() {
      ["calc-snapshot", "sell-snapshot", "sv-snapshot"].forEach(function (id) {
        if ($(id)) $(id).textContent = snapshotNote(P);
      });
      var bl = $("bl-snapshot"), section = $("bullion-calc");
      if (bl && section) {
        bl.textContent = bullionSnapshotNote(P, section.getAttribute("data-source") || "");
      }
    }
    renderSnapshotNotes();

    // ---- gold value calculator
    if ($("value-calc")) {
      var vc = $("value-calc");
      var upd = function () {
        // Persist FIRST, before any validation can return. Every early return below is a
        // state the reader can walk away from - an emptied field most of all - and a return
        // that skipped the save left the PREVIOUS value in storage, so a reload brought back
        // a number the reader had deliberately deleted. Saving first also means the stored
        // object always matches the boxes on screen, whatever path the handler takes.
        saveInputs(vc);
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
      };
      vc.addEventListener("input", upd);
      vc.addEventListener("change", function (e) {
        upd();
        if (e.target.id === "calc-karat") track("karat_selected", { page: PAGE, locale: LOCALE, metal: "gold", karat: e.target.value });
      });
      if (restoreInputs(vc)) upd();
      $("calc-weight").addEventListener("change", function () { track("calculator_used", { page: PAGE, locale: LOCALE, metal: "gold", tool: "value" }); });
    }

    // ---- gold bullion offer comparison (Arabic and English share this code)
    // What this tool is: the reader's own quoted TOTAL, set beside the value of the metal
    // in the bar. What it is not: a verdict on a dealer. The gap between a bar's price and
    // its metal content pays for refining, minting, the dealer's margin and whatever else
    // the dealer puts in the number - we cannot see the split, so we do not narrate it,
    // score it, colour it, or call the cheaper total the better buy.
    if ($("bullion-calc")) {
      var blc = $("bullion-calc");
      var blSummary = $("bl-summary"), blDetail = $("bl-detail");
      var lastSummary = null;

      // <bdi> on every figure: an Arabic line that ends in "-1.25%" otherwise reorders into
      // "%1.25-" and changes what the number says.
      function sar(x) { return "<bdi>" + fmt(x) + t(" ريال", " SAR") + "</bdi>"; }
      function signed(x, suffix) {
        var sign = x < 0 ? "−" : x > 0 ? "+" : "";
        return "<bdi>" + sign + fmt(Math.abs(x)) + suffix + "</bdi>";
      }
      function gramsText(g) { return "<bdi>" + fmt(g, g === Math.round(g) ? 0 : 2) + t(" جرام", " g") + "</bdi>"; }
      function offerLabel(k) { return k === "a" ? t("أ", "A") : t("ب", "B"); }
      function directionWord(d) {
        if (d === "above") return t("أعلى من قيمة الذهب المرجعية", "above the reference metal value");
        if (d === "below") return t("أقل من قيمة الذهب المرجعية", "below the reference metal value");
        return t("مطابق لقيمة الذهب المرجعية", "equal to the reference metal value");
      }
      function offerProblem(k, reason) {
        var L = offerLabel(k);
        if (reason === "negative") return t("مبلغ العرض " + L + " لا يكون بالسالب.", "Offer " + L + " cannot be negative.");
        if (reason === "zero") return t("أدخل للعرض " + L + " مبلغًا أكبر من صفر، أو اترك الخانة فارغة.",
                                        "Enter an amount greater than zero for offer " + L + ", or leave it blank.");
        return t("لم نفهم مبلغ العرض " + L + ". اكتبه بالأرقام، مثل 20000",
                 "Offer " + L + " could not be read. Enter digits, for example 20000");
      }

      // An error has to be attached to the field it is about, not just floated near the
      // result. Each input owns a stable, always-present slot that its aria-describedby
      // already points at, so turning an error on and off is a text change plus an
      // aria-invalid flag - no attribute wiring at runtime, nothing to leave dangling, and
      // no focus moved out from under whoever is typing. Clearing it is as important as
      // setting it: a fixed field that still reads "invalid" is its own bug.
      function setFieldError(id, message) {
        var input = $(id), slot = $(id + "-error");
        if (!input || !slot) return;
        if (message) {
          if (slot.textContent !== message) slot.textContent = message;
          input.setAttribute("aria-invalid", "true");
        } else {
          if (slot.textContent) slot.textContent = "";
          input.removeAttribute("aria-invalid");
        }
      }
      function markOfferFields() {
        ["a", "b"].forEach(function (k) {
          var o = M.checkOffer($("bl-offer-" + k).value);
          setFieldError("bl-offer-" + k, o.state === "invalid" ? offerProblem(k, o.reason) : "");
        });
      }

      // The one live region on this tool: a single short sentence, rewritten only when its
      // text really changes, so a screen reader is not made to re-read the whole breakdown
      // on every keystroke. It is visually hidden because every fact in it is also in the
      // breakdown below, which is NOT a live region - showing both would print the same
      // conclusion twice on screen.
      // With no breakdown to show, the results box would otherwise sit there as an empty
      // bordered panel. It is stripped of its chrome rather than hidden: the announcement
      // line lives inside it and must stay reachable.
      function setDetail(html) {
        blDetail.innerHTML = html;
        $("bl-result").classList.toggle("is-empty", !html);
      }
      function announce(text) {
        if (text === lastSummary) return;
        lastSummary = text;
        blSummary.textContent = text;
      }

      var updBullion = function () {
        saveInputs(blc);                     // before validation: see the gold calculator above
        var r = M.bullion({
          weight: $("bl-weight").value,
          unit: $("bl-unit").value,
          fineness: $("bl-fineness").value,
          offerA: $("bl-offer-a").value,
          offerB: $("bl-offer-b").value,
          goldSarG: P.gold_sar_g
        });

        if (!r.ok) {
          // Said out loud AND shown on the field itself: the live region is invisible, so an
          // error that only went there would be an error nobody sighted could see.
          var problem = weightMessage(r.weight.reason, "bullion");
          setFieldError("bl-weight", problem);
          // The offers are still judged while the weight is being fixed, so a bad B stays
          // flagged on its own field and a corrected one stops being flagged.
          markOfferFields();
          setDetail("");
          announce(problem);
          return;
        }
        setFieldError("bl-weight", "");

        var fineLabel = "<bdi>" + r.fineness + " (" + r.finenessPercent + "%)</bdi>";
        var rows = [
          [t("الوزن", "Weight"), gramsText(r.grams) + (r.unit === "kg" ? t(" (1 كيلو = 1000 جرام)", " (1 kg = 1000 g)") : "")],
          [t("نقاء السبيكة", "Fineness"), fineLabel],
          [t("الذهب الخالص في السبيكة", "Pure gold in the bar"), gramsText(r.fineGrams)]
        ];

        var summary;
        if (!r.snapshotOk) {
          rows.push([t("قيمة الذهب المرجعية", "Reference metal value"),
                     t("غير متاحة", "not available")]);
          summary = t("سعر الذهب المرجعي غير متاح الآن، فلا يمكن حساب قيمة السبيكة ولا أي فرق عنها.",
                      "The reference gold price is not available right now, so neither the bar's metal value nor any difference from it can be calculated.");
        } else {
          rows.push([t("سعر الجرام عند هذا النقاء", "Per gram at this fineness"), sar(M.round2(r.perGramFine))]);
          rows.push([t("قيمة الذهب المرجعية", "Reference metal value"), "<strong>" + sar(M.round2(r.reference)) + "</strong>"]);
          summary = t("قيمة الذهب المرجعية لسبيكة ", "Reference metal value for a ") +
            fmt(r.grams, r.grams === Math.round(r.grams) ? 0 : 2) + t(" جرام نقاء ", " g bar at ") + r.fineness +
            t(": ", " fineness: ") + fmt(M.round2(r.reference)) + t(" ريال.", " SAR.");
        }

        var html = "<table class='bl-table'><tbody>" + rows.map(function (row) {
          return "<tr><th scope='row'>" + row[0] + "</th><td>" + row[1] + "</td></tr>";
        }).join("") + "</tbody></table>";

        // Each offer stands on its own. A B that cannot be read never suppresses a valid A
        // and never becomes a winner.
        ["a", "b"].forEach(function (k) {
          var o = r.offers[k];
          setFieldError("bl-offer-" + k,
                        o.state === "invalid" ? offerProblem(k, o.reason) : "");
          if (o.state === "absent") return;
          if (o.state === "invalid") {
            summary += " " + offerProblem(k, o.reason);
            return;
          }
          var line = "<p class='bl-offer'><strong>" + t("العرض ", "Offer ") + offerLabel(k) + "</strong>: " +
            t("الإجمالي ", "total ") + sar(o.totalShown) + t(" · للجرام ", " · per gram ") + sar(o.perGramShown) +
            t(" (على وزن السبيكة كاملًا)", " (on the full bar weight)");
          if (o.direction) {
            line += "<br><span class='bl-diff'>" + signed(o.premiumSarShown, t(" ريال", " SAR")) +
              " " + directionWord(o.direction) + " (" + signed(o.premiumPctShown, "%") + ")</span>";
            summary += t(" العرض ", " Offer ") + offerLabel(k) + " " + directionWord(o.direction) +
              t(" بـ ", " by ") + fmt(Math.abs(o.premiumSarShown)) + t(" ريال.", " SAR.");
          }
          html += line + "</p>";
        });

        if (r.compare) {
          var cmp;
          if (r.compare.lower === "equal") {
            cmp = t("العرضان متساويان في الإجمالي عند الدقة المعروضة.",
                    "The two totals are equal at the precision shown.");
          } else {
            cmp = t("العرض ", "Offer ") + offerLabel(r.compare.lower) +
              t(" أقل إجمالًا بـ ", " is the lower total, by ") + fmt(r.compare.diffSar) + t(" ريال.", " SAR.");
          }
          html += "<p class='bl-compare'>" + cmp + t(" هذه مقارنة بين مبلغين أدخلتهما أنت، لا أكثر.",
                                                     " That is a comparison of two amounts you entered, nothing more.") + "</p>";
          summary += " " + cmp;
        }

        setDetail(html);
        announce(summary);
      };

      blc.addEventListener("input", updBullion);
      blc.addEventListener("change", updBullion);
      blc.querySelectorAll(".presets button").forEach(function (b) {
        b.addEventListener("click", function () {
          $("bl-weight").value = b.getAttribute("data-weight");
          $("bl-unit").value = b.getAttribute("data-unit");
          updBullion();
        });
      });
      // bl-snapshot itself is written by renderSnapshotNotes(), which re-runs on the
      // freshness timer - see the note above snapshotNote().
      restoreInputs(blc);          // saved values win over the markup defaults
      updBullion();                // and a first visit still sees the 10 g reference value
    }

    // ---- used-gold sell calculator
    if ($("sell-calc")) {
      var sc = $("sell-calc");
      var updSell = function () {
        saveInputs(sc);                      // before validation: see the gold calculator above
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
        saveInputs(sv);                      // before validation: see the gold calculator above
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
      var what = metal === "silver" ? t("قيمة الفضة", "the metal value")
               : metal === "bullion" ? t("قيمة الذهب المرجعية", "the reference metal value")
               : t("القيمة", "the value");
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
        saveInputs(zc);                      // before validation: see the gold calculator above
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
    setInterval(function () { renderStale(); renderSnapshotNotes(); checkForNewer(); }, CHECK_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { renderStale(); renderSnapshotNotes(); checkForNewer(); }
    });
  });
})(this);
