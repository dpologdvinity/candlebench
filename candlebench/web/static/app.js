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
  // The leaderboard row being drilled into, and where in its trades we are.
  selected: null,
  sample: "discovery",
  page: { offset: 0, limit: 100, sort: null, desc: true },
  pageTotal: 0,
  runs: [],
};

// Columns of the drill-down table. `sort` is the frame column the server sorts
// by, which is not always the column shown: direction displays as long/short.
const TRADE_COLUMNS = [
  { key: "session", label: "session", fmt: (t) => t.session },
  { key: "symbol", label: "symbol", fmt: (t) => t.symbol },
  { key: "direction", label: "side", fmt: (t) => (t.direction > 0 ? "long" : "short") },
  { key: "entry_minute", label: "from open", fmt: (t) => (t.entry_minute === null ? '<span class="na">n/a</span>' : `${t.entry_minute}m`) },
  { key: "entry_price", label: "entry", fmt: (t) => num(t.entry_price, 2) },
  { key: "exit_price", label: "exit", fmt: (t) => num(t.exit_price, 2) },
  { key: "stop_price", label: "stop", fmt: (t) => num(t.stop_price, 2) },
  { key: "target_price", label: "target", fmt: (t) => num(t.target_price, 2) },
  { key: "bars_held", label: "bars", fmt: (t) => num(t.bars_held, 0) },
  { key: "exit_reason", label: "exit", fmt: (t) => t.exit_reason },
  { key: "net_r", label: "net r", fmt: (t) => signed(t.net_r) },
];

const COLUMNS = [
  { key: "rank", label: "#", fmt: (s, i) => String(i + 1) },
  { key: "pattern", label: "pattern", fmt: (s) => s.pattern + (s.kind === "control" ? ' <span class="star">★</span>' : "") },
  { key: "trades", label: "trades", fmt: (s) => num(s.trades, 0) },
  { key: "win_rate", label: "win%", fmt: (s) => pct(s.win_rate) },
  { key: "expectancy_r", label: "exp R", fmt: (s) => signed(s.expectancy_r) },
  { key: "ci_low", label: "95% session ci", fmt: (s) => ci(s) },
  { key: "baseline_delta_r", label: "vs ctrl", title: "Gross R advantage over this pattern's own matched controls (same direction and stop distance, random entry 1-5 bars later): signal before costs", fmt: (s) => signed(s.baseline_delta_r) },
  { key: "baseline_ci_low", label: "95% paired ci", fmt: (s) => ci({ci_low: s.baseline_ci_low, ci_high: s.baseline_ci_high}) },
  { key: "p_delta_adjusted", label: "delta adj p", fmt: (s) => num(s.p_delta_adjusted, 4) },
  { key: "mde_delta_r", label: "detectable", title: "Smallest true advantage over matched controls, in gross R, this row's corrected test would catch 80% of the time; a NOISE row can still hide a smaller edge", fmt: (s) => (s.mde_delta_r === null || s.mde_delta_r === undefined ? '<span class="na">n/a</span>' : `≥${s.mde_delta_r.toFixed(2)}`) },
  { key: "p_expectancy_adjusted", label: "mean adj p", fmt: (s) => num(s.p_expectancy_adjusted, 4) },
  { key: "sessions", label: "dates", fmt: (s) => num(s.sessions, 0) },
  { key: "validation", label: "validation", fmt: (s) => validationCell(s) },
  { key: "profit_factor", label: "pf", fmt: (s) => num(s.profit_factor, 2) },
  { key: "consistency", label: "consist", fmt: (s) => pct(s.consistency) },
  { key: "stability", label: "stab", fmt: (s) => pct(s.stability) },
  { key: "max_drawdown_r", label: "maxdd r", fmt: (s) => num(s.max_drawdown_r, 2) },
  { key: "signals", label: "signals", fmt: (s) => num(s.signals, 0) },
  { key: "verdict", label: "verdict", fmt: (s) => `<span class="verdict ${s.verdict}">${s.verdict}${s.verdict === "EDGE" && !s.validation ? " (historical)" : ""}</span>` },
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
  if (s.ci_low == null || s.ci_high == null) return '<span class="na">n/a</span>';
  const crosses = s.ci_low < 0 && s.ci_high > 0;
  return `<span class="${crosses ? "na" : ""}">[${s.ci_low >= 0 ? "+" : ""}${s.ci_low.toFixed(2)},${s.ci_high >= 0 ? "+" : ""}${s.ci_high.toFixed(2)}]</span>`;
}


function validationCell(s) {
  if (s.kind === "control") return "control";
  if (s.validation) return `<span class="verdict ${s.validation.verdict}">${s.validation.verdict}</span> ${signed(s.validation.expectancy_r)}R`;
  if (s.discovery_verdict === "EDGE") return "unconfirmed";
  return s.discovery_verdict ? "not selected" : '<span class="na">unavailable</span>';
}

