/* global echarts, Papa */

const STATE_META = {
  WARMUP: { color: "#3498db", dotClass: "warmup" },
  NORMAL: { color: "#2ecc71", dotClass: "normal" },
  DEFCON2: { color: "#e67e22", dotClass: "defcon2" },
  DEFCON1: { color: "#e74c3c", dotClass: "defcon1" },
};

const DEFAULT_PATHS = {
  nasdaq1d: "../data/nasdaq_dly_ixic_1d.csv",
  states: "../data/market_states_daily.csv",
  portfolio: "../data/portfolio_daily.csv",
};

const PAGE_SIZE = 200;

function toUtcMs(dateStr) {
  // Keep everything pinned to UTC midnight to avoid timezone drift.
  return Date.parse(`${dateStr}T00:00:00Z`);
}

function msToDate(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

function nearestNasdaqDate(state, ms) {
  const arr = state.nasdaqPoints || [];
  if (!arr.length || !Number.isFinite(ms)) return null;

  let lo = 0;
  let hi = arr.length - 1;
  if (ms <= arr[0].ms) return arr[0].date;
  if (ms >= arr[hi].ms) return arr[hi].date;

  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const v = arr[mid].ms;
    if (v === ms) return arr[mid].date;
    if (v < ms) lo = mid + 1;
    else hi = mid - 1;
  }

  // lo is the first index with ms < arr[lo].ms; hi is lo-1.
  const a = arr[Math.max(0, Math.min(arr.length - 1, hi))];
  const b = arr[Math.max(0, Math.min(arr.length - 1, lo))];
  return Math.abs(a.ms - ms) <= Math.abs(b.ms - ms) ? a.date : b.date;
}

function nearestStateDate(state, ms) {
  const arr = state.statePoints || [];
  if (!arr.length || !Number.isFinite(ms)) return null;

  let lo = 0;
  let hi = arr.length - 1;
  if (ms <= arr[0].ms) return arr[0].date;
  if (ms >= arr[hi].ms) return arr[hi].date;

  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const v = arr[mid].ms;
    if (v === ms) return arr[mid].date;
    if (v < ms) lo = mid + 1;
    else hi = mid - 1;
  }

  const a = arr[Math.max(0, Math.min(arr.length - 1, hi))];
  const b = arr[Math.max(0, Math.min(arr.length - 1, lo))];
  return Math.abs(a.ms - ms) <= Math.abs(b.ms - ms) ? a.date : b.date;
}

function addDays(dateStr, days) {
  const ms = toUtcMs(dateStr) + days * 86400000;
  return msToDate(ms);
}

function fmtNum(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function pct(a, b) {
  if (!Number.isFinite(a) || !Number.isFinite(b) || a === 0) return null;
  return ((b - a) / a) * 100.0;
}

function getSelectedStates() {
  const set = new Set();
  document.querySelectorAll('input[type="checkbox"][data-state]').forEach((el) => {
    if (el.checked) set.add(el.getAttribute("data-state"));
  });
  return set;
}

function parseCsv(text) {
  const parsed = Papa.parse(text, {
    header: true,
    skipEmptyLines: true,
  });
  if (parsed.errors && parsed.errors.length) {
    // Bubble the first parse issue; the UI will show a friendly hint.
    throw new Error(`CSV parse error: ${parsed.errors[0].message || "unknown error"}`);
  }
  return parsed.data || [];
}

async function fetchText(url) {
  // Cache busting: append timestamp to force fresh fetch
  const cacheBuster = (url.includes("?") ? "&" : "?") + "t=" + Date.now();
  const resp = await fetch(url + cacheBuster, { cache: "no-store" });
  if (!resp.ok) throw new Error(`fetch failed (${resp.status}) ${url}`);
  return await resp.text();
}

async function fetchTextOptional(url) {
  try {
    return await fetchText(url);
  } catch {
    return null;
  }
}

function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(new Error("failed to read file"));
    r.onload = () => resolve(String(r.result || ""));
    r.readAsText(file);
  });
}

function buildStateSegments(stateRows, startDate, endDate, enabledStates) {
  const out = [];
  const rows = stateRows.filter((r) => r.date >= startDate && r.date <= endDate);
  if (!rows.length) return out;

  let curState = rows[0].state;
  let segStart = rows[0].date;
  for (let i = 1; i < rows.length; i += 1) {
    const r = rows[i];
    if (r.state !== curState) {
      out.push({ state: curState, start: segStart, endExclusive: r.date });
      curState = r.state;
      segStart = r.date;
    }
  }
  out.push({ state: curState, start: segStart, endExclusive: addDays(rows[rows.length - 1].date, 1) });

  // Convert to ECharts markArea format; skip unchecked states.
  const markAreaData = [];
  for (const seg of out) {
    if (!enabledStates.has(seg.state)) continue;
    const meta = STATE_META[seg.state] || { color: "#999" };
    markAreaData.push([
      { xAxis: toUtcMs(seg.start), itemStyle: { color: meta.color, opacity: 0.12 } },
      { xAxis: toUtcMs(seg.endExclusive) },
    ]);
  }
  return markAreaData;
}

