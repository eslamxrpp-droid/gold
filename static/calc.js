// Calculators. Same formulas as prices.py (tests/test_prices.py checks both agree).
(function (root) {
  "use strict";

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

    // --- staleness ------------------------------------------------------
    // The build refuses to publish a bad price, so when the feed breaks the pages
    // simply stop changing: correct, but they still say "last updated" as if it were
    // now. This runs in the visitor's browser, so it keeps working while the build is
    // broken - which is exactly when it is needed.
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
    staleMessage: function (minutes, lang) {
      var age = M.humanAge(minutes, lang);
      return lang === "en"
        ? "This price has not updated for " + age + ". It may no longer match the market."
        : "لم يتم تحديث هذا السعر منذ " + age + "، وقد لا يطابق السوق الآن.";
    }
  };
  if (typeof module !== "undefined") { module.exports = M; return; }

  function fmt(x) { return x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function num(el) { var v = parseFloat(el && el.value); return isFinite(v) && v >= 0 ? v : 0; }
  function $(id) { return document.getElementById(id); }

  document.addEventListener("DOMContentLoaded", function () {
    var pEl = $("prices");
    if (!pEl) return;
    var P = JSON.parse(pEl.textContent);

    // Value calculator
    if ($("value-calc")) {
      var upd = function () {
        var w = num($("calc-weight")), k = +$("calc-karat").value, mk = num($("calc-making"));
        if (!w) { $("calc-result").textContent = "أدخل الوزن لعرض القيمة"; return; }
        var raw = M.itemValue(w, k, P.gold_sar_g, 0), total = M.itemValue(w, k, P.gold_sar_g, mk);
        $("calc-result").innerHTML = "قيمة الذهب: <strong>" + fmt(raw) + " ريال</strong>" +
          (mk ? "<br>مع المصنعية: <strong>" + fmt(total) + " ريال</strong>" : "");
      };
      ["calc-weight", "calc-karat", "calc-making"].forEach(function (id) { $(id).addEventListener("input", upd); });
    }

    // Sell calculator
    if ($("sell-calc")) {
      var updSell = function () {
        var w = num($("sell-weight")), k = +$("sell-karat").value, d = Math.min(num($("sell-deduction")), 100);
        if (!w) { $("sell-result").textContent = "أدخل الوزن لعرض القيمة"; return; }
        var raw = M.sellValue(w, k, P.gold_bid_sar_g, 0), net = M.sellValue(w, k, P.gold_bid_sar_g, d);
        $("sell-result").innerHTML = "القيمة بسعر البيع في السوق: <strong>" + fmt(raw) + " ريال</strong>" +
          (d ? "<br>بعد خصم المحل " + d + "%: <strong>" + fmt(net) + " ريال</strong>" : "");
      };
      ["sell-weight", "sell-karat", "sell-deduction"].forEach(function (id) { $(id).addEventListener("input", updSell); });
    }

    // Zakat calculator
    if ($("zakat-calc")) {
      var rows = $("zakat-gold-rows");
      $("zakat-add-row").addEventListener("click", function () {
        var r = rows.querySelector(".row").cloneNode(true);
        r.querySelector("input").value = "";
        rows.appendChild(r);
      });
      var updZakat = function () {
        var items = [];
        rows.querySelectorAll(".row").forEach(function (r) {
          var w = num(r.querySelector(".zg-weight"));
          if (w) items.push([w, +r.querySelector(".zg-karat").value]);
        });
        var sw = num($("zs-weight")), sp = +$("zs-purity").value;
        var jewel = document.querySelector("input[name=zjewel]:checked").value === "yes";
        if (!items.length && !sw) { $("zakat-result").textContent = "أدخل الوزن لعرض النتيجة"; return; }
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
        if (sw) {
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
      $("zakat-calc").addEventListener("input", updZakat);
      $("zakat-calc").addEventListener("change", updZakat);
    }
  });

  // Show the stale banner if the last successful build is old.
  document.addEventListener("DOMContentLoaded", function () {
    var box = $("stale-notice"), data = $("prices");
    if (!box || !data) return;
    var P;
    try { P = JSON.parse(data.textContent); } catch (e) { return; }
    if (!P.updated_utc || !P.stale_after_minutes) return;
    var mins = M.ageMinutes(P.updated_utc, Date.now());
    if (mins === null || mins < P.stale_after_minutes) return;
    var lang = (document.documentElement.getAttribute("lang") || "ar").slice(0, 2);
    box.textContent = M.staleMessage(mins, lang);
    box.hidden = false;
  });
})(this);