// Server error strings quote the value that caused them, so they are written as
// text, never as markup. The page only ever sends vetted values, but one
// reflected string is all a future caller would need.
function fail(id, message) {
  const el = $(id);
  el.textContent = "";
  const note = document.createElement("p");
  note.className = "hint";
  note.textContent = message;
  el.appendChild(note);
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
  if (state.meta.read_only) {
    // A published copy shows saved runs; starting work is refused server-side
    // too, so hiding the buttons is courtesy, not the protection.
    $("run").classList.add("hidden");
    $("fetch").classList.add("hidden");
    banner("Read-only demo: browse the saved runs below. Starting a run is disabled on this server.", "info");
  }
  // Not awaited: describing a large cache reads every symbol's bars, and the
  // last run's results do not depend on it.
  refreshCache();
  try {
    const results = await api("/api/results");
    if (results && results.stats) {
      state.results = results;
      render();
    }
  } catch (err) {
    banner(`Could not load results: ${err.message}`, "error");
  }
  await refreshRuns();
  pollStatus();
}

function buildControls() {
  const c = state.meta.config;
  $("trials").value = c.run.trials;
  $("seed").value = c.run.seed;
  $("windows").value = c.run.windows;
  $("holdout-fraction").value = c.run.holdout_fraction ?? 0.2;
  $("min-sessions").value = c.stats.min_sessions ?? 10;
  $("bootstrap-samples").value = c.stats.bootstrap_samples ?? 10000;
  $("experiment-count").value = c.stats.experiment_count ?? 1;
  $("lookback").value = c.run.lookback_days;
  $("source").innerHTML = (state.meta.sources || [])
    .map((name) => `<option value="${name}"${name === c.run.source ? " selected" : ""}>${name}</option>`)
    .join("");
  $("reward").value = c.trade.reward_multiple;
  $("stop-buffer").value = c.trade.stop_buffer;
  $("max-hold").value = c.trade.max_hold_bars;
  $("slippage").value = c.costs.slippage_bps;
  $("cost-model").innerHTML = (state.meta.cost_models || [])
    .map((m) => `<option value="${m}"${m === c.costs.model ? " selected" : ""}>${m}</option>`)
    .join("");
  $("sample-size").value = c.universe.sample_size;

  $("rank-by").innerHTML = state.meta.rank_keys
    .map((k) => `<option value="${k}"${k === c.stats.rank_by ? " selected" : ""}>${k}</option>`)
    .join("");

  buildIntervalChips();

  const on = new Set(c.patterns);
  $("patterns").innerHTML = state.meta.patterns
    .map((p) => {
      const trend = p.requires_trend === -1 ? "after a downtrend" : p.requires_trend === 1 ? "after an uptrend" : "any trend";
      const tip = `${p.bias}, ${p.bars_required}-bar, ${trend}${p.kind === "control" ? " — random-entry control" : ""}`;
      return `<button class="chip ${p.kind}" data-pattern="${p.name}" aria-pressed="${on.has(p.name)}" title="${tip}">${p.name}</button>`;
    })
    .join("");

  document.querySelectorAll("#patterns .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      chip.setAttribute("aria-pressed", chip.getAttribute("aria-pressed") !== "true");
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
  $("detail-sample").addEventListener("change", () => {
    state.sample = $("detail-sample").value;
    state.page.offset = 0;
    renderDetail();
  });
  $("detail-close").addEventListener("click", closeDetail);
  $("trades-prev").addEventListener("click", () => pageBy(-1));
  $("trades-next").addEventListener("click", () => pageBy(1));
  $("session-symbol").addEventListener("change", () => { buildSessionPickers(); loadSession(); });
  $("session-day").addEventListener("change", loadSession);
  $("breakdown-by").innerHTML = (state.meta.breakdowns || [])
    .map((b, i) => `<option value="${b}"${i ? "" : " selected"}>${b.replace(/_/g, " ")}</option>`)
    .join("");
  $("source").addEventListener("change", () => { buildIntervalChips(); refreshCache(); });
  $("breakdown-by").addEventListener("change", loadBreakdown);
  $("run-a").addEventListener("change", compareRuns);
  $("run-b").addEventListener("change", compareRuns);
}

// Which intervals exist depends on the source: only Alpaca reaches below a
// minute, so the chips are rebuilt whenever the source changes rather than
// offering a timeframe the selected source cannot serve.
function buildIntervalChips() {
  const c = state.meta.config;
  const source = ($("source") && $("source").value) || c.run.source;
  const offered = (state.meta.intervals_by_source || {})[source] || state.meta.intervals;
  const enabled = new Set(c.run.intervals);
  $("intervals").innerHTML = offered
    .map((iv) => {
      const days = (state.meta.lookback_days || {})[iv];
      const note = days ? `${days} days of history by default` : "resampled from raw trades";
      return `<button class="chip" data-interval="${iv}" aria-pressed="${enabled.has(iv)}" title="${note}">${iv}</button>`;
    })
    .join("");
  $("intervals").querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      chip.setAttribute("aria-pressed", chip.getAttribute("aria-pressed") !== "true");
      refreshCache();
    });
  });
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
      windows: Number($("windows").value),
      holdout_fraction: Number($("holdout-fraction").value),
      source: $("source").value,
      lookback_days: Number($("lookback").value),
      intervals: picked("interval"),
    },
    universe: { sample_size: Number($("sample-size").value) },
    trade: {
      reward_multiple: Number($("reward").value),
      stop_buffer: Number($("stop-buffer").value),
      max_hold_bars: Number($("max-hold").value),
    },
    costs: { model: $("cost-model").value, slippage_bps: Number($("slippage").value) },
    stats: { rank_by: $("rank-by").value,
      min_sessions: Number($("min-sessions").value),
      bootstrap_samples: Number($("bootstrap-samples").value),
      experiment_count: Number($("experiment-count").value) },
    patterns: picked("pattern"),
  };
}