function buildStateCounts(stateRows, startDate, endDate, enabledStates, onlyTradingDays, closeByDate) {
  const counts = { WARMUP: 0, NORMAL: 0, DEFCON2: 0, DEFCON1: 0 };
  for (const r of stateRows) {
    if (r.date < startDate || r.date > endDate) continue;
    if (!enabledStates.has(r.state)) continue;
    if (onlyTradingDays && !closeByDate.has(r.date)) continue;
    if (counts[r.state] === undefined) counts[r.state] = 0;
    counts[r.state] += 1;
  }
  return counts;
}

function makeStatePill(state) {
  const meta = STATE_META[state] || { color: "#999", dotClass: "" };
  const pill = document.createElement("span");
  pill.className = "pill";
  const swatch = document.createElement("span");
  swatch.className = "swatch";
  swatch.style.background = meta.color;
  pill.appendChild(swatch);
  pill.appendChild(document.createTextNode(state));
  return pill;
}

function clampRange(minDate, maxDate, startDate, endDate) {
  const s = startDate < minDate ? minDate : startDate;
  const e = endDate > maxDate ? maxDate : endDate;
  if (e < s) return { startDate: s, endDate: s };
  return { startDate: s, endDate: e };
}

function attachRangeButtons(state) {
  document.querySelectorAll(".seg-btn[data-range]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const kind = btn.getAttribute("data-range");
      const end = state.maxDate;
      let start = state.minDate;
      if (kind === "1y") start = addDays(end, -365);
      if (kind === "5y") start = addDays(end, -365 * 5);
      if (kind === "max") start = state.minDate;
      const clamped = clampRange(state.minDate, state.maxDate, start, end);
      state.ui.start.value = clamped.startDate;
      state.ui.end.value = clamped.endDate;
      renderAll(state);
    });
  });
}

function buildMainOption(state, startDate, endDate, enabledStates) {
  const points = [];
  for (const p of state.nasdaqPoints) {
    if (p.date < startDate || p.date > endDate) continue;
    points.push([p.ms, p.close]);
  }

  const markAreaData = buildStateSegments(state.stateRows, startDate, endDate, enabledStates);

  return {
    animationDuration: 300,
    grid: { left: 52, right: 20, top: 24, bottom: 40 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line" },
      backgroundColor: "rgba(21, 19, 18, 0.92)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
      formatter: (params) => {
        const p0 = Array.isArray(params) ? params[0] : params;
        if (!p0 || !p0.value) return "";
        const ms = p0.value[0];
        const d = msToDate(ms);
        // Remember the exact date shown in the tooltip so clicks can jump to the same day.
        state._lastTooltipMs = ms;
        state._lastTooltipDate = d;
        const close = p0.value[1];
        const st = state.stateByDate.get(d);
        const score = state.scoreByDate.get(d);
        const trend = state.trendByDate ? state.trendByDate.get(d) : null;
        const equity = state.equityByDate ? state.equityByDate.get(d) : null;
        const action = state.actionByDate ? state.actionByDate.get(d) : null;
        const port = state.portfolioByDate ? state.portfolioByDate.get(d) : null;
        const grossText = port && port.gross !== null && port.gross !== undefined ? `${fmtNum(port.gross, 2)}x` : "—";
        const cashText =
          port && port.cash !== null && port.cash !== undefined ? `${fmtNum(port.cash * 100.0, 1)}%` : "—";
        const portText = port && port.topText ? String(port.topText) : "—";
        const equityText = equity === null || equity === undefined ? "—" : `${fmtNum(equity * 100.0, 1)}%`;
        return [
          `<div style="font-weight:700;margin-bottom:6px;">${d}</div>`,
          `<div>NASDAQ: <b>${fmtNum(close, 2)}</b></div>`,
          `<div>State: <b>${st || "—"}</b></div>`,
          `<div>Score: <b>${score === null || score === undefined ? "—" : fmtNum(score, 3)}</b></div>`,
          `<div>Trend: <b>${trend || "—"}</b></div>`,
          `<div>Equity: <b>${equityText}</b></div>`,
          `<div>Action: <b>${action || "—"}</b></div>`,
          `<div>Gross: <b>${grossText}</b> / Cash: <b>${cashText}</b></div>`,
          `<div>Portfolio: <b>${portText}</b></div>`,
        ].join("");
      },
    },
    xAxis: {
      type: "time",
      min: toUtcMs(startDate),
      max: toUtcMs(endDate),
      axisLabel: { color: "rgba(21, 19, 18, 0.65)" },
      axisLine: { lineStyle: { color: "rgba(21, 19, 18, 0.22)" } },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "rgba(21, 19, 18, 0.65)" },
      axisLine: { lineStyle: { color: "rgba(21, 19, 18, 0.22)" } },
      splitLine: { lineStyle: { color: "rgba(21, 19, 18, 0.10)" } },
    },
    series: [
      {
        name: "NASDAQ Close",
        type: "line",
        showSymbol: false,
        symbol: "none",
        triggerLineEvent: true,
        smooth: 0.08,
        lineStyle: { width: 2.2, color: "rgba(21, 19, 18, 0.86)" },
        data: points,
        markArea: {
          silent: true,
          data: markAreaData,
        },
      },
    ],
    dataZoom: [
      { type: "inside", throttle: 32 },
      { type: "slider", height: 22, bottom: 10 },
    ],
  };
}

