"use strict";
/* 시그널 레저 — 프론트엔드. 외부 차트 라이브러리 없이 Canvas로 직접 그린다. */

const API = "/api";

/* 검증된 시리즈 팔레트 (dataviz validate_palette.js, dark surface #0E1519 전 항목 통과).
   민트 계열은 UI 크롬 전용이라 시리즈에 쓰지 않는다. */
const C = {
  ink: "#E3EDEB", ink2: "#93A8AC", ink3: "#5F7378",
  grid: "#16222700", gridLine: "#152128",
  bull: "#2BB98A", bear: "#E5484D", warn: "#E0A32E", accent: "#3EE8B0",
  ema: "#199e70", vwap: "#c98500", rf: "#3987e5",
  band: "#46606B", fib: "#7C8FA6", fbb: "#8E6FB0",
};

const state = {
  market: "crypto", symbol: "BTCUSDT", interval: "1h", limit: 500,
  data: null,
  view: { start: 0, count: 160 },
  toggles: { ema: true, vwap: true, bb: true, fib: true, rf: false, fbb: false,
             candlePat: true, chartPat: true, customPat: true },
  hover: null, drag: null,
  selectMode: false, selecting: null, selectedRange: null,
  customPatterns: [], watchlist: [],
  activeListTab: "chart", activeBuilderTab: "rule", activeView: "overview",
  markersCache: [], evHover: null,
};

/* ── 유틸 ───────────────────────────────────────────────────── */
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const $ = (id) => document.getElementById(id);

function fmtPrice(p) {
  if (p == null || Number.isNaN(p)) return "—";
  const a = Math.abs(p);
  const d = a >= 1000 ? 2 : a >= 100 ? 2 : a >= 1 ? 3 : a >= 0.01 ? 5 : 8;
  return p.toLocaleString("ko-KR", { maximumFractionDigits: d, minimumFractionDigits: a >= 1000 ? 2 : 0 });
}
const fmtPct = (p) => (p * 100).toFixed(0) + "%";

/* 축 라벨은 폭이 좁아 축약해서 쓴다 — 툴팁·장부에는 전체 정밀도를 유지한다 */
function fmtAxis(p) {
  if (p == null || Number.isNaN(p)) return "—";
  const a = Math.abs(p);
  if (a >= 1000) return (p / 1000).toFixed(a >= 100000 ? 0 : 2) + "K";
  return p.toFixed(a >= 100 ? 0 : a >= 1 ? 2 : 4);
}

