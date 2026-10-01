/* Lemma widget kit v1: what every widget used to paste in, provided once.
 *
 * `lemma.query(sql)`   -> Promise<rows[]>  (rows.truncated says the row cap cut it)
 * `lemma.records(table, options)` -> Promise<{ items, total }>
 * `lemma.compose(text, { newConversation })` -> Promise<boolean>, fills the chat
 * `lemma.canCompose()` -> Promise<boolean>, draw a follow-up button only if true
 * `lemma.client()` -> Promise<LemmaClient>, authenticated, for anything else
 * `lemma.esc / num / money / pct / date` -> formatting for text that reaches the DOM
 *
 * The browser SDK loads on the first call that needs it, so a widget that draws
 * data it already carries pays nothing for it. Failures reject with a sentence a
 * person can read; "can't load here" is not "signed out" -- a framed page is
 * often denied the cookie even when the person is signed in.
 */
(function () {
  if (window.lemma && window.lemma.kit) return;
  var ready = null;

  function sdk() {
    return new Promise(function (resolve, reject) {
      if (window.LemmaClient) return resolve(window.LemmaClient);
      var api = ((window.__LEMMA_CONFIG__ || {}).apiUrl || "").replace(/\/$/, "");
      if (!api) return reject(new Error("This view has no connection to the pod here."));
      var tag = document.createElement("script");
      tag.src = api + "/public/sdk/lemma-client.js";
      tag.onload = function () { resolve(window.LemmaClient); };
      tag.onerror = function () { reject(new Error("The pod could not be reached from here.")); };
      document.head.appendChild(tag);
    });
  }

  function client() {
    if (!ready) {
      ready = sdk().then(function (ns) {
        var c = new ns.LemmaClient();
        return c.initialize().then(function (auth) {
          if (!auth || auth.status !== "authenticated") {
            throw new Error("This view can't load the pod's data in this browser.");
          }
          return c;
        });
      });
      ready.catch(function () { ready = null; });
    }
    return ready;
  }

  function locale(value, options) {
    var n = Number(value);
    return isFinite(n) ? new Intl.NumberFormat(undefined, options).format(n) : "—";
  }

  window.lemma = {
    kit: 1,
    client: client,
    query: function (sql) {
      return client().then(function (c) { return c.datastore.query(sql); }).then(function (r) {
        var rows = (r && r.items) || [];
        rows.truncated = !!(r && r.truncated);
        return rows;
      });
    },
    records: function (table, options) {
      return client().then(function (c) { return c.records.list(table, options || {}); });
    },
    canCompose: function () {
      if (window.parent === window) return Promise.resolve(false);
      return sdk().then(function (ns) {
        return !!(ns.canComposeInConversation && ns.canComposeInConversation());
      }, function () { return false; });
    },
    compose: function (text, options) {
      return sdk().then(function (ns) {
        return ns.composeInConversation ? ns.composeInConversation(String(text), options) : false;
      }, function () { return false; });
    },
    esc: function (value) {
      var span = document.createElement("span");
      span.textContent = value == null ? "" : String(value);
      return span.innerHTML;
    },
    num: function (value, digits) {
      // Small figures keep their decimals: a 3.97% failure rate is not "4".
      var size = Math.abs(Number(value));
      return locale(value, { maximumFractionDigits: digits != null ? digits : size < 10 ? 2 : size < 100 ? 1 : 0 });
    },
    money: function (value, currency) {
      return locale(value, { style: "currency", currency: currency || "USD", currencyDisplay: "narrowSymbol", maximumFractionDigits: 2 });
    },
    pct: function (value, digits) {
      return locale(value, { style: "percent", maximumFractionDigits: digits == null ? 1 : digits });
    },
    date: function (value) {
      var d = new Date(value);
      return isNaN(d) ? "—" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    }
  };
})();
