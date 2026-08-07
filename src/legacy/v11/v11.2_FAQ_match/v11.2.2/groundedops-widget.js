/*!
 * GroundedOps embeddable support widget (v2)
 * ------------------------------------------------------------------
 * v2 adds:
 *   1. Guided onboarding — visitors pick an intent from suggested
 *      pointers before free text is available.
 *   2. Mandatory scope — a category AND product must be chosen before a
 *      question can be asked, so retrieval is always scoped and the
 *      backend never answers from the whole corpus by accident.
 *   3. Suggested questions — pulled live from the curated FAQ for the
 *      chosen product, so the first answer costs no LLM call.
 *   4. Session persistence — the conversation survives a page refresh,
 *      and the visitor is asked whether to continue or start fresh.
 *
 * Embed:
 *   <script
 *     src="/widget/groundedops-widget.js"
 *     data-api="https://your-backend.example.com"
 *     data-title="Innovative Technology"
 *     data-accent="#E4002B"
 *     data-agent-name="David"
 *     data-sales-email="sales@example.com"
 *   ></script>
 *
 * Backend endpoints used: GET /catalog, GET /faq?product=, POST /query.
 * No API key ever reaches the browser.
 */
(function () {
  "use strict";

  if (window.__groundedOpsWidgetLoaded) return;
  window.__groundedOpsWidgetLoaded = true;

  var script =
    document.currentScript ||
    (function () {
      var s = document.getElementsByTagName("script");
      return s[s.length - 1];
    })();

  function attr(name, fallback) {
    return script.getAttribute(name) || fallback;
  }

  var cfg = {
    api: attr("data-api", "").replace(/\/+$/, ""),
    title: attr("data-title", "Support"),
    agent: attr("data-agent-name", "Assistant"),
    avatar: attr("data-avatar-url", ""),
    accent: attr("data-accent", "#E4002B"),
    launcherLabel: attr("data-launcher-label", "Ask a question"),
    salesEmail: attr("data-sales-email", ""),
    supportEmail: attr("data-support-email", ""),
    welcome: attr("data-welcome", "Welcome to Innovative Technology, the home of transaction automation"),
    prompt: attr("data-prompt", "How can I help today?"),
  };

  if (!cfg.api) {
    console.error("[GroundedOps] Missing data-api on the widget script tag.");
    return;
  }

  // ── persistence ───────────────────────────────────────────────────────
  // One key holds the whole widget state, so a refresh restores the
  // conversation, the chosen scope AND the session_id — the last of these
  // matters because the backend keys conversational memory on it. Losing
  // it would silently break follow-up questions after a refresh.
  var STORE_KEY = "groundedops_widget_v2";
  var MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000; // resume offer expires after a week

  var state = {
    sessionId: null,
    stage: "intent",     // intent | category | product | chat
    intent: null,
    category: null,      // {key, name}
    product: null,       // {key, name}
    messages: [],        // {role:'bot'|'user', text, sources?, flagged?}
    updatedAt: 0,
  };

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function save() {
    state.updatedAt = Date.now();
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (e) {
      /* private mode / quota — widget still works, just won't resume */
    }
  }

  function loadSaved() {
    try {
      var raw = localStorage.getItem(STORE_KEY);
      if (!raw) return null;
      var s = JSON.parse(raw);
      if (!s || !s.messages || !s.messages.length) return null;
      if (Date.now() - (s.updatedAt || 0) > MAX_AGE_MS) return null;
      return s;
    } catch (e) {
      return null;
    }
  }

  function clearSaved() {
    try {
      localStorage.removeItem(STORE_KEY);
    } catch (e) {}
  }

  // ── styles ────────────────────────────────────────────────────────────
  var css =
    ".go-w,.go-w *{box-sizing:border-box}" +
    ".go-w{--a:" + cfg.accent + ";--ink:#16191c;--mut:#6b7480;--line:#e6e8eb;--bg:#fff;--pane:#f6f7f9;" +
    "position:fixed;bottom:20px;right:20px;z-index:2147483000;" +
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;" +
    "font-size:15px;line-height:1.5;color:var(--ink)}" +
    ".go-launch{display:inline-flex;align-items:center;gap:8px;cursor:pointer;border:0;background:var(--a);" +
    "color:#fff;padding:13px 20px;border-radius:999px;font-size:15px;font-weight:600;" +
    "box-shadow:0 6px 24px rgba(0,0,0,.2);transition:transform .12s}" +
    ".go-launch:hover{transform:translateY(-1px)}" +
    ".go-launch:focus-visible{outline:3px solid rgba(0,0,0,.3);outline-offset:2px}" +
    ".go-launch svg{width:18px;height:18px}" +
    ".go-panel{position:absolute;bottom:0;right:0;width:400px;max-width:calc(100vw - 32px);height:600px;" +
    "max-height:calc(100vh - 40px);background:var(--bg);border-radius:14px;overflow:hidden;" +
    "box-shadow:0 24px 60px rgba(0,0,0,.25);display:none;flex-direction:column}" +
    ".go-open .go-panel{display:flex}.go-open .go-launch{display:none}" +
    // header
    ".go-head{background:var(--a);color:#fff;padding:14px 16px;display:flex;align-items:center;gap:11px;flex:0 0 auto}" +
    ".go-av{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.25);flex:0 0 auto;" +
    "display:flex;align-items:center;justify-content:center;font-weight:700;overflow:hidden}" +
    ".go-av img{width:100%;height:100%;object-fit:cover}" +
    ".go-hname{font-weight:700;font-size:16px;flex:1}" +
    ".go-hbtn{border:0;background:transparent;color:#fff;cursor:pointer;padding:5px;border-radius:7px;line-height:0;opacity:.9}" +
    ".go-hbtn:hover{background:rgba(255,255,255,.18)}.go-hbtn svg{width:17px;height:17px}" +
    // scope bar
    ".go-scope{display:flex;align-items:center;gap:8px;padding:8px 14px;background:#eef1f4;" +
    "border-bottom:1px solid var(--line);font-size:12.5px;color:var(--mut);flex:0 0 auto}" +
    ".go-scope b{color:var(--ink);font-weight:600}" +
    ".go-scope button{margin-left:auto;border:0;background:transparent;color:var(--a);cursor:pointer;" +
    "font:inherit;font-size:12.5px;font-weight:600;padding:2px 4px;border-radius:5px}" +
    ".go-scope button:hover{text-decoration:underline}" +
    // log
    ".go-log{flex:1 1 auto;overflow-y:auto;padding:16px 14px;background:var(--bg);display:flex;flex-direction:column;gap:10px}" +
    ".go-row{display:flex;gap:9px;align-items:flex-end}" +
    ".go-row.u{justify-content:flex-end}" +
    ".go-mav{width:28px;height:28px;border-radius:50%;background:var(--pane);flex:0 0 auto;overflow:hidden;" +
    "display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:var(--mut)}" +
    ".go-mav img{width:100%;height:100%;object-fit:cover}" +
    ".go-b{max-width:78%;padding:10px 14px;border-radius:16px;white-space:pre-wrap;word-wrap:break-word;font-size:14.5px}" +
    ".go-b.bot{background:var(--pane);border-bottom-left-radius:5px}" +
    ".go-b.usr{background:#2f3a45;color:#fff;border-bottom-right-radius:5px}" +
    ".go-b.warn{background:#fdf6e7;border:1px solid #e8d9b0}" +
    ".go-status{font-size:12.5px;color:var(--mut);display:flex;align-items:center;gap:7px;padding:2px 0}" +
    ".go-spin{width:12px;height:12px;border:2px solid var(--line);border-top-color:var(--mut);" +
    "border-radius:50%;animation:go-spin .7s linear infinite}" +
    "@keyframes go-spin{to{transform:rotate(360deg)}}" +
    // chips (suggested pointers)
    ".go-chips{display:flex;flex-direction:column;align-items:flex-end;gap:8px;margin-top:2px}" +
    ".go-chip{border:0;background:#2f3a45;color:#fff;padding:11px 17px;border-radius:999px;cursor:pointer;" +
    "font:inherit;font-size:14px;font-weight:600;text-align:right;max-width:88%;transition:background .12s}" +
    ".go-chip:hover{background:#1d252d}" +
    ".go-chip:focus-visible{outline:3px solid var(--a);outline-offset:2px}" +
    ".go-chip.alt{background:transparent;color:var(--a);border:1.5px solid var(--line);font-weight:600}" +
    ".go-chip.alt:hover{background:var(--pane)}" +
    ".go-chip.q{background:var(--bg);color:var(--ink);border:1.5px solid var(--line);font-weight:500;text-align:left}" +
    ".go-chip.q:hover{border-color:var(--a);background:var(--pane)}" +
    ".go-chiplabel{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);" +
    "align-self:flex-end;margin-top:6px}" +
    // sources
    ".go-src{margin-top:9px;border-top:1px solid var(--line);padding-top:8px}" +
    ".go-srch{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);" +
    "margin-bottom:5px;display:flex;align-items:center;gap:5px}.go-srch svg{width:12px;height:12px}" +
    ".go-srci{font-size:12.5px;margin-bottom:5px}" +
    ".go-srcn{font-weight:600}.go-srcs{color:var(--mut);font-size:12px}" +
    ".go-pg{font-weight:500;color:var(--mut);font-size:11.5px}" +
    ".go-dl{display:inline-block;margin-top:4px;font-size:12px;font-weight:600;color:var(--a);text-decoration:none}" +
    ".go-dl:hover{text-decoration:underline}" +
    ".go-badge{display:inline-block;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;" +
    "color:var(--mut);border:1px solid var(--line);border-radius:4px;padding:1px 5px;margin-top:7px}" +
    // composer
    ".go-form{display:flex;gap:8px;padding:11px;border-top:1px solid var(--line);background:var(--bg);flex:0 0 auto}" +
    ".go-in{flex:1;resize:none;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;" +
    "font-size:14px;max-height:110px;color:var(--ink);background:var(--bg)}" +
    ".go-in:focus{outline:2px solid var(--a);border-color:var(--a)}" +
    ".go-in:disabled{background:var(--pane);color:var(--mut);cursor:not-allowed}" +
    ".go-send{border:0;background:var(--a);color:#fff;border-radius:10px;width:42px;cursor:pointer;flex:0 0 auto;" +
    "display:flex;align-items:center;justify-content:center}" +
    ".go-send:disabled{opacity:.4;cursor:not-allowed}.go-send svg{width:18px;height:18px}" +
    ".go-foot{text-align:center;font-size:11px;color:var(--mut);padding:0 10px 9px;background:var(--bg);flex:0 0 auto}" +
    "@media (prefers-reduced-motion:reduce){.go-spin{animation:none}}";

  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  // ── icons ─────────────────────────────────────────────────────────────
  var I_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>';
  var I_MIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 12h14"/></svg>';
  var I_RESET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3.2-6.9"/><path d="M21 3v6h-6"/></svg>';
  var I_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-7 20-4-9-9-4 20-7z"/></svg>';
  var I_SHIELD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>';

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ── DOM ───────────────────────────────────────────────────────────────
  var root = document.createElement("div");
  root.className = "go-w";
  root.setAttribute("data-go-root", "");
  var avatarHtml = cfg.avatar
    ? '<img src="' + esc(cfg.avatar) + '" alt="">'
    : esc(cfg.agent.charAt(0).toUpperCase());
  root.innerHTML =
    '<button class="go-launch" type="button" aria-haspopup="dialog" aria-expanded="false">' +
      I_CHAT + "<span>" + esc(cfg.launcherLabel) + "</span></button>" +
    '<section class="go-panel" role="dialog" aria-label="' + esc(cfg.title) + '">' +
      '<div class="go-head"><div class="go-av">' + avatarHtml + "</div>" +
        '<div class="go-hname">' + esc(cfg.agent) + "</div>" +
        '<button class="go-hbtn go-restart" type="button" aria-label="Start over" title="Start over">' + I_RESET + "</button>" +
        '<button class="go-hbtn go-min" type="button" aria-label="Minimise">' + I_MIN + "</button>" +
      "</div>" +
      '<div class="go-scope" hidden></div>' +
      '<div class="go-log" role="log" aria-live="polite"></div>' +
      '<form class="go-form"><textarea class="go-in" rows="1" aria-label="Your question"></textarea>' +
        '<button class="go-send" type="submit" aria-label="Send" disabled>' + I_SEND + "</button></form>" +
      '<div class="go-foot">Answers are generated from our product documentation</div>' +
    "</section>";
  document.body.appendChild(root);

  var $launch = root.querySelector(".go-launch");
  var $min = root.querySelector(".go-min");
  var $restart = root.querySelector(".go-restart");
  var $scope = root.querySelector(".go-scope");
  var $log = root.querySelector(".go-log");
  var $form = root.querySelector(".go-form");
  var $in = root.querySelector(".go-in");
  var $send = root.querySelector(".go-send");

  var catalogCache = null;
  var busy = false;
  var opened = false;

  // ── rendering ─────────────────────────────────────────────────────────
  function scrollDown() {
    $log.scrollTop = $log.scrollHeight;
  }

  function bubble(msg) {
    var row = document.createElement("div");
    row.className = "go-row" + (msg.role === "user" ? " u" : "");
    if (msg.role !== "user") {
      var av = document.createElement("div");
      av.className = "go-mav";
      av.innerHTML = avatarHtml;
      row.appendChild(av);
    }
    var b = document.createElement("div");
    b.className = "go-b " + (msg.role === "user" ? "usr" : msg.flagged ? "bot warn" : "bot");
    var txt = document.createElement("div");
    txt.textContent = msg.text;
    b.appendChild(txt);

    if (msg.sources && msg.sources.length) {
      var s = document.createElement("div");
      s.className = "go-src";
      s.innerHTML = '<div class="go-srch">' + I_SHIELD + "Sources</div>";
      msg.sources.slice(0, 4).forEach(function (x) {
        var i = document.createElement("div");
        i.className = "go-srci";
        // v3.3.0: name + page reference + download link for the original.
        // The page label is built server-side so every client renders it
        // identically ("page 12" / "pages 12, 14").
        var head = '<div class="go-srcn">' + esc(pretty(x.source)) +
          (x.page_label ? ' <span class="go-pg">' + esc(x.page_label) + "</span>" : "") +
          "</div>";
        var dl = x.download_url
          ? '<a class="go-dl" href="' + esc(cfg.api + x.download_url) +
            '" target="_blank" rel="noopener">Download source</a>'
          : "";
        i.innerHTML = head +
          (x.snippet ? '<div class="go-srcs">' + esc(x.snippet) + "</div>" : "") + dl;
        s.appendChild(i);
      });
      b.appendChild(s);
    }
    if (msg.badge) {
      var bd = document.createElement("div");
      bd.className = "go-badge";
      bd.textContent = msg.badge;
      b.appendChild(bd);
    }
    row.appendChild(b);
    return row;
  }

  function pretty(n) {
    return String(n || "document").replace(/\.[a-z0-9]+$/i, "").replace(/[_-]+/g, " ").trim();
  }

  function renderLog() {
    $log.innerHTML = "";
    state.messages.forEach(function (m) {
      $log.appendChild(bubble(m));
    });
  }

  function say(text, extra) {
    var m = Object.assign({ role: "bot", text: text }, extra || {});
    state.messages.push(m);
    $log.appendChild(bubble(m));
    scrollDown();
    save();
  }

  function heard(text) {
    var m = { role: "user", text: text };
    state.messages.push(m);
    $log.appendChild(bubble(m));
    scrollDown();
    save();
  }

  function status(text) {
    var d = document.createElement("div");
    d.className = "go-status";
    d.innerHTML = '<span class="go-spin"></span><span>' + esc(text) + "</span>";
    $log.appendChild(d);
    scrollDown();
    return d;
  }

  /** Render a set of tappable pointers. Chips are ephemeral UI derived
   *  from the current stage — deliberately NOT stored in messages, so a
   *  resumed conversation doesn't show stale buttons for choices that
   *  were already made. */
  function chips(items, label) {
    var wrap = document.createElement("div");
    wrap.className = "go-chips";
    wrap.setAttribute("data-chips", "");
    if (label) {
      var l = document.createElement("div");
      l.className = "go-chiplabel";
      l.textContent = label;
      wrap.appendChild(l);
    }
    items.forEach(function (it) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "go-chip" + (it.style ? " " + it.style : "");
      b.textContent = it.label;
      b.addEventListener("click", function () {
        clearChips();
        it.onClick();
      });
      wrap.appendChild(b);
    });
    $log.appendChild(wrap);
    scrollDown();
  }

  function clearChips() {
    Array.prototype.forEach.call($log.querySelectorAll("[data-chips]"), function (n) {
      n.remove();
    });
  }

  function renderScopeBar() {
    if (!state.product && !state.category) {
      $scope.hidden = true;
      return;
    }
    $scope.hidden = false;
    var name = state.product ? state.product.name : state.category.name;
    $scope.innerHTML =
      "<span>Asking about <b>" + esc(name) + "</b></span>" +
      '<button type="button">Change</button>';
    $scope.querySelector("button").addEventListener("click", function () {
      state.product = null;
      state.category = null;
      state.stage = "category";
      renderScopeBar();
      lockComposer();
      say("No problem — which product range would you like to ask about?");
      askCategory();
    });
  }

  // The composer is the gate: free text is impossible until a product is
  // chosen, which guarantees every /query call carries a scope.
  function lockComposer(reason) {
    $in.disabled = true;
    $send.disabled = true;
    $in.placeholder = reason || "Choose an option above to continue…";
  }

  function unlockComposer() {
    $in.disabled = false;
    $in.placeholder = "Ask about " + (state.product ? state.product.name : "this product") + "…";
    $send.disabled = !$in.value.trim();
  }

  // ── flow ──────────────────────────────────────────────────────────────
  function startFresh() {
    clearSaved();
    state = {
      sessionId: uuid(),
      stage: "intent",
      intent: null,
      category: null,
      product: null,
      messages: [],
      updatedAt: Date.now(),
    };
    $log.innerHTML = "";
    renderScopeBar();
    lockComposer();
    say(cfg.welcome);
    say(cfg.prompt);
    askIntent();
    save();
  }

  function askIntent() {
    state.stage = "intent";
    save();
    var items = [
      {
        label: "Request technical support",
        onClick: function () {
          heard("Request technical support");
          state.intent = "support";
          say("I can answer technical questions from our product documentation. First, which product range?");
          askCategory();
        },
      },
      {
        label: "Find a product",
        onClick: function () {
          heard("Find a product");
          state.intent = "product";
          say("Let's find the right one. Which range are you interested in?");
          askCategory();
        },
      },
      {
        label: "Get spare parts & accessories",
        onClick: function () {
          heard("Get spare parts & accessories");
          state.intent = "parts";
          say("I can look up parts and accessories referenced in the documentation. Which product is it for?");
          askCategory();
        },
      },
      {
        label: "Speak to sales",
        style: "alt",
        onClick: function () {
          heard("Speak to sales");
          state.intent = "sales";
          say(
            cfg.salesEmail
              ? "Happy to put you in touch. You can reach our sales team at " +
                  cfg.salesEmail +
                  ". If you'd like, I can also answer technical questions in the meantime."
              : "Happy to help with that — our sales team will follow up. In the meantime, I can answer technical questions from the documentation."
          );
          chips(
            [
              {
                label: "Ask a technical question",
                onClick: function () {
                  heard("Ask a technical question");
                  askCategory();
                },
              },
            ],
            null
          );
        },
      },
    ];
    chips(items, "Choose one");
  }

  function fetchCatalog() {
    if (catalogCache) return Promise.resolve(catalogCache);
    return fetch(cfg.api + "/catalog")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        catalogCache = d && d.categories ? d.categories : [];
        return catalogCache;
      });
  }

  function askCategory() {
    state.stage = "category";
    lockComposer();
    save();
    var s = status("Loading product ranges");
    fetchCatalog()
      .then(function (cats) {
        s.remove();
        // Only offer ranges that actually have ingested documents —
        // /catalog annotates doc_count for exactly this purpose.
        var usable = cats.filter(function (c) {
          return countDocs(c) > 0;
        });
        if (!usable.length) {
          say("I don't have any product documentation loaded yet, so I can't answer questions right now. Please contact us directly and we'll help.", { flagged: true });
          return;
        }
        chips(
          usable.map(function (c) {
            return {
              label: c.name,
              style: "alt",
              onClick: function () {
                heard(c.name);
                state.category = { key: c.key, name: c.name };
                renderScopeBar();
                askProduct(c);
              },
            };
          }),
          "Product range"
        );
      })
      .catch(function (e) {
        s.remove();
        say("I couldn't load the product list just now. Please try again in a moment.", { flagged: true });
        console.error("[GroundedOps] /catalog failed:", e);
      });
  }

  function countDocs(c) {
    if (typeof c.doc_count === "number") return c.doc_count;
    return (c.products || []).reduce(function (n, p) {
      return n + (p.doc_count || 0);
    }, 0);
  }

  function askProduct(cat) {
    state.stage = "product";
    lockComposer();
    save();
    var prods = (cat.products || []).filter(function (p) {
      return (p.doc_count || 0) > 0;
    });

    // A range with exactly one documented product doesn't need a second
    // question — skip straight to chat rather than asking a question with
    // one possible answer.
    if (prods.length === 1) {
      state.product = { key: prods[0].key, name: prods[0].name };
      renderScopeBar();
      enterChat();
      return;
    }
    if (!prods.length) {
      say("I don't have documentation for that range yet. Please pick another, or contact us directly.", { flagged: true });
      askCategory();
      return;
    }

    say("Which product specifically?");
    var items = prods.map(function (p) {
      return {
        label: p.name,
        style: "alt",
        onClick: function () {
          heard(p.name);
          state.product = { key: p.key, name: p.name };
          renderScopeBar();
          enterChat();
        },
      };
    });
    // Let them ask across the whole range if they're not sure which model.
    items.push({
      label: "Not sure — ask across the whole range",
      style: "alt",
      onClick: function () {
        heard("Not sure — ask across the whole range");
        state.product = { key: cat.key, name: cat.name + " (all)" };
        renderScopeBar();
        enterChat();
      },
    });
    chips(items, "Product");
  }

  function enterChat() {
    state.stage = "chat";
    save();
    unlockComposer();
    say("Great — ask me anything about " + state.product.name + ". Here are some common questions to get you started.");
    suggestQuestions();
    setTimeout(function () {
      $in.focus();
    }, 50);
  }

  /** Curated FAQ questions for the chosen scope, offered as pointers.
   *  Picking one usually resolves against the curated answer with no LLM
   *  call at all. Failure here is non-fatal — the visitor can still type. */
  function suggestQuestions() {
    fetch(cfg.api + "/faq?product=" + encodeURIComponent(state.product.key))
      .then(function (r) {
        return r.ok ? r.json() : { faq: [] };
      })
      .then(function (d) {
        var qs = (d.faq || [])
          .filter(function (f) {
            return f.question && (f.answer || "").trim();
          })
          .slice(0, 4);
        if (!qs.length) return;
        chips(
          qs.map(function (f) {
            return {
              label: f.question,
              style: "q",
              onClick: function () {
                ask(f.question);
              },
            };
          }),
          "Common questions"
        );
      })
      .catch(function () {});
  }

  function offerResume(saved) {
    $log.innerHTML = "";
    var last = null;
    for (var i = saved.messages.length - 1; i >= 0; i--) {
      if (saved.messages[i].role === "user") {
        last = saved.messages[i].text;
        break;
      }
    }
    var scopeName = saved.product ? saved.product.name : saved.category ? saved.category.name : null;
    say(
      "Welcome back. We were talking" +
        (scopeName ? " about " + scopeName : "") +
        (last ? ', and your last question was "' + last + '"' : "") +
        ". Would you like to carry on or start again?"
    );
    lockComposer("Choose continue or start over…");
    chips(
      [
        {
          label: "Continue this conversation",
          onClick: function () {
            state = saved;
            renderLog();
            renderScopeBar();
            // Only re-open the composer if a scope was actually chosen;
            // otherwise resume the picker where they left off.
            if (state.product) {
              state.stage = "chat";
              unlockComposer();
              scrollDown();
            } else {
              say("Before we continue — which product range is this about?");
              askCategory();
            }
            save();
          },
        },
        { label: "Start a new conversation", style: "alt", onClick: startFresh },
      ],
      null
    );
  }

  // ── query ─────────────────────────────────────────────────────────────
  /** Ask, but explicitly skip the curated FAQ short-circuit. Used when the
   *  visitor rejects a "did you mean" suggestion — at that point they've
   *  told us the FAQ doesn't have their question, so going straight to
   *  retrieval over the manuals is the right move.
   *  Requires `skip_faq: bool = False` on QueryRequest in main.py, and
   *  `and not payload.skip_faq` on the FAQ block's condition. */
  function askDocs(q) {
    ask(q, { skipFaq: true });
  }

  function ask(q, opts) {
    opts = opts || {};
    if (busy) return;
    // Belt-and-braces: the composer is disabled without a scope, but a
    // chip callback or a future code path could still get here.
    if (!state.product) {
      say("Let me get the right documentation first — which product range?");
      askCategory();
      return;
    }
    busy = true;
    $send.disabled = true;
    clearChips();
    heard(q);
    var s = status("Checking the documentation");

    fetch(cfg.api + "/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        q: q,
        session_id: state.sessionId,
        product: state.product.key,
        category: state.category ? state.category.key : null,
        skip_faq: !!opts.skipFaq,
        faq_id: opts.faqId || null,
      }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        s.remove();
        // v2.1: FAQ near-miss comes back as a clarify with suggestions
        // rather than a served answer. Offer it as a chip so one tap asks
        // the exact curated question.
        // v3.2.0: the backend no longer guesses whether a curated FAQ
        // matches — it offers candidates and we let the user decide.
        // Selecting one sends its faq_id, so the exact chosen answer is
        // served with no re-matching. "Something else" logs a FAQ gap and
        // answers from the documents instead.
        if (!opts.skipFaq && d.faq_candidates && d.faq_candidates.length) {
          say(d.answer || "Is one of these what you meant?");
          chips(
            d.faq_candidates
              .map(function (c) {
                return {
                  label: c.question,
                  style: "q",
                  onClick: function () { ask(c.question, { faqId: c.id }); },
                };
              })
              .concat([{
                label: "None of these \u2014 I'm asking something else",
                style: "alt",
                onClick: function () { askDocs(q); },
              }]),
            "Reviewed answers"
          );
          return;
        }
        say(d.answer || "No answer returned.", {
          sources: d.flagged ? null : d.sources,
          flagged: !!d.flagged,
          badge: d.from_faq ? "Reviewed answer" : null,
        });
      })
      .catch(function (e) {
        s.remove();
        say("That didn't go through — please check your connection and try again.", { flagged: true });
        console.error("[GroundedOps] /query failed:", e);
      })
      .then(function () {
        busy = false;
        if (state.product) unlockComposer();
        $in.focus();
      });
  }

  // ── events ────────────────────────────────────────────────────────────
  $launch.addEventListener("click", function () {
    root.classList.add("go-open");
    $launch.setAttribute("aria-expanded", "true");
    if (!opened) {
      opened = true;
      var saved = loadSaved();
      if (saved) offerResume(saved);
      else startFresh();
    }
  });

  function close() {
    root.classList.remove("go-open");
    $launch.setAttribute("aria-expanded", "false");
    $launch.focus();
  }
  $min.addEventListener("click", close);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && root.classList.contains("go-open")) close();
  });

  $restart.addEventListener("click", function () {
    if (state.messages.length > 2 && !window.confirm("Start a new conversation? This will clear the current chat.")) return;
    startFresh();
  });

  $in.addEventListener("input", function () {
    $in.style.height = "auto";
    $in.style.height = Math.min($in.scrollHeight, 110) + "px";
    $send.disabled = !$in.value.trim() || busy || $in.disabled;
  });
  $in.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $form.requestSubmit();
    }
  });
  $form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = $in.value.trim();
    if (!q || busy || $in.disabled) return;
    $in.value = "";
    $in.style.height = "auto";
    ask(q);
  });

  lockComposer();
})();