function buildMixOption(counts) {
  const cats = ["WARMUP", "NORMAL", "DEFCON2", "DEFCON1"];
  const data = cats.map((s) => ({
    name: s,
    value: counts[s] || 0,
    itemStyle: { color: (STATE_META[s] || { color: "#999" }).color },
  }));

  return {
    animationDuration: 300,
    grid: { left: 44, right: 18, top: 20, bottom: 36 },
    xAxis: {
      type: "category",
      data: cats,
      axisLabel: { color: "rgba(21, 19, 18, 0.68)" },
      axisLine: { lineStyle: { color: "rgba(21, 19, 18, 0.22)" } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "rgba(21, 19, 18, 0.65)" },
      splitLine: { lineStyle: { color: "rgba(21, 19, 18, 0.10)" } },
    },
    tooltip: { trigger: "item" },
    series: [
      {
        type: "bar",
        data,
        barWidth: "56%",
        borderRadius: [8, 8, 2, 2],
      },
    ],
  };
}

function renderStats(state, startDate, endDate, enabledStates) {
  const statsEl = state.ui.stats;
  statsEl.innerHTML = "";

  const firstPoint = state.nasdaqPoints.find((p) => p.date >= startDate);
  const lastPoint = [...state.nasdaqPoints].reverse().find((p) => p.date <= endDate);
  const ret = firstPoint && lastPoint ? pct(firstPoint.close, lastPoint.close) : null;

  const counts = buildStateCounts(
    state.stateRows,
    startDate,
    endDate,
    enabledStates,
    state.ui.onlyTradingDays.checked,
    state.closeByDate
  );

  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;

  const addRow = (label, value) => {
    const row = document.createElement("div");
    row.className = "row";
    const left = document.createElement("div");
    left.className = "tag";
    left.textContent = label;
    const right = document.createElement("div");
    right.textContent = value;
    row.appendChild(left);
    row.appendChild(right);
    statsEl.appendChild(row);
  };

  addRow("Date range", `${startDate} to ${endDate}`);
  addRow("NASDAQ return", ret === null ? "—" : `${fmtNum(ret, 2)}%`);

  for (const k of ["WARMUP", "NORMAL", "DEFCON2", "DEFCON1"]) {
    const meta = STATE_META[k] || { dotClass: "" };
    const row = document.createElement("div");
    row.className = "row";

    const left = document.createElement("div");
    left.className = "tag";
    const dot = document.createElement("span");
    dot.className = `dot ${meta.dotClass || ""}`;
    left.appendChild(dot);
    left.appendChild(document.createTextNode(k));

    const right = document.createElement("div");
    const c = counts[k] || 0;
    right.textContent = `${c.toLocaleString()} (${fmtNum((c / total) * 100.0, 1)}%)`;

    row.appendChild(left);
    row.appendChild(right);
    statsEl.appendChild(row);
  }

  return counts;
}