async function refreshCache() {
  const chosen = picked("interval");
  try {
    const cache = await api(`/api/cache?source=${encodeURIComponent($("source").value)}`);
    const missing = chosen.filter((iv) => (cache.intervals[iv]?.sessions ?? 0) === 0);
    const counts = chosen
      .map((iv) => `${iv} ${(cache.intervals[iv]?.sessions ?? 0)} sessions`)
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
      try {
        state.results = await api("/api/results");
        render();
        refreshRuns();
      } catch (err) {
        banner(`Could not load results: ${err.message}`, "error");
      }
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
      // A deep offset would land past the end of a thinner timeframe.
      state.page.offset = 0;
      render();
    });
  });

  // A selection that the new results no longer contain would drill into
  // nothing, so it is dropped rather than left pointing at a stale row.
  if (state.selected && !rows().some((s) => s.pattern === state.selected.pattern)) {
    state.selected = null;
  }

  renderTable();
  renderCharts();
  ["summary", "board", "charts"].forEach((id) => $(id).classList.remove("hidden"));
  if (state.selected) renderDetail(); else $("detail").classList.add("hidden");
}

function renderSummary() {
  const r = state.results;
  const c = r.config;
  const rows = r.stats.filter((s) => s.kind !== "control");
  const edges = rows.filter((s) => s.verdict === "EDGE" && s.validation?.verdict === "EDGE" && r.validation);
  const beating = rows.filter((s) => s.baseline_delta_r > 0 && s.p_delta_adjusted != null && s.p_delta_adjusted <= 0.05 && s.baseline_ci_low > 0);

  const cost = costHeadline(r, c);
  $("summary-body").innerHTML = `
    <div class="stat-grid">
      <div class="stat"><b>${r.trials.length}</b><span>trials</span></div>
      <div class="stat"><b>${r.sessions_evaluated}</b><span>sessions</span></div>
      <div class="stat"><b>${c.universe.sample_size}</b><span>symbols</span></div>
      <div class="stat"><b class="${edges.length ? "pos" : ""}">${edges.length}</b><span>with an edge</span></div>
      <div class="stat"><b>${beating.length}</b><span>beating control</span></div>
      <div class="stat"><b>${cost.value}</b><span>${cost.label}</span></div>
      <div class="stat"><b>${c.run.seed}</b><span>seed</span></div>
    </div>
    <p class="hint"><a id="download-report" href="/api/report" download>Download this run as a self-contained HTML report</a></p>
    <p class="hint">${edges.length
      ? `<b>${edges.map((s) => `${s.pattern} (${s.interval})`).join(", ")}</b> passed corrected discovery tests and independent later validation.`
      : `No pattern established a confirmed edge. ${beating.length} discovery rows show a corrected advantage over their controls; that alone does not establish profitable trading.`}</p>
    <p id="validation-summary" class="hint"></p>
    ${r.warnings.map((w) => `<p class="hint">⚠ ${w}</p>`).join("")}`;

  const validation = r.validation;
  $("validation-summary").textContent = !validation || !Object.keys(validation).length
    ? "Historical report: validation evidence unavailable. Earlier EDGE labels are exploratory."
    : !validation.enabled
      ? "Validation disabled: exploratory results only. Discovery candidates remain unconfirmed."
      : `Holdout starts ${validation.cutoff}: ${validation.discovery_trials} discovery / ${validation.validation_trials} validation trials; ${(validation.candidates || []).length} selected candidates. ${validation.evaluated ? "Validation evaluated." : "Holdout untouched: no candidate passed discovery."} ${validation.warning || "Reusing validation to choose settings invalidates confirmation."}`;
  const windows = c.run.windows > 1
    ? ` Trials are split across ${c.run.windows} walk-forward windows, and <b>stab</b> is the share of
       those windows whose expectancy was positive &mdash; a different and harder test than
       <b>consist</b>, which counts individual trials.`
    : "";
  $("caveats").innerHTML = `Stop at the pattern extreme &minus;${(c.trade.stop_buffer * 100).toFixed(3)}%,
    target ${c.trade.reward_multiple}R, max hold ${c.trade.max_hold_bars} bars,
    net of ${r.costs_description || `${c.costs.slippage_bps} bps slippage`}.
    A bar touching both stop and target counts as a stop unless it opened past the target; gaps fill at the open.
    Cached sampled dates span ${r.trials.length ? [...r.trials.map(t => t.session)].sort()[0] : "n/a"}
    to ${r.trials.length ? [...r.trials.map(t => t.session)].sort().at(-1) : "n/a"}.
    ${r.inference?.cluster === "market_date" ? `Pointwise 95% intervals resample market dates, preserving trades on a date together.
    Verdicts use Holm correction across rows and ${c.stats.experiment_count ?? 1} declared experiment(s).` : "Historical inference method: current date-clustered and corrected validation evidence is unavailable."}
    Overlapping timeframes and trades are not independent evidence. ${windows}
    ${provenanceLine(r.provenance)}`;
}