function fmtTime(ms, interval) {
  const d = new Date(ms);
  const p2 = (n) => String(n).padStart(2, "0");
  if (interval === "1d" || interval === "1w") return `${d.getFullYear()}.${p2(d.getMonth() + 1)}.${p2(d.getDate())}`;
  return `${p2(d.getMonth() + 1)}/${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
}

function hexA(hex, a) {
  const c = hex.replace("#", "");
  const n = parseInt(c.length === 3 ? c.split("").map(x => x + x).join("") : c, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail || `요청에 실패했습니다 (${res.status})`);
  }
  return res.json();
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

/* ── DOM ────────────────────────────────────────────────────── */
const el = {
  chart: $("chartCanvas"), vol: $("volCanvas"), evidence: $("evidenceCanvas"),
  spark: $("verdictSpark"), obsSpark: $("obsSpark"),
  crosshair: $("crosshairReadout"), tooltip: $("tooltip"), evTip: $("evidenceTip"),
  market: $("marketSelect"), symbol: $("symbolSelect"), custom: $("customSymbol"),
  interval: $("intervalSelect"), loadBtn: $("loadBtn"),
};
const ctx = el.chart.getContext("2d");
const vctx = el.vol.getContext("2d");
const ectx = el.evidence.getContext("2d");

const MARGIN = { top: 16, right: 68, bottom: 26, left: 10 };

/* ── 로딩 ───────────────────────────────────────────────────── */
async function loadMarkets() {
  window.__markets = await api("/markets");
  populateSymbolSelect();
}

function populateSymbolSelect() {
  const list = (window.__markets || {})[state.market] || [];
  el.symbol.innerHTML = list.map(p => `<option value="${p.symbol}">${p.name} (${p.symbol})</option>`).join("");
  if (list.length) state.symbol = list[0].symbol;
}

function symbolDisplayName(market, symbol) {
  const list = (window.__markets || {})[market] || [];
  const hit = list.find(p => p.symbol === symbol);
  return hit ? hit.name : symbol;
}

const SOURCE_LABEL = {
  binance: "BINANCE SPOT · LIVE",
  yahoo: "YAHOO FINANCE · LIVE",
  toss_openapi: "토스증권 OPEN API · LIVE",
  toss_unofficial: "토스증권 · LIVE",
  demo: "샘플 데이터",
};

async function loadAnalysis() {
  const sym = el.custom.value.trim() || state.symbol;
  const btn = $("refreshBtn");
  btn.classList.add("busy");
  el.loadBtn.disabled = true;
  el.loadBtn.textContent = "불러오는 중…";
  try {
    const data = await api(`/analysis?market=${state.market}&symbol=${encodeURIComponent(sym)}&interval=${state.interval}&limit=${state.limit}`);
    state.data = data;
    state.symbol = sym;
    state.view.count = Math.min(160, data.candles.length);
    state.view.start = Math.max(0, data.candles.length - state.view.count);

    const feed = $("feedLabel");
    feed.textContent = SOURCE_LABEL[data.source] || data.source;
    feed.parentElement.classList.toggle("is-demo", data.source === "demo");
    $("updatedAt").textContent = new Date().toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit", second: "2-digit" }) + " 갱신";
    $("noteBanner").textContent = data.note || "";
    renderAll();
    updateWatchState();
  } catch (e) {
    toast("데이터를 불러오지 못했습니다: " + e.message);
  } finally {
    btn.classList.remove("busy");
    el.loadBtn.disabled = false;
    el.loadBtn.textContent = "불러오기";
  }
}

async function loadCustomPatterns() {
  state.customPatterns = await api("/patterns/custom");
  renderCustomPatternMgmt();
}
async function loadWatchlist() {
  state.watchlist = await api("/watchlist");
  renderWatchlist();
  updateWatchState();
}

/* ── 좌표계 ─────────────────────────────────────────────────── */
function plotRect(canvas, m = MARGIN) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  return { x0: m.left, y0: m.top, x1: w - m.right, y1: h - m.bottom,
           w: w - m.left - m.right, h: h - m.top - m.bottom };
}

function visibleSlice() {
  const n = state.data.candles.length;
  const start = clamp(state.view.start, 0, Math.max(0, n - 1));
  return { start, count: clamp(state.view.count, 10, n - start), end: start + clamp(state.view.count, 10, n - start) };
}

function priceRangeForView() {
  const { start, end } = visibleSlice();
  const c = state.data.candles;
  let lo = Infinity, hi = -Infinity;
  for (let i = start; i < end; i++) { lo = Math.min(lo, c[i].l); hi = Math.max(hi, c[i].h); }
  const keys = [];
  if (state.toggles.ema) keys.push("ema9", "ema21", "ema55", "ema200");
  if (state.toggles.vwap) keys.push("vwap_up2", "vwap_dn2");
  if (state.toggles.bb) keys.push("bb_upper", "bb_lower");
  if (state.toggles.fib) keys.push("fib_236", "fib_786");
  const s = state.data.series;
  for (const k of keys) {
    const arr = s[k]; if (!arr) continue;
    for (let i = start; i < end; i++) { const v = arr[i]; if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
  const pad = (hi - lo) * 0.08 || hi * 0.01 || 1;
  return { lo: lo - pad, hi: hi + pad };
}

function makeScales() {
  const rect = plotRect(el.chart);
  const { start, count } = visibleSlice();
  const { lo, hi } = priceRangeForView();
  return {
    rect, start, count, lo, hi, candleW: rect.w / count,
    xOf: (i) => rect.x0 + ((i - start + 0.5) / count) * rect.w,
    yOf: (p) => rect.y0 + (1 - (p - lo) / (hi - lo || 1)) * rect.h,
    idxOfX: (x) => Math.round(start + ((x - rect.x0) / rect.w) * count - 0.5),
  };
}

/* ── 렌더 엔트리 ────────────────────────────────────────────── */
function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  }
}
function paint(context, canvas, fn) {
  const dpr = window.devicePixelRatio || 1;
  context.save();
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  fn();
  context.restore();
}

function renderAll() {
  if (!state.data) return;
  [el.chart, el.vol, el.evidence, el.spark, el.obsSpark].forEach(fitCanvas);
  renderOverview();
  drawChart();
  drawVolume();
  renderPatternList();
}

function line(context, pts, color, width = 1.4, dash = []) {
  if (!pts.length) return;
  context.save();
  context.strokeStyle = color; context.lineWidth = width;
  context.lineJoin = "round"; context.lineCap = "round";
  context.setLineDash(dash);
  context.beginPath();
  let started = false;
  for (const [x, y] of pts) {
    if (y == null || Number.isNaN(y)) { started = false; continue; }
    if (!started) { context.moveTo(x, y); started = true; } else context.lineTo(x, y);
  }
  context.stroke(); context.restore();
}

const seriesPts = (sc, arr) => {
  const pts = [];
  for (let i = sc.start; i < sc.start + sc.count; i++) pts.push([sc.xOf(i), arr[i] == null ? null : sc.yOf(arr[i])]);
  return pts;
};

/* ══════════════════ 개요 화면 ══════════════════ */
const VERDICT = {
  strong_buy:  { word: "적극 매수", tone: C.bull, pill: "bull", note: "복수 근거가 매수 방향으로 정렬" },
  normal_buy:  { word: "매수 우위", tone: C.bull, pill: "bull", note: "매수 근거가 기준선을 넘음" },
  strong_sell: { word: "적극 매도", tone: C.bear, pill: "bear", note: "복수 근거가 매도 방향으로 정렬" },
  normal_sell: { word: "매도 우위", tone: C.bear, pill: "bear", note: "매도 근거가 기준선을 넘음" },
  sideways:    { word: "관망",      tone: C.ink3, pill: "",     note: "EMA 간격이 좁아 방향성 판단을 보류" },
  monitor:     { word: "관망",      tone: C.ink3, pill: "",     note: "점수 기준(5점)을 만족하는 단방향 신호가 없음" },
};

const TF_LABEL = { "15m": "15분", "1h": "1시간", "4h": "4시간", "1d": "1일", "1w": "1주" };

function renderOverview() {
  const d = state.data, dash = d.dashboard, s = d.series;
  const last = d.candles.length - 1;
  const v = VERDICT[dash.signal] || VERDICT.monitor;
  const topScore = Math.max(dash.buy_score, dash.sell_score);

  // 판정 히어로
  const card = document.querySelector(".verdict-card");
  card.style.setProperty("--verdict-tone", v.tone);
  $("verdictTf").textContent = TF_LABEL[state.interval] || state.interval;
  $("verdictWord").textContent = v.word;
  const regime = dash.strong_trend ? "추세 구간" : "횡보 구간";
  const rfState = d.range_filter.upward[last] ? "상승 유지" : d.range_filter.downward[last] ? "하락 유지" : "중립 유지";
  $("verdictSub").textContent = `신뢰 점수 ${topScore} / 22 · ${regime} · Range Filter ${rfState}`;

  $("priceLabel").textContent = `${state.symbol} · 현재 종가`;
  $("priceValue").textContent = fmtPrice(dash.price);
  const prev = d.candles[last - 1]?.c;
  const deltaEl = $("priceDelta");
  if (prev) {
    const pct = (dash.price - prev) / prev * 100;
    deltaEl.textContent = `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}% · ${symbolDisplayName(state.market, state.symbol)}`;
    deltaEl.className = "delta " + (pct > 0 ? "up" : pct < 0 ? "down" : "");
  } else { deltaEl.textContent = "—"; deltaEl.className = "delta"; }

  // 종합 신호
  const sigCard = document.querySelector(".signal-card");
  sigCard.style.setProperty("--signal-tone", v.tone);
  $("signalWord").textContent = v.word;
  $("signalNote").textContent = v.note + "입니다.";
  $("signalPill").textContent = dash.signal === "monitor" || dash.signal === "sideways" ? "방향 대기" : "신호 발생";
  $("signalPill").className = "regime-pill " + v.pill;
  $("gaugeScore").textContent = topScore;
  $("gaugeFill").style.strokeDashoffset = String(327 * (1 - clamp(topScore / 22, 0, 1)));
  $("buyScoreNum").textContent = dash.buy_score;
  $("sellScoreNum").textContent = dash.sell_score;
  $("buyScoreBar").style.width = `${clamp(dash.buy_score / 22, 0, 1) * 100}%`;
  $("sellScoreBar").style.width = `${clamp(dash.sell_score / 22, 0, 1) * 100}%`;
  $("scoreFine").textContent = `22점 만점 · 매수 ${dash.buy_score} · 매도 ${dash.sell_score} · ${regime} 필터 적용 후 점수입니다.`;

  $("regimePill").textContent = regime;
  $("regimePill").className = "regime-pill " + (dash.strong_trend ? "warn" : "");
  $("evidenceTitle").textContent = `${state.symbol} 시장 구조`;

  // 가격 장부
  const hasBuy = dash.signal === "strong_buy" || dash.signal === "normal_buy";
  const hasSell = dash.signal === "strong_sell" || dash.signal === "normal_sell";
  const rows = [
    { dot: C.ink2, label: "현재 가격", value: fmtPrice(dash.price) },
    { dot: C.vwap, label: "VWAP 기준", value: fmtPrice(s.vwap[last]) },
    { dot: C.ema,  label: "Fib. 지지 (0.618)", value: fmtPrice(s.fib_618[last]) },
    { dot: C.bear, label: "Fib. 저항 (0.382)", value: fmtPrice(s.fib_382[last]) },
    { dot: C.warn, label: "TP 제안", value: hasBuy ? fmtPrice(dash.tp_buy) : hasSell ? fmtPrice(dash.tp_sell) : "—", muted: !hasBuy && !hasSell, gap: true },
    { dot: C.bear, label: "SL 제안", value: hasBuy ? fmtPrice(dash.sl_buy) : hasSell ? fmtPrice(dash.sl_sell) : "—", muted: !hasBuy && !hasSell },
  ];
  $("levelList").innerHTML = rows.map(r => `
    <div class="level-row${r.gap ? " spacer-top" : ""}">
      <dt><span class="level-dot" style="background:${r.dot}"></span>${r.label}</dt>
      <dd class="${r.muted ? "muted" : ""}">${r.value}</dd>
    </div>`).join("");

  renderTiles(dash, s, last);

  // 관측 카드
  const volRatio = dash.vol_ratio || 0;
  const layers = Object.entries(state.toggles).filter(([k, val]) => val && ["ema","vwap","bb","fib","rf","fbb"].includes(k)).length;
  $("obsTitle").textContent = `Range Filter ${rfState}`;
  $("obsBody").textContent = `거래량은 20기간 평균의 ${volRatio.toFixed(2)}배이며, 현재 활성 차트 레이어는 ${layers}개입니다.`;

  drawEvidence();
  drawSparkline(el.spark, C[v === VERDICT.strong_buy || v === VERDICT.normal_buy ? "bull" : "ink3"]);
  drawSparkline(el.obsSpark, C.accent);
  updateLayerSummary();
}

const TILE_ICONS = {
  adx: '<path d="M3 17l5-5 4 3 8-9"/><path d="M21 6h-4M21 6v4"/>',
  rsi: '<path d="M3 12h3l2.5-7 3.5 14 3-9 2 4h5"/>',
  obv: '<path d="M4 20V9M10 20V4M16 20v-8M22 20V13"/>',
  rf:  '<path d="M4 8h16M4 16h16"/><path d="M8 12h8"/>',
};

function renderTiles(dash, s, last) {
  const rsi = dash.rsi;
  const rfUp = state.data.range_filter.upward[last], rfDn = state.data.range_filter.downward[last];
  const tiles = [
    { key: "adx", label: "ADX 추세", value: dash.adx.toFixed(1),
      tone: dash.strong_trend ? C.warn : C.ink3, note: dash.strong_trend ? "추세 있음" : "횡보" },
    { key: "rsi", label: "RSI (14)", value: rsi.toFixed(1),
      tone: rsi > 70 ? C.bear : rsi < 30 ? C.bull : C.ink3,
      note: rsi > 70 ? "과매수" : rsi < 30 ? "과매도" : "중립" },
    { key: "obv", label: "OBV 흐름", word: dash.obv_state === "bull_div" ? "매수 다이버전스" : dash.obv_state === "bear_div" ? "매도 다이버전스" : dash.obv_state === "up" ? "상승" : "하락",
      tone: dash.obv_state === "up" || dash.obv_state === "bull_div" ? C.bull : C.bear, note: "20 MA 기준" },
    { key: "rf", label: "Range Filter", word: rfUp ? "상승" : rfDn ? "하락" : "중립",
      tone: rfUp ? C.bull : rfDn ? C.bear : C.ink3, note: `Range Filter ${rfUp ? "상승" : rfDn ? "하락" : "중립"} 유지` },
  ];
  $("tileRow").innerHTML = tiles.map(t => `
    <div class="tile" style="--tile-tone:${t.tone}">
      <div class="tile-icon"><svg viewBox="0 0 24 24" aria-hidden="true">${TILE_ICONS[t.key]}</svg></div>
      <div>
        <p class="tile-label">${t.label}</p>
        <p class="tile-value${t.word ? " is-word" : ""}">${t.word || t.value}</p>
      </div>
      <p class="tile-note">${t.note}</p>
    </div>`).join("");
}

function updateLayerSummary() {
  const keys = ["ema", "vwap", "bb", "fib", "rf", "fbb"];
  const on = keys.filter(k => state.toggles[k]);
  $("layerCount").textContent = `${on.length}/${keys.length}`;
  $("layerSummary").textContent = `${on.length}개 레이어 활성`;
}

/* 개요 미니 차트 — 가격 라인 + 활성 레이어, 크로스헤어와 툴팁 포함 */
function evidenceGeom() {
  const rect = plotRect(el.evidence, { top: 16, right: 64, bottom: 24, left: 8 });
  const d = state.data, n = d.candles.length;
  const count = Math.min(n, 180);
  const start = n - count;
  const s = d.series;
  let lo = Infinity, hi = -Infinity;
  for (let i = start; i < n; i++) {
    lo = Math.min(lo, d.candles[i].c); hi = Math.max(hi, d.candles[i].c);
    if (state.toggles.bb) { const u = s.bb_upper[i], l = s.bb_lower[i]; if (u != null) hi = Math.max(hi, u); if (l != null) lo = Math.min(lo, l); }
    if (state.toggles.vwap) { const v = s.vwap[i]; if (v != null) { hi = Math.max(hi, v); lo = Math.min(lo, v); } }
  }
  const pad = (hi - lo) * 0.12 || 1;
  lo -= pad; hi += pad;
  return { rect, start, count, n, lo, hi,
    xOf: (i) => rect.x0 + ((i - start) / (count - 1)) * rect.w,
    yOf: (p) => rect.y0 + (1 - (p - lo) / (hi - lo || 1)) * rect.h,
    idxOfX: (x) => clamp(Math.round(start + ((x - rect.x0) / rect.w) * (count - 1)), start, n - 1) };
}

function drawEvidence() {
  const d = state.data, s = d.series;
  paint(ectx, el.evidence, () => {
    const g = evidenceGeom();
    const { rect } = g;

    // 가로 그리드 + 가격축
    ectx.font = "10px 'IBM Plex Mono', monospace";
    for (let i = 0; i <= 4; i++) {
      const p = g.lo + (g.hi - g.lo) * (i / 4), y = g.yOf(p);
      ectx.strokeStyle = C.gridLine; ectx.lineWidth = 1;
      ectx.setLineDash([2, 4]);
      ectx.beginPath(); ectx.moveTo(rect.x0, y); ectx.lineTo(rect.x1, y); ectx.stroke();
      ectx.setLineDash([]);
      ectx.fillStyle = C.ink3; ectx.fillText(fmtAxis(p), rect.x1 + 8, y + 3);
    }

    const pts = (arr) => { const o = []; for (let i = g.start; i < g.n; i++) o.push([g.xOf(i), arr[i] == null ? null : g.yOf(arr[i])]); return o; };

    // 밴드는 채움으로 후퇴시킨다
    if (state.toggles.bb) {
      const up = pts(s.bb_upper), dn = pts(s.bb_lower);
      ectx.save(); ectx.beginPath();
      let started = false;
      for (const [x, y] of up) { if (y == null) continue; started ? ectx.lineTo(x, y) : (ectx.moveTo(x, y), started = true); }
      for (let i = dn.length - 1; i >= 0; i--) { const [x, y] = dn[i]; if (y != null) ectx.lineTo(x, y); }
      ectx.closePath(); ectx.fillStyle = hexA(C.band, 0.16); ectx.fill(); ectx.restore();
    }
    if (state.toggles.vwap) line(ectx, pts(s.vwap), hexA(C.vwap, 0.9), 1.4);
    if (state.toggles.ema)  line(ectx, pts(s.ema21), hexA(C.ema, 0.9), 1.4);
    if (state.toggles.rf)   line(ectx, pts(d.range_filter.filter), hexA(C.rf, 0.9), 1.4);

    // 가격 — 면 + 선, 끝점 강조
    const price = [];
    for (let i = g.start; i < g.n; i++) price.push([g.xOf(i), g.yOf(d.candles[i].c)]);
    ectx.save(); ectx.beginPath();
    ectx.moveTo(price[0][0], rect.y1);
    price.forEach(([x, y]) => ectx.lineTo(x, y));
    ectx.lineTo(price[price.length - 1][0], rect.y1); ectx.closePath();
    const grad = ectx.createLinearGradient(0, rect.y0, 0, rect.y1);
    grad.addColorStop(0, hexA(C.ink, 0.16)); grad.addColorStop(1, hexA(C.ink, 0));
    ectx.fillStyle = grad; ectx.fill(); ectx.restore();
    line(ectx, price, C.ink, 2);

    const [lx, ly] = price[price.length - 1];
    ectx.fillStyle = C.ink; ectx.beginPath(); ectx.arc(lx, ly, 3.5, 0, Math.PI * 2); ectx.fill();
    ectx.fillStyle = C.ink3; ectx.font = "10px 'IBM Plex Mono', monospace";
    ectx.fillText("현재", lx - 12, rect.y0 - 2);

    // 시간축
    for (const f of [0, 0.5, 1]) {
      const idx = Math.round(g.start + (g.count - 1) * f);
      const x = clamp(g.xOf(idx), rect.x0, rect.x1 - 60);
      ectx.fillStyle = C.ink3;
      ectx.fillText(fmtTime(d.candles[idx].t, state.interval), f === 0 ? rect.x0 : x, rect.y1 + 15);
    }

    // 크로스헤어
    if (state.evHover != null) {
      const i = state.evHover, x = g.xOf(i), y = g.yOf(d.candles[i].c);
      ectx.save();
      ectx.strokeStyle = hexA(C.ink, 0.28); ectx.setLineDash([3, 3]); ectx.lineWidth = 1;
      ectx.beginPath(); ectx.moveTo(x, rect.y0); ectx.lineTo(x, rect.y1); ectx.stroke();
      ectx.restore();
      ectx.fillStyle = C.ink; ectx.beginPath(); ectx.arc(x, y, 4, 0, Math.PI * 2); ectx.fill();
      ectx.strokeStyle = "#0D1519"; ectx.lineWidth = 2; ectx.stroke();
    }
  });
}

function drawSparkline(canvas, color) {
  if (!canvas || !canvas.clientWidth) return;
  const d = state.data;
  const cx = canvas.getContext("2d");
  paint(cx, canvas, () => {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const n = Math.min(d.candles.length, 120);
    const arr = d.candles.slice(-n).map(c => c.c);
    const lo = Math.min(...arr), hi = Math.max(...arr);
    cx.beginPath();
    arr.forEach((v, i) => {
      const x = (i / (n - 1)) * w;
      const y = h - 6 - ((v - lo) / (hi - lo || 1)) * (h * 0.62);
      i === 0 ? cx.moveTo(x, y) : cx.lineTo(x, y);
    });
    cx.lineTo(w, h); cx.lineTo(0, h); cx.closePath();
    const grad = cx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, hexA(color, 0.5)); grad.addColorStop(1, hexA(color, 0));
    cx.fillStyle = grad; cx.fill();
  });
}

/* ══════════════════ 메인 차트 ══════════════════ */
function drawChart() {
  const d = state.data, s = d.series;
  paint(ctx, el.chart, () => {
    const sc = makeScales();
    const { rect, start, count, lo, hi } = sc;

    ctx.font = "10.5px 'IBM Plex Mono', monospace";
    for (let g = 0; g <= 6; g++) {
      const p = lo + (hi - lo) * (g / 6), y = sc.yOf(p);
      ctx.strokeStyle = C.gridLine; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(rect.x0, y); ctx.lineTo(rect.x1, y); ctx.stroke();
      ctx.fillStyle = C.ink3; ctx.fillText(fmtAxis(p), rect.x1 + 8, y + 3);
    }
    for (let t = 0; t <= 6; t++) {
      const idx = clamp(Math.round(start + (count - 1) * (t / 6)), start, start + count - 1);
      const cd = d.candles[idx]; if (!cd) continue;
      ctx.fillStyle = C.ink3;
      ctx.fillText(fmtTime(cd.t, state.interval), clamp(sc.xOf(idx) - 30, rect.x0, rect.x1 - 74), rect.y1 + 17);
    }

    for (let i = start; i < start + count; i++) {
      if (d.events.strong_buy[i]) shade(sc, i, hexA(C.bull, 0.16));
      else if (d.events.normal_buy[i]) shade(sc, i, hexA(C.bull, 0.08));
      else if (d.events.strong_sell[i]) shade(sc, i, hexA(C.bear, 0.16));
      else if (d.events.normal_sell[i]) shade(sc, i, hexA(C.bear, 0.08));
    }

    if (state.toggles.fib) drawFib(sc, s);
    if (state.toggles.bb) drawBand(sc, s.bb_upper, s.bb_lower, C.band, 0.1, s.bb_mid);
    if (state.toggles.vwap) {
      drawBand(sc, s.vwap_up2, s.vwap_dn2, C.vwap, 0.055);
      line(ctx, seriesPts(sc, s.vwap_up1), hexA(C.vwap, 0.32), 1);
      line(ctx, seriesPts(sc, s.vwap_dn1), hexA(C.vwap, 0.32), 1);
      line(ctx, seriesPts(sc, s.vwap), C.vwap, 1.8);
    }
    if (state.toggles.fbb) {
      const f = d.fibonacci_bb;
      drawBand(sc, f.u236, f.l236, C.fbb, 0.05);
      line(ctx, seriesPts(sc, f.basis), C.fbb, 1.6);
      line(ctx, seriesPts(sc, f.u1000), hexA(C.fbb, 0.55), 1.2);
      line(ctx, seriesPts(sc, f.l1000), hexA(C.fbb, 0.55), 1.2);
    }
    if (state.toggles.rf) {
      const rf = d.range_filter;
      line(ctx, seriesPts(sc, rf.hi_band), hexA(C.rf, 0.3), 1);
      line(ctx, seriesPts(sc, rf.lo_band), hexA(C.rf, 0.3), 1);
      line(ctx, seriesPts(sc, rf.filter), C.rf, 1.8);
    }
    // EMA는 하나의 계열 — 색 대신 굵기/투명도로 구분한다
    if (state.toggles.ema) {
      line(ctx, seriesPts(sc, s.ema9), hexA(C.ema, 0.45), 1);
      line(ctx, seriesPts(sc, s.ema21), hexA(C.ema, 0.68), 1.2);
      line(ctx, seriesPts(sc, s.ema55), hexA(C.ema, 0.86), 1.4);
      line(ctx, seriesPts(sc, s.ema200), C.ema, 2);
    }

    drawCandles(sc, d.candles);

    const markers = [];
    if (state.toggles.chartPat) drawChartPatterns(sc, d.chart_patterns, markers);
    if (state.toggles.candlePat) drawCandlePatterns(sc, d.candle_patterns, markers);
    if (state.toggles.customPat) drawCustomMatches(sc, d.custom_matches, markers);
    drawScoreMarkers(sc, d.events, d.series, markers);
    state.markersCache = markers;

    drawSelection(sc);
    drawCrosshair(sc);
  });
}

function shade(sc, i, color) {
  ctx.fillStyle = color;
  ctx.fillRect(sc.xOf(i) - sc.candleW / 2, sc.rect.y0, sc.candleW, sc.rect.h);
}

function drawBand(sc, upArr, dnArr, color, alpha, midArr) {
  const up = seriesPts(sc, upArr), dn = seriesPts(sc, dnArr);
  ctx.save(); ctx.beginPath();
  let started = false;
  for (const [x, y] of up) { if (y == null) continue; started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true); }
  for (let i = dn.length - 1; i >= 0; i--) { const [x, y] = dn[i]; if (y != null) ctx.lineTo(x, y); }
  ctx.closePath(); ctx.fillStyle = hexA(color, alpha); ctx.fill(); ctx.restore();
  line(ctx, up, hexA(color, 0.5), 1);
  line(ctx, dn, hexA(color, 0.5), 1);
  if (midArr) line(ctx, seriesPts(sc, midArr), hexA(color, 0.65), 1, [3, 3]);
}

function drawFib(sc, s) {
  const levels = [["fib_786", "0.786"], ["fib_618", "0.618"], ["fib_500", "0.5"], ["fib_382", "0.382"], ["fib_236", "0.236"]];
  ctx.font = "9.5px 'IBM Plex Mono', monospace";
  for (const [key, label] of levels) {
    line(ctx, seriesPts(sc, s[key]), hexA(C.fib, 0.4), 1, [2, 3]);
    const v = s[key][sc.start + sc.count - 1];
    if (v != null) { ctx.fillStyle = hexA(C.fib, 0.75); ctx.fillText(label, sc.rect.x0 + 4, sc.yOf(v) - 3); }
  }
}

function drawCandles(sc, candles) {
  for (let i = sc.start; i < sc.start + sc.count; i++) {
    const cd = candles[i]; if (!cd) continue;
    const x = sc.xOf(i), bull = cd.c >= cd.o, color = bull ? C.bull : C.bear;
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, sc.yOf(cd.h)); ctx.lineTo(x, sc.yOf(cd.l)); ctx.stroke();
    const top = sc.yOf(Math.max(cd.o, cd.c)), bot = sc.yOf(Math.min(cd.o, cd.c));
    const bw = Math.max(1, sc.candleW * 0.6);
    ctx.fillRect(x - bw / 2, top, bw, Math.max(1, bot - top));
  }
}

const PAT_TONE = { bullish: C.bull, bearish: C.bear, neutral: "#A98BE0" };

function drawChartPatterns(sc, patterns, markers) {
  const end = sc.start + sc.count;
  ctx.font = "10.5px 'IBM Plex Sans KR', sans-serif";
  for (const p of patterns) {
    if (p.end_idx < sc.start || p.start_idx > end) continue;
    const color = PAT_TONE[p.direction] || PAT_TONE.neutral;
    ctx.save();
    ctx.strokeStyle = hexA(color, 0.8); ctx.lineWidth = 1.3; ctx.setLineDash([4, 3]);
    ctx.beginPath();
    p.points.forEach((pt, i) => { const x = sc.xOf(pt.idx), y = sc.yOf(pt.price); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke(); ctx.restore();
    for (const pt of p.points) {
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(sc.xOf(pt.idx), sc.yOf(pt.price), 2.6, 0, Math.PI * 2); ctx.fill();
    }
    const lastPt = p.points[p.points.length - 1];
    const lx = sc.xOf(lastPt.idx), ly = sc.yOf(lastPt.price);
    ctx.fillStyle = color; ctx.fillText(p.name_kr, lx + 6, ly - 6);
    markers.push({ x0: sc.xOf(p.start_idx) - 4, x1: lx + 4, y0: sc.rect.y0, y1: sc.rect.y1,
      tooltip: `${p.name_kr}\n신뢰도 ${fmtPct(p.confidence)}${p.target ? `\n목표가 ${fmtPrice(p.target)}` : ""}\n${p.note || ""}` });
  }
}

function drawCandlePatterns(sc, patterns, markers) {
  const end = sc.start + sc.count, d = state.data;
  for (const p of patterns) {
    if (p.index < sc.start || p.index >= end) continue;
    const cd = d.candles[p.index], x = sc.xOf(p.index);
    const down = p.direction === "bearish";
    const y = down ? sc.yOf(cd.h) - 9 : sc.yOf(cd.l) + 9;
    ctx.fillStyle = PAT_TONE[p.direction] || C.warn;
    ctx.beginPath();
    if (down) { ctx.moveTo(x, y - 4.5); ctx.lineTo(x - 4.5, y + 3.5); ctx.lineTo(x + 4.5, y + 3.5); }
    else { ctx.moveTo(x, y + 4.5); ctx.lineTo(x - 4.5, y - 3.5); ctx.lineTo(x + 4.5, y - 3.5); }
    ctx.closePath(); ctx.fill();
    markers.push({ x0: x - 6, x1: x + 6, y0: y - 8, y1: y + 8, tooltip: `${p.name_kr}\n강도 ${fmtPct(p.strength)}` });
  }
}

function drawCustomMatches(sc, matches, markers) {
  const end = sc.start + sc.count;
  ctx.font = "10.5px 'IBM Plex Sans KR', sans-serif";
  for (const m of matches) {
    const color = m.direction === "bullish" ? C.rf : m.direction === "bearish" ? C.warn : "#A98BE0";
    if (m.kind === "custom_rule") {
      if (m.index < sc.start || m.index >= end) continue;
      const x = sc.xOf(m.index);
      ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 1.8;
      ctx.beginPath(); ctx.moveTo(x, sc.rect.y1); ctx.lineTo(x, sc.rect.y1 - 13); ctx.stroke();
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, sc.rect.y1 - 13, 2.6, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      markers.push({ x0: x - 5, x1: x + 5, y0: sc.rect.y1 - 19, y1: sc.rect.y1, tooltip: `${m.name}\n커스텀 규칙 일치` });
    } else {
      if (m.end_idx < sc.start || m.start_idx > end) continue;
      const x0 = sc.xOf(m.start_idx), x1 = sc.xOf(m.end_idx);
      ctx.save();
      ctx.fillStyle = hexA(color, 0.07); ctx.fillRect(x0, sc.rect.y0, x1 - x0, sc.rect.h);
      ctx.strokeStyle = hexA(color, 0.5); ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
      ctx.strokeRect(x0, sc.rect.y0, x1 - x0, sc.rect.h);
      ctx.restore();
      ctx.fillStyle = color;
      ctx.fillText(`${m.name} ${Math.round(m.score * 100)}%`, x0 + 4, sc.rect.y0 + 12);
      markers.push({ x0, x1, y0: sc.rect.y0, y1: sc.rect.y0 + 18, tooltip: `${m.name}\n모양 유사도 ${Math.round(m.score * 100)}%` });
    }
  }
}

function drawScoreMarkers(sc, events, series, markers) {
  const end = sc.start + sc.count, d = state.data;
  ctx.font = "9.5px 'IBM Plex Mono', monospace";
  for (let i = sc.start; i < end; i++) {
    const cd = d.candles[i];
    if (events.strong_buy[i] || events.normal_buy[i]) {
      const strong = events.strong_buy[i], x = sc.xOf(i), y = sc.yOf(cd.l) + (strong ? 22 : 15);
      arrowLabel(x, y, "up", C.bull, `${series.buy_score[i]}`, strong);
      markers.push({ x0: x - 13, x1: x + 13, y0: y - 6, y1: y + 13,
        tooltip: `${strong ? "적극 매수" : "매수 우위"} · ${series.buy_score[i]}점` });
    }
    if (events.strong_sell[i] || events.normal_sell[i]) {
      const strong = events.strong_sell[i], x = sc.xOf(i), y = sc.yOf(cd.h) - (strong ? 22 : 15);
      arrowLabel(x, y, "down", C.bear, `${series.sell_score[i]}`, strong);
      markers.push({ x0: x - 13, x1: x + 13, y0: y - 13, y1: y + 6,
        tooltip: `${strong ? "적극 매도" : "매도 우위"} · ${series.sell_score[i]}점` });
    }
  }
}

function arrowLabel(x, y, dir, color, text, strong) {
  ctx.save();
  ctx.fillStyle = strong ? color : hexA(color, 0.65);
  ctx.beginPath();
  if (dir === "up") { ctx.moveTo(x, y - 5.5); ctx.lineTo(x - 5, y + 4); ctx.lineTo(x + 5, y + 4); }
  else { ctx.moveTo(x, y + 5.5); ctx.lineTo(x - 5, y - 4); ctx.lineTo(x + 5, y - 4); }
  ctx.closePath(); ctx.fill();
  ctx.fillStyle = hexA(color, 0.9); ctx.fillText(text, x + 7, y + 3);
  ctx.restore();
}

function drawSelection(sc) {
  if (state.selecting) {
    const { x0, x1 } = state.selecting;
    ctx.save();
    ctx.fillStyle = hexA(C.accent, 0.1);
    ctx.fillRect(Math.min(x0, x1), sc.rect.y0, Math.abs(x1 - x0), sc.rect.h);
    ctx.strokeStyle = C.accent; ctx.setLineDash([4, 3]); ctx.lineWidth = 1.2;
    ctx.strokeRect(Math.min(x0, x1), sc.rect.y0, Math.abs(x1 - x0), sc.rect.h);
    ctx.restore();
  } else if (state.selectedRange) {
    const { start_idx, end_idx } = state.selectedRange;
    if (end_idx >= sc.start && start_idx <= sc.start + sc.count) {
      ctx.save();
      ctx.strokeStyle = C.accent; ctx.lineWidth = 1.4; ctx.setLineDash([4, 3]);
      ctx.strokeRect(sc.xOf(start_idx), sc.rect.y0, sc.xOf(end_idx) - sc.xOf(start_idx), sc.rect.h);
      ctx.restore();
    }
  }
}

function drawCrosshair(sc) {
  if (!state.hover) { el.crosshair.style.display = "none"; return; }
  const { index } = state.hover, d = state.data;
  if (index < sc.start || index >= sc.start + sc.count) return;
  const cd = d.candles[index]; if (!cd) return;
  const x = sc.xOf(index);
  ctx.save();
  ctx.strokeStyle = hexA(C.ink, 0.25); ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, sc.rect.y0); ctx.lineTo(x, sc.rect.y1); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(sc.rect.x0, state.hover.y); ctx.lineTo(sc.rect.x1, state.hover.y); ctx.stroke();
  ctx.restore();

  const s = d.series;
  el.crosshair.style.display = "block";
  el.crosshair.textContent =
    `${fmtTime(cd.t, state.interval)}\n` +
    `O ${fmtPrice(cd.o)}  H ${fmtPrice(cd.h)}\nL ${fmtPrice(cd.l)}  C ${fmtPrice(cd.c)}\n` +
    `RSI ${s.rsi[index]?.toFixed(1) ?? "—"}  ADX ${s.adx[index]?.toFixed(1) ?? "—"}\n` +
    `매수 ${s.buy_score[index] ?? 0} · 매도 ${s.sell_score[index] ?? 0}`;
}

function drawVolume() {
  const d = state.data;
  paint(vctx, el.vol, () => {
    const sc = makeScales();
    const y1 = el.vol.clientHeight - 6, y0 = 16;
    let maxV = 0;
    for (let i = sc.start; i < sc.start + sc.count; i++) maxV = Math.max(maxV, d.candles[i].v);
    maxV = maxV || 1;
    for (let i = sc.start; i < sc.start + sc.count; i++) {
      const cd = d.candles[i], x = sc.xOf(i), h = (cd.v / maxV) * (y1 - y0);
      vctx.fillStyle = hexA(cd.c >= cd.o ? C.bull : C.bear, d.events.vol_spike[i] ? 0.85 : 0.42);
      const bw = Math.max(1, sc.candleW * 0.6);
      vctx.fillRect(x - bw / 2, y1 - h, bw, h);
    }
    vctx.fillStyle = C.ink3; vctx.font = "10px 'IBM Plex Mono', monospace";
    vctx.fillText("VOLUME", MARGIN.left, 11);
  });
}

/* ══════════════════ 사이드 패널 ══════════════════ */
const MARKET_LABEL = { crypto: "코인", us: "미국", kr: "한국" };
const X_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

/* 배지는 종목명 첫 글자를 쓴다 — 국내주식 종목코드(005930)의 "0"보다
   "삼"이 훨씬 알아보기 쉽다. 프리셋에 없는 종목은 심볼 첫 글자로 넘어간다. */
function watchBadge(w) {
  const name = symbolDisplayName(w.market, w.symbol);
  return (name[0] || w.symbol[0] || "?").toUpperCase();
}

function renderWatchlist() {
  $("watchCount").textContent = state.watchlist.length;
  if (!state.watchlist.length) {
    $("watchlistList").innerHTML = `<p class="empty-msg">관심종목이 없습니다</p>`;
    return;
  }
  $("watchlistList").innerHTML = state.watchlist.map(w => `
    <div class="watch-row" data-market="${w.market}" data-symbol="${w.symbol}" role="button" tabindex="0">
      <span class="watch-badge">${watchBadge(w)}</span>
      <span class="watch-meta">
        <strong>${w.symbol}</strong>
        <span>${MARKET_LABEL[w.market] || w.market} · ${symbolDisplayName(w.market, w.symbol)}</span>
      </span>
      <button class="del-btn" data-market="${w.market}" data-symbol="${w.symbol}" aria-label="${w.symbol} 삭제">${X_ICON}</button>
    </div>`).join("");

  $("watchlistList").querySelectorAll(".watch-row").forEach(row => {
    const go = (e) => {
      if (e.target.closest(".del-btn")) return;
      const { market, symbol } = row.dataset;
      el.market.value = market; state.market = market;
      populateSymbolSelect();
      el.custom.value = symbol;
      if ([...el.symbol.options].some(o => o.value === symbol)) el.symbol.value = symbol;
      loadAnalysis();
    };
    row.addEventListener("click", go);
    row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(e); } });
  });
  $("watchlistList").querySelectorAll(".del-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const { market, symbol } = btn.dataset;
      await removeWatch(market, symbol);
      toast(`${symbol} 을(를) 관심종목에서 뺐습니다.`);
    });
  });
}

function updateWatchState() {
  document.querySelectorAll(".watch-row").forEach(r => {
    r.classList.toggle("current", r.dataset.market === state.market && r.dataset.symbol === state.symbol);
  });
}

const isWatched = (market, symbol) => state.watchlist.some(w => w.market === market && w.symbol === symbol);

async function addWatch(market, symbol) {
  await api("/watchlist", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ market, symbol }) });
  await loadWatchlist();
}
async function removeWatch(market, symbol) {
  await api(`/watchlist?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}`, { method: "DELETE" });
  await loadWatchlist();
}

/* ══════════════════ 종목 추가 모달 ══════════════════ */
const PICKER_MARKET_LABEL = { crypto: "코인", kr: "국내주식", us: "해외주식" };
state.pickerMarket = "crypto";

function openPicker() {
  $("pickerModal").classList.remove("hidden");
  $("pickerError").textContent = "";
  $("pickerSearch").value = "";
  renderPicker();
  $("pickerSearch").focus();
}
function closePicker() { $("pickerModal").classList.add("hidden"); }

function pickerCandidates() {
  const list = (window.__markets || {})[state.pickerMarket] || [];
  const q = $("pickerSearch").value.trim().toLowerCase();
  if (!q) return list;
  return list.filter(p => {
    const hay = [p.symbol, p.name, ...(p.aliases || [])].join(" ").toLowerCase();
    return hay.includes(q);
  });
}

function renderPicker() {
  const market = state.pickerMarket;
  const rows = pickerCandidates();
  const box = $("pickerResults");
  if (!rows.length) {
    box.innerHTML = `<p class="empty-msg">검색 결과가 없습니다.<br>아래에서 티커를 직접 입력해 추가할 수 있습니다.</p>`;
    return;
  }
  box.innerHTML = rows.map(p => {
    const on = isWatched(market, p.symbol);
    return `<button class="picker-row ${on ? "added" : ""}" data-symbol="${p.symbol}">
      <span class="pr-meta">
        <span class="pr-name">${p.name}</span>
        <span class="pr-sym">${p.symbol}${p.aliases && p.aliases.length ? " · " + p.aliases[0] : ""}</span>
      </span>
      <span class="pr-action">${on ? "담김 ✓" : "＋ 담기"}</span>
    </button>`;
  }).join("");

  box.querySelectorAll(".picker-row").forEach(row => {
    row.addEventListener("click", async () => {
      const symbol = row.dataset.symbol;
      if (isWatched(market, symbol)) {
        await removeWatch(market, symbol);
        toast(`${symbol} 을(를) 관심종목에서 뺐습니다.`);
      } else {
        await addWatch(market, symbol);
        toast(`${symbol} 을(를) 관심종목에 담았습니다.`);
      }
      renderPicker();
    });
  });
}

$("openPickerBtn").addEventListener("click", openPicker);
$("closePickerBtn").addEventListener("click", closePicker);
$("pickerModal").addEventListener("click", (e) => { if (e.target === $("pickerModal")) closePicker(); });
$("pickerSearch").addEventListener("input", renderPicker);

document.querySelectorAll("#pickerTabs .tab").forEach(btn => {
  btn.addEventListener("click", () => {
    state.pickerMarket = btn.dataset.pmarket;
    document.querySelectorAll("#pickerTabs .tab").forEach(b => b.classList.toggle("active", b === btn));
    $("pickerError").textContent = "";
    renderPicker();
  });
});

/* 목록에 없는 종목을 티커로 직접 추가 */
async function addDirectSymbol() {
  const raw = $("directSymbol").value.trim();
  const err = $("pickerError");
  err.textContent = "";
  if (!raw) { err.textContent = "티커를 입력하세요."; return; }

  const market = state.pickerMarket;
  let symbol = market === "kr" ? raw : raw.toUpperCase();
  if (market === "crypto" && !symbol.endsWith("USDT")) symbol += "USDT";
  if (market === "kr" && !/^\d{6}$/.test(symbol)) {
    err.textContent = "국내주식은 6자리 종목코드로 입력하세요 (예: 005930).";
    return;
  }
  if (isWatched(market, symbol)) { err.textContent = "이미 관심종목에 있습니다."; return; }

  // 실제로 데이터를 받아올 수 있는 종목인지 먼저 확인한다 — 오타를 담아두면
  // 나중에 눌렀을 때 데모 데이터가 떠서 혼란스럽기 때문.
  const btn = $("directAddBtn");
  btn.disabled = true; btn.textContent = "확인 중…";
  try {
    const probe = await api(`/candles?market=${market}&symbol=${encodeURIComponent(symbol)}&interval=1d&limit=60`);
    if (probe.source === "demo") {
      err.textContent = `${symbol} 의 시세를 가져오지 못했습니다. 티커를 확인해주세요.`;
      return;
    }
    await addWatch(market, symbol);
    $("directSymbol").value = "";
    renderPicker();
    toast(`${symbol} 을(를) 관심종목에 담았습니다.`);
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = "추가";
  }
}
$("directAddBtn").addEventListener("click", addDirectSymbol);
$("directSymbol").addEventListener("keydown", (e) => { if (e.key === "Enter") addDirectSymbol(); });

function renderPatternList() {
  const d = state.data;
  let items = [];
  if (state.activeListTab === "chart") {
    items = d.chart_patterns.map(p => ({ title: p.name_kr, meta: fmtPct(p.confidence),
      sub: `${p.note || ""}${p.target ? ` · 목표가 ${fmtPrice(p.target)}` : ""}`, direction: p.direction, idx: p.end_idx }));
  } else if (state.activeListTab === "candle") {
    items = d.candle_patterns.map(p => ({ title: p.name_kr, meta: fmtPct(p.strength),
      sub: fmtTime(d.candles[p.index].t, state.interval), direction: p.direction, idx: p.index }));
  } else {
    items = d.custom_matches.map(m => ({ title: m.name,
      meta: m.kind === "custom_shape" ? `${Math.round(m.score * 100)}%` : "일치",
      sub: m.kind === "custom_shape" ? "모양 유사도" : "규칙 매칭",
      direction: m.direction, idx: m.kind === "custom_shape" ? m.end_idx : m.index }));
  }
  items.sort((a, b) => b.idx - a.idx);
  const list = $("patternList");
  if (!items.length) { list.innerHTML = `<p class="empty-msg">탐지된 패턴이 없습니다</p>`; return; }
  list.innerHTML = items.slice(0, 80).map(it => `
    <button class="pattern-item ${it.direction}" data-idx="${it.idx}">
      <span class="pi-head"><span>${it.title}</span><span class="pi-conf">${it.meta}</span></span>
      <span class="pi-sub">${it.sub}</span>
    </button>`).join("");
  list.querySelectorAll(".pattern-item").forEach(node => {
    node.addEventListener("click", () => {
      const idx = parseInt(node.dataset.idx, 10);
      state.view.start = clamp(idx - Math.round(state.view.count * 0.7), 0, Math.max(0, d.candles.length - state.view.count));
      drawChart(); drawVolume();
    });
  });
}

function renderCustomPatternMgmt() {
  const box = $("customPatternMgmt");
  if (!state.customPatterns.length) { box.innerHTML = `<p class="empty-msg">등록된 패턴이 없습니다</p>`; return; }
  box.innerHTML = state.customPatterns.map(p => `
    <div class="custom-pat-row">
      <span class="cp-name"><span class="cp-kind">${p.type === "shape" ? "모양" : "규칙"}</span>${p.name}</span>
      <button class="del-btn" data-id="${p.id}" aria-label="${p.name} 삭제">${X_ICON}</button>
    </div>`).join("");
  box.querySelectorAll(".del-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      await api(`/patterns/custom/${btn.dataset.id}`, { method: "DELETE" });
      await loadCustomPatterns();
      if (state.data) await loadAnalysis();
      toast("패턴을 지웠습니다.");
    });
  });
}

/* ══════════════════ 화면 전환 ══════════════════ */
function switchView(name) {
  state.activeView = name;
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.dataset.view === name));
  $("ledgerSymbol").textContent = state.symbol;
  if (state.data) requestAnimationFrame(renderAll);
}
document.querySelectorAll("[data-view]").forEach(btn => {
  if (btn.tagName !== "BUTTON") return;
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

const CHIP_TARGET = { ledger: "ledger", patterns: "ledger", tpsl: "overview", custom: "ledger", settings: "settings" };
document.querySelectorAll(".chip[data-chip]").forEach(chip => {
  chip.addEventListener("click", () => {
    const kind = chip.dataset.chip;
    switchView(CHIP_TARGET[kind] || "overview");
    if (kind === "patterns") { state.activeListTab = "chart"; syncTabs(); renderPatternList(); }
    if (kind === "custom") { state.activeListTab = "custom"; syncTabs(); renderPatternList(); openBuilder("rule"); }
    if (kind === "tpsl") document.querySelector(".ledger-card")?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
});
function syncTabs() {
  document.querySelectorAll("#patternTabs .tab").forEach(b => b.classList.toggle("active", b.dataset.tab === state.activeListTab));
}
document.querySelectorAll("#patternTabs .tab").forEach(btn => {
  btn.addEventListener("click", () => { state.activeListTab = btn.dataset.tab; syncTabs(); renderPatternList(); });
});

document.querySelectorAll("#tfSegmented button").forEach(btn => {
  btn.addEventListener("click", () => {
    state.interval = btn.dataset.tf;
    el.interval.value = state.interval;
    document.querySelectorAll("#tfSegmented button").forEach(b => b.classList.toggle("active", b === btn));
    loadAnalysis();
  });
});
$("refreshBtn").addEventListener("click", () => loadAnalysis());

/* ══════════════════ 차트 인터랙션 ══════════════════ */
const mx = (e, c) => e.clientX - c.getBoundingClientRect().left;
const my = (e, c) => e.clientY - c.getBoundingClientRect().top;

el.chart.addEventListener("wheel", (e) => {
  if (!state.data) return;
  e.preventDefault();
  const sc = makeScales();
  const mouseIdx = sc.idxOfX(mx(e, el.chart));
  const n = state.data.candles.length;
  const newCount = clamp(Math.round(state.view.count * (e.deltaY > 0 ? 1.15 : 1 / 1.15)), 20, n);
  const ratio = (mouseIdx - state.view.start) / state.view.count;
  state.view.start = clamp(Math.round(mouseIdx - ratio * newCount), 0, Math.max(0, n - newCount));
  state.view.count = newCount;
  drawChart(); drawVolume();
}, { passive: false });

el.chart.addEventListener("mousedown", (e) => {
  if (!state.data) return;
  el.tooltip.classList.add("hidden");
  const x = mx(e, el.chart);
  if (state.selectMode) state.selecting = { x0: x, x1: x };
  else state.drag = { x0: x, viewStart0: state.view.start };
});

window.addEventListener("mousemove", (e) => {
  if (!state.data) return;
  if (state.selecting) { state.selecting.x1 = mx(e, el.chart); drawChart(); return; }
  if (state.drag) {
    const sc = makeScales();
    const n = state.data.candles.length;
    state.view.start = clamp(state.drag.viewStart0 + Math.round((state.drag.x0 - mx(e, el.chart)) / sc.candleW), 0, Math.max(0, n - state.view.count));
    drawChart(); drawVolume();
    return;
  }
  const r = el.chart.getBoundingClientRect();
  const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
  if (inside) {
    const x = mx(e, el.chart), y = my(e, el.chart);
    state.hover = { index: clamp(makeScales().idxOfX(x), 0, state.data.candles.length - 1), y };
    drawChart();
    const hit = state.markersCache.find(m => x >= m.x0 && x <= m.x1 && y >= m.y0 && y <= m.y1);
    if (hit) {
      el.tooltip.textContent = hit.tooltip;
      el.tooltip.style.left = (e.clientX + 14) + "px";
      el.tooltip.style.top = (e.clientY + 14) + "px";
      el.tooltip.classList.remove("hidden");
    } else el.tooltip.classList.add("hidden");
  } else if (state.hover) {
    state.hover = null; el.tooltip.classList.add("hidden"); drawChart();
  }
});

window.addEventListener("mouseup", () => {
  if (state.selecting) { finishSelection(); state.selecting = null; }
  state.drag = null;
});

/* 개요 미니 차트 호버 */
el.evidence.addEventListener("mousemove", (e) => {
  if (!state.data) return;
  const g = evidenceGeom();
  const i = g.idxOfX(mx(e, el.evidence));
  state.evHover = i;
  drawEvidence();
  const d = state.data, cd = d.candles[i], s = d.series;
  const tip = el.evTip;
  tip.textContent = `${fmtTime(cd.t, state.interval)}\n종가 ${fmtPrice(cd.c)}\nVWAP ${fmtPrice(s.vwap[i])}\nRSI ${s.rsi[i]?.toFixed(1) ?? "—"}`;
  tip.classList.remove("hidden");
  const box = el.evidence.getBoundingClientRect();
  const localX = mx(e, el.evidence);
  tip.style.left = clamp(localX + 14, 0, box.width - 150) + "px";
  tip.style.top = clamp(my(e, el.evidence) - 10, 0, box.height - 80) + "px";
});
el.evidence.addEventListener("mouseleave", () => {
  state.evHover = null; el.evTip.classList.add("hidden");
  if (state.data) drawEvidence();
});

function finishSelection() {
  const sc = makeScales();
  const { x0, x1 } = state.selecting;
  const i0 = clamp(sc.idxOfX(Math.min(x0, x1)), 0, state.data.candles.length - 1);
  const i1 = clamp(sc.idxOfX(Math.max(x0, x1)), 0, state.data.candles.length - 1);
  if (i1 - i0 < 3) { state.selectedRange = null; drawChart(); return; }
  state.selectedRange = { start_idx: i0, end_idx: i1 };
  setSelectMode(false);
  openBuilder("shape");
  drawShapePreview();
  drawChart();
}

/* ══════════════════ 커스텀 패턴 빌더 ══════════════════ */
const FIELDS = [["close","종가"],["open","시가"],["high","고가"],["low","저가"],["volume","거래량"],
  ["body","몸통크기"],["range","전체범위"],["upper_wick","윗꼬리"],["lower_wick","아랫꼬리"],["body_pct","몸통비율"]];
const OPS = [[">", ">"],["<", "<"],[">=","≥"],["<=","≤"],["==","="],["!=","≠"]];

function setSelectMode(on) {
  state.selectMode = on === undefined ? !state.selectMode : on;
  $("selectModeBtn").classList.toggle("active", state.selectMode);
  $("selectHint").textContent = state.selectMode ? "차트를 드래그해 구간을 선택하세요" : "";
}
$("selectModeBtn").addEventListener("click", () => setSelectMode());

function openBuilder(tab) { $("builderModal").classList.remove("hidden"); switchBuilderTab(tab || state.activeBuilderTab); }
function closeBuilder() { $("builderModal").classList.add("hidden"); }
$("closeBuilderBtn").addEventListener("click", closeBuilder);
$("openBuilderBtn").addEventListener("click", () => openBuilder("rule"));
$("builderModal").addEventListener("click", (e) => { if (e.target === $("builderModal")) closeBuilder(); });
window.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeBuilder(); closePicker(); } });

document.querySelectorAll("[data-btab]").forEach(b => b.addEventListener("click", () => switchBuilderTab(b.dataset.btab)));
function switchBuilderTab(tab) {
  state.activeBuilderTab = tab;
  document.querySelectorAll("[data-btab]").forEach(b => b.classList.toggle("active", b.dataset.btab === tab));
  $("ruleBuilder").classList.toggle("hidden", tab !== "rule");
  $("shapeBuilder").classList.toggle("hidden", tab !== "shape");
}

function addCandleRule(offset) {
  const node = document.createElement("div");
  node.className = "candle-rule-box";
  node.innerHTML = `
    <div class="crb-head">
      <label>오프셋 <input type="number" class="offset-input" value="${offset}" max="0"></label>
      <button class="rm-candle-btn" type="button">캔들 삭제</button>
    </div>
    <div class="cond-list"></div>
    <button class="add-cond-btn ghost-btn" type="button">＋ 조건 추가</button>`;
  $("ruleCandles").appendChild(node);
  node.querySelector(".rm-candle-btn").addEventListener("click", () => node.remove());
  node.querySelector(".add-cond-btn").addEventListener("click", () => addCondRow(node));
  addCondRow(node);
}

function addCondRow(candleNode) {
  const row = document.createElement("div");
  row.className = "cond-row";
  row.innerHTML = `
    <select class="field-sel">${FIELDS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
    <select class="op-sel">${OPS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
    <select class="vtype-sel"><option value="number">숫자</option><option value="field">다른 필드</option></select>
    <input class="value-input" value="0" />
    <button class="rm-btn" type="button" aria-label="조건 삭제">${X_ICON}</button>`;
  candleNode.querySelector(".cond-list").appendChild(row);
  row.querySelector(".rm-btn").addEventListener("click", () => row.remove());
  row.querySelector(".vtype-sel").addEventListener("change", (e) => {
    const cur = row.querySelector(".value-input");
    if (e.target.value === "field") {
      const sel = document.createElement("select");
      sel.className = "value-input";
      sel.innerHTML = FIELDS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
      cur.replaceWith(sel);
    } else {
      const inp = document.createElement("input");
      inp.className = "value-input"; inp.value = "0";
      cur.replaceWith(inp);
    }
  });
}
$("addCandleBtn").addEventListener("click", () => addCandleRule(0));

function collectRule() {
  return [...document.querySelectorAll("#ruleCandles .candle-rule-box")].map(box => ({
    offset: Math.min(0, parseInt(box.querySelector(".offset-input").value, 10) || 0),
    conditions: [...box.querySelectorAll(".cond-row")].map(row => {
      const value_type = row.querySelector(".vtype-sel").value;
      const valEl = row.querySelector(".value-input");
      return { field: row.querySelector(".field-sel").value, op: row.querySelector(".op-sel").value,
               value_type, value: value_type === "number" ? parseFloat(valEl.value) : valEl.value };
    }),
  }));
}

$("saveRuleBtn").addEventListener("click", async () => {
  const name = $("ruleName").value.trim();
  const candles = collectRule();
  const err = $("ruleError");
  err.className = "error"; err.textContent = "";
  if (!name) { err.textContent = "패턴 이름을 입력하세요."; return; }
  if (!candles.length || candles.some(c => !c.conditions.length)) { err.textContent = "캔들과 조건을 최소 하나씩 추가하세요."; return; }
  try {
    await api("/patterns/custom/rule", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, direction: $("ruleDirection").value, candles }) });
    closeBuilder(); $("ruleName").value = "";
    await loadCustomPatterns();
    if (state.data) await loadAnalysis();
    toast(`"${name}" 패턴을 저장했습니다.`);
  } catch (e) { err.textContent = e.message; }
});

function drawShapePreview() {
  const canvas = $("shapePreview"), pctx = canvas.getContext("2d");
  pctx.clearRect(0, 0, canvas.width, canvas.height);
  $("saveShapeBtn").disabled = true;
  if (!state.selectedRange) return;
  const { start_idx, end_idx } = state.selectedRange;
  const closes = state.data.candles.slice(start_idx, end_idx + 1).map(c => c.c);
  const lo = Math.min(...closes), hi = Math.max(...closes);
  pctx.strokeStyle = C.accent; pctx.lineWidth = 2;
  pctx.lineJoin = "round"; pctx.lineCap = "round";
  pctx.beginPath();
  closes.forEach((v, i) => {
    const x = (i / (closes.length - 1)) * (canvas.width - 16) + 8;
    const y = canvas.height - 12 - ((v - lo) / (hi - lo || 1)) * (canvas.height - 24);
    i ? pctx.lineTo(x, y) : pctx.moveTo(x, y);
  });
  pctx.stroke();
  $("saveShapeBtn").disabled = false;
}

$("saveShapeBtn").addEventListener("click", async () => {
  const name = $("shapeName").value.trim();
  const err = $("shapeError");
  err.className = "error"; err.textContent = "";
  if (!name) { err.textContent = "패턴 이름을 입력하세요."; return; }
  if (!state.selectedRange) { err.textContent = "먼저 차트에서 구간을 선택하세요."; return; }
  try {
    await api("/patterns/custom/shape", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, direction: $("shapeDirection").value, market: state.market,
        symbol: state.symbol, interval: state.interval, limit: state.limit,
        start_idx: state.selectedRange.start_idx, end_idx: state.selectedRange.end_idx }) });
    closeBuilder(); $("shapeName").value = "";
    state.selectedRange = null;
    await loadCustomPatterns();
    if (state.data) await loadAnalysis();
    toast(`"${name}" 모양 패턴을 저장했습니다.`);
  } catch (e) { err.textContent = e.message; }
});

/* ══════════════════ 설정 컨트롤 ══════════════════ */
el.market.addEventListener("change", () => {
  state.market = el.market.value;
  populateSymbolSelect();
  el.custom.value = "";
});
el.loadBtn.addEventListener("click", () => {
  state.symbol = el.symbol.value;
  state.interval = el.interval.value;
  syncSegmented();
  loadAnalysis();
});
el.interval.addEventListener("change", () => { state.interval = el.interval.value; syncSegmented(); });
function syncSegmented() {
  document.querySelectorAll("#tfSegmented button").forEach(b => b.classList.toggle("active", b.dataset.tf === state.interval));
}

for (const [id, key] of [["tgEma","ema"],["tgVwap","vwap"],["tgBb","bb"],["tgFib","fib"],["tgRf","rf"],
                          ["tgFbb","fbb"],["tgCandlePat","candlePat"],["tgChartPat","chartPat"],["tgCustomPat","customPat"]]) {
  $(id).addEventListener("change", (e) => {
    state.toggles[key] = e.target.checked;
    if (state.data) { drawChart(); drawEvidence(); updateLayerSummary(); }
  });
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.data) renderAll(); }, 120);
});

/* 캔버스는 카드 높이에 따라 늘어나므로, 실제 크기가 바뀔 때마다 다시 그린다.
   (비트맵만 늘어나 그림이 찌그러지는 것을 막는다) */
if (window.ResizeObserver) {
  const redraw = { evidence: drawEvidence, chart: () => { drawChart(); drawVolume(); } };
  const ro = new ResizeObserver((entries) => {
    if (!state.data) return;
    for (const entry of entries) {
      const canvas = entry.target;
      const dpr = window.devicePixelRatio || 1;
      const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
      if (!w || !h) continue;
      if (canvas.width === w && canvas.height === h) continue;
      canvas.width = w; canvas.height = h;
      (redraw[canvas.dataset.redraw] || (() => {}))();
    }
  });
  el.evidence.dataset.redraw = "evidence";
  el.chart.dataset.redraw = "chart";
  ro.observe(el.evidence);
  ro.observe(el.chart);
}

/* ══════════════════ 시작 ══════════════════ */
(async function init() {
  addCandleRule(0);
  updateLayerSummary();
  try {
    await loadMarkets();
    await loadCustomPatterns();
    await loadWatchlist();
    await loadAnalysis();
  } catch (e) {
    toast("초기화에 실패했습니다: " + e.message);
  }
})();
