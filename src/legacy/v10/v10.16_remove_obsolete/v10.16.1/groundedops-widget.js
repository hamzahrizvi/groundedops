/*!
 * GroundedOps embeddable support widget (v1)
 * ------------------------------------------------------------------
 * Drop-in chat widget that talks to a GroundedOps backend's POST /query.
 * No build step, no dependencies. Works on any site.
 *
 * Embed:
 *   <script
 *     src="https://your-cdn/groundedops-widget.js"
 *     data-api="https://your-backend.example.com"
 *     data-title="Product support"
 *     data-scope-product=""          (optional: restrict to one product key)
 *     data-scope-category=""         (optional: restrict to one category key)
 *     data-accent="#1F6F5C"          (optional)
 *     data-launcher-label="Ask a question"   (optional)
 *   ></script>
 *
 * The backend decides the model (local/Online) from its own config; the
 * widget never holds an API key. It only ever sends the visitor's
 * question, a per-browser session id, and the optional scope.
 *
 * NOTE (functional-first): the backend must allow this page's origin via
 * CORS. See the CORS snippet shipped alongside this file. Locking the
 * allowed origins down (and auth / rate limiting / GDPR) is deliberately
 * out of scope for this first cut.
 */
(function () {
  "use strict";

  // Guard against double-inclusion.
  if (window.__groundedOpsWidgetLoaded) return;
  window.__groundedOpsWidgetLoaded = true;

  var script =
    document.currentScript ||
    (function () {
      var s = document.getElementsByTagName("script");
      return s[s.length - 1];
    })();

  var cfg = {
    api: (script.getAttribute("data-api") || "").replace(/\/+$/, ""),
    title: script.getAttribute("data-title") || "Support",
    product: script.getAttribute("data-scope-product") || null,
    category: script.getAttribute("data-scope-category") || null,
    accent: script.getAttribute("data-accent") || "#1F6F5C",
    launcherLabel: script.getAttribute("data-launcher-label") || "Ask a question",
    greeting:
      script.getAttribute("data-greeting") ||
      "Ask about the product documentation. Every answer is checked against the source material — if it isn't in the docs, I'll say so rather than guess.",
  };

  if (!cfg.api) {
    console.error("[GroundedOps] Missing data-api on the widget script tag.");
    return;
  }

  // ---- session id (per browser, survives reloads) ----------------------
  var SESSION_KEY = "groundedops_session_id";
  var sessionId;
  try {
    sessionId = localStorage.getItem(SESSION_KEY);
    if (!sessionId) {
      sessionId = uuid();
      localStorage.setItem(SESSION_KEY, sessionId);
    }
  } catch (e) {
    sessionId = uuid(); // private mode / storage blocked — ephemeral is fine
  }

  function uuid() {
    if (crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  // ---- styles (scoped under .go-widget, injected once) -----------------
  var accent = cfg.accent;
  var css =
    "" +
    ".go-widget,.go-widget *{box-sizing:border-box}" +
    ".go-widget{--go-accent:" +
    accent +
    ";--go-ink:#14201c;--go-muted:#5b6b64;--go-line:#e4e7e4;--go-bg:#ffffff;--go-panel:#f7f8f7;" +
    "position:fixed;bottom:20px;right:20px;z-index:2147483000;" +
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;" +
    "font-size:15px;line-height:1.5;color:var(--go-ink)}" +
    // launcher
    ".go-launcher{display:inline-flex;align-items:center;gap:8px;cursor:pointer;border:0;" +
    "background:var(--go-accent);color:#fff;padding:12px 18px;border-radius:999px;" +
    "font-size:15px;font-weight:600;box-shadow:0 6px 24px rgba(20,32,28,.22);" +
    "transition:transform .12s ease,box-shadow .12s ease}" +
    ".go-launcher:hover{transform:translateY(-1px);box-shadow:0 10px 30px rgba(20,32,28,.28)}" +
    ".go-launcher:focus-visible{outline:3px solid rgba(31,111,92,.4);outline-offset:2px}" +
    ".go-launcher svg{width:18px;height:18px}" +
    // panel
    ".go-panel{position:absolute;bottom:0;right:0;width:390px;max-width:calc(100vw - 32px);" +
    "height:560px;max-height:calc(100vh - 40px);background:var(--go-bg);border:1px solid var(--go-line);" +
    "border-radius:16px;box-shadow:0 24px 60px rgba(20,32,28,.24);display:none;flex-direction:column;" +
    "overflow:hidden;transform-origin:bottom right;animation:go-pop .16s ease}" +
    "@keyframes go-pop{from{opacity:0;transform:scale(.96) translateY(8px)}to{opacity:1;transform:none}}" +
    ".go-open .go-panel{display:flex}.go-open .go-launcher{display:none}" +
    // header
    ".go-head{display:flex;align-items:center;justify-content:space-between;gap:10px;" +
    "padding:14px 16px;border-bottom:1px solid var(--go-line);background:var(--go-bg)}" +
    ".go-head-t{font-weight:700;font-size:15px;letter-spacing:-.01em}" +
    ".go-head-badge{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--go-muted);margin-top:2px}" +
    ".go-dot{width:7px;height:7px;border-radius:50%;background:var(--go-accent)}" +
    ".go-x{border:0;background:transparent;cursor:pointer;color:var(--go-muted);padding:6px;border-radius:8px;line-height:0}" +
    ".go-x:hover{background:var(--go-panel);color:var(--go-ink)}" +
    ".go-x svg{width:18px;height:18px}" +
    // messages
    ".go-log{flex:1;overflow-y:auto;padding:16px;background:var(--go-panel);display:flex;flex-direction:column;gap:12px}" +
    ".go-msg{max-width:88%;padding:10px 13px;border-radius:14px;white-space:pre-wrap;word-wrap:break-word}" +
    ".go-user{align-self:flex-end;background:var(--go-accent);color:#fff;border-bottom-right-radius:4px}" +
    ".go-bot{align-self:flex-start;background:var(--go-bg);border:1px solid var(--go-line);border-bottom-left-radius:4px}" +
    ".go-bot.go-refused{border-color:#e3d3b0;background:#fbf7ee}" +
    ".go-intro{align-self:stretch;max-width:100%;color:var(--go-muted);font-size:13.5px;background:transparent;padding:2px 2px 4px}" +
    // sources
    ".go-src{margin-top:9px;border-top:1px solid var(--go-line);padding-top:9px}" +
    ".go-src-h{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--go-muted);margin-bottom:6px;display:flex;align-items:center;gap:5px}" +
    ".go-src-h svg{width:12px;height:12px}" +
    ".go-src-item{font-size:13px;margin-bottom:6px}" +
    ".go-src-name{font-weight:600;color:var(--go-ink)}" +
    ".go-src-snip{color:var(--go-muted);font-size:12.5px;margin-top:1px}" +
    ".go-meta{margin-top:7px;font-size:11px;color:var(--go-muted)}" +
    // typing
    ".go-typing{align-self:flex-start;display:inline-flex;gap:4px;padding:12px 14px;background:var(--go-bg);border:1px solid var(--go-line);border-radius:14px}" +
    ".go-typing span{width:6px;height:6px;border-radius:50%;background:var(--go-muted);animation:go-blink 1.2s infinite}" +
    ".go-typing span:nth-child(2){animation-delay:.2s}.go-typing span:nth-child(3){animation-delay:.4s}" +
    "@keyframes go-blink{0%,60%,100%{opacity:.25}30%{opacity:1}}" +
    // composer
    ".go-form{display:flex;gap:8px;padding:12px;border-top:1px solid var(--go-line);background:var(--go-bg)}" +
    ".go-input{flex:1;resize:none;border:1px solid var(--go-line);border-radius:10px;padding:10px 12px;" +
    "font:inherit;font-size:14px;max-height:120px;color:var(--go-ink)}" +
    ".go-input:focus{outline:2px solid rgba(31,111,92,.35);border-color:var(--go-accent)}" +
    ".go-send{border:0;background:var(--go-accent);color:#fff;border-radius:10px;width:42px;cursor:pointer;flex:0 0 auto;display:flex;align-items:center;justify-content:center}" +
    ".go-send:disabled{opacity:.45;cursor:default}.go-send svg{width:18px;height:18px}" +
    ".go-foot{text-align:center;font-size:11px;color:var(--go-muted);padding:0 12px 10px;background:var(--go-bg)}" +
    "@media (prefers-reduced-motion:reduce){.go-panel{animation:none}.go-typing span{animation:none}}";

  var styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ---- svg icons -------------------------------------------------------
  var ICON_CHAT =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>';
  var ICON_CLOSE =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  var ICON_SEND =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-7 20-4-9-9-4 20-7z"/></svg>';
  var ICON_SHIELD =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>';

  // ---- DOM -------------------------------------------------------------
  var root = document.createElement("div");
  root.className = "go-widget";
  root.setAttribute("data-go-root", "");
  root.innerHTML =
    '<button class="go-launcher" type="button" aria-haspopup="dialog" aria-expanded="false">' +
    ICON_CHAT +
    "<span>" +
    esc(cfg.launcherLabel) +
    "</span></button>" +
    '<section class="go-panel" role="dialog" aria-modal="false" aria-label="' +
    esc(cfg.title) +
    '">' +
    '<header class="go-head"><div><div class="go-head-t">' +
    esc(cfg.title) +
    "</div>" +
    '<div class="go-head-badge">' +
    ICON_SHIELD +
    "Answers verified against the docs</div></div>" +
    '<button class="go-x" type="button" aria-label="Close">' +
    ICON_CLOSE +
    "</button></header>" +
    '<div class="go-log" role="log" aria-live="polite"></div>' +
    '<form class="go-form"><textarea class="go-input" rows="1" placeholder="Type your question…" aria-label="Your question"></textarea>' +
    '<button class="go-send" type="submit" aria-label="Send" disabled>' +
    ICON_SEND +
    "</button></form>" +
    '<div class="go-foot">Powered by your documentation</div>' +
    "</section>";
  document.body.appendChild(root);

  var launcher = root.querySelector(".go-launcher");
  var panel = root.querySelector(".go-panel");
  var closeBtn = root.querySelector(".go-x");
  var log = root.querySelector(".go-log");
  var form = root.querySelector(".go-form");
  var input = root.querySelector(".go-input");
  var send = root.querySelector(".go-send");

  var greeted = false;
  var busy = false;

  function openPanel() {
    root.classList.add("go-open");
    launcher.setAttribute("aria-expanded", "true");
    if (!greeted) {
      addIntro(cfg.greeting);
      greeted = true;
    }
    setTimeout(function () {
      input.focus();
    }, 60);
  }
  function closePanel() {
    root.classList.remove("go-open");
    launcher.setAttribute("aria-expanded", "false");
    launcher.focus();
  }

  launcher.addEventListener("click", openPanel);
  closeBtn.addEventListener("click", closePanel);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && root.classList.contains("go-open")) closePanel();
  });

  // grow textarea + enable send
  input.addEventListener("input", function () {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
    send.disabled = !input.value.trim() || busy;
  });
  // Enter sends, Shift+Enter newline
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q || busy) return;
    input.value = "";
    input.style.height = "auto";
    ask(q);
  });

  // ---- rendering helpers ----------------------------------------------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function scrollDown() {
    log.scrollTop = log.scrollHeight;
  }
  function addIntro(text) {
    var el = document.createElement("div");
    el.className = "go-intro";
    el.textContent = text;
    log.appendChild(el);
    scrollDown();
  }
  function addUser(text) {
    var el = document.createElement("div");
    el.className = "go-msg go-user";
    el.textContent = text;
    log.appendChild(el);
    scrollDown();
  }
  function addTyping() {
    var el = document.createElement("div");
    el.className = "go-typing";
    el.innerHTML = "<span></span><span></span><span></span>";
    log.appendChild(el);
    scrollDown();
    return el;
  }
  function addBot(data) {
    var refused = !!data.flagged; // suppressed / not-found answers come back flagged
    var el = document.createElement("div");
    el.className = "go-msg go-bot" + (refused ? " go-refused" : "");

    var body = document.createElement("div");
    body.textContent = data.answer || "No answer returned.";
    el.appendChild(body);

    // sources (only for grounded answers that actually have them)
    if (!refused && Array.isArray(data.sources) && data.sources.length) {
      var src = document.createElement("div");
      src.className = "go-src";
      src.innerHTML =
        '<div class="go-src-h">' + ICON_SHIELD + "Sources</div>";
      data.sources.slice(0, 4).forEach(function (s) {
        var item = document.createElement("div");
        item.className = "go-src-item";
        item.innerHTML =
          '<div class="go-src-name">' + esc(prettyName(s.source)) + "</div>" +
          (s.snippet ? '<div class="go-src-snip">' + esc(s.snippet) + "</div>" : "");
        src.appendChild(item);
      });
      el.appendChild(src);
    }

    if (typeof data.response_time_ms === "number") {
      var meta = document.createElement("div");
      meta.className = "go-meta";
      meta.textContent = "Answered in " + (data.response_time_ms / 1000).toFixed(1) + "s";
      el.appendChild(meta);
    }

    log.appendChild(el);
    scrollDown();
  }
  function prettyName(name) {
    return String(name || "document")
      .replace(/\.[a-z0-9]+$/i, "")
      .replace(/[_-]+/g, " ")
      .trim();
  }

  // ---- the network call ------------------------------------------------
  function ask(q) {
    busy = true;
    send.disabled = true;
    addUser(q);
    var typing = addTyping();

    var payload = { q: q, session_id: sessionId };
    if (cfg.product) payload.product = cfg.product;
    if (cfg.category) payload.category = cfg.category;

    fetch(cfg.api + "/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        typing.remove();
        addBot(data);
      })
      .catch(function (err) {
        typing.remove();
        addBot({
          flagged: true,
          answer:
            "That didn't go through. Check your connection and try again — if it keeps happening, the support service may be temporarily unavailable.",
        });
        console.error("[GroundedOps] query failed:", err);
      })
      .then(function () {
        busy = false;
        send.disabled = !input.value.trim();
        input.focus();
      });
  }
})();