// Which code, settings and bars produced this run, so two runs that disagree
// can be told apart by more than their seed.
function provenanceLine(p) {
  if (!p) return "Historical report: code and data provenance were not recorded.";
  const short = (h) => (h ? h.slice(0, 10) : "n/a");
  const code = p.git ? `${short(p.git.commit)}${p.git.dirty ? " (uncommitted changes)" : ""}` : `version ${p.candlebench}`;
  const data = p.data || {};
  const missing = data.missing ? `, ${data.missing} missing` : "";
  return `Computed ${p.created_at} by code ${code}; config ${short(p.config_sha256)}; ${data.source} data ${short(data.sha256)} over ${data.files} cached file(s)${missing}.`;
}

// The headline cost must be the figure the cost line describes. It once showed
// the estimator's half-spread, labelled "measured", on runs priced from quotes,
// so a run charged 7.50 bps per leg displayed 1.00.
function costHeadline(r, c) {
  const known = (v) => v !== null && v !== undefined;
  if (c.costs.model === "quoted" && known(r.charged_bps)) {
    return {
      value: r.charged_bps.toFixed(2),
      label: r.quoted_share > 0 ? "bps paid per leg (quoted)" : "bps paid per leg (estimated; no quotes)",
    };
  }
  if (c.costs.model === "fixed" || !known(r.spread_bps)) {
    return { value: `${c.costs.slippage_bps}`, label: "bps cost per leg (fixed)" };
  }
  return { value: (r.spread_bps / 2).toFixed(2), label: `bps cost per leg (estimated, ${r.spread_interval})` };
}

function sorted(rows) {
  const { key, desc } = state.sort;
  const measurable = (s) => s.verdict !== "INSUFFICIENT" && s[key] !== null && s[key] !== undefined;
  return [...rows].sort((a, b) => {
    // Unmeasurable rows sort last whichever direction is chosen: a pattern
    // that produced nothing is not "the best" simply because it has no number.
    if (measurable(a) !== measurable(b)) return measurable(a) ? -1 : 1;
    if (key === "validation") {
      const av = a.validation?.verdict || "", bv = b.validation?.verdict || "";
      return desc ? bv.localeCompare(av) : av.localeCompare(bv);
    }
    if (key === "pattern" || key === "verdict") {
      return desc ? b[key].localeCompare(a[key]) : a[key].localeCompare(b[key]);
    }
    const av = a[key] ?? 0, bv = b[key] ?? 0;
    return desc ? bv - av : av - bv;
  });
}

function rows() {
  return state.results.stats.filter((s) => s.interval === state.interval);
}

function renderTable() {
  const dominated = (state.results.cost_dominated || []).includes(state.interval);
  const note = $("interval-note");
  note.classList.toggle("hidden", !dominated);
  if (dominated) {
    const losers = (state.results.losing_controls || {})[state.interval] || ["Random entry"];
    note.innerHTML = `<b>${losers.join(", ")}</b> reliably ${losers.length > 1 ? "lose" : "loses"} at this timeframe after costs, so NEGATIVE verdicts here partly reflect cost drag rather than the pattern. Read <b>vs ctrl</b> together with its paired interval and adjusted p-value; a positive average alone does not establish signal.`;
  }

  $("table").tHead.innerHTML = `<tr>${COLUMNS.map((col) => {
    const active = col.key === state.sort.key;
    return `<th data-key="${col.key}" title="${col.title ? `${col.title}. ` : ""}sort by ${col.label}">${col.label}${active ? (state.sort.desc ? " ▾" : " ▴") : ""}</th>`;
  }).join("")}</tr>`;

  $("table").tBodies[0].innerHTML = sorted(rows())
    .map((s, i) => `<tr class="${s.kind}${s.pattern === (state.selected || {}).pattern ? " picked" : ""}" data-pattern="${s.pattern}">${COLUMNS.map((col) => `<td>${col.fmt(s, i)}</td>`).join("")}</tr>`)
    .join("");

  $("table").tBodies[0].querySelectorAll("tr[data-pattern]").forEach((tr) => {
    tr.addEventListener("click", () => select(tr.dataset.pattern));
  });

  document.querySelectorAll("th[data-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (key === "rank") return;
      state.sort = { key, desc: state.sort.key === key ? !state.sort.desc : true };
      renderTable();
    });
  });
}

// ---------- comparing two runs ----------

