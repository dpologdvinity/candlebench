"use strict";

// No framework and no build step, matching the project's dependency posture.

const $ = (id) => document.getElementById(id);
const POOLED = "all";

const state = {
  meta: null,
  results: null,
  interval: null,
  sort: { key: "ci_low", desc: true },
  polling: null,
};

const COLUMNS = [
  { key: "rank", label: "#", fmt: (s, i) => String(i + 1) },
  { key: "pattern", label: "pattern", fmt: (s) => s.pattern + (s.kind === "control" ? ' <span class="star">★</span>' : "") },
  { key: "trades", label: "trades", fmt: (s) => num(s.trades, 0) },
  { key: "win_rate", label: "win%", fmt: (s) => pct(s.win_rate) },
  { key: "expectancy_r", label: "exp R", fmt: (s) => signed(s.expectancy_r) },
  { key: "ci_low", label: "95% ci", fmt: (s) => ci(s) },
  { key: "baseline_delta_r", label: "vs ctrl", fmt: (s) => signed(s.baseline_delta_r) },
  { key: "profit_factor", label: "pf", fmt: (s) => num(s.profit_factor, 2) },
  { key: "consistency", label: "consist", fmt: (s) => pct(s.consistency) },
  { key: "max_drawdown_r", label: "maxdd r", fmt: (s) => num(s.max_drawdown_r, 2) },
  { key: "signals", label: "signals", fmt: (s) => num(s.signals, 0) },
  { key: "verdict", label: "verdict", fmt: (s) => `<span class="verdict ${s.verdict}">${s.verdict}</span>` },
];

// ---------- formatting ----------

function num(v, places) {
  if (v === null || v === undefined) return '<span class="na">n/a</span>';
  if (v === Infinity || v === "Infinity" || v > 1e12) return "inf";
  return Number(v).toFixed(places);
}

function signed(v) {
  if (v === null || v === undefined) return '<span class="na">n/a</span>';
  const cls = v > 0 ? "pos" : v < 0 ? "neg" : "";
  return `<span class="${cls}">${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}</span>`;
}

function pct(v) {
  if (v === null || v === undefined) return '<span class="na">n/a</span>';
  return (v * 100).toFixed(1);
}

function ci(s) {
  if (s.ci_low === null || s.ci_high === null) return '<span class="na">n/a</span>';
  const crosses = s.ci_low < 0 && s.ci_high > 0;
  return `<span class="${crosses ? "na" : ""}">[${s.ci_low >= 0 ? "+" : ""}${s.ci_low.toFixed(2)},${s.ci_high >= 0 ? "+" : ""}${s.ci_high.toFixed(2)}]</span>`;
}

function banner(text, kind) {
  const el = $("banner");
  if (!text) { el.classList.add("hidden"); return; }
  el.textContent = text;
  el.className = `banner ${kind || "info"}`;
}

// ---------- loading ----------

