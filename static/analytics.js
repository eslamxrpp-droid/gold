// Analytics adapter - DISABLED. Nothing in this file runs until config.json sets
// analytics.provider to something other than "none" AND build.py is changed to emit the
// <script> tag for it. It is committed switched off on purpose: a measurement tool is a
// third party reading our visitors, and that is a decision for Eslam to take deliberately,
// not something that arrives with a refactor.
//
// The contract it implements is documented in MEASUREMENT.md. In one line: events say
// WHICH TOOL was used and WHICH FIXED-LIST CHOICE was made. They never carry a weight, a
// price, a quote, a computed value, an IP-derived location, or anything a reader typed.
//
// To turn it on:
//   1. Read MEASUREMENT.md and decide the provider.
//   2. Set analytics.provider (+ domain or measurement_id) in config.json.
//   3. Add the provider's own script tag to templates/base.html, then this file after it.
//   4. Re-run the tests: test_analytics_is_off_until_configured will fail until step 2 and
//      3 agree, which is the point - a half-configured tracker must not ship.
(function (root) {
  "use strict";

  var CFG = (root.MITHQAL_ANALYTICS || {});          // {provider, domain, measurement_id}
  var PROVIDER = CFG.provider || "none";

  // The full event vocabulary. An event not on this list is dropped rather than sent:
  // that is what stops a future edit from quietly widening what we collect.
  var ALLOWED = {
    calculator_used: ["page", "locale", "metal", "tool"],
    karat_selected: ["page", "locale", "metal", "karat"],
    purity_selected: ["page", "locale", "metal", "purity"],
    unit_selected: ["page", "locale", "metal", "unit"],
    preset_used: ["page", "locale", "metal", "preset"],
    quote_compared: ["page", "locale", "metal", "basis"],
    language_switch: ["page", "locale"],
    refresh_clicked: ["page", "locale"]
  };

  function clean(name, params) {
    var allow = ALLOWED[name];
    if (!allow) return null;
    var out = {};
    allow.forEach(function (k) {
      var v = params[k];
      if (v === undefined || v === null) return;
      v = String(v);
      if (v.length > 32) return;                     // a long string is not a fixed-list choice
      out[k] = v;
    });
    return out;
  }

  root.mithqalTrack = function (name, params) {
    if (PROVIDER === "none") return;
    var payload = clean(name, params || {});
    if (!payload) return;
    try {
      if (PROVIDER === "plausible" && root.plausible) {
        root.plausible(name, { props: payload });
      } else if (PROVIDER === "ga4" && root.gtag) {
        root.gtag("event", name, payload);
      } else if (PROVIDER === "console") {
        root.console.log("[mithqal event]", name, payload);   // for local verification only
      }
    } catch (e) { /* a metric must never break a page */ }
  };
})(this);