async function refreshRuns() {
  let body;
  try {
    body = await api("/api/runs");
  } catch {
    return;
  }
  state.runs = body.runs;
  $("compare").classList.toggle("hidden", body.runs.length < 2);
  if (body.runs.length < 2) return;

  $("compare-keep").textContent = `The last ${body.keep} runs are kept; ${body.runs.length} saved.`;
  // Default to the two most recent, newest as the "against" side, so the
  // deltas read as "what the latest change did".
  const options = (selected) => body.runs
    .map((r) => `<option value="${r.id}"${r.id === selected ? " selected" : ""}>${describeRun(r)}</option>`)
    .join("");
  $("run-a").innerHTML = options(body.runs[1].id);
  $("run-b").innerHTML = options(body.runs[0].id);
  compareRuns();
}

function describeRun(r) {
  const when = r.saved_at ? r.saved_at.slice(0, 19).replace("T", " ") : r.id;
  const spread = r.spread_bps === null || r.spread_bps === undefined
    ? "fixed cost" : `${r.spread_bps.toFixed(2)} bps`;
  return `${when} — ${r.trials} trials, seed ${r.seed}, ${r.reward_multiple}R, ${spread}`;
}

async function compareRuns() {
  const a = $("run-a").value, b = $("run-b").value;
  if (!a || !b) return;
  if (a === b) {
    $("compare-body").innerHTML = '<p class="hint">Pick two different runs.</p>';
    return;
  }

  let left, right;
  try {
    [left, right] = await Promise.all([
      api(`/api/runs?id=${encodeURIComponent(a)}`),
      api(`/api/runs?id=${encodeURIComponent(b)}`),
    ]);
  } catch (err) {
    fail("compare-body", err.message);
    return;
  }

  const key = (s) => `${s.pattern}@${s.interval}`;
  const before = new Map(left.stats.map((s) => [key(s), s]));
  const rows = right.stats
    .filter((s) => before.has(key(s)))
    .map((s) => {
      const was = before.get(key(s));
      const movable = s.expectancy_r !== null && was.expectancy_r !== null;
      return {
        pattern: s.pattern,
        interval: s.interval,
        kind: s.kind,
        was: was.expectancy_r,
        now: s.expectancy_r,
        delta: movable ? s.expectancy_r - was.expectancy_r : null,
        verdictWas: was.verdict,
        verdictNow: s.verdict,
      };
    })
    // Largest movement first, in either direction: the point is what changed.
    .sort((x, y) => Math.abs(y.delta ?? 0) - Math.abs(x.delta ?? 0));

  const flips = rows.filter((r) => r.verdictWas !== r.verdictNow);
  const settings = describeSettingChanges(left.config, right.config);

  $("compare-body").innerHTML = `
    <p class="hint">${settings.length
      ? `Changed between these runs: <b>${settings.join(", ")}</b>.`
      : "The two runs used identical settings, so any difference is sampling noise."}
      ${flips.length ? `${flips.length} verdict${flips.length === 1 ? "" : "s"} changed.` : "No verdict changed."}</p>
    <div class="table-wrap"><table>
      <thead><tr><th>pattern</th><th>tf</th><th>was</th><th>now</th><th>delta</th><th>verdict</th></tr></thead>
      <tbody>${rows.slice(0, 40).map((r) => `
        <tr class="${r.kind}">
          <td>${r.pattern}${r.kind === "control" ? ' <span class="star">★</span>' : ""}</td>
          <td>${r.interval}</td>
          <td>${signed(r.was)}</td>
          <td>${signed(r.now)}</td>
          <td>${signed(r.delta)}</td>
          <td>${r.verdictWas === r.verdictNow
            ? `<span class="verdict ${r.verdictNow}">${r.verdictNow}</span>`
            : `<span class="verdict ${r.verdictWas}">${r.verdictWas}</span> → <span class="verdict ${r.verdictNow}">${r.verdictNow}</span>`}</td>
        </tr>`).join("")}</tbody>
    </table></div>`;
}

// Only the settings that can move a result are compared; the seed is reported
// separately because a different seed explains a difference without being a
// change in method.
function describeSettingChanges(a, b) {
  const out = [];
  const pairs = [
    ["trials", a.run.trials, b.run.trials],
    ["seed", a.run.seed, b.run.seed],
    ["windows", a.run.windows, b.run.windows],
    ["holdout fraction", a.run.holdout_fraction, b.run.holdout_fraction],
    ["minimum dates", a.stats.min_sessions, b.stats.min_sessions],
    ["experiments tried", a.stats.experiment_count, b.stats.experiment_count],
    ["bootstrap samples", a.stats.bootstrap_samples, b.stats.bootstrap_samples],
    ["patterns", (a.patterns || []).join("/"), (b.patterns || []).join("/")],
    ["intervals", (a.run.intervals || []).join("/"), (b.run.intervals || []).join("/")],
    ["reward", a.trade.reward_multiple, b.trade.reward_multiple],
    ["stop buffer", a.trade.stop_buffer, b.trade.stop_buffer],
    ["max hold", a.trade.max_hold_bars, b.trade.max_hold_bars],
    ["cost model", a.costs.model, b.costs.model],
    ["slippage", a.costs.slippage_bps, b.costs.slippage_bps],
    ["symbols", a.universe.sample_size, b.universe.sample_size],
  ];
  for (const key of Object.keys(a.thresholds || {}).sort()) {
    pairs.push([`threshold ${key}`, a.thresholds[key], b.thresholds?.[key]]);
  }
  for (const [label, was, now] of pairs) {
    if (String(was) !== String(now)) out.push(`${label} ${was} → ${now}`);
  }
  return out;
}