async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${path} returned ${res.status}`);
  return body;
}

async function boot() {
  try {
    state.meta = await api("/api/meta");
  } catch (err) {
    banner(`Could not reach the server: ${err.message}`, "error");
    return;
  }
  buildControls();
  await refreshCache();
  const results = await api("/api/results");
  if (results && results.stats) {
    state.results = results;
    render();
  }
  pollStatus();
}

function buildControls() {
  const c = state.meta.config;
  $("trials").value = c.run.trials;
  $("seed").value = c.run.seed;
  $("reward").value = c.trade.reward_multiple;
  $("stop-buffer").value = c.trade.stop_buffer;
  $("max-hold").value = c.trade.max_hold_bars;
  $("slippage").value = c.costs.slippage_bps;
  $("sample-size").value = c.universe.sample_size;

  $("rank-by").innerHTML = state.meta.rank_keys
    .map((k) => `<option value="${k}"${k === c.stats.rank_by ? " selected" : ""}>${k}</option>`)
    .join("");

  const enabled = new Set(c.run.intervals);
  $("intervals").innerHTML = state.meta.intervals
    .map((iv) => {
      const days = state.meta.lookback_days[iv];
      return `<button class="chip" data-interval="${iv}" aria-pressed="${enabled.has(iv)}" title="${days} days of history available">${iv}</button>`;
    })
    .join("");

  const on = new Set(c.patterns);
  $("patterns").innerHTML = state.meta.patterns
    .map((p) => {
      const trend = p.requires_trend === -1 ? "after a downtrend" : p.requires_trend === 1 ? "after an uptrend" : "any trend";
      const tip = `${p.bias}, ${p.bars_required}-bar, ${trend}${p.kind === "control" ? " — random-entry control" : ""}`;
      return `<button class="chip ${p.kind}" data-pattern="${p.name}" aria-pressed="${on.has(p.name)}" title="${tip}">${p.name}</button>`;
    })
    .join("");

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      chip.setAttribute("aria-pressed", chip.getAttribute("aria-pressed") !== "true");
      if (chip.dataset.interval) refreshCache();
    });
  });

  $("toggle-all").addEventListener("click", (e) => {
    e.preventDefault();
    const chips = [...document.querySelectorAll("[data-pattern]")];
    const turnOn = chips.some((c) => c.getAttribute("aria-pressed") !== "true");
    chips.forEach((c) => c.setAttribute("aria-pressed", turnOn));
  });

  $("run").addEventListener("click", () => start("/api/run"));
  $("fetch").addEventListener("click", () => start("/api/fetch"));
}

function picked(attr) {
  return [...document.querySelectorAll(`[data-${attr}][aria-pressed="true"]`)]
    .map((el) => el.dataset[attr]);
}

function requestBody() {
  return {
    run: {
      trials: Number($("trials").value),
      seed: Number($("seed").value),
      intervals: picked("interval"),
    },
    universe: { sample_size: Number($("sample-size").value) },
    trade: {
      reward_multiple: Number($("reward").value),
      stop_buffer: Number($("stop-buffer").value),
      max_hold_bars: Number($("max-hold").value),
    },
    costs: { slippage_bps: Number($("slippage").value) },
    stats: { rank_by: $("rank-by").value },
    patterns: picked("pattern"),
  };
}

async function refreshCache() {
  const chosen = picked("interval");
  try {
    const cache = await api("/api/cache");
    const missing = chosen.filter((iv) => cache.intervals[iv].sessions === 0);
    const counts = chosen
      .map((iv) => `${iv} ${cache.intervals[iv].sessions} sessions`)
      .join(", ");
    $("cache-hint").innerHTML = missing.length
      ? `No cached bars for <b>${missing.join(", ")}</b>. Warm the cache before running, or the run will have nothing to measure.`
      : `Cached: ${counts || "nothing selected"}.`;
  } catch (err) {
    $("cache-hint").textContent = `Could not read the cache: ${err.message}`;
  }
}

async function start(path) {
  banner("");
  try {
    await api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody()),
    });
  } catch (err) {
    banner(err.message, "error");
    return;
  }
  $("run").disabled = $("fetch").disabled = true;
  $("progress").classList.remove("hidden");
  pollStatus();
}

function pollStatus() {
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    let s;
    try {
      s = await api("/api/status");
    } catch {
      return;
    }
    const working = s.status === "working";
    $("run").disabled = $("fetch").disabled = working;
    $("progress").classList.toggle("hidden", s.status === "idle");
    $("bar-fill").style.width = `${s.percent}%`;
    $("progress-text").textContent = working
      ? `${s.kind}: ${s.message} (${s.percent}%)`
      : s.message;

    if (working) return;
    clearInterval(state.polling);

    if (s.status === "error") {
      banner(s.error, "error");
      return;
    }
    if (s.status === "done") {
      if (s.kind === "fetch") {
        banner(s.message, "info");
        refreshCache();
        return;
      }
      state.results = await api("/api/results");
      render();
    }
  }, 700);
}

// ---------- rendering ----------

function intervalsPresent() {
  const order = [...state.meta.intervals, POOLED];
  const seen = new Set(state.results.stats.map((s) => s.interval));
  return order.filter((iv) => seen.has(iv));
}

function render() {
  const r = state.results;
  if (!r || !r.stats || !r.stats.length) return;

  const present = intervalsPresent();
  if (!present.includes(state.interval)) state.interval = present[present.length - 1];
  state.sort.key = r.config.stats.rank_by;

  renderSummary();
  $("tabs").innerHTML = present
    .map((iv) => `<button class="tab" data-tab="${iv}" aria-selected="${iv === state.interval}">${iv === POOLED ? "pooled" : iv}</button>`)
    .join("");
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.interval = tab.dataset.tab;
      render();
    });
  });

  renderTable();
  renderCharts();
  ["summary", "board", "charts"].forEach((id) => $(id).classList.remove("hidden"));
}

function renderSummary() {
  const r = state.results;
  const c = r.config;
  const rows = r.stats.filter((s) => s.kind !== "control");
  const edges = rows.filter((s) => s.verdict === "EDGE");
  const beating = rows.filter((s) => s.baseline_delta_r > 0 && s.verdict !== "INSUFFICIENT");

  $("summary-body").innerHTML = `
    <div class="stat-grid">
      <div class="stat"><b>${r.trials.length}</b><span>trials</span></div>
      <div class="stat"><b>${r.sessions_evaluated}</b><span>sessions</span></div>
      <div class="stat"><b>${c.universe.sample_size}</b><span>symbols</span></div>
      <div class="stat"><b class="${edges.length ? "pos" : ""}">${edges.length}</b><span>with an edge</span></div>
      <div class="stat"><b>${beating.length}</b><span>beating control</span></div>
      <div class="stat"><b>${c.run.seed}</b><span>seed</span></div>
    </div>
    <p class="hint">${edges.length
      ? `<b>${edges.map((s) => `${s.pattern} (${s.interval})`).join(", ")}</b> cleared both tests: an interval excluding zero and a positive edge over the random-entry control.`
      : `No pattern cleared both tests. ${beating.length} beat the random-entry control on signal alone, which is a real but unprofitable edge once costs are charged.`}</p>
    ${r.warnings.map((w) => `<p class="hint">⚠ ${w}</p>`).join("")}`;

  $("caveats").innerHTML = `Stop at the pattern extreme &minus;${(c.trade.stop_buffer * 100).toFixed(3)}%,
    target ${c.trade.reward_multiple}R, max hold ${c.trade.max_hold_bars} bars,
    net of ${c.costs.slippage_bps} bps slippage. A bar touching both stop and target counts as a stop;
    gaps fill at the open. History spans at most 28 days at 1m and 59 at coarser intervals, so this is
    one market regime, not several.`;
}

function sorted(rows) {
  const { key, desc } = state.sort;
  const measurable = (s) => s.verdict !== "INSUFFICIENT" && s[key] !== null && s[key] !== undefined;
  return [...rows].sort((a, b) => {
    // Unmeasurable rows sort last whichever direction is chosen: a pattern
    // that produced nothing is not "the best" simply because it has no number.
    if (measurable(a) !== measurable(b)) return measurable(a) ? -1 : 1;
    if (key === "pattern" || key === "verdict") {
      return desc ? b[key].localeCompare(a[key]) : a[key].localeCompare(b[key]);
    }
    const av = a[key] ?? 0, bv = b[key] ?? 0;
    return desc ? bv - av : av - bv;
  });
}

function renderTable() {
  const rows = state.results.stats.filter((s) => s.interval === state.interval);
  const dominated = (state.results.cost_dominated || []).includes(state.interval);
  const note = $("interval-note");
  note.classList.toggle("hidden", !dominated);
  if (dominated) {
    note.innerHTML = "Random entry itself loses at this timeframe, so trading costs exceed any edge a pattern could have. Every row reads NEGATIVE. Read the <b>vs ctrl</b> column instead: a positive value is real signal that the costs ate.";
  }

  $("table").tHead.innerHTML = `<tr>${COLUMNS.map((col) => {
    const active = col.key === state.sort.key;
    return `<th data-key="${col.key}" title="sort by ${col.label}">${col.label}${active ? (state.sort.desc ? " ▾" : " ▴") : ""}</th>`;
  }).join("")}</tr>`;

  $("table").tBodies[0].innerHTML = sorted(rows)
    .map((s, i) => `<tr class="${s.kind}">${COLUMNS.map((col) => `<td>${col.fmt(s, i)}</td>`).join("")}</tr>`)
    .join("");

  document.querySelectorAll("th[data-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (key === "rank") return;
      state.sort = { key, desc: state.sort.key === key ? !state.sort.desc : true };
      renderTable();
    });
  });
}

// ---------- charts, hand-rolled SVG ----------

function svg(width, height, inner) {
  return `<svg viewBox="0 0 ${width} ${height}" role="img">${inner}</svg>`;
}

function renderCharts() {
  const rows = state.results.stats
    .filter((s) => s.interval === state.interval && s.ci_low !== null)
    .sort((a, b) => (b.expectancy_r ?? 0) - (a.expectancy_r ?? 0));

  $("chart-ci").innerHTML = rows.length ? ciChart(rows) : '<p class="hint">No measurable rows at this timeframe.</p>';

  const deltas = rows.filter((s) => s.kind !== "control" && s.baseline_delta_r !== null);
  $("chart-delta").innerHTML = deltas.length ? deltaChart(deltas) : '<p class="hint">No control to compare against.</p>';
}

function scale(values, width, pad) {
  const lo = Math.min(0, ...values), hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  return { x: (v) => pad + ((v - lo) / span) * (width - pad - 12), lo, hi };
}

function ciChart(rows) {
  const rowH = 18, labelW = 132, width = 520;
  const height = rows.length * rowH + 22;
  const s = scale(rows.flatMap((r) => [r.ci_low, r.ci_high]), width, labelW);
  const zero = s.x(0);

  const bars = rows.map((r, i) => {
    const y = i * rowH + 12;
    const colour = r.kind === "control" ? "var(--muted)"
      : r.ci_low > 0 ? "var(--edge)" : r.ci_high < 0 ? "var(--negative)" : "var(--noise)";
    const name = r.pattern + (r.kind === "control" ? " ★" : "");
    return `
      <text x="${labelW - 6}" y="${y + 4}" text-anchor="end">${name}</text>
      <line x1="${s.x(r.ci_low)}" y1="${y}" x2="${s.x(r.ci_high)}" y2="${y}" stroke="${colour}" stroke-width="1.5" opacity="0.55"/>
      <line x1="${s.x(r.ci_low)}" y1="${y - 3}" x2="${s.x(r.ci_low)}" y2="${y + 3}" stroke="${colour}" stroke-width="1.5"/>
      <line x1="${s.x(r.ci_high)}" y1="${y - 3}" x2="${s.x(r.ci_high)}" y2="${y + 3}" stroke="${colour}" stroke-width="1.5"/>
      <circle cx="${s.x(r.expectancy_r)}" cy="${y}" r="2.6" fill="${colour}"/>`;
  }).join("");

  return svg(width, height, `
    <line x1="${zero}" y1="4" x2="${zero}" y2="${height - 14}" stroke="var(--line)" stroke-dasharray="2 3"/>
    <text x="${zero}" y="${height - 3}" text-anchor="middle">0</text>
    <text x="${labelW}" y="${height - 3}">${s.lo.toFixed(2)}R</text>
    <text x="${width - 12}" y="${height - 3}" text-anchor="end">${s.hi.toFixed(2)}R</text>
    ${bars}`);
}

function deltaChart(rows) {
  const ordered = [...rows].sort((a, b) => b.baseline_delta_r - a.baseline_delta_r);
  const rowH = 18, labelW = 132, width = 520;
  const height = ordered.length * rowH + 22;
  const s = scale(ordered.map((r) => r.baseline_delta_r), width, labelW);
  const zero = s.x(0);

  const bars = ordered.map((r, i) => {
    const y = i * rowH + 12;
    const v = s.x(r.baseline_delta_r);
    const colour = r.baseline_delta_r > 0 ? "var(--edge)" : "var(--negative)";
    return `
      <text x="${labelW - 6}" y="${y + 4}" text-anchor="end">${r.pattern}</text>
      <rect x="${Math.min(zero, v)}" y="${y - 5}" width="${Math.abs(v - zero)}" height="10" fill="${colour}" opacity="0.75" rx="1"/>
      <text x="${v + (r.baseline_delta_r > 0 ? 4 : -4)}" y="${y + 4}" text-anchor="${r.baseline_delta_r > 0 ? "start" : "end"}">${r.baseline_delta_r >= 0 ? "+" : ""}${r.baseline_delta_r.toFixed(2)}</text>`;
  }).join("");

  return svg(width, height, `
    <line x1="${zero}" y1="4" x2="${zero}" y2="${height - 14}" stroke="var(--line)" stroke-dasharray="2 3"/>
    <text x="${zero}" y="${height - 3}" text-anchor="middle">0</text>
    ${bars}`);
}

boot();