function renderTable(state, filteredRows) {
  const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const page = Math.min(totalPages - 1, Math.max(0, state.page));
  state.page = page;

  const start = page * PAGE_SIZE;
  const slice = filteredRows.slice(start, start + PAGE_SIZE);

  const tbody = state.ui.tableBody;
  tbody.innerHTML = "";
  let selectedTr = null;

  for (const r of slice) {
    const tr = document.createElement("tr");
    if (state.selectedDate && r.date === state.selectedDate) {
      tr.classList.add("selected");
      selectedTr = tr;
    }
    tr.addEventListener("click", () => {
      state.selectedDate = r.date;
      renderPortfolioPanel(state, r.date);
      renderTable(state, state.filteredRows || []);
    });

    const tdDate = document.createElement("td");
    tdDate.textContent = r.date;
    tr.appendChild(tdDate);

    const tdState = document.createElement("td");
    tdState.appendChild(makeStatePill(r.state));
    tr.appendChild(tdState);

    const tdScore = document.createElement("td");
    tdScore.className = "num";
    tdScore.textContent = r.score === null ? "—" : fmtNum(r.score, 3);
    tr.appendChild(tdScore);

    const tdTrend = document.createElement("td");
    tdTrend.textContent = r.trend ? String(r.trend) : "—";
    tr.appendChild(tdTrend);

    const tdEquity = document.createElement("td");
    tdEquity.className = "num";
    tdEquity.textContent = r.equity === null ? "—" : `${fmtNum(r.equity * 100.0, 1)}%`;
    tr.appendChild(tdEquity);

    const tdAction = document.createElement("td");
    tdAction.textContent = r.action ? String(r.action) : "—";
    tr.appendChild(tdAction);

    const tdGross = document.createElement("td");
    tdGross.className = "num";
    tdGross.textContent = r.gross === null ? "—" : `${fmtNum(r.gross, 2)}x`;
    tr.appendChild(tdGross);

    const tdCash = document.createElement("td");
    tdCash.className = "num";
    tdCash.textContent = r.cash === null ? "—" : `${fmtNum(r.cash * 100.0, 1)}%`;
    tr.appendChild(tdCash);

    const tdPort = document.createElement("td");
    tdPort.textContent = r.portfolioTop ? String(r.portfolioTop) : "—";
    if (r.portfolioTitle) tdPort.title = r.portfolioTitle;
    tr.appendChild(tdPort);

    const tdClose = document.createElement("td");
    tdClose.className = "num";
    tdClose.textContent = r.close === null ? "—" : fmtNum(r.close, 2);
    tr.appendChild(tdClose);

    const tdTrig = document.createElement("td");
    tdTrig.textContent = r.triggers || "";
    if (r.triggers) tdTrig.title = r.triggers;
    tr.appendChild(tdTrig);

    tbody.appendChild(tr);
  }

  state.ui.pageInfo.textContent = `Page ${page + 1} / ${totalPages}  (rows: ${filteredRows.length.toLocaleString()})`;
  state.ui.btnPrev.disabled = page <= 0;
  state.ui.btnNext.disabled = page >= totalPages - 1;

  if (selectedTr) {
    try {
      selectedTr.scrollIntoView({ block: "center", behavior: "smooth" });
    } catch {
      selectedTr.scrollIntoView(true);
    }
  }
}

function buildFilteredRows(state, startDate, endDate, enabledStates) {
  const q = (state.ui.search.value || "").trim().toLowerCase();
  const onlyTradingDays = state.ui.onlyTradingDays.checked;

  const out = [];
  for (const r of state.stateRows) {
    if (r.date < startDate || r.date > endDate) continue;
    if (!enabledStates.has(r.state)) continue;

    const close = state.closeByDate.get(r.date);
    if (onlyTradingDays && close === undefined) continue;

    const triggers = r.triggers || "";
    if (q && !triggers.toLowerCase().includes(q)) continue;

    const port = state.portfolioByDate ? state.portfolioByDate.get(r.date) : null;

    out.push({
      date: r.date,
      state: r.state,
      score: r.score,
      trend: r.trend || "",
      equity: r.equity === undefined ? null : r.equity,
      action: r.action || "",
      gross: port && port.gross !== undefined ? port.gross : null,
      cash: port && port.cash !== undefined ? port.cash : null,
      portfolioTop: port && port.topText ? String(port.topText) : "",
      portfolioTitle: port && port.fullText ? String(port.fullText) : "",
      triggers,
      close: close === undefined ? null : close,
    });
  }
  out.sort((a, b) => (a.date < b.date ? 1 : -1)); // desc
  return out;
}

function jumpToDate(state, dateStr) {
  if (!dateStr) return;
  if (dateStr < state.minDate || dateStr > state.maxDate) return;

  state.selectedDate = dateStr;

  const tryJump = () => {
    const idx = (state.filteredRows || []).findIndex((r) => r.date === dateStr);
    if (idx === -1) return false;
    state.page = Math.floor(idx / PAGE_SIZE);
    renderTable(state, state.filteredRows || []);
    const table = document.getElementById("dataTable");
    if (table) {
      try {
        table.scrollIntoView({ block: "start", behavior: "smooth" });
      } catch {
        table.scrollIntoView(true);
      }
    }
    return true;
  };

  // First attempt with current filters.
  renderAll(state);
  if (tryJump()) return;

  // Relax filters just enough to make the selected date visible.
  const origSearch = state.ui.search.value;
  if (origSearch && origSearch.trim() !== "") {
    state.ui.search.value = "";
    renderAll(state);
    if (tryJump()) return;
  }

  const st = state.stateByDate.get(dateStr);
  if (st) {
    const cb = document.querySelector(`input[type=\"checkbox\"][data-state=\"${st}\"]`);
    if (cb && !cb.checked) {
      cb.checked = true;
      renderAll(state);
      if (tryJump()) return;
    }
  }

  if (state.ui.onlyTradingDays.checked && !state.closeByDate.has(dateStr)) {
    state.ui.onlyTradingDays.checked = false;
    renderAll(state);
    if (tryJump()) return;
  }

  state.ui.pageInfo.textContent = `Selected ${dateStr} is filtered out by current settings.`;
}