// ---------- drill-down ----------

// The pooled view has no single interval, and the server only accepts real
// ones, so the filter is simply omitted there and the curve spans every
// timeframe — which is what "pooled" means.
function selectedInterval() {
  return state.interval === POOLED ? null : state.interval;
}

function intervalQuery() {
  const iv = selectedInterval();
  return (iv ? `&interval=${encodeURIComponent(iv)}` : "") + `&sample=${state.sample}`;
}

function select(pattern) {
  if (state.selected && state.selected.pattern === pattern) { closeDetail(); return; }
  state.selected = { pattern };
  state.sample = "discovery";
  state.page = { offset: 0, limit: 100, sort: null, desc: true };
  renderTable();
  renderDetail();
}

function closeDetail() {
  state.selected = null;
  $("detail").classList.add("hidden");
  renderTable();
}

async function renderDetail() {
  const { pattern } = state.selected;
  const label = state.interval === POOLED ? "all timeframes pooled" : state.interval;
  $("detail-who").textContent = `${pattern} at ${label}`;
  $("detail").classList.remove("hidden");

  const evidence = rows().find(s => s.pattern === pattern)?.validation;
  $("detail-sample").querySelector('[value="validation"]').disabled = !evidence;
  if (!evidence) state.sample = "discovery";
  $("detail-sample").value = state.sample;
  buildSessionPickers();
  await Promise.all([loadEquity(pattern), loadTrades(), loadSession(), loadBreakdown()]);
}

async function loadBreakdown() {
  const by = $("breakdown-by").value;
  if (!by || !state.selected) return;
  let body;
  try {
    body = await api(
      `/api/breakdown?by=${encodeURIComponent(by)}` +
      `&pattern=${encodeURIComponent(state.selected.pattern)}${intervalQuery()}`
    );
  } catch (err) {
    fail("breakdown-body", err.message);
    return;
  }

  if (!body.rows.length) {
    $("breakdown-body").innerHTML = '<p class="hint">No trades to group.</p>';
    return;
  }

  // Ordered by expectancy for every key except time of day, whose session order
  // is the point of looking at it.
  const rows = by === "time_of_day"
    ? body.rows
    : [...body.rows].sort((a, b) => b.expectancy_r - a.expectancy_r);

  $("breakdown-body").innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>${by.replace(/_/g, " ")}</th><th>trades</th><th>win%</th>
      <th>exp R</th><th>total R</th><th>exits</th></tr></thead>
    <tbody>${rows.map((r) => `<tr>
      <td>${r.key}</td>
      <td>${num(r.trades, 0)}</td>
      <td>${pct(r.win_rate)}</td>
      <td>${signed(r.expectancy_r)}</td>
      <td>${signed(r.total_r)}</td>
      <td>${Object.entries(r.exit_mix).map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`).join(", ")}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

// The pickers offer only trials the run actually drew, so a chosen session is
// always one the leaderboard counted.
function buildSessionPickers() {
  const trials = (state.results.trials || []).filter(t => (t.sample || "discovery") === state.sample);
  const symbols = [...new Set(trials.map((t) => t.symbol))].sort();
  const keep = $("session-symbol").value;
  $("session-symbol").innerHTML = symbols
    .map((s) => `<option value="${s}"${s === keep ? " selected" : ""}>${s}</option>`)
    .join("");

  const chosen = $("session-symbol").value;
  const days = [...new Set(trials.filter((t) => t.symbol === chosen).map((t) => t.session))].sort();
  const keepDay = $("session-day").value;
  $("session-day").innerHTML = days
    .map((d) => `<option value="${d}"${d === keepDay ? " selected" : ""}>${d}</option>`)
    .join("");
  $("session-figure").classList.toggle("hidden", !symbols.length);
}

async function loadSession() {
  const symbol = $("session-symbol").value;
  const day = $("session-day").value;
  // The pooled tab has no single interval to draw bars for, so the finest
  // enabled one stands in and the caption says which.
  const interval = selectedInterval() || state.results.config.run.intervals[0];
  if (!symbol || !day || !state.selected) return;

  let body;
  try {
    body = await api(
      `/api/session?symbol=${encodeURIComponent(symbol)}&session=${encodeURIComponent(day)}` +
      `&interval=${encodeURIComponent(interval)}&pattern=${encodeURIComponent(state.selected.pattern)}&sample=${state.sample}`
    );
  } catch (err) {
    fail("chart-session", err.message);
    return;
  }
  $("chart-session").innerHTML = sessionChart(body, interval);
}

async function loadEquity(pattern) {
  let body;
  try {
    body = await api(`/api/equity?pattern=${encodeURIComponent(pattern)}${intervalQuery()}`);
  } catch (err) {
    fail("chart-equity", err.message);
    return;
  }
  const series = [body.pattern];
  if (body.control && body.control.points.length) series.push(body.control);
  $("chart-equity").innerHTML = body.pattern.points.length
    ? equityChart(series)
    : '<p class="hint">This pattern produced no trades at this timeframe.</p>';
}

async function loadTrades() {
  const { pattern } = state.selected;
  const p = state.page;
  const sort = p.sort ? `&sort=${encodeURIComponent(p.sort)}&desc=${p.desc ? 1 : 0}` : "";
  let body;
  try {
    body = await api(
      `/api/trades?pattern=${encodeURIComponent(pattern)}${intervalQuery()}` +
      `&limit=${p.limit}&offset=${p.offset}${sort}`
    );
  } catch (err) {
    $("trades-count").textContent = err.message;
    return;
  }

  state.pageTotal = body.total;
  // The server decides the page size, so a capped request reports what it
  // actually served rather than what was asked for.
  state.page.limit = body.limit;
  const last = Math.min(body.offset + body.trades.length, body.total);
  $("trades-count").textContent = body.total
    ? `Trades ${body.offset + 1}–${last} of ${body.total}`
    : "No trades for this pattern at this timeframe.";
  $("trades-prev").disabled = body.offset === 0;
  $("trades-next").disabled = last >= body.total;

  $("trades-table").tHead.innerHTML = `<tr>${TRADE_COLUMNS.map((col) => {
    const active = col.key === p.sort;
    return `<th data-trade-key="${col.key}" title="sort by ${col.label}">${col.label}${active ? (p.desc ? " ▾" : " ▴") : ""}</th>`;
  }).join("")}</tr>`;

  $("trades-table").tBodies[0].innerHTML = body.trades
    .map((t) => `<tr>${TRADE_COLUMNS.map((col) => `<td>${col.fmt(t)}</td>`).join("")}</tr>`)
    .join("");

  $("trades-table").tHead.querySelectorAll("th[data-trade-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.tradeKey;
      state.page.desc = p.sort === key ? !p.desc : true;
      state.page.sort = key;
      state.page.offset = 0;
      loadTrades();
    });
  });
}

