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

  // Build stamp. A browser-cached old build behaves like a broken new one,
  // and this is the fastest way to tell them apart - check the console.
  var BUILD = "v12.0";
  window.__groundedOpsBuild = BUILD;
  console.info("GroundedOps widget build " + BUILD + " (endpoints: /widget/*)");

  var script =
    document.currentScript ||
    (function () {
      var s = document.getElementsByTagName("script");
      return s[s.length - 1];
    })();

  function attr(name, fallback) {
    return script.getAttribute(name) || fallback;
  }

  // ── configuration: server first, attributes as fallback ───────────────
  // The console's Widget-design page is the source of truth for branding,
  // the opening options and the contact forms. It used to have no effect at
  // all here: this file read data-* attributes only and never fetched
  // /widget/config, so saving in the console changed nothing a visitor saw.
  //
  // data-* attributes are kept, and still win when explicitly present on the
  // script tag — an existing embed that hard-codes its own accent or title
  // must not change appearance because this file learned to fetch config.
  // Anything NOT set as an attribute now comes from the server, so the
  // console governs it. data-api and data-token stay attribute-only: one is
  // how the widget finds the server, the other is the visitor's identity,
  // and neither can come from the thing it is used to reach.
  function hasAttr(name) {
    return script.getAttribute(name) !== null;
  }

  var cfg = {
    api: attr("data-api", "").replace(/\/+$/, ""),
    title: attr("data-title", "Support"),
    agent: attr("data-agent-name", "Assistant"),
    avatar: attr("data-avatar-url", ""),
    accent: attr("data-accent", "#E4002B"),
    launcherLabel: attr("data-launcher-label", "Ask a question"),
    salesEmail: attr("data-sales-email", ""),
    // Signed by the website server-side for logged-in users. Absent for
    // anonymous visitors, who get curated FAQ answers only.
    token: attr("data-token", ""),
    signInUrl: attr("data-sign-in-url", ""),
    supportEmail: attr("data-support-email", ""),
    welcome: attr("data-welcome", "Welcome to Innovative Technology, the home of transaction automation"),
    prompt: attr("data-prompt", "How can I help today?"),
  };

  // Filled by /widget/config before the panel first opens. Defaults keep the
  // widget fully usable if that fetch fails — a support widget that renders
  // nothing because a config request timed out would be a worse outcome than
  // one showing its built-in wording.
  var serverCfg = {
    intro_options: null,
    sales_form: null,
    support_form: null,
  };

  function applyServerConfig(d) {
    if (!d) return;
    if (d.name && !hasAttr("data-agent-name")) cfg.agent = d.name;
    if (d.name && !hasAttr("data-title")) cfg.title = d.name;
    if (d.welcome && !hasAttr("data-welcome")) cfg.welcome = d.welcome;
    if (d.color && !hasAttr("data-accent")) {
      cfg.accent = d.color;
      // The stylesheet bakes the accent in as `.go-w{--a:...}`; an inline
      // custom property on the same element overrides it, which is how the
      // colour can change after the <style> block has already been written.
      root.style.setProperty("--a", cfg.accent);
    }
    if (d.icon_url && !hasAttr("data-avatar-url")) {
      cfg.avatar = d.icon_url;
      avatarHtml = '<img src="' + esc(cfg.avatar) + '" alt="">';
      Array.prototype.forEach.call(root.querySelectorAll(".go-mav"), function (n) {
        n.innerHTML = avatarHtml;
      });
    }
    if (d.intro_options && d.intro_options.length)
      serverCfg.intro_options = d.intro_options;
    if (d.sales_form) serverCfg.sales_form = d.sales_form;
    if (d.support_form) serverCfg.support_form = d.support_form;
    if (d.sign_in_url && !hasAttr("data-sign-in-url")) cfg.signInUrl = d.sign_in_url;
  }

  var configLoaded = null;   // a promise, so the panel can await it once
  function loadConfig() {
    if (configLoaded) return configLoaded;
    configLoaded = fetch(cfg.api + "/widget/config?visitor_id=" +
                         encodeURIComponent(visitorId()))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { applyServerConfig(d); return d; })
      .catch(function (e) {
        // Deliberately not surfaced to the visitor: the widget still works
        // on its built-in wording, and "could not load configuration" is
        // not their problem to read.
        if (window.console) console.warn("[GroundedOps] config unavailable", e);
        return null;
      });
    return configLoaded;
  }

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
  // Visitor id is stored SEPARATELY from conversation state and is never
  // cleared by "Start over" or by clearSaved(). It used to be the session
  // id, which startFresh() regenerates - so restarting the chat minted a
  // fresh anonymous identity and reset the daily FAQ allowance. The IP
  // ceiling still applied, but the per-visitor limit was one click away
  // from being meaningless.
  var VISITOR_KEY = "groundedops_visitor_id";
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

  function visitorId() {
    try {
      var v = localStorage.getItem(VISITOR_KEY);
      if (!v) {
        v = uuid();
        localStorage.setItem(VISITOR_KEY, v);
      }
      return v;
    } catch (e) {
      // Storage blocked (private mode): fall back to a per-page id. The
      // server-side IP ceiling is what actually bounds these callers.
      if (!window.__goVisitorFallback) window.__goVisitorFallback = uuid();
      return window.__goVisitorFallback;
    }
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
    "font-size:15px;line-height:1.5;color:var(--ink);text-align:left}" +
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
    // Markdown blocks. The bubble sets white-space:pre-wrap for plain text,
    // which would add phantom blank lines around real block elements, so
    // rendered markdown resets it to normal.
    ".go-b .go-md-p,.go-b .go-md-list,.go-b .go-md-tw{white-space:normal}" +
    ".go-md-p{margin:0 0 7px}" +
    ".go-md-p:last-child{margin-bottom:0}" +
    ".go-md-list{margin:4px 0 7px;padding-left:19px}" +
    ".go-md-list li{margin:2px 0}" +
    // Wide pinout tables scroll inside the bubble rather than stretching it.
    ".go-md-tw{overflow-x:auto;margin:6px 0 8px;max-width:100%}" +
    ".go-md-table{border-collapse:collapse;font-size:12.5px;min-width:100%}" +
    ".go-md-table th,.go-md-table td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top;font-variant-numeric:tabular-nums}" +
    ".go-md-table th{font-weight:600;background:rgba(0,0,0,.05)}" +
    ".go-b code{font-family:ui-monospace,Consolas,monospace;font-size:.88em;background:rgba(0,0,0,.06);padding:.1em .32em;border-radius:3px}" +
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
    // contact forms (sales / support), built from the console's config
    ".go-fwrap{background:var(--bg);border:1.5px solid var(--line);border-radius:14px;" +
    "padding:14px 15px;margin-top:8px;display:flex;flex-direction:column;gap:0;align-self:stretch}" +
    ".go-fhead{font-size:14px;font-weight:600;color:var(--ink);margin-bottom:10px}" +
    ".go-flab{display:block;font-size:12px;color:var(--mut);margin:9px 0 4px}" +
    ".go-fin{width:100%;box-sizing:border-box;font:inherit;font-size:14px;color:var(--ink);" +
    "background:var(--bg);border:1.5px solid var(--line);border-radius:9px;padding:9px 10px;resize:vertical}" +
    ".go-fin:focus{outline:none;border-color:var(--a)}" +
    ".go-fbtn{margin-top:12px;border:0;background:var(--a);color:#fff;font:inherit;font-size:14px;" +
    "font-weight:600;padding:11px 16px;border-radius:999px;cursor:pointer}" +
    ".go-fbtn:disabled{opacity:.6;cursor:default}" +
    ".go-ferr{font-size:12.5px;color:#b3261e;margin-top:7px;min-height:0}" +
    ".go-fnote{font-size:12px;color:var(--mut);margin-top:8px;line-height:1.45}" +
    // sources
    ".go-src{margin-top:9px;border-top:1px solid var(--line);padding-top:8px}" +
    // The summary is the button. list-style:none plus the ::-webkit- rule
    // removes the native triangle in every engine that still ships one, so
    // the chevron below is the only marker and it can be rotated.
    ".go-srch{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);" +
    "display:flex;align-items:center;gap:5px;cursor:pointer;list-style:none;" +
    "padding:3px 0;border-radius:6px;user-select:none}" +
    ".go-srch::-webkit-details-marker{display:none}" +
    ".go-srch:hover{color:var(--ink)}" +
    ".go-srch svg{width:12px;height:12px}" +
    ".go-srch::after{content:'';width:6px;height:6px;margin-left:2px;" +
    "border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;" +
    "transform:rotate(45deg);transition:transform .12s}" +
    ".go-src[open] .go-srch::after{transform:rotate(-135deg)}" +
    ".go-src[open] .go-srch{margin-bottom:5px}" +
    ".go-srci{font-size:12.5px;margin-bottom:4px;line-height:1.45}" +
    ".go-srcn{font-weight:600}" +
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
    ".go-settings{padding:12px 14px;border-bottom:1px solid var(--line);background:var(--pane);flex:0 0 auto}" +
    ".go-set-t{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:7px}" +
    ".go-set-row{font-size:13px;margin-top:6px}" +
    ".go-set-note{font-size:12.5px;color:var(--mut);margin-top:7px;line-height:1.45}" +
    ".go-set-note a{color:var(--a);font-weight:600}" +
    ".go-bar{height:6px;border-radius:99px;background:var(--line);overflow:hidden}" +
    ".go-bar span{display:block;height:100%;background:var(--a)}" +
    ".go-foot{text-align:center;font-size:11px;color:var(--mut);padding:0 10px 9px;background:var(--bg);flex:0 0 auto}" +
    "@media (prefers-reduced-motion:reduce){.go-spin{animation:none}}";

  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  // ── icons ─────────────────────────────────────────────────────────────
  var I_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>';
  var I_MIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 12h14"/></svg>';
  var I_GEAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';
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
        '<button class="go-hbtn go-settings-btn" type="button" aria-label="Usage and settings" title="Usage and settings">' + I_GEAR + "</button>" +
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
  var quotaState = null;
  var busy = false;
  var opened = false;

  // ── rendering ─────────────────────────────────────────────────────────
  function scrollDown() {
    $log.scrollTop = $log.scrollHeight;
  }

  /* ── markdown rendering ────────────────────────────────────────────────
   * Answers come back as markdown: pipe tables for pinouts and spec rows,
   * "- item" lists, **bold**. This used to be assigned with textContent, so a
   * pinout arrived as one unreadable run of "| 1 | Vend 1 | Output | ..." on
   * the customer-facing surface, while the admin console rendered it properly.
   *
   * Deliberately small and dependency-free, matching the admin console's
   * renderer: escape everything first, then re-introduce ONLY bullets,
   * numbered lists, pipe tables, bold and code. Nothing in a model answer can
   * inject markup. Written in ES5 style because the widget is plain browser
   * JS with no build step and may run on old customer sites.
   */
  function mdEsc(t) {
    return String(t == null ? "" : t)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function mdInline(t) {
    return mdEsc(t)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }

  var MD_BULLET = /^\s*[-*•]\s+(.*)$/;
  var MD_NUM = /^\s*\d+[.)]\s+(.*)$/;
  var MD_ROW = /^\s*\|.*\|\s*$/;
  var MD_SEP = /^[\s|:\-]+$/;

  function renderMd(text, into) {
    // A model sometimes runs a table onto the same line as its heading
    // ("**Pulse:** | Pin | Name |"), so split pipe runs onto their own lines
    // before parsing or the whole thing reads as one paragraph.
    var normalized = String(text == null ? "" : text).replace(/\s\|\s*\n?/g, function (m) {
      return m.indexOf("\n") >= 0 ? m : " | ";
    });
    var lines = normalized.split(/\r?\n/);
    var i = 0;

    function flushList(ordered) {
      var list = document.createElement(ordered ? "ol" : "ul");
      list.className = "go-md-list";
      while (i < lines.length) {
        var m = lines[i].match(ordered ? MD_NUM : MD_BULLET);
        if (!m) break;
        var li = document.createElement("li");
        li.innerHTML = mdInline(m[1]);
        list.appendChild(li);
        i++;
      }
      into.appendChild(list);
    }

    while (i < lines.length) {
      var line = lines[i];
      if (!line.replace(/\s/g, "")) { i++; continue; }

      if (MD_ROW.test(line)) {
        var wrap = document.createElement("div");
        wrap.className = "go-md-tw";
        var tbl = document.createElement("table");
        tbl.className = "go-md-table";
        var first = true;
        while (i < lines.length && MD_ROW.test(lines[i])) {
          if (!MD_SEP.test(lines[i])) {
            var cells = lines[i].replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|");
            var tr = document.createElement("tr");
            for (var c = 0; c < cells.length; c++) {
              // First non-separator row is the header, which is what makes a
              // pinout readable at a glance.
              var cell = document.createElement(first ? "th" : "td");
              cell.innerHTML = mdInline(cells[c].replace(/^\s+|\s+$/g, ""));
              tr.appendChild(cell);
            }
            tbl.appendChild(tr);
            first = false;
          }
          i++;
        }
        wrap.appendChild(tbl);
        into.appendChild(wrap);
        continue;
      }

      if (MD_BULLET.test(line)) { flushList(false); continue; }
      if (MD_NUM.test(line)) { flushList(true); continue; }

      var para = [];
      while (i < lines.length && lines[i].replace(/\s/g, "")
             && !MD_BULLET.test(lines[i]) && !MD_NUM.test(lines[i])
             && !MD_ROW.test(lines[i])) {
        para.push(lines[i]); i++;
      }
      var p = document.createElement("p");
      p.className = "go-md-p";
      p.innerHTML = mdInline(para.join(" "));
      into.appendChild(p);
    }
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
    // Assistant answers carry markdown (tables, lists, bold); a user's own
    // message is literal text and must never be parsed as markup.
    if (msg.role === "user") {
      txt.textContent = msg.text;
    } else {
      renderMd(msg.text, txt);
    }
    b.appendChild(txt);

    if (msg.sources && msg.sources.length) {
      // COLLAPSED by default, and short when open. It used to be an always-
      // expanded list carrying a text snippet per source, which put several
      // lines of document prose under every answer -- the answer is the
      // answer, and provenance is something you go and look at.
      //
      // <details>/<summary> rather than a click handler: the open/closed
      // state, the keyboard behaviour and the ARIA semantics all come free,
      // which matters in a widget injected into someone else's page.
      var s = document.createElement("details");
      s.className = "go-src";
      var n = Math.min(msg.sources.length, 4);
      s.innerHTML = '<summary class="go-srch">' + I_SHIELD + "Sources (" +
        n + ")</summary>";
      msg.sources.slice(0, 4).forEach(function (x) {
        var i = document.createElement("div");
        i.className = "go-srci";
        // Name, the pages that actually carried the answer, and a way to
        // open the original. The snippet is deliberately gone: it repeated
        // in prose what the answer had just said.
        //
        // The page label is built server-side so every client renders it
        // identically ("page 12" / "pages 12, 14").
        var head = '<span class="go-srcn">' + esc(pretty(x.source)) + "</span>" +
          (x.page_label ? ' <span class="go-pg">' + esc(x.page_label) + "</span>" : "");
        // Fetched with the bearer token rather than a plain link: external
        // /source_file access now requires a valid member token, and an
        // <a href> cannot carry an Authorization header.
        var dl = x.download_url
          ? ' <a class="go-dl" href="#" data-dl="' + esc(x.download_url) + '">Download</a>'
          : "";
        i.innerHTML = head + dl;
        s.appendChild(i);
      });
      s.querySelectorAll("[data-dl]").forEach(function (a) {
        a.addEventListener("click", function (ev) {
          ev.preventDefault();
          var headers = {};
          if (cfg.token) headers["Authorization"] = "Bearer " + cfg.token;
          fetch(cfg.api + a.getAttribute("data-dl"), { headers: headers })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.blob(); })
            .then(function (blob) {
              var url = URL.createObjectURL(blob);
              var tmp = document.createElement("a");
              tmp.href = url;
              tmp.download = a.getAttribute("data-dl").split("/").pop();
              tmp.click();
              setTimeout(function () { URL.revokeObjectURL(url); }, 5000);
            })
            .catch(function () { a.textContent = "Download unavailable"; });
        });
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

  /** Fetch the caller's tier and remaining allowance. Shown in the
   *  settings panel and used to decide whether to offer AI answers. */
  function refreshQuota() {
    var headers = {};
    if (cfg.token) headers["Authorization"] = "Bearer " + cfg.token;
    return fetch(cfg.api + "/widget/quota?visitor_id=" + encodeURIComponent(visitorId()),
                 { headers: headers })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) quotaState = d; return d; })
      .catch(function () { return null; });
  }

  function fmtReset(ts) {
    if (!ts) return "";
    var mins = Math.max(0, Math.round((ts * 1000 - Date.now()) / 60000));
    if (mins < 60) return "resets in " + mins + " min";
    return "resets in " + Math.round(mins / 60) + " h";
  }

  function toggleSettings() {
    var existing = root.querySelector("[data-go-settings]");
    if (existing) { existing.remove(); return; }
    var box = document.createElement("div");
    box.className = "go-settings";
    box.setAttribute("data-go-settings", "");
    box.innerHTML = '<div class="go-set-t">Your usage</div><div class="go-set-b">Loading...</div>';
    root.querySelector(".go-panel").insertBefore(box, $log);
    refreshQuota().then(function (d) {
      var body = box.querySelector(".go-set-b");
      if (!d) { body.textContent = "Usage information is unavailable."; return; }
      var pct = d.limit ? Math.min(100, Math.round((d.used / d.limit) * 100)) : 0;
      var rows =
        '<div class="go-bar"><span style="width:' + pct + '%"></span></div>' +
        '<div class="go-set-row"><b>' + d.remaining + '</b> of ' + d.limit +
          " " + (d.unit || "credits") + " left &middot; " + fmtReset(d.reset_at) + "</div>";
      if (d.tier === "anonymous" && !d.ai_available) {
        // account_notice is set in the console (policy.anon_notice), so the
        // operator decides how guests are told, not this file.
        rows +=
          '<div class="go-set-note">' +
          esc(d.account_notice || "You are browsing as a guest, so I can answer " +
              "from our reviewed FAQs only.") +
          (cfg.signInUrl
            ? ' <a href="' + esc(cfg.signInUrl) + '">Sign in</a> for full AI support.'
            : "") +
          "</div>";
      } else if (d.tier === "anonymous") {
        rows += '<div class="go-set-note">Guest access includes AI answers, ' +
          "with a smaller daily allowance than an account.</div>";
      } else {
        rows += '<div class="go-set-note">Standard answer: ' +
          (d.standard_cost || 1) + " credit. Detailed answer: " +
          (d.deep_cost || 4) + " credits.</div>";
      }
      body.innerHTML = rows;
    });
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
    // Sequenced, not fire-and-forget: announceGuestLimits() depends on a
    // quota fetch that can resolve AFTER these two synchronous calls would
    // already have put askIntent()'s menu on screen. The visible effect was
    // a "you must sign in" notice appearing to have the last word, stacked
    // UNDER a menu that already offered other ways to continue -- the menu
    // still worked, since nothing removes it, but reading top-to-bottom it
    // looked like signing in was the only option left. Waiting for it keeps
    // the order a visitor actually reads in sane: limits stated first, then
    // the real menu right below.
    announceGuestLimits().then(function () {
      say(cfg.prompt);
      askIntent();
    });
    save();
  }

  /** Tell a signed-out visitor what they are and are not getting, at the
   *  start rather than when they hit the wall. Someone who reads "reviewed
   *  FAQs only" up front can decide to sign in; someone who finds out after
   *  typing a real question has wasted their time.
   *
   *  Quota may not have loaded yet on a cold open, so this resolves against
   *  the fetch rather than whatever happens to be cached. Returns a promise
   *  either way so startFresh() can sequence what follows against it. */
  function announceGuestLimits() {
    var show = function (d) {
      if (!d || d.tier !== "anonymous" || d.ai_available) return;
      say(d.account_notice ||
          "Full AI support is for account holders. I can still answer from " +
          "our reviewed FAQs, or put you in touch with our team.");
      if (cfg.signInUrl) {
        chips([{
          label: "Sign in for full AI support",
          onClick: function () { window.open(cfg.signInUrl, "_blank"); },
        }], null);
      }
    };
    if (quotaState) return Promise.resolve(show(quotaState));
    return refreshQuota().then(show);
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
          openContactForm("sales");
        },
      },
    ];

    // The console's configured opening options replace the built-in list
    // when there are any. The built-ins stay as the fallback for a widget
    // whose config fetch failed, so the opening screen is never empty.
    if (serverCfg.intro_options && serverCfg.intro_options.length) {
      items = serverCfg.intro_options.map(function (o) {
        return {
          label: o.label,
          style: o.action === "sales" || o.action === "support" ? "alt" : null,
          onClick: function () {
            heard(o.label);
            runIntroAction(o.action, o.label);
          },
        };
      });
    }

    chips(items, "Choose one");
  }

  /** One of the four actions the console can attach to an opening option:
   *  product (pick a range, then a product), general (ask straight away),
   *  sales / support (open that contact form). */
  function runIntroAction(action, label) {
    if (action === "sales" || action === "support") {
      state.intent = action;
      openContactForm(action);
      return;
    }
    if (action === "general") {
      state.intent = "general";
      say("Go ahead — ask me anything covered by our product documentation.");
      unlockComposer();
      return;
    }
    // "product" and anything unrecognised: the safe path is the one that
    // asks which product, since every answer is scoped to one.
    state.intent = "product";
    say("Which product range is this about?");
    askCategory();
  }

  // ── contact forms (sales / support) ───────────────────────────────────
  // Built entirely from the console's configuration: the title, the fields
  // and whether a chat summary may be attached are all decided there, so
  // adding a field to the "Talk to sales" form in the console adds it here
  // with no change to this file.

  var FALLBACK_FORM = {
    sales: {
      title: "Talk to sales",
      allow_summary: true,
      routed: false,
      fields: [
        { id: "f_name", label: "Your name", type: "text", required: true },
        { id: "f_email", label: "Email", type: "email", required: true },
        { id: "f_msg", label: "What are you looking for?", type: "textarea", required: false },
      ],
    },
    support: {
      title: "Talk to support",
      allow_summary: true,
      routed: false,
      fields: [
        { id: "s_name", label: "Your name", type: "text", required: true },
        { id: "s_email", label: "Email", type: "email", required: true },
        { id: "s_msg", label: "What has gone wrong?", type: "textarea", required: false },
      ],
    },
  };

  function formFor(kind) {
    var f = kind === "sales" ? serverCfg.sales_form : serverCfg.support_form;
    return f && f.fields && f.fields.length ? f : FALLBACK_FORM[kind];
  }

  /** True when there is a real conversation worth summarising. Offering to
   *  "use our chat" before anything has been said would be nonsense. */
  function hasConversation() {
    var real = state.messages.filter(function (m) { return m.role === "user"; });
    return real.length > 0;
  }

  function openContactForm(kind) {
    var form = formFor(kind);
    state.intent = kind;
    state.stage = "form";
    lockComposer("Fill in the form above");
    save();

    say(form.routed
      ? "I can pass this to our " + (kind === "sales" ? "sales" : "support") +
        " team. A few details first."
      : "I can take your details and our " + (kind === "sales" ? "sales" : "support") +
        " team will pick it up. A few details first.");

    // The chat-summary route is offered ONLY when the server will actually
    // rewrite the conversation into a summary -- which needs an account
    // (see /widget/draft_enquiry: guests get an assembled transcript, not a
    // written one). Offering "use our chat so far" to a guest and then
    // pasting the raw back-and-forth into the enquiry is worse than not
    // offering it: it reads as a summary, and it is not one.
    if (form.allow_summary && hasConversation() && canSummarise()) {
      askSummaryChoice(kind, form);
    } else if (form.allow_summary && hasConversation()) {
      askOwnWordsOrSkip(kind, form);
    } else {
      renderForm(kind, form, "", "none");
    }
  }

  /** Whether the server can turn this conversation into a real summary.
   *  quotaState.ai_available is the same flag that governs whether a guest
   *  reaches the model at all, so this tracks the operator's Access &
   *  limits setting without the widget needing to know about it. */
  function canSummarise() {
    return !!(quotaState && quotaState.ai_available);
  }

  /** No AI summary available, but they still have a conversation behind
   *  them. Offer to describe it or skip -- never to "attach the chat". */
  function askOwnWordsOrSkip(kind, form) {
    chips([
      {
        label: "Describe what you need",
        onClick: function () {
          heard("Describe what you need");
          askOwnWords(kind, form);
        },
      },
      {
        label: "Skip \u2014 just my details",
        style: "alt",
        onClick: function () {
          heard("Skip \u2014 just my details");
          renderForm(kind, form, "", "none");
        },
      },
    ], "In your own words?");
  }

  /** The option the request asked for: attach what was already discussed,
   *  or describe it themselves. Nobody is made to re-type a conversation
   *  they have just had, and nobody is forced to send one they would rather
   *  summarise in their own words. */
  function askSummaryChoice(kind, form) {
    chips([
      {
        label: "Summarise our chat for them",
        onClick: function () {
          heard("Summarise our chat for them");
          draftThen(kind, form, "chat", "");
        },
      },
      {
        label: "I'll describe it myself",
        style: "alt",
        onClick: function () {
          heard("I'll describe it myself");
          askOwnWords(kind, form);
        },
      },
      {
        label: "Skip — just my details",
        style: "alt",
        onClick: function () {
          heard("Skip — just my details");
          renderForm(kind, form, "", "none");
        },
      },
    ], "What should they see?");
  }

  function askOwnWords(kind, form) {
    var wrap = document.createElement("div");
    wrap.className = "go-fwrap";
    wrap.setAttribute("data-chips", "");
    var lab = document.createElement("label");
    lab.className = "go-flab";
    lab.textContent = "In your own words";
    var ta = document.createElement("textarea");
    ta.className = "go-fin";
    ta.rows = 4;
    ta.placeholder = "What do you need help with?";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "go-fbtn";
    btn.textContent = "Continue";
    btn.addEventListener("click", function () {
      var v = ta.value.trim();
      if (!v) { ta.focus(); return; }
      clearChips();
      heard(v);
      draftThen(kind, form, "written", v);
    });
    wrap.appendChild(lab);
    wrap.appendChild(ta);
    wrap.appendChild(btn);
    $log.appendChild(wrap);
    scrollDown();
    ta.focus();
  }

  /** Ask the server to write the enquiry up, then show it for editing.
   *  A failure here is not fatal: the endpoint always returns something
   *  usable, and the visitor can rewrite it anyway. */
  function draftThen(kind, form, source, notes) {
    var st = status("Writing this up…");
    fetch(cfg.api + "/widget/draft_enquiry", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        kind: kind,
        visitor_id: visitorId(),
        product: state.product ? state.product.key : null,
        notes: notes,
        source: source,
        transcript: source === "chat" ? transcriptForServer() : [],
      }),
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        st.remove();
        renderForm(kind, form, (d && d.draft) || notes, source,
                   d && d.written_by === "model");
      })
      .catch(function () {
        st.remove();
        // Straight to the form with whatever they typed. Losing the
        // write-up is a downgrade; losing the enquiry would not be.
        renderForm(kind, form, notes, source, false);
      });
  }

  function transcriptForServer() {
    return state.messages.slice(-12).map(function (m) {
      return { role: m.role, text: m.text };
    });
  }

  function authHeaders() {
    var h = { "Content-Type": "application/json" };
    if (cfg.token) h["Authorization"] = "Bearer " + cfg.token;
    return h;
  }

  function renderForm(kind, form, enquiry, source, byModel) {
    var wrap = document.createElement("div");
    wrap.className = "go-fwrap";
    wrap.setAttribute("data-chips", "");

    var h = document.createElement("div");
    h.className = "go-fhead";
    h.textContent = form.title || (kind === "sales" ? "Talk to sales" : "Talk to support");
    wrap.appendChild(h);

    // The alternative to waiting for a reply. Shown above the fields, so
    // someone who would rather just phone does not fill a form first.
    if (form.phone) {
      var ph = document.createElement("div");
      ph.className = "go-fnote";
      ph.textContent = "Prefer to call? " + form.phone;
      wrap.appendChild(ph);
    }

    var inputs = {};
    form.fields.forEach(function (f) {
      var lab = document.createElement("label");
      lab.className = "go-flab";
      lab.textContent = f.label + (f.required ? " *" : "");
      var node;
      if (f.type === "textarea") {
        node = document.createElement("textarea");
        node.rows = 3;
      } else {
        node = document.createElement("input");
        // text / email / tel — the browser's own keyboard and validation
        // for each, which matters most on a phone.
        node.type = f.type === "email" ? "email" : f.type === "tel" ? "tel" : "text";
      }
      node.className = "go-fin";
      if (f.required) node.required = true;
      inputs[f.id] = node;
      wrap.appendChild(lab);
      wrap.appendChild(node);
    });

    var enqBox = null;
    if (form.allow_summary && (enquiry || source !== "none")) {
      var elab = document.createElement("label");
      elab.className = "go-flab";
      elab.textContent = byModel
        ? "Summary (written for you \u2014 edit it if it's wrong)"
        : source === "written" ? "What you told us" : "Your conversation";
      enqBox = document.createElement("textarea");
      enqBox.className = "go-fin";
      enqBox.rows = 5;
      enqBox.value = enquiry || "";
      wrap.appendChild(elab);
      wrap.appendChild(enqBox);
    }

    if (form.cc_visitor) {
      var ccNote = document.createElement("div");
      ccNote.className = "go-fnote";
      ccNote.textContent = "We will copy you in on the reply, so you have the "
        + "thread and can chase it directly.";
      wrap.appendChild(ccNote);
    }

    var err = document.createElement("div");
    err.className = "go-ferr";
    wrap.appendChild(err);

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "go-fbtn";
    btn.textContent = "Send";
    btn.addEventListener("click", function () {
      var values = {};
      var missing = null;
      var badEmail = null;
      form.fields.forEach(function (f) {
        var v = (inputs[f.id].value || "").trim();
        if (f.required && !v && !missing) missing = f.label;
        // A reply goes to this address, so a typo here loses the enquiry
        // silently. Checked in the browser as well as on the server.
        if (v && f.type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v)
            && !badEmail) badEmail = f.label;
        if (v) values[f.id] = v;
      });
      if (missing) {
        err.textContent = missing + " is required — we cannot reply without it.";
        return;
      }
      if (badEmail) {
        err.textContent = "That " + badEmail.toLowerCase() +
          " does not look right. We need a working address to reply to.";
        return;
      }
      err.textContent = "";
      btn.disabled = true;
      btn.textContent = "Sending…";

      fetch(cfg.api + "/widget/lead", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          kind: kind,
          values: values,
          product: state.product ? state.product.key : null,
          transcript: form.allow_summary ? transcriptForServer() : [],
          enquiry: enqBox ? enqBox.value.trim() : "",
          summary_source: enqBox && enqBox.value.trim() ? source : "none",
        }),
      })
        .then(function (r) {
          if (!r.ok) return r.json().then(function (d) {
            throw new Error((d && d.detail) || "HTTP " + r.status);
          });
          return r.json();
        })
        .then(function () {
          wrap.remove();
          say("Thanks — that's been recorded and our " +
              (kind === "sales" ? "sales" : "support") +
              " team will be in touch. Anything else I can help with?");
          chips([
            {
              label: "Ask a question",
              onClick: function () {
                heard("Ask a question");
                askCategory();
              },
            },
          ], null);
        })
        .catch(function (e) {
          btn.disabled = false;
          btn.textContent = "Send";
          err.textContent = e.message || "That didn't send. Please try again.";
        });
    });
    wrap.appendChild(btn);

    $log.appendChild(wrap);
    scrollDown();
    var first = form.fields.length ? inputs[form.fields[0].id] : null;
    if (first) first.focus();
  }

  function fetchCatalog() {
    if (catalogCache) return Promise.resolve(catalogCache);
    return fetch(cfg.api + "/widget/catalog")
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
    fetch(cfg.api + "/widget/faq?product=" + encodeURIComponent(state.product.key))
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

  // ── "let me talk to a person" ─────────────────────────────────────────
  // Recognised on the client, before the question is sent anywhere. Three
  // reasons for doing it here rather than in the backend: it costs nothing,
  // it works for anonymous visitors who cannot reach the model at all, and
  // it does not consume one of their questions to be told "sure, here is a
  // form". Deliberately conservative -- a false positive interrupts someone
  // who was asking a real question, which is worse than missing one.
  var HUMAN_RE = /(speak|talk|chat|connect|put me (?:in touch|through))[^.?!]{0,30}(human|person|someone|somebody|agent|advisor|adviser|rep|representative|team|staff|engineer)/i;
  var SUPPORT_RE = /(?:contact|call|email|reach|raise (?:a )?(?:ticket|case)|log (?:a )?(?:ticket|case))[^.?!]{0,20}(support|service|help ?desk|technical)|(?:customer|tech(?:nical)?) support|support (?:team|number|line|desk|email)/i;
  var SALES_RE = /(?:contact|call|email|reach|speak to|talk to)[^.?!]{0,20}sales|sales (?:team|rep|number|line|enquiry|enquiries|inquiry)|(?:get|request) a (?:quote|price|pricing)|buy|purchase order/i;

  /** Which contact route a typed message is asking for, or null.
   *  Sales is tested first: "talk to someone in sales" matches both, and
   *  the more specific intent is the one they said out loud. */
  function contactIntent(text) {
    var t = (text || "").trim();
    if (!t || t.length > 200) return null;
    if (SALES_RE.test(t)) return "sales";
    if (SUPPORT_RE.test(t)) return "support";
    if (HUMAN_RE.test(t)) return "support";
    return null;
  }

  /** Offer the route rather than jumping straight into the form: they may
   *  have meant something adjacent, and a form that opens unbidden over a
   *  half-typed question is worse than a button. */
  function offerContact(kind, typed) {
    heard(typed);
    var form = formFor(kind);
    var line = kind === "sales"
      ? "Of course — I can put you in touch with our sales team."
      : "Of course — I can put you through to our support team.";
    if (form.phone) line += " You can also call us on " + form.phone + ".";
    say(line);
    var items = [{
      label: form.title || (kind === "sales" ? "Talk to sales" : "Talk to support"),
      onClick: function () { openContactForm(kind); },
    }];
    // Not a dead end: they might have been mid-question.
    items.push({
      label: "No, carry on here",
      style: "alt",
      onClick: function () {
        say("No problem — what would you like to know?");
        if (state.product) unlockComposer();
      },
    });
    chips(items, "Contact us");
  }

  function ask(q, opts) {
    opts = opts || {};
    if (busy) return;

    // Asking for a human is answered here, not by the pipeline. Skipped for
    // a question the assistant itself put on screen (opts.silent), which is
    // a re-ask rather than something the visitor typed.
    if (!opts.silent && !opts.faqId) {
      var want = contactIntent(q);
      if (want) {
        clearChips();
        offerContact(want, q);
        return;
      }
    }

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

    var headers = { "Content-Type": "application/json" };
    if (cfg.token) headers["Authorization"] = "Bearer " + cfg.token;

    fetch(cfg.api + "/widget/ask", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({
        q: q,
        session_id: state.sessionId,
        visitor_id: visitorId(),
        effort: opts.effort || (quotaState && quotaState.ai_available ? "standard" : "faq_only"),
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
        // v12.0: FAQ near-miss comes back as a clarify with suggestions
        // rather than a served answer. Offer it as a chip so one tap asks
        // the exact curated question.
        // v12.0: the backend no longer guesses whether a curated FAQ
        // matches — it offers candidates and we let the user decide.
        // Selecting one sends its faq_id, so the exact chosen answer is
        // served with no re-matching. "Something else" logs a FAQ gap and
        // answers from the documents instead.
        if (d.quota) quotaState = d.quota;

        // Backend says a full answer needs an account.
        if (d.needs_sign_in) {
          say(d.answer, { flagged: false });
          if (d.sign_in_url || cfg.signInUrl) {
            chips([{
              label: "Sign in for a full answer",
              onClick: function () { window.open(d.sign_in_url || cfg.signInUrl, "_blank"); },
            }], null);
          }
          return;
        }

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
                label: "None of these \u2014 search the documents",
                style: "alt",
                onClick: function () { askDocs(q); },
              }]),
            "Select a question"
          );
          return;
        }
        say(d.answer || "No answer returned.", {
          sources: d.flagged ? null : d.sources,
          flagged: !!d.flagged,
          badge: d.from_faq ? "Reviewed answer" : null,
        });

        // A refusal with no next step leaves the visitor stuck: the
        // documentation genuinely does not cover it, and the widget just says
        // so and stops. Offer a person. The backend sets offer_support on any
        // refusal branch, so this covers a low-confidence miss, a suppressed
        // ungrounded answer, and the model declining on its own.
        if (d.offer_support) {
          // Say what actually happened, in the terms a person would use:
          // this is not in what I can read, here is how to reach someone who
          // knows. The backend's refusal text is accurate but bare, and
          // "I cannot answer that" with no route onward reads as a brush-off.
          var sForm = formFor("support");
          var offer = "That is not something I have in my knowledge base, so I "
            + "would rather point you at someone than guess.";
          if (sForm.phone) {
            offer += " You can email our support team, or call them on "
                   + sForm.phone + ".";
          } else {
            offer += " I can pass it to our support team by email.";
          }
          say(offer);

          var supportChips = [{
            // The support form, not a mailto: — it collects the configured
            // fields, can carry a write-up of this very conversation, and
            // lands in the console where it will actually be seen. The old
            // mailto: threw the visitor into their mail client with the
            // question pasted in and no record kept anywhere.
            label: "Email support",
            onClick: function () { openContactForm("support"); },
          }];
          supportChips.push({
            label: "Try rewording my question",
            style: "alt",
            onClick: function () { $in.focus(); },
          });
          chips(supportChips, "What would you like to do?");
        }
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
      // Config must be applied before the first screen is drawn: the welcome
      // line and the opening options both come from it, and drawing the
      // built-in ones first would show a flash of the wrong wording. The
      // fetch resolves even on failure, so this cannot leave the panel blank.
      loadConfig().then(function () {
        var saved = loadSaved();
        if (saved) offerResume(saved);
        else startFresh();
      });
    }
  });

  // Warm the config as soon as the script runs rather than on first open, so
  // the panel is usually ready instantly. Harmless if never opened: one
  // small GET.
  loadConfig();

  function close() {
    root.classList.remove("go-open");
    $launch.setAttribute("aria-expanded", "false");
    $launch.focus();
  }
  $min.addEventListener("click", close);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && root.classList.contains("go-open")) close();
  });

  root.querySelector(".go-settings-btn").addEventListener("click", toggleSettings);

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