function getPortfolioForDate(state, dateStr) {
  if (!dateStr || !state.portfolioByDate) return null;
  const direct = state.portfolioByDate.get(dateStr);
  if (direct) return direct;

  // Fallback for non-trading days: snap to the nearest NASDAQ trading day.
  const ms = toUtcMs(dateStr);
  const nearest = nearestNasdaqDate(state, ms);
  if (nearest) return state.portfolioByDate.get(nearest) || null;
  return null;
}

function renderPortfolioPanel(state, dateStr) {
  const dateEl = state.ui.portfolioDate;
  const metaEl = state.ui.portfolioMeta;
  const weightsEl = state.ui.portfolioWeights;
  if (!dateEl || !metaEl || !weightsEl) return;

  const rec = getPortfolioForDate(state, dateStr);
  const shownDate = rec ? rec.date : dateStr || "—";
  dateEl.textContent = shownDate || "—";

  if (!rec) {
    metaEl.textContent = "No portfolio data loaded for this date.";
    weightsEl.innerHTML = "";
    return;
  }

  const gross = rec.gross;
  const cash = rec.cash;
  const parts = [];
  if (gross !== null && gross !== undefined) parts.push(`Gross ${fmtNum(gross, 2)}x`);
  if (cash !== null && cash !== undefined) parts.push(`Cash ${fmtNum(cash * 100.0, 1)}%`);
  metaEl.textContent = parts.length ? parts.join(" / ") : "—";

  weightsEl.innerHTML = "";

  const weights = Array.isArray(rec.weights) ? rec.weights.slice(0, 8) : [];
  for (const w of weights) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${w.asset} ${fmtNum(w.w * 100.0, 1)}%`;
    weightsEl.appendChild(chip);
  }

  if (cash !== null && cash !== undefined) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `CASH ${fmtNum(cash * 100.0, 1)}%`;
    weightsEl.appendChild(chip);
  }
}

function renderAll(state) {
  const enabledStates = getSelectedStates();
  const clamped = clampRange(state.minDate, state.maxDate, state.ui.start.value, state.ui.end.value);
  state.ui.start.value = clamped.startDate;
  state.ui.end.value = clamped.endDate;

  const mainOpt = buildMainOption(state, clamped.startDate, clamped.endDate, enabledStates);
  state.charts.main.setOption(mainOpt, true);

  const counts = renderStats(state, clamped.startDate, clamped.endDate, enabledStates);
  state.charts.mix.setOption(buildMixOption(counts), true);

  const filtered = buildFilteredRows(state, clamped.startDate, clamped.endDate, enabledStates);
  state.filteredRows = filtered;
  renderTable(state, filtered);

  const focusDate = state.selectedDate || clamped.endDate;
  renderPortfolioPanel(state, focusDate);
}

function wireUi(state) {
  state.ui.start.addEventListener("change", () => renderAll(state));
  state.ui.end.addEventListener("change", () => renderAll(state));
  state.ui.onlyTradingDays.addEventListener("change", () => {
    state.page = 0;
    renderAll(state);
  });
  document.querySelectorAll('input[type="checkbox"][data-state]').forEach((el) => {
    el.addEventListener("change", () => {
      state.page = 0;
      renderAll(state);
    });
  });
  state.ui.search.addEventListener("input", () => {
    state.page = 0;
    renderAll(state);
  });

  state.ui.btnPrev.addEventListener("click", () => {
    state.page -= 1;
    renderTable(state, state.filteredRows || []);
  });
  state.ui.btnNext.addEventListener("click", () => {
    state.page += 1;
    renderTable(state, state.filteredRows || []);
  });
}

function pickDefaultRange(state) {
  // Default: start from the first non-WARMUP day if available, otherwise from data min.
  let start = state.minDate;
  const firstNonWarm = state.stateRows.find((r) => r.state && r.state !== "WARMUP");
  if (firstNonWarm && firstNonWarm.date) start = firstNonWarm.date;

  const clamped = clampRange(state.minDate, state.maxDate, start, state.maxDate);
  state.ui.start.value = clamped.startDate;
  state.ui.end.value = clamped.endDate;
}

function initCharts() {
  const main = echarts.init(document.getElementById("chartMain"));
  const mix = echarts.init(document.getElementById("chartMix"));
  window.addEventListener("resize", () => {
    main.resize();
    mix.resize();
  });
  return { main, mix };
}

function wireChartInteractions(state) {
  const chart = state.charts.main;
  state._lastChartJumpAt = state._lastChartJumpAt || 0;
  state._lastTooltipMs = state._lastTooltipMs ?? null;
  state._lastTooltipDate = state._lastTooltipDate ?? null;

  const isPointerCursor = (ev) => {
    try {
      // Prefer the actual DOM target, then fall back to known chart DOM roots.
      const dom = chart.getDom?.();
      const canvas = dom?.querySelector?.("canvas") || null;

      const candidates = [];
      if (ev?.event?.target) candidates.push(ev.event.target);
      if (dom) candidates.push(dom);
      if (canvas) candidates.push(canvas);

      for (const el of candidates) {
        if (!el || typeof window.getComputedStyle !== "function") continue;
        const cursor = window.getComputedStyle(el).cursor;
        if (cursor === "pointer") return true;
      }
      return false;
    } catch {
      return false;
    }
  };

  const jumpByMs = (ms, mode = "state") => {
    const now = Date.now();
    if (now - state._lastChartJumpAt < 120) return; // de-dupe zr vs echarts click
    state._lastChartJumpAt = now;

    // Default: map to the nearest daily state date (calendar-accurate).
    // For direct clicks on a NASDAQ data point, pass mode="nasdaq".
    const dateStr =
      (mode === "nasdaq" ? nearestNasdaqDate(state, ms) : msToDate(ms)) || msToDate(ms);
    jumpToDate(state, dateStr);
  };

  // Clicking the line (or other series items) will trigger this.
  chart.on("click", (params) => {
    try {
      if (!isPointerCursor(params?.event)) return;

      // If ECharts gives us a dataIndex, that's the most accurate selection possible.
      if (
        params?.componentType === "series" &&
        params?.seriesType === "line" &&
        typeof params.dataIndex === "number" &&
        params.dataIndex >= 0 &&
        state.nasdaqPoints &&
        state.nasdaqPoints[params.dataIndex]
      ) {
        jumpToDate(state, state.nasdaqPoints[params.dataIndex].date);
        return;
      }

      let v = params?.value ?? params?.data ?? null;
      let ms = null;
      if (Array.isArray(v)) ms = v[0];
      else if (v instanceof Date) ms = v.getTime();
      else if (typeof v === "number") ms = v;
      else if (typeof v === "string") ms = Date.parse(v);
      else if (typeof params?.name === "string") ms = Date.parse(params.name);
      if (!Number.isFinite(ms)) return;
      // If this came from a line series item, snap to the actual NASDAQ data point.
      const isNasdaqLine = params?.componentType === "series" && params?.seriesType === "line";
      jumpByMs(ms, isNasdaqLine ? "nasdaq" : "state");
    } catch {
      // Ignore click parsing errors; zr handler below may still work.
    }
  });

  // Click anywhere in the plot area to jump the table to the nearest trading day.
  const jumpFromAnyClick = (ev) => {
    // Only navigate when the chart is showing an interactive pointer cursor.
    if (!isPointerCursor(ev)) return;

    // If a tooltip is visible, always jump to exactly that day (user expectation).
    const tipDate = state._lastTooltipDate;
    if (typeof tipDate === "string" && tipDate.length === 10) {
      jumpToDate(state, tipDate);
      return;
    }

    // zrender event objects can vary by browser/device; normalize coordinates.
    const x =
      (typeof ev.offsetX === "number" ? ev.offsetX : null) ??
      (typeof ev.zrX === "number" ? ev.zrX : null) ??
      (typeof ev.event?.offsetX === "number" ? ev.event.offsetX : null) ??
      (typeof ev.event?.zrX === "number" ? ev.event.zrX : null);
    const y =
      (typeof ev.offsetY === "number" ? ev.offsetY : null) ??
      (typeof ev.zrY === "number" ? ev.zrY : null) ??
      (typeof ev.event?.offsetY === "number" ? ev.event.offsetY : null) ??
      (typeof ev.event?.zrY === "number" ? ev.event.zrY : null);

    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    const pt = [x, y];
    if (!chart.containPixel("grid", pt)) return;

    const coord = chart.convertFromPixel({ seriesIndex: 0 }, pt);
    let ms = Array.isArray(coord) ? coord[0] : coord;
    if (ms instanceof Date) ms = ms.getTime();
    if (typeof ms === "string") ms = Date.parse(ms);
    if (!Number.isFinite(ms)) return;

    jumpByMs(ms);
  };

  chart.getZr().on("click", jumpFromAnyClick);
  chart.getZr().on("mouseup", jumpFromAnyClick);
  chart.getZr().on("tap", jumpFromAnyClick);

  // Last-resort: DOM click (some environments don't dispatch zr click reliably).
  chart.getDom().addEventListener("click", (ev) => {
    const rect = chart.getDom().getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    jumpFromAnyClick({ offsetX: x, offsetY: y });
  });
}

function showLoadCard(show) {
  const el = document.getElementById("cardLoad");
  el.hidden = !show;
}

function setLoadStatus(msg) {
  const el = document.getElementById("loadStatus");
  el.textContent = msg || "";
}

function normalizeStateRows(rows) {
  const out = [];
  for (const r of rows) {
    const date = String(r.as_of_date || "").trim();
    const state = String(r.state || "").trim();
    if (!date || !state) continue;
    const scoreRaw = r.score;
    let score = null;
    if (scoreRaw !== undefined && scoreRaw !== null && String(scoreRaw).trim() !== "") {
      const n = Number(scoreRaw);
      score = Number.isFinite(n) ? n : null;
    }
    const triggers = r.triggers ? String(r.triggers) : "";
    const action = r.action ? String(r.action).trim() : "";

    let equity = null;
    const equityRaw = r.equity_weight ?? r.equity ?? null;
    if (equityRaw !== undefined && equityRaw !== null && String(equityRaw).trim() !== "") {
      const n = Number(equityRaw);
      equity = Number.isFinite(n) ? n : null;
    }

    const trend = r.trend_signal ? String(r.trend_signal).trim() : "";

    out.push({ date, state, score, triggers, action, equity, trend });
  }
  out.sort((a, b) => (a.date < b.date ? -1 : 1));
  return out;
}

function normalizeNasdaqRows(rows) {
  const out = [];
  for (const r of rows) {
    const date = String(r.time || "").trim();
    if (!date) continue;
    const close = Number(r.close);
    if (!Number.isFinite(close)) continue;
    out.push({ date, ms: toUtcMs(date), close });
  }
  out.sort((a, b) => a.ms - b.ms);
  return out;
}

function normalizePortfolioRows(rows) {
  const out = [];
  for (const r of rows) {
    const date = String(r.date || "").trim();
    if (!date) continue;

    const grossRaw = r.gross_exposure;
    const cashRaw = r.cash_weight;
    const gross = grossRaw === undefined || grossRaw === null || String(grossRaw).trim() === "" ? null : Number(grossRaw);
    const cash = cashRaw === undefined || cashRaw === null || String(cashRaw).trim() === "" ? null : Number(cashRaw);

    const weights = [];
    for (const k of Object.keys(r)) {
      if (!k || typeof k !== "string" || !k.startsWith("w_")) continue;
      const vRaw = r[k];
      if (vRaw === undefined || vRaw === null || String(vRaw).trim() === "") continue;
      const v = Number(vRaw);
      if (!Number.isFinite(v) || v <= 0) continue;
      weights.push({ asset: k.slice(2), w: v });
    }
    weights.sort((a, b) => b.w - a.w);

    const topText = weights
      .slice(0, 4)
      .map((x) => `${x.asset} ${fmtNum(x.w * 100.0, 1)}%`)
      .join(" | ");
    const fullText = weights
      .map((x) => `${x.asset} ${fmtNum(x.w * 100.0, 1)}%`)
      .join(" | ");

    out.push({
      date,
      gross: Number.isFinite(gross) ? gross : null,
      cash: Number.isFinite(cash) ? cash : null,
      weights,
      topText,
      fullText,
    });
  }
  out.sort((a, b) => (a.date < b.date ? -1 : 1));
  return out;
}

function buildMaps(state) {
  state.closeByDate = new Map();
  for (const p of state.nasdaqPoints) state.closeByDate.set(p.date, p.close);

  state.stateByDate = new Map();
  state.scoreByDate = new Map();
  state.actionByDate = new Map();
  state.equityByDate = new Map();
  state.trendByDate = new Map();
  for (const r of state.stateRows) {
    state.stateByDate.set(r.date, r.state);
    state.scoreByDate.set(r.date, r.score);
    state.actionByDate.set(r.date, r.action || null);
    state.equityByDate.set(r.date, r.equity === undefined ? null : r.equity);
    state.trendByDate.set(r.date, r.trend || null);
  }

  state.portfolioByDate = new Map();
  for (const r of state.portfolioRows || []) state.portfolioByDate.set(r.date, r);

  // For accurate click-to-date mapping, use the daily state calendar (not only trading days).
  state.statePoints = state.stateRows.map((r) => ({ date: r.date, ms: toUtcMs(r.date) }));

  state.minDate = state.stateRows.length ? state.stateRows[0].date : state.nasdaqPoints[0].date;
    state.maxDate = state.stateRows.length 
      ? state.stateRows[state.stateRows.length - 1].date 
      : state.nasdaqPoints[state.nasdaqPoints.length - 1].date;
      
    // Extend maxDate by 7 days to ensure recent data is always visible despite timezone/lag issues.
    if (state.maxDate) {
      state.maxDate = addDays(state.maxDate, 7);
    }
  }

function syncDateLimits(state) {
  if (!state.ui?.start || !state.ui?.end) return;
  state.ui.start.min = state.minDate;
  state.ui.start.max = state.maxDate;
  state.ui.end.min = state.minDate;
  state.ui.end.max = state.maxDate;
}

function ensureLatestVisible(state) {
  const latest = state.maxDate;
  if (!latest) return;
  if (state.ui.onlyTradingDays.checked && !state.closeByDate.has(latest)) {
    state.ui.onlyTradingDays.checked = false;
  }
}

async function loadFromDefaultPaths(state) {
  setLoadStatus("Loading CSVs...");
  const [nasdaqText, statesText, portfolioText] = await Promise.all([
    fetchText(DEFAULT_PATHS.nasdaq1d),
    fetchText(DEFAULT_PATHS.states),
    fetchTextOptional(DEFAULT_PATHS.portfolio),
  ]);

  const nasdaqRows = parseCsv(nasdaqText);
  const statesRows = parseCsv(statesText);
  const portfolioRows = portfolioText ? parseCsv(portfolioText) : [];
  state.nasdaqPoints = normalizeNasdaqRows(nasdaqRows);
  state.stateRows = normalizeStateRows(statesRows);
  state.portfolioRows = normalizePortfolioRows(portfolioRows);
  buildMaps(state);
  syncDateLimits(state);
  ensureLatestVisible(state);
}

async function loadFromSelectedFiles(state) {
  const fNasdaq = state.ui.fileNasdaq.files[0];
  const fStates = state.ui.fileStates.files[0];
  if (!fNasdaq || !fStates) throw new Error("please select both CSV files");
  const fPortfolio = state.ui.filePortfolio ? state.ui.filePortfolio.files[0] : null;

  setLoadStatus("Reading files...");
  const [nasdaqText, statesText, portfolioText] = await Promise.all([
    readFileAsText(fNasdaq),
    readFileAsText(fStates),
    fPortfolio ? readFileAsText(fPortfolio) : Promise.resolve(null),
  ]);
  const nasdaqRows = parseCsv(nasdaqText);
  const statesRows = parseCsv(statesText);
  const portfolioRows = portfolioText ? parseCsv(portfolioText) : [];
  state.nasdaqPoints = normalizeNasdaqRows(nasdaqRows);
  state.stateRows = normalizeStateRows(statesRows);
  state.portfolioRows = normalizePortfolioRows(portfolioRows);
  buildMaps(state);
  syncDateLimits(state);
  ensureLatestVisible(state);
}

function boot() {
  const state = {
    charts: initCharts(),
    page: 0,
    filteredRows: [],
    selectedDate: null,
    nasdaqPoints: [],
    stateRows: [],
    portfolioRows: [],
    closeByDate: new Map(),
    stateByDate: new Map(),
    scoreByDate: new Map(),
    portfolioByDate: new Map(),
    minDate: "1970-01-01",
    maxDate: "1970-01-01",
    ui: {
      start: document.getElementById("startDate"),
      end: document.getElementById("endDate"),
      search: document.getElementById("search"),
      tableBody: document.getElementById("tableBody"),
      pageInfo: document.getElementById("pageInfo"),
      btnPrev: document.getElementById("btnPrev"),
      btnNext: document.getElementById("btnNext"),
      onlyTradingDays: document.getElementById("onlyTradingDays"),
      stats: document.getElementById("stats"),
      fileNasdaq: document.getElementById("fileNasdaq"),
      fileStates: document.getElementById("fileStates"),
      filePortfolio: document.getElementById("filePortfolio"),
      portfolioDate: document.getElementById("portfolioDate"),
      portfolioMeta: document.getElementById("portfolioMeta"),
      portfolioWeights: document.getElementById("portfolioWeights"),
    },
  };

  wireUi(state);
  attachRangeButtons(state);
  wireChartInteractions(state);

  document.getElementById("btnReload").addEventListener("click", async () => {
    state.page = 0;
    setLoadStatus("");
    try {
      showLoadCard(false);
      await loadFromDefaultPaths(state);
      pickDefaultRange(state);
      renderAll(state);
    } catch (e) {
      showLoadCard(true);
      setLoadStatus(String(e && e.message ? e.message : e));
    }
  });

  document.getElementById("btnLoadFiles").addEventListener("click", async () => {
    state.page = 0;
    try {
      await loadFromSelectedFiles(state);
      showLoadCard(false);
      pickDefaultRange(state);
      renderAll(state);
      setLoadStatus("");
    } catch (e) {
      showLoadCard(true);
      setLoadStatus(String(e && e.message ? e.message : e));
    }
  });

  // Initial load.
  (async () => {
    try {
      await loadFromDefaultPaths(state);
      pickDefaultRange(state);
      renderAll(state);
      showLoadCard(false);
      setLoadStatus("");
    } catch (e) {
      showLoadCard(true);
      setLoadStatus(String(e && e.message ? e.message : e));
    }
  })();
}

boot();
