/*
  Coupang Trader Combo Widget (food ads)
  - Primary: GET /api/coupang-food-ads (REST)
  - Fallback: local JSON dataset
  - Built-in client cache + refresh cooldown to reduce request volume

  Integration:
    <div id="cp-trader-combo"
         data-endpoint="/site2/api/coupang-food-ads"
         data-products="/site2/coupang-products-2026.json"
         data-variant="inline"
         data-theme="auto"
         data-subid="marketmonitor"
         data-category="food"></div>
    <script src="/site2/coupang-trader-combo-widget.js" defer></script>
*/

(function () {
  "use strict";

  var SLOT_ID = "cp-trader-combo";
  var HIDE_KEY = "cp_trader_combo_hide_until";
  var PAYLOAD_CACHE_KEY = "cp_trader_combo_payload_cache_v1";

  var DEFAULT_HIDE_HOURS = 6;
  var DEFAULT_ENDPOINT = "/api/coupang-food-ads";
  var DEFAULT_PRODUCTS_URL = "/site2/coupang-products-2026.json";
  var PAYLOAD_CACHE_TTL_MS = 15 * 60 * 1000;
  var REFRESH_COOLDOWN_MS = 10 * 1000;
  var DISCLOSURE_TEXT = "※ 이 링크는 쿠팡 파트너스 활동의 일환으로 일정 수수료를 제공받을 수 있습니다.";

  var localItemsCache = null;
  var localItemsCacheAt = 0;
  var lastRefreshAt = 0;

  var THEMES = {
    auto: {
      title: "트레이더 추천 식품 3종 세트",
      tagline: "시장 모니터링용 식품 추천",
      cta: "쿠팡에서 보기",
    },
    focus: {
      title: "집중력 보완 식품 3종",
      tagline: "커피/단백질/간식 조합",
      cta: "바로 확인",
    },
    fresh: {
      title: "리프레시 식품 3종",
      tagline: "가벼운 음료/견과/과일 조합",
      cta: "상큼하게 보기",
    },
    meal: {
      title: "든든한 식품 3종",
      tagline: "간편식 + 음료 + 단백질 조합",
      cta: "한 끼 보완",
    },
    night: {
      title: "야간 세션 식품 3종",
      tagline: "야간/마감용 식품 조합",
      cta: "야식 조합 보기",
    },
  };

  function nowMs() {
    return Date.now ? Date.now() : new Date().getTime();
  }

  function esc(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeHref(href) {
    var v = String(href || "").trim();
    if (!v) return "#";
    if (/^https?:\/\//i.test(v)) return v;
    return "#";
  }

  function safeImage(src) {
    var v = String(src || "").trim();
    if (!v) return "";
    try {
      return encodeURI(v);
    } catch (e) {
      return v;
    }
  }

  function toNumber(value) {
    if (typeof value === "number" && isFinite(value)) return Math.round(value);
    var digits = String(value || "").replace(/[^\d]/g, "");
    if (!digits) return NaN;
    return parseInt(digits, 10);
  }

  function formatWon(value) {
    if (typeof value !== "number" || !isFinite(value)) return "";
    return value.toLocaleString("ko-KR") + "원";
  }

  function buildUrl(base, params) {
    var q = [];
    for (var k in params) {
      if (!Object.prototype.hasOwnProperty.call(params, k)) continue;
      var v = params[k];
      if (v === undefined || v === null || v === "" || v === "auto") continue;
      q.push(encodeURIComponent(k) + "=" + encodeURIComponent(String(v)));
    }
    if (!q.length) return base;
    return base + (base.indexOf("?") >= 0 ? "&" : "?") + q.join("&");
  }

  function getHideUntil() {
    try {
      var v = localStorage.getItem(HIDE_KEY);
      var n = parseInt(v || "0", 10);
      return isFinite(n) ? n : 0;
    } catch (e) {
      return 0;
    }
  }

  function setHide(hours) {
    try {
      var until = nowMs() + hours * 3600 * 1000;
      localStorage.setItem(HIDE_KEY, String(until));
    } catch (e) {}
  }

  function ensureSlot() {
    var el = document.getElementById(SLOT_ID);
    if (el) return el;
    el = document.createElement("div");
    el.id = SLOT_ID;
    el.setAttribute("data-variant", "floating");
    document.body.appendChild(el);
    return el;
  }

  function resolveTheme(slot) {
    var key = String(slot.getAttribute("data-theme") || "auto").toLowerCase();
    return THEMES[key] || THEMES.auto;
  }

  function resolveEndpoint(slot) {
    var endpoint = slot.getAttribute("data-endpoint") || "";
    return endpoint || DEFAULT_ENDPOINT;
  }

  function resolveProductsUrl(slot) {
    var dataUrl = slot.getAttribute("data-products") || slot.getAttribute("data-products-url") || "";
    return dataUrl || DEFAULT_PRODUCTS_URL;
  }

  function resolveCategory(slot) {
    var category = String(slot.getAttribute("data-category") || "food").toLowerCase();
    return category || "food";
  }

  function cacheKeyFor(slot, theme, subId, category) {
    return [resolveEndpoint(slot), theme || "auto", subId || "", category || "food"].join("|");
  }

  function readPayloadCache(slot, theme, subId, category) {
    try {
      var raw = localStorage.getItem(PAYLOAD_CACHE_KEY);
      if (!raw) return null;
      var all = JSON.parse(raw);
      if (!all || typeof all !== "object") return null;
      var key = cacheKeyFor(slot, theme, subId, category);
      var hit = all[key];
      if (!hit || typeof hit !== "object") return null;
      if (!hit.payload || typeof hit.at !== "number") return null;
      if (nowMs() - hit.at > PAYLOAD_CACHE_TTL_MS) return null;
      return hit.payload;
    } catch (e) {
      return null;
    }
  }

  function writePayloadCache(slot, theme, subId, category, payload) {
    try {
      var raw = localStorage.getItem(PAYLOAD_CACHE_KEY);
      var all = raw ? JSON.parse(raw) : {};
      if (!all || typeof all !== "object") all = {};
      var key = cacheKeyFor(slot, theme, subId, category);
      all[key] = {
        at: nowMs(),
        payload: payload,
      };
      localStorage.setItem(PAYLOAD_CACHE_KEY, JSON.stringify(all));
    } catch (e) {}
  }

  function injectStylesOnce() {
    if (document.getElementById("cp-trader-combo-style")) return;
    var css = ""
      + "#cp-trader-combo{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}"
      + "#cp-trader-combo.cp-hidden{display:none !important;}"
      + ".cp-tc-card{border:1px solid rgba(148,163,184,.25);background:rgba(15,23,42,.92);backdrop-filter:blur(10px);color:#e2e8f0;border-radius:14px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.35);}"
      + ".cp-tc-floating{position:fixed;right:16px;bottom:16px;z-index:2147482000;width:min(360px,calc(100vw - 32px));}"
      + ".cp-tc-header{display:flex;align-items:flex-start;gap:10px;padding:12px 12px 10px;}"
      + ".cp-tc-badge{font-size:11px;line-height:1;padding:4px 6px;border-radius:999px;background:rgba(249,115,22,.18);color:#fdba74;border:1px solid rgba(249,115,22,.35);}"
      + ".cp-tc-title{font-size:14px;font-weight:700;letter-spacing:-.2px;margin:0;}"
      + ".cp-tc-tagline{font-size:12px;color:rgba(226,232,240,.75);margin:2px 0 0;line-height:1.35;}"
      + ".cp-tc-actions{margin-left:auto;display:flex;gap:6px;}"
      + ".cp-tc-btn{cursor:pointer;user-select:none;border:1px solid rgba(148,163,184,.25);background:rgba(2,6,23,.35);color:#e2e8f0;border-radius:10px;padding:6px 8px;font-size:12px;line-height:1;}"
      + ".cp-tc-btn:hover{background:rgba(2,6,23,.55);}"
      + ".cp-tc-btn:disabled{opacity:.45;cursor:not-allowed;}"
      + ".cp-tc-body{padding:0 12px 12px;}"
      + ".cp-tc-items{display:grid;grid-template-columns:1fr;gap:8px;margin:0;padding:0;list-style:none;}"
      + ".cp-tc-item{display:flex;align-items:center;gap:10px;padding:8px;border:1px solid rgba(148,163,184,.15);border-radius:12px;background:rgba(2,6,23,.25);text-decoration:none;color:inherit;}"
      + ".cp-tc-item:hover{background:rgba(2,6,23,.4);}"
      + ".cp-tc-img{width:52px;height:52px;border-radius:10px;object-fit:cover;background:rgba(148,163,184,.12);flex:0 0 auto;}"
      + ".cp-tc-name{font-size:12px;font-weight:650;line-height:1.3;margin:0;max-height:2.6em;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;}"
      + ".cp-tc-price{font-size:12px;color:rgba(226,232,240,.8);margin-top:2px;}"
      + ".cp-tc-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:10px;}"
      + ".cp-tc-total{font-size:13px;font-weight:750;}"
      + ".cp-tc-cta{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:9px 10px;border-radius:12px;text-decoration:none;border:1px solid rgba(34,197,94,.35);background:rgba(34,197,94,.14);color:#bbf7d0;font-size:12px;font-weight:700;}"
      + ".cp-tc-cta:hover{background:rgba(34,197,94,.2);}"
      + ".cp-tc-disclosure{margin-top:8px;font-size:11px;color:rgba(226,232,240,.6);line-height:1.35;}"
      + ".cp-tc-skel{opacity:.85;}"
      + "@media (max-width: 420px){.cp-tc-floating{right:10px;bottom:10px;width:calc(100vw - 20px);} .cp-tc-img{width:46px;height:46px;}}";

    var style = document.createElement("style");
    style.id = "cp-trader-combo-style";
    style.type = "text/css";
    style.appendChild(document.createTextNode(css));
    document.head.appendChild(style);
  }

  function skeletonHtml(showClose) {
    var closeBtn = showClose ? '<button class="cp-tc-btn" type="button" data-cp-close="1">닫기</button>' : "";
    return ""
      + '<div class="cp-tc-card cp-tc-skel">'
      + '  <div class="cp-tc-header">'
      + '    <span class="cp-tc-badge">광고(식품)</span>'
      + "    <div>"
      + '      <p class="cp-tc-title">트레이더 식품 3종 세트</p>'
      + '      <p class="cp-tc-tagline">추천 상품을 불러오는 중…</p>'
      + "    </div>"
      + '    <div class="cp-tc-actions">'
      + closeBtn
      + "    </div>"
      + "  </div>"
      + '  <div class="cp-tc-body">'
      + '    <ul class="cp-tc-items">'
      + '      <li class="cp-tc-item"><span class="cp-tc-img"></span><div><p class="cp-tc-name">로딩…</p><div class="cp-tc-price">-</div></div></li>'
      + '      <li class="cp-tc-item"><span class="cp-tc-img"></span><div><p class="cp-tc-name">로딩…</p><div class="cp-tc-price">-</div></div></li>'
      + '      <li class="cp-tc-item"><span class="cp-tc-img"></span><div><p class="cp-tc-name">로딩…</p><div class="cp-tc-price">-</div></div></li>'
      + "    </ul>"
      + '    <div class="cp-tc-disclosure">' + esc(DISCLOSURE_TEXT) + "</div>"
      + "  </div>"
      + "</div>";
  }

  function renderError(slot, message, showClose) {
    var closeBtn = showClose ? '<button class="cp-tc-btn" type="button" data-cp-close="1">닫기</button>' : "";
    slot.innerHTML = ""
      + '<div class="cp-tc-card">'
      + '  <div class="cp-tc-header">'
      + '    <span class="cp-tc-badge">광고(식품)</span>'
      + "    <div>"
      + '      <p class="cp-tc-title">트레이더 식품 3종 세트</p>'
      + '      <p class="cp-tc-tagline">' + esc(message || "추천 상품을 불러오지 못했습니다.") + "</p>"
      + "    </div>"
      + '    <div class="cp-tc-actions">'
      + closeBtn
      + "    </div>"
      + "  </div>"
      + '  <div class="cp-tc-body">'
      + '    <div class="cp-tc-disclosure">' + esc(DISCLOSURE_TEXT) + "</div>"
      + "  </div>"
      + "</div>";
  }

  function normalizeApiPayload(payload) {
    var p = payload || {};
    var src = Array.isArray(p.items) ? p.items : [];
    var out = [];

    for (var i = 0; i < src.length; i++) {
      var it = src[i] || {};
      var name = it.title || it.keyword || it.food_name || "";
      var link = safeHref(it.link || it.url || "");
      if (!name || link === "#") continue;
      var priceValue = toNumber(it.price_krw || it.price_value);

      out.push({
        title: String(name),
        link: link,
        image: it.image ? String(it.image) : "",
        price: String(it.price || formatWon(priceValue) || "가격 확인"),
        price_value: isFinite(priceValue) ? priceValue : NaN,
      });
    }

    if (!out.length) {
      return { ok: false, message: p.message || "추천 상품이 없습니다.", items: [] };
    }

    return {
      ok: p.ok !== false,
      theme: p.theme || null,
      items: out.slice(0, 3),
      total_price: p.total_price || "",
      disclosure: p.disclosure || DISCLOSURE_TEXT,
      message: p.message || "",
    };
  }

  function normalizeLocalDataset(json) {
    var src = [];
    if (json && Array.isArray(json.items)) src = json.items;
    else if (Array.isArray(json)) src = json;

    var out = [];
    for (var i = 0; i < src.length; i++) {
      var it = src[i] || {};
      var name = it.name || it.title || it.keyword || it.food_name || "";
      var link = safeHref(it.link || it.url || "");
      if (!name || link === "#") continue;
      var priceValue = toNumber(it.price_value || it.price_krw);
      out.push({
        title: String(name),
        link: link,
        image: it.image ? String(it.image) : "",
        price: String(it.price || formatWon(priceValue) || "가격 확인"),
        price_value: isFinite(priceValue) ? priceValue : NaN,
      });
    }
    return out;
  }

  function makePayloadFromLocal(slot, items) {
    var theme = resolveTheme(slot);
    var picked = items.slice(0, 3);
    if (!picked.length) {
      return { ok: false, message: "추천 상품이 없습니다.", items: [] };
    }

    var total = 0;
    for (var i = 0; i < picked.length; i++) {
      if (isFinite(picked[i].price_value)) total += picked[i].price_value;
    }

    return {
      ok: true,
      theme: theme,
      items: picked,
      total_price: total > 0 ? formatWon(total) : "",
      disclosure: DISCLOSURE_TEXT,
      message: "",
    };
  }

  function fetchLocalItems(slot, forceReload) {
    if (!forceReload && localItemsCache && localItemsCache.length && (nowMs() - localItemsCacheAt < PAYLOAD_CACHE_TTL_MS)) {
      return Promise.resolve(localItemsCache.slice());
    }

    var dataUrl = resolveProductsUrl(slot);
    var bust = "_t=" + String(Math.floor(nowMs() / 60000));
    var url = dataUrl + (dataUrl.indexOf("?") >= 0 ? "&" : "?") + bust;

    return fetch(url, { cache: "no-store", credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("상품 데이터 로드 실패 (HTTP " + res.status + ")");
        return res.json();
      })
      .then(function (json) {
        var items = normalizeLocalDataset(json);
        if (!items.length) throw new Error("상품 데이터가 비어 있습니다.");
        localItemsCache = items.slice();
        localItemsCacheAt = nowMs();
        return items;
      });
  }

  function fetchCombo(slot, forceTheme, bypassCache) {
    var endpoint = resolveEndpoint(slot);
    var theme = forceTheme || slot.getAttribute("data-theme") || "auto";
    var subId = slot.getAttribute("data-subid") || slot.getAttribute("data-subId") || slot.getAttribute("data-project") || "";
    var category = resolveCategory(slot);

    if (!bypassCache) {
      var cached = readPayloadCache(slot, theme, subId, category);
      if (cached && cached.ok && cached.items && cached.items.length) {
        return Promise.resolve(cached);
      }
    }

    var url = buildUrl(endpoint, {
      theme: theme,
      subId: subId,
      category: category,
      _t: String(Math.floor(nowMs() / 60000)),
    });

    return fetch(url, { cache: "no-store", credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (json) {
        var normalized = normalizeApiPayload(json);
        if (normalized.ok && normalized.items && normalized.items.length) {
          writePayloadCache(slot, theme, subId, category, normalized);
        }
        return normalized;
      });
  }

  function loadPayload(slot, forceTheme, bypassCache) {
    return fetchCombo(slot, forceTheme, bypassCache)
      .then(function (payload) {
        if (payload && payload.ok && payload.items && payload.items.length) {
          return payload;
        }
        throw new Error((payload && payload.message) || "추천 상품이 없습니다.");
      })
      .catch(function () {
        return fetchLocalItems(slot, !bypassCache).then(function (items) {
          return makePayloadFromLocal(slot, items);
        });
      });
  }

  function renderPayload(slot, payload, showClose) {
    var theme = (payload && payload.theme) || resolveTheme(slot);
    var items = (payload && payload.items) || [];

    if (!payload || payload.ok !== true || !items.length) {
      renderError(slot, (payload && payload.message) || "추천 상품이 없습니다.", showClose);
      return;
    }

    var totalText = payload.total_price || "";
    var disclosure = payload.disclosure || DISCLOSURE_TEXT;
    var ctaLink = safeHref((items[0] && items[0].link) || "");
    var ctaText = theme.cta || "쿠팡에서 보기";

    var closeBtn = showClose ? '<button class="cp-tc-btn" type="button" data-cp-close="1">닫기</button>' : "";

    var listHtml = "";
    for (var i = 0; i < items.length && i < 3; i++) {
      var it = items[i] || {};
      var link = safeHref(it.link);
      var img = safeImage(it.image);
      var name = it.title || "";
      var price = it.price || "";

      listHtml += ""
        + "<li>"
        + '  <a class="cp-tc-item" href="' + esc(link) + '" target="_blank" rel="nofollow noopener noreferrer">'
        + "    " + (img ? '<img class="cp-tc-img" src="' + esc(img) + '" alt="' + esc(name) + '" loading="lazy" />' : '<span class="cp-tc-img"></span>')
        + "    <div>"
        + '      <p class="cp-tc-name">' + esc(name) + "</p>"
        + '      <div class="cp-tc-price">' + esc(price) + "</div>"
        + "    </div>"
        + "  </a>"
        + "</li>";
    }

    slot.innerHTML = ""
      + '<div class="cp-tc-card">'
      + '  <div class="cp-tc-header">'
      + '    <span class="cp-tc-badge">광고(식품)</span>'
      + "    <div>"
      + '      <p class="cp-tc-title">' + esc(theme.title || "트레이더 추천 식품 3종 세트") + "</p>"
      + '      <p class="cp-tc-tagline">' + esc(theme.tagline || "") + "</p>"
      + "    </div>"
      + '    <div class="cp-tc-actions">'
      + '      <button class="cp-tc-btn" type="button" data-cp-refresh="1">새 조합</button>'
      + closeBtn
      + "    </div>"
      + "  </div>"
      + '  <div class="cp-tc-body">'
      + '    <ul class="cp-tc-items">' + listHtml + "</ul>"
      + '    <div class="cp-tc-footer">'
      + '      <div class="cp-tc-total">합계: ' + esc(totalText || "쿠팡에서 확인") + "</div>"
      + "      " + (ctaLink !== "#" ? ('<a class="cp-tc-cta" href="' + esc(ctaLink) + '" target="_blank" rel="nofollow noopener noreferrer">' + esc(ctaText) + " ↗</a>") : "")
      + "    </div>"
      + '    <div class="cp-tc-disclosure">' + esc(disclosure) + "</div>"
      + "  </div>"
      + "</div>";
  }

  function wireEvents(slot, isFloating) {
    slot.addEventListener("click", function (ev) {
      var t = ev.target;
      if (!t) return;

      if (t.getAttribute && t.getAttribute("data-cp-close") === "1") {
        ev.preventDefault();
        if (isFloating) {
          setHide(DEFAULT_HIDE_HOURS);
          slot.classList.add("cp-hidden");
        }
        return;
      }

      if (t.getAttribute && t.getAttribute("data-cp-refresh") === "1") {
        ev.preventDefault();

        if (nowMs() - lastRefreshAt < REFRESH_COOLDOWN_MS) {
          return;
        }
        lastRefreshAt = nowMs();

        slot.innerHTML = skeletonHtml(isFloating);
        loadPayload(slot, "auto", true)
          .then(function (payload) {
            renderPayload(slot, payload, isFloating);
          })
          .catch(function (err) {
            renderError(slot, String(err && err.message ? err.message : err), isFloating);
          });
        return;
      }
    });
  }

  var slot = ensureSlot();
  injectStylesOnce();

  var variant = (slot.getAttribute("data-variant") || "floating").toLowerCase();
  var isFloating = variant === "floating";
  if (isFloating) slot.classList.add("cp-tc-floating");

  if (isFloating && getHideUntil() > nowMs()) {
    return;
  }

  slot.innerHTML = skeletonHtml(isFloating);
  wireEvents(slot, isFloating);

  loadPayload(slot, null, false)
    .then(function (payload) {
      renderPayload(slot, payload, isFloating);
    })
    .catch(function (err) {
      renderError(slot, String(err && err.message ? err.message : err), isFloating);
    });
})();