function pageBy(step) {
  // Clamped to a page boundary rather than to total-1: landing mid-page would
  // show a single row and misalign every subsequent step.
  const limit = state.page.limit;
  const last = Math.max(0, Math.floor(Math.max(0, state.pageTotal - 1) / limit) * limit);
  state.page.offset = Math.min(Math.max(0, state.page.offset + step * limit), last);
  loadTrades();
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

// Cumulative R against trade sequence. The x axis is trade number rather than
// calendar time: the trades are already in the order they happened, and spacing
// them by clock time would compress a busy session into a single pixel.
function equityChart(series) {
  // The control fires several times as often as most patterns, so its raw
  // cumulative R ran off the scale and squeezed the pattern into a corner.
  // It is drawn scaled to the pattern's trade count: the curve the control's
  // average trade would trace over the same number of trades. Each line spans
  // the full width, in its own chronological order.
  const main0 = series[0];
  series = series.map((s, n) => {
    const scale = n && s.trades && main0.trades ? main0.trades / s.trades : 1;
    return { ...s, scale, points: [0, ...s.points.map((v) => v * scale)] };
  });
  const width = 1080, height = 260, padL = 46, padR = 14, padT = 12, padB = 26;
  const values = series.flatMap((s) => s.points).concat([0]);
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || 1;

  const xOf = (i, count) => padL + (count < 2 ? 0 : (i / (count - 1)) * (width - padL - padR));
  const y = (v) => padT + (1 - (v - lo) / span) * (height - padT - padB);

  const colours = ["var(--accent)", "var(--muted)"];
  const lines = series.map((s, n) => {
    const path = s.points.map((v, i) => `${i ? "L" : "M"}${xOf(i, s.points.length).toFixed(1)},${y(v).toFixed(1)}`).join("");
    return `<path d="${path}" fill="none" stroke="${colours[n] || "var(--muted)"}"
      stroke-width="${n ? 1 : 1.6}" ${n ? 'stroke-dasharray="3 3"' : ""}/>`;
  }).join("");

  // The drawdown band marks the span the server reported, drawn from the peak
  // that preceded the trough so the depth on screen is the reported number.
  const main = series[0];
  const x = (i) => xOf(i, main.points.length);
  let band = "";
  if (main.max_drawdown_r > 0) {
    let peak = 0, peakAt = 0, worst = 0, from = 0, to = 0;
    main.points.forEach((v, i) => {
      if (v > peak) { peak = v; peakAt = i; }
      if (peak - v > worst) { worst = peak - v; from = peakAt; to = i; }
    });
    band = `<rect x="${x(from).toFixed(1)}" y="${y(main.points[from]).toFixed(1)}"
      width="${Math.max(1, x(to) - x(from)).toFixed(1)}"
      height="${Math.max(1, y(main.points[to]) - y(main.points[from])).toFixed(1)}"
      fill="var(--negative)" opacity="0.14"/>`;
  }

  const legend = series.map((s, n) =>
    `<text x="${padL + 4 + n * 260}" y="${padT + 10}" fill="${colours[n]}">${s.pattern} (${s.trades} trades${s.scale !== 1 ? `, scaled to ${main.trades}` : ""})</text>`
  ).join("");

  return svg(width, height, `
    <line x1="${padL}" y1="${y(0).toFixed(1)}" x2="${width - padR}" y2="${y(0).toFixed(1)}"
      stroke="var(--line)" stroke-dasharray="2 3"/>
    ${band}${lines}
    <text x="${padL - 6}" y="${y(hi).toFixed(1)}" text-anchor="end">${hi.toFixed(1)}R</text>
    <text x="${padL - 6}" y="${y(lo).toFixed(1)}" text-anchor="end">${lo.toFixed(1)}R</text>
    <text x="${padL}" y="${height - 6}">${main.first_session || ""}</text>
    <text x="${width - padR}" y="${height - 6}" text-anchor="end">${main.last_session || ""}</text>
    <text x="${(padL + width - padR) / 2}" y="${height - 6}" text-anchor="middle">deepest drawdown ${num(main.max_drawdown_r, 2)}R</text>
    ${legend}`);
}

// Candlesticks for one session, with the pattern's signal bars outlined and the
// levels of each recorded trade drawn across the bars it was open for.
function sessionChart(body, interval) {
  const bars = body.bars;
  if (!bars.length) return '<p class="hint">No bars for this session.</p>';

  const width = 1080, height = 320, padL = 46, padR = 14, padT = 14, padB = 30;
  const levels = body.trades.flatMap((t) => [t.stop_price, t.target_price, t.entry_price]);
  const lo = Math.min(...bars.map((b) => b.l), ...levels);
  const hi = Math.max(...bars.map((b) => b.h), ...levels);
  const span = hi - lo || 1;
  const step = (width - padL - padR) / bars.length;
  const bodyW = Math.max(1, Math.min(7, step * 0.65));

  const x = (i) => padL + (i + 0.5) * step;
  const y = (v) => padT + (1 - (v - lo) / span) * (height - padT - padB);

  const signals = new Set(body.signals);
  const candles = bars.map((b, i) => {
    const up = b.c >= b.o;
    const colour = up ? "var(--edge)" : "var(--negative)";
    const top = y(Math.max(b.o, b.c)), bottom = y(Math.min(b.o, b.c));
    const marked = signals.has(i);
    return `<line x1="${x(i).toFixed(1)}" y1="${y(b.h).toFixed(1)}" x2="${x(i).toFixed(1)}" y2="${y(b.l).toFixed(1)}" stroke="${colour}" stroke-width="0.8" opacity="0.8"/>
      <rect x="${(x(i) - bodyW / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bodyW.toFixed(1)}"
        height="${Math.max(0.8, bottom - top).toFixed(1)}" fill="${colour}" opacity="${marked ? 1 : 0.55}"
        ${marked ? 'stroke="var(--accent)" stroke-width="1.2"' : ""}/>`;
  }).join("");

  const marks = body.trades.map((t) => {
    const x1 = x(t.entry_index), x2 = x(t.exit_index);
    const line = (v, colour, dash) =>
      `<line x1="${x1.toFixed(1)}" y1="${y(v).toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y(v).toFixed(1)}"
        stroke="${colour}" stroke-width="1" stroke-dasharray="${dash}" opacity="0.85"/>`;
    return `
      ${line(t.target_price, "var(--edge)", "4 2")}
      ${line(t.stop_price, "var(--negative)", "4 2")}
      ${line(t.entry_price, "var(--accent)", "0")}
      <text x="${(x2 + 4).toFixed(1)}" y="${(y(t.entry_price) + 3).toFixed(1)}"
        fill="${t.net_r >= 0 ? "var(--edge)" : "var(--negative)"}">${t.net_r >= 0 ? "+" : ""}${t.net_r.toFixed(2)}R</text>`;
  }).join("");

  const ticks = bars
    .map((b, i) => [b, i])
    .filter(([, i]) => i % Math.ceil(bars.length / 8) === 0)
    .map(([b, i]) => `<text x="${x(i).toFixed(1)}" y="${height - 6}" text-anchor="middle">${b.t}</text>`)
    .join("");

  const note = body.kind === "control"
    ? "random-entry control: its signals are drawn by the runner, so no bars are outlined"
    : `${body.signals.length} signal bars, ${body.trades.length} trades, trend window ${body.trend_lookback} bars`;

  return svg(width, height, `
    <text x="${padL - 6}" y="${y(hi).toFixed(1)}" text-anchor="end">${hi.toFixed(2)}</text>
    <text x="${padL - 6}" y="${y(lo).toFixed(1)}" text-anchor="end">${lo.toFixed(2)}</text>
    ${candles}${marks}${ticks}
    <text x="${padL}" y="${padT}">${body.symbol} ${body.session} ${interval} — ${note}</text>`);
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
