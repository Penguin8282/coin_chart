"use strict";
/* 개인 차트 분석 시스템 — 프론트엔드 (외부 라이브러리 없이 순수 Canvas로 구현) */

const API = "/api";

const state = {
  market: "crypto",
  symbol: "BTCUSDT",
  interval: "1h",
  limit: 500,
  data: null,           // /api/analysis 응답 전체
  view: { start: 0, count: 160 },
  toggles: {
    ema: true, vwap: true, bb: true, fib: true, rf: false, fbb: false,
    candlePat: true, chartPat: true, customPat: true,
  },
  hover: null,          // { index, x, y }
  drag: null,           // 팬(이동) 드래그 상태
  selectMode: false,
  selecting: null,      // 구간선택 드래그 상태 { x0, x1 }
  selectedRange: null,  // { start_idx, end_idx }
  customPatterns: [],
  watchlist: [],
  activeListTab: "chart",
  activeBuilderTab: "rule",
  markersCache: [],     // 마지막 draw에서 계산된 클릭/호버 가능 마커 목록
};

// ── 유틸 ──────────────────────────────────────────────────────────────
function fmtPrice(p) {
  if (p === null || p === undefined || Number.isNaN(p)) return "-";
  const ap = Math.abs(p);
  const digits = ap >= 1000 ? 1 : ap >= 100 ? 2 : ap >= 1 ? 3 : ap >= 0.01 ? 5 : 8;
  return p.toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}
function fmtPct(p) { return (p * 100).toFixed(1) + "%"; }
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function fmtTime(ms, interval) {
  const d = new Date(ms);
  if (interval === "1d" || interval === "1w") {
    return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")}`;
  }
  return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API 오류 (${res.status})`);
  }
  return res.json();
}

// ── DOM 참조 ──────────────────────────────────────────────────────────
const el = {
  market: document.getElementById("marketSelect"),
  symbol: document.getElementById("symbolSelect"),
  customSymbol: document.getElementById("customSymbol"),
  interval: document.getElementById("intervalSelect"),
  loadBtn: document.getElementById("loadBtn"),
  sourceBadge: document.getElementById("sourceBadge"),
  noteBanner: document.getElementById("noteBanner"),
  chartCanvas: document.getElementById("chartCanvas"),
  volCanvas: document.getElementById("volCanvas"),
  crosshair: document.getElementById("crosshairReadout"),
  dashboardTable: document.getElementById("dashboardTable"),
  patternList: document.getElementById("patternList"),
  customMgmt: document.getElementById("customPatternMgmt"),
  selectModeBtn: document.getElementById("selectModeBtn"),
  watchAddBtn: document.getElementById("watchAddBtn"),
  watchlistList: document.getElementById("watchlistList"),
  selectHint: document.getElementById("selectHint"),
  tooltip: document.getElementById("tooltip"),
  builderModal: document.getElementById("builderModal"),
};
const ctx = el.chartCanvas.getContext("2d");
const vctx = el.volCanvas.getContext("2d");

const MARGIN = { top: 14, right: 64, bottom: 26, left: 8 };

// ═══════════════════════════════════════════════════════════════════════
// 데이터 로딩
// ═══════════════════════════════════════════════════════════════════════
async function loadMarkets() {
  const markets = await api("/markets");
  window.__markets = markets;
  populateSymbolSelect();
}

function populateSymbolSelect() {
  const list = window.__markets[state.market] || [];
  el.symbol.innerHTML = list.map(p => `<option value="${p.symbol}">${p.name} (${p.symbol})</option>`).join("");
  if (list.length) state.symbol = list[0].symbol;
}

async function loadAnalysis() {
  const sym = el.customSymbol.value.trim() || state.symbol;
  el.loadBtn.textContent = "불러오는 중...";
  el.loadBtn.disabled = true;
  try {
    const data = await api(`/analysis?market=${state.market}&symbol=${encodeURIComponent(sym)}&interval=${state.interval}&limit=${state.limit}`);
    state.data = data;
    state.symbol = sym;
    state.view.count = Math.min(160, data.candles.length);
    state.view.start = Math.max(0, data.candles.length - state.view.count);
    el.sourceBadge.textContent = data.source === "demo" ? "DEMO 데이터" : `LIVE (${data.source})`;
    el.sourceBadge.className = "badge " + (data.source === "demo" ? "demo" : "live");
    el.noteBanner.textContent = data.note || "";
    renderAll();
    updateWatchAddBtn();
  } catch (e) {
    alert("데이터 로드 실패: " + e.message);
  } finally {
    el.loadBtn.textContent = "불러오기";
    el.loadBtn.disabled = false;
  }
}

async function loadCustomPatterns() {
  state.customPatterns = await api("/patterns/custom");
  renderCustomPatternMgmt();
}

async function loadWatchlist() {
  state.watchlist = await api("/watchlist");
  renderWatchlist();
  updateWatchAddBtn();
}

// ═══════════════════════════════════════════════════════════════════════
// 좌표 변환
// ═══════════════════════════════════════════════════════════════════════
function getPlotRect(canvas) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  return {
    x0: MARGIN.left, y0: MARGIN.top,
    x1: w - MARGIN.right, y1: h - MARGIN.bottom,
    w: w - MARGIN.left - MARGIN.right, h: h - MARGIN.top - MARGIN.bottom,
  };
}

function visibleSlice() {
  const n = state.data.candles.length;
  const start = clamp(state.view.start, 0, Math.max(0, n - 1));
  const count = clamp(state.view.count, 10, n - start);
  return { start, count, end: start + count };
}

function priceRangeForView() {
  const { start, end } = visibleSlice();
  const c = state.data.candles;
  let lo = Infinity, hi = -Infinity;
  for (let i = start; i < end; i++) {
    lo = Math.min(lo, c[i].l);
    hi = Math.max(hi, c[i].h);
  }
  const overlayKeys = [];
  if (state.toggles.ema) overlayKeys.push("ema9", "ema21", "ema55", "ema200");
  if (state.toggles.vwap) overlayKeys.push("vwap_up2", "vwap_dn2");
  if (state.toggles.bb) overlayKeys.push("bb_upper", "bb_lower");
  if (state.toggles.fib) overlayKeys.push("fib_236", "fib_786");
  const s = state.data.series;
  for (const k of overlayKeys) {
    const arr = s[k];
    if (!arr) continue;
    for (let i = start; i < end; i++) {
      const v = arr[i];
      if (v !== null && v !== undefined) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
  const pad = (hi - lo) * 0.08 || hi * 0.01 || 1;
  return { lo: lo - pad, hi: hi + pad };
}

function makeScales() {
  const rect = getPlotRect(el.chartCanvas);
  const { start, count } = visibleSlice();
  const { lo, hi } = priceRangeForView();
  const xOf = (i) => rect.x0 + ((i - start + 0.5) / count) * rect.w;
  const yOf = (p) => rect.y0 + (1 - (p - lo) / (hi - lo || 1)) * rect.h;
  const idxOfX = (x) => Math.round(start + ((x - rect.x0) / rect.w) * count - 0.5);
  return { rect, start, count, lo, hi, xOf, yOf, idxOfX, candleW: rect.w / count };
}

// ═══════════════════════════════════════════════════════════════════════
// 렌더링
// ═══════════════════════════════════════════════════════════════════════
function resizeCanvases() {
  for (const canvas of [el.chartCanvas, el.volCanvas]) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
  }
}

function renderAll() {
  if (!state.data) return;
  resizeCanvases();
  drawChart();
  drawVolume();
  renderDashboard();
  renderPatternList();
}

function withDpr(context, canvas, fn) {
  const dpr = window.devicePixelRatio || 1;
  context.save();
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  fn();
  context.restore();
}

function line(context, pts, color, width = 1.3, dash = []) {
  if (!pts.length) return;
  context.save();
  context.strokeStyle = color;
  context.lineWidth = width;
  context.setLineDash(dash);
  context.beginPath();
  let started = false;
  for (const [x, y] of pts) {
    if (y === null || y === undefined || Number.isNaN(y)) { started = false; continue; }
    if (!started) { context.moveTo(x, y); started = true; } else context.lineTo(x, y);
  }
  context.stroke();
  context.restore();
}

function seriesPts(scales, arr) {
  const pts = [];
  for (let i = scales.start; i < scales.start + scales.count; i++) {
    const v = arr[i];
    pts.push([scales.xOf(i), v === null || v === undefined ? null : scales.yOf(v)]);
  }
  return pts;
}

function drawChart() {
  const d = state.data;
  const s = d.series;
  withDpr(ctx, el.chartCanvas, () => {
    const scales = makeScales();
    const { rect, start, count, lo, hi } = scales;

    // 배경 그리드 + 가격축
    ctx.fillStyle = "#0a0e14";
    ctx.fillRect(0, 0, el.chartCanvas.clientWidth, el.chartCanvas.clientHeight);
    ctx.strokeStyle = "#1a2130";
    ctx.lineWidth = 1;
    const gridN = 6;
    ctx.font = "11px sans-serif";
    for (let g = 0; g <= gridN; g++) {
      const p = lo + (hi - lo) * (g / gridN);
      const y = scales.yOf(p);
      ctx.beginPath(); ctx.moveTo(rect.x0, y); ctx.lineTo(rect.x1, y); ctx.stroke();
      ctx.fillStyle = "#7a8699";
      ctx.fillText(fmtPrice(p), rect.x1 + 6, y + 3);
    }
    // 시간축 라벨
    const timeTicks = 6;
    for (let t = 0; t <= timeTicks; t++) {
      const idx = clamp(Math.round(start + (count - 1) * (t / timeTicks)), start, start + count - 1);
      const cndl = d.candles[idx];
      if (!cndl) continue;
      const x = scales.xOf(idx);
      ctx.fillStyle = "#7a8699";
      ctx.fillText(fmtTime(cndl.t, state.interval), clamp(x - 30, rect.x0, rect.x1 - 70), rect.y1 + 18);
    }

    // 매수/매도 강신호 배경 음영
    for (let i = start; i < start + count; i++) {
      if (d.events.strong_buy[i]) shadeBar(scales, i, "rgba(13,71,161,0.20)");
      else if (d.events.normal_buy[i]) shadeBar(scales, i, "rgba(21,101,192,0.10)");
      else if (d.events.strong_sell[i]) shadeBar(scales, i, "rgba(183,28,28,0.20)");
      else if (d.events.normal_sell[i]) shadeBar(scales, i, "rgba(198,40,40,0.10)");
    }

    if (state.toggles.fib) drawFibonacci(scales, s);
    if (state.toggles.bb) drawBand(scales, s.bb_upper, s.bb_lower, "#607D8B", 0.06, s.bb_mid, "#90A4AE");
    if (state.toggles.vwap) drawVwap(scales, s);
    if (state.toggles.fbb) drawFbb(scales, d.fibonacci_bb);
    if (state.toggles.rf) drawRangeFilter(scales, d.range_filter);
    if (state.toggles.ema) drawEma(scales, s);

    drawCandles(scales, d.candles);

    const markers = [];
    if (state.toggles.chartPat) drawChartPatterns(scales, d.chart_patterns, markers);
    if (state.toggles.candlePat) drawCandlePatterns(scales, d.candle_patterns, markers);
    if (state.toggles.customPat) drawCustomMatches(scales, d.custom_matches, markers);
    drawScoreMarkers(scales, d.events, d.series, markers);
    state.markersCache = markers;

    drawSelection(scales);
    drawCrosshair(scales);
  });
}

function shadeBar(scales, i, color) {
  const x = scales.xOf(i);
  const w = scales.candleW;
  ctx.fillStyle = color;
  ctx.fillRect(x - w / 2, scales.rect.y0, w, scales.rect.h);
}

function drawEma(scales, s) {
  line(ctx, seriesPts(scales, s.ema9), "#00E5FF", 1.1);
  line(ctx, seriesPts(scales, s.ema21), "#FF9800", 1.1);
  line(ctx, seriesPts(scales, s.ema55), "#CE93D8", 1.1);
  line(ctx, seriesPts(scales, s.ema200), "#F44336", 1.6);
}

function drawBand(scales, upperArr, lowerArr, color, alpha, midArr, midColor) {
  const up = seriesPts(scales, upperArr), dn = seriesPts(scales, lowerArr);
  ctx.save();
  ctx.beginPath();
  let started = false;
  for (const [x, y] of up) { if (y == null) continue; if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y); }
  for (let i = dn.length - 1; i >= 0; i--) { const [x, y] = dn[i]; if (y == null) continue; ctx.lineTo(x, y); }
  ctx.closePath();
  ctx.fillStyle = hexToRgba(color, alpha);
  ctx.fill();
  ctx.restore();
  line(ctx, up, hexToRgba(color, 0.7), 1);
  line(ctx, dn, hexToRgba(color, 0.7), 1);
  if (midArr) line(ctx, seriesPts(scales, midArr), midColor, 1, [3, 3]);
}

function drawVwap(scales, s) {
  line(ctx, seriesPts(scales, s.vwap), "#FFD700", 1.8);
  line(ctx, seriesPts(scales, s.vwap_up1), hexToRgba("#FFD700", 0.4), 1);
  line(ctx, seriesPts(scales, s.vwap_dn1), hexToRgba("#FFD700", 0.4), 1);
  drawBand(scales, s.vwap_up2, s.vwap_dn2, "#FFD700", 0.045);
}

function drawFibonacci(scales, s) {
  const levels = [
    ["fib_786", "#9C27B0", "0.786"], ["fib_618", "#00BCD4", "0.618"], ["fib_500", "#FFFFFF", "0.5"],
    ["fib_382", "#FF9800", "0.382"], ["fib_236", "#FFD700", "0.236"],
  ];
  for (const [key, color, label] of levels) {
    const arr = s[key];
    const v = arr[scales.start + scales.count - 1];
    line(ctx, seriesPts(scales, arr), hexToRgba(color, 0.55), 1, [2, 2]);
    if (v !== null && v !== undefined) {
      ctx.fillStyle = hexToRgba(color, 0.9);
      ctx.font = "10px sans-serif";
      ctx.fillText(label, scales.rect.x0 + 4, scales.yOf(v) - 3);
    }
  }
}

function drawFbb(scales, fbb) {
  drawBand(scales, fbb.u236, fbb.l236, "#ffffff", 0.02);
  line(ctx, seriesPts(scales, fbb.basis), "#e040fb", 1.6);
  line(ctx, seriesPts(scales, fbb.u1000), "#ff5252", 1.3);
  line(ctx, seriesPts(scales, fbb.l1000), "#69f0ae", 1.3);
}

function drawRangeFilter(scales, rf) {
  line(ctx, seriesPts(scales, rf.hi_band), "rgba(5,255,155,0.35)", 1);
  line(ctx, seriesPts(scales, rf.lo_band), "rgba(255,5,131,0.35)", 1);
  line(ctx, seriesPts(scales, rf.filter), "#e0e0e0", 1.8);
}

function drawCandles(scales, candles) {
  const { start, count, candleW } = scales;
  for (let i = start; i < start + count; i++) {
    const cnd = candles[i];
    if (!cnd) continue;
    const x = scales.xOf(i);
    const bull = cnd.c >= cnd.o;
    const color = bull ? "#05ff9b" : "#ff3376";
    const bodyTop = scales.yOf(Math.max(cnd.o, cnd.c));
    const bodyBot = scales.yOf(Math.min(cnd.o, cnd.c));
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, scales.yOf(cnd.h));
    ctx.lineTo(x, scales.yOf(cnd.l));
    ctx.stroke();
    const bw = Math.max(1, candleW * 0.62);
    const bh = Math.max(1, bodyBot - bodyTop);
    ctx.fillRect(x - bw / 2, bodyTop, bw, bh);
  }
}

function hexToRgba(hex, a) {
  const c = hex.replace("#", "");
  const bigint = parseInt(c.length === 3 ? c.split("").map(x => x + x).join("") : c, 16);
  const r = (bigint >> 16) & 255, g = (bigint >> 8) & 255, b = bigint & 255;
  return `rgba(${r},${g},${b},${a})`;
}

const CHART_PAT_COLOR = { bullish: "#05ff9b", bearish: "#ff3376", neutral: "#ce93d8" };

function drawChartPatterns(scales, patterns, markers) {
  const { start, count } = scales;
  const end = start + count;
  for (const p of patterns) {
    if (p.end_idx < start || p.start_idx > end) continue;
    const color = CHART_PAT_COLOR[p.direction] || "#ce93d8";
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    p.points.forEach((pt, i) => {
      const x = scales.xOf(pt.idx), y = scales.yOf(pt.price);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.restore();
    for (const pt of p.points) {
      const x = scales.xOf(pt.idx), y = scales.yOf(pt.price);
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
    }
    const last = p.points[p.points.length - 1];
    const lx = scales.xOf(last.idx), ly = scales.yOf(last.price);
    ctx.font = "11px sans-serif";
    ctx.fillStyle = color;
    ctx.fillText(p.name_kr, lx + 6, ly - 6);
    markers.push({ x0: scales.xOf(p.start_idx) - 4, x1: lx + 4, y0: scales.rect.y0, y1: scales.rect.y1, tooltip: `📐 ${p.name_kr} (${p.direction})\n신뢰도 ${fmtPct(p.confidence)}${p.target ? `\n목표가 ${fmtPrice(p.target)}` : ""}\n${p.note || ""}` });
  }
}

function drawCandlePatterns(scales, patterns, markers) {
  const { start, count } = scales;
  const end = start + count;
  const d = state.data;
  for (const p of patterns) {
    if (p.index < start || p.index >= end) continue;
    const cnd = d.candles[p.index];
    const x = scales.xOf(p.index);
    const up = p.direction === "bearish";
    const y = up ? scales.yOf(cnd.h) - 8 : scales.yOf(cnd.l) + 8;
    const color = p.direction === "bullish" ? "#05ff9b" : p.direction === "bearish" ? "#ff3376" : "#ffd700";
    ctx.fillStyle = color;
    ctx.beginPath();
    if (up) { ctx.moveTo(x, y - 5); ctx.lineTo(x - 5, y + 4); ctx.lineTo(x + 5, y + 4); }
    else { ctx.moveTo(x, y + 5); ctx.lineTo(x - 5, y - 4); ctx.lineTo(x + 5, y - 4); }
    ctx.closePath(); ctx.fill();
    markers.push({ x0: x - 6, x1: x + 6, y0: y - 8, y1: y + 8, tooltip: `🕯️ ${p.name_kr} (${p.direction})\n강도 ${fmtPct(p.strength)}` });
  }
}

function drawCustomMatches(scales, matches, markers) {
  const { start, count } = scales;
  const end = start + count;
  for (const m of matches) {
    const color = m.direction === "bullish" ? "#2979ff" : m.direction === "bearish" ? "#ff6d00" : "#ce93d8";
    if (m.kind === "custom_rule") {
      if (m.index < start || m.index >= end) continue;
      const x = scales.xOf(m.index);
      ctx.save();
      ctx.strokeStyle = color; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x, scales.rect.y1); ctx.lineTo(x, scales.rect.y1 - 14); ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(x, scales.rect.y1 - 14, 3, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      markers.push({ x0: x - 5, x1: x + 5, y0: scales.rect.y1 - 20, y1: scales.rect.y1, tooltip: `🧩 ${m.name} (커스텀 규칙)` });
    } else {
      if (m.end_idx < start || m.start_idx > end) continue;
      const x0 = scales.xOf(m.start_idx), x1 = scales.xOf(m.end_idx);
      ctx.save();
      ctx.fillStyle = hexToRgba(color, 0.08);
      ctx.fillRect(x0, scales.rect.y0, x1 - x0, scales.rect.h);
      ctx.strokeStyle = hexToRgba(color, 0.6);
      ctx.setLineDash([3, 3]);
      ctx.strokeRect(x0, scales.rect.y0, x1 - x0, scales.rect.h);
      ctx.restore();
      ctx.fillStyle = color;
      ctx.font = "11px sans-serif";
      ctx.fillText(`🧩 ${m.name} (${Math.round(m.score * 100)}%)`, x0 + 3, scales.rect.y0 + 12);
      markers.push({ x0, x1, y0: scales.rect.y0, y1: scales.rect.y0 + 18, tooltip: `🧩 ${m.name} (커스텀 모양, 유사도 ${Math.round(m.score * 100)}%)` });
    }
  }
}

function drawScoreMarkers(scales, events, series, markers) {
  const { start, count } = scales;
  const end = start + count;
  const d = state.data;
  for (let i = start; i < end; i++) {
    const cnd = d.candles[i];
    if (events.strong_buy[i] || events.normal_buy[i]) {
      const strong = events.strong_buy[i];
      const x = scales.xOf(i), y = scales.yOf(cnd.l) + (strong ? 22 : 15);
      drawArrowLabel(x, y, "up", strong ? "#0D47A1" : "#1565C0", `${series.buy_score[i]}점`);
      markers.push({ x0: x - 14, x1: x + 14, y0: y - 6, y1: y + 14, tooltip: `${strong ? "🚀 강한 매수" : "📈 매수"} ${series.buy_score[i]}점` });
    }
    if (events.strong_sell[i] || events.normal_sell[i]) {
      const strong = events.strong_sell[i];
      const x = scales.xOf(i), y = scales.yOf(cnd.h) - (strong ? 22 : 15);
      drawArrowLabel(x, y, "down", strong ? "#B71C1C" : "#C62828", `${series.sell_score[i]}점`);
      markers.push({ x0: x - 14, x1: x + 14, y0: y - 14, y1: y + 6, tooltip: `${strong ? "🔻 강한 매도" : "📉 매도"} ${series.sell_score[i]}점` });
    }
  }
}

function drawArrowLabel(x, y, dir, color, text) {
  ctx.save();
  ctx.fillStyle = color;
  ctx.beginPath();
  if (dir === "up") { ctx.moveTo(x, y - 6); ctx.lineTo(x - 6, y + 5); ctx.lineTo(x + 6, y + 5); }
  else { ctx.moveTo(x, y + 6); ctx.lineTo(x - 6, y - 5); ctx.lineTo(x + 6, y - 5); }
  ctx.closePath(); ctx.fill();
  ctx.font = "10px sans-serif";
  ctx.fillText(text, x + 8, y + 3);
  ctx.restore();
}

function drawSelection(scales) {
  if (state.selecting) {
    const { x0, x1 } = state.selecting;
    ctx.save();
    ctx.fillStyle = "rgba(255,215,0,0.12)";
    ctx.fillRect(Math.min(x0, x1), scales.rect.y0, Math.abs(x1 - x0), scales.rect.h);
    ctx.strokeStyle = "#ffd700"; ctx.setLineDash([4, 3]);
    ctx.strokeRect(Math.min(x0, x1), scales.rect.y0, Math.abs(x1 - x0), scales.rect.h);
    ctx.restore();
  } else if (state.selectedRange) {
    const { start_idx, end_idx } = state.selectedRange;
    if (end_idx >= scales.start && start_idx <= scales.start + scales.count) {
      const x0 = scales.xOf(start_idx), x1 = scales.xOf(end_idx);
      ctx.save();
      ctx.strokeStyle = "#ffd700"; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
      ctx.strokeRect(x0, scales.rect.y0, x1 - x0, scales.rect.h);
      ctx.restore();
    }
  }
}

function drawCrosshair(scales) {
  if (!state.hover) return;
  const { index } = state.hover;
  const d = state.data;
  if (index < scales.start || index >= scales.start + scales.count) return;
  const cnd = d.candles[index];
  if (!cnd) return;
  const x = scales.xOf(index);
  ctx.save();
  ctx.strokeStyle = "rgba(255,255,255,0.35)";
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(x, scales.rect.y0); ctx.lineTo(x, scales.rect.y1); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(scales.rect.x0, state.hover.y); ctx.lineTo(scales.rect.x1, state.hover.y); ctx.stroke();
  ctx.restore();

  const s = d.series;
  el.crosshair.style.display = "block";
  el.crosshair.textContent =
    `${fmtTime(cnd.t, state.interval)}\n` +
    `O ${fmtPrice(cnd.o)}  H ${fmtPrice(cnd.h)}  L ${fmtPrice(cnd.l)}  C ${fmtPrice(cnd.c)}\n` +
    `RSI ${s.rsi[index]?.toFixed(1) ?? "-"}  ADX ${s.adx[index]?.toFixed(1) ?? "-"}  ` +
    `매수 ${s.buy_score[index] ?? 0}점 / 매도 ${s.sell_score[index] ?? 0}점`;
}

function drawVolume() {
  const d = state.data;
  withDpr(vctx, el.volCanvas, () => {
    const rect = { x0: MARGIN.left, y0: 6, x1: el.volCanvas.clientWidth - MARGIN.right, y1: el.volCanvas.clientHeight - 6 };
    const scales = makeScales();
    const { start, count } = scales;
    let maxV = 0;
    for (let i = start; i < start + count; i++) maxV = Math.max(maxV, d.candles[i].v);
    maxV = maxV || 1;
    for (let i = start; i < start + count; i++) {
      const cnd = d.candles[i];
      const x = scales.xOf(i);
      const h = (cnd.v / maxV) * (rect.y1 - rect.y0);
      const bull = cnd.c >= cnd.o;
      vctx.fillStyle = bull ? "rgba(5,255,155,0.55)" : "rgba(255,51,118,0.55)";
      const bw = Math.max(1, scales.candleW * 0.62);
      vctx.fillRect(x - bw / 2, rect.y1 - h, bw, h);
    }
    vctx.fillStyle = "#7a8699";
    vctx.font = "10px sans-serif";
    vctx.fillText("거래량", rect.x0, 14);
  });
}

// ═══════════════════════════════════════════════════════════════════════
// 대시보드 & 패턴 리스트 (사이드 패널)
// ═══════════════════════════════════════════════════════════════════════
const SIGNAL_LABEL = {
  strong_buy: ["🚀 강한 매수", "#0D47A1"], normal_buy: ["📈 매수", "#1565C0"],
  strong_sell: ["🔻 강한 매도", "#B71C1C"], normal_sell: ["📉 매도", "#C62828"],
  sideways: ["⏸ 횡보 관망", "#37474F"], monitor: ["🔍 모니터링", "#37474F"],
};

function row(label, valueHtml, color) {
  return `<tr><td>${label}</td><td style="${color ? `color:${color}` : ""}">${valueHtml}</td></tr>`;
}

function renderDashboard() {
  const dash = state.data.dashboard;
  const s = state.data.series;
  const last = state.data.candles.length - 1;
  const [sigLabel, sigColor] = SIGNAL_LABEL[dash.signal] || ["-", "#fff"];
  const vwapLabel = { above_2sigma: "상단 2σ ⚠️", above_1sigma: "상단 1σ", above: "VWAP 위 🟢", below: "VWAP 아래 🔴", below_2sigma: "하단 2σ 💡" }[dash.vwap_state];
  const obvLabel = { bull_div: "매수 다이버전스 🔥", bear_div: "매도 다이버전스 ⚠️", up: "상승 🟢", down: "하락 🔴" }[dash.obv_state];
  const macdLabel = { golden_cross: "골든크로스 🚀", dead_cross: "데드크로스 🔻", up: "상승 중 🟢", down: "하락 중 🔴" }[dash.macd_state];
  const emaLabel = { bull_stack: "정배열 📈", bear_stack: "역배열 📉", neutral: "중립" }[dash.ema_state];
  const bbLabel = { squeeze: "스퀴즈 ⚡", break_upper: "상단 돌파 ⚠️", break_lower: "하단 돌파 💡", inside: "밴드 내" }[dash.bb_state];

  let html = "";
  html += row("현재가", fmtPrice(dash.price));
  html += row("매수 신호", `${dash.buy_score}점`, "#2979ff");
  html += row("매도 신호", `${dash.sell_score}점`, "#ff3376");
  html += row("ADX 추세", `${dash.strong_trend ? "🔥" : "⚠️"} ${dash.adx.toFixed(1)}`);
  html += row("VWAP", vwapLabel || "-");
  html += row("OBV", obvLabel || "-");
  html += row("RSI", `${dash.rsi > 70 ? "과매수 ⚠️ " : dash.rsi < 30 ? "과매도 💡 " : "중립 "}${dash.rsi.toFixed(1)}`);
  html += row("MACD", macdLabel || "-");
  html += row("스토캐스틱", `${s.stoch_k[last]?.toFixed(1) ?? "-"}`);
  html += row("EMA 배열", emaLabel || "-");
  html += row("볼린저", bbLabel || "-");
  html += row("거래량", `${dash.vol_spike ? "🔥 급등 " : ""}${dash.vol_ratio.toFixed(1)}x`);
  if (dash.signal === "strong_buy" || dash.signal === "normal_buy") {
    html += row("TP / SL", `${fmtPrice(dash.tp_buy)} / ${fmtPrice(dash.sl_buy)}`);
  }
  if (dash.signal === "strong_sell" || dash.signal === "normal_sell") {
    html += row("TP / SL", `${fmtPrice(dash.tp_sell)} / ${fmtPrice(dash.sl_sell)}`);
  }
  html += `<tr><td><b>🎯 종합 판단</b></td><td style="color:${sigColor === "#37474F" ? "#aaa" : sigColor}"><b>${sigLabel}</b></td></tr>`;
  el.dashboardTable.innerHTML = html;
}

function renderPatternList() {
  const d = state.data;
  let items = [];
  if (state.activeListTab === "chart") {
    items = d.chart_patterns.map(p => ({
      title: p.name_kr, sub: `${p.note || ""} · 신뢰도 ${fmtPct(p.confidence)}${p.target ? ` · 목표가 ${fmtPrice(p.target)}` : ""}`,
      direction: p.direction, idx: p.end_idx,
    }));
  } else if (state.activeListTab === "candle") {
    items = d.candle_patterns.map(p => ({
      title: p.name_kr, sub: `강도 ${fmtPct(p.strength)} · ${fmtTime(d.candles[p.index].t, state.interval)}`,
      direction: p.direction, idx: p.index,
    }));
  } else {
    items = d.custom_matches.map(m => ({
      title: m.name, sub: m.kind === "custom_shape" ? `모양 유사도 ${Math.round(m.score * 100)}%` : "규칙 매칭",
      direction: m.direction, idx: m.kind === "custom_shape" ? m.end_idx : m.index,
    }));
  }
  items.sort((a, b) => b.idx - a.idx);
  if (!items.length) {
    el.patternList.innerHTML = `<div class="empty-msg">탐지된 패턴이 없습니다</div>`;
    return;
  }
  el.patternList.innerHTML = items.slice(0, 80).map(it => `
    <div class="pattern-item ${it.direction}" data-idx="${it.idx}">
      <div class="pi-head"><span>${it.title}</span></div>
      <div class="pi-sub">${it.sub}</div>
    </div>`).join("");
  el.patternList.querySelectorAll(".pattern-item").forEach(node => {
    node.addEventListener("click", () => {
      const idx = parseInt(node.dataset.idx, 10);
      state.view.start = clamp(idx - Math.round(state.view.count * 0.7), 0, Math.max(0, d.candles.length - state.view.count));
      renderAll();
    });
  });
}

function renderCustomPatternMgmt() {
  if (!state.customPatterns.length) {
    el.customMgmt.innerHTML = `<div class="empty-msg">등록된 커스텀 패턴이 없습니다</div>`;
    return;
  }
  el.customMgmt.innerHTML = state.customPatterns.map(p => `
    <div class="custom-pat-row">
      <span>${p.type === "shape" ? "✂️" : "🧩"} ${p.name}</span>
      <button class="del-btn" data-id="${p.id}">삭제</button>
    </div>`).join("");
  el.customMgmt.querySelectorAll(".del-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      await api(`/patterns/custom/${btn.dataset.id}`, { method: "DELETE" });
      await loadCustomPatterns();
      if (state.data) { await loadAnalysis(); }
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════
// 관심종목
// ═══════════════════════════════════════════════════════════════════════
const MARKET_LABEL = { crypto: "코인", us: "미국", kr: "한국" };

function renderWatchlist() {
  if (!state.watchlist.length) {
    el.watchlistList.innerHTML = `<div class="empty-msg">관심종목이 없습니다</div>`;
    return;
  }
  el.watchlistList.innerHTML = state.watchlist.map(w => `
    <div class="watch-row" data-market="${w.market}" data-symbol="${w.symbol}">
      <span><span class="wr-market">${MARKET_LABEL[w.market] || w.market}</span>${w.symbol}</span>
      <button class="del-btn" data-market="${w.market}" data-symbol="${w.symbol}">삭제</button>
    </div>`).join("");
  el.watchlistList.querySelectorAll(".watch-row").forEach(row => {
    row.addEventListener("click", (e) => {
      if (e.target.closest(".del-btn")) return;
      const { market, symbol } = row.dataset;
      el.market.value = market;
      state.market = market;
      populateSymbolSelect();
      el.customSymbol.value = symbol;
      el.symbol.value = [...el.symbol.options].some(o => o.value === symbol) ? symbol : el.symbol.value;
      el.interval.value = state.interval;
      loadAnalysis();
    });
  });
  el.watchlistList.querySelectorAll(".del-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const { market, symbol } = btn.dataset;
      await api(`/watchlist?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}`, { method: "DELETE" });
      await loadWatchlist();
    });
  });
}

function updateWatchAddBtn() {
  if (!state.data) return;
  const inList = state.watchlist.some(w => w.market === state.market && w.symbol === state.symbol);
  el.watchAddBtn.textContent = inList ? "★ 관심종목" : "☆ 관심추가";
  el.watchAddBtn.classList.toggle("active", inList);
}

el.watchAddBtn.addEventListener("click", async () => {
  if (!state.data) return;
  const inList = state.watchlist.some(w => w.market === state.market && w.symbol === state.symbol);
  if (inList) {
    await api(`/watchlist?market=${encodeURIComponent(state.market)}&symbol=${encodeURIComponent(state.symbol)}`, { method: "DELETE" });
  } else {
    await api("/watchlist", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ market: state.market, symbol: state.symbol }),
    });
  }
  await loadWatchlist();
});

// ═══════════════════════════════════════════════════════════════════════
// 인터랙션: 줌 / 팬 / 크로스헤어 / 구간선택
// ═══════════════════════════════════════════════════════════════════════
function canvasMouseX(e, canvas) {
  const r = canvas.getBoundingClientRect();
  return e.clientX - r.left;
}
function canvasMouseY(e, canvas) {
  const r = canvas.getBoundingClientRect();
  return e.clientY - r.top;
}

el.chartCanvas.addEventListener("wheel", (e) => {
  if (!state.data) return;
  e.preventDefault();
  const scales = makeScales();
  const mouseIdx = scales.idxOfX(canvasMouseX(e, el.chartCanvas));
  const factor = e.deltaY > 0 ? 1.15 : 1 / 1.15;
  const n = state.data.candles.length;
  const newCount = clamp(Math.round(state.view.count * factor), 20, n);
  const ratio = (mouseIdx - state.view.start) / state.view.count;
  state.view.start = clamp(Math.round(mouseIdx - ratio * newCount), 0, Math.max(0, n - newCount));
  state.view.count = newCount;
  renderAll();
}, { passive: false });

el.chartCanvas.addEventListener("mousedown", (e) => {
  if (!state.data) return;
  el.tooltip.classList.add("hidden");
  const x = canvasMouseX(e, el.chartCanvas);
  if (state.selectMode) {
    state.selecting = { x0: x, x1: x };
  } else {
    state.drag = { x0: x, viewStart0: state.view.start };
  }
});

window.addEventListener("mousemove", (e) => {
  if (!state.data) return;
  const x = canvasMouseX(e, el.chartCanvas);
  const y = canvasMouseY(e, el.chartCanvas);
  const rect = el.chartCanvas.getBoundingClientRect();
  const inside = e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;

  if (state.selecting) {
    state.selecting.x1 = x;
    drawChart();
    return;
  }
  if (state.drag) {
    const scales = makeScales();
    const dIdx = Math.round((state.drag.x0 - x) / scales.candleW);
    const n = state.data.candles.length;
    state.view.start = clamp(state.drag.viewStart0 + dIdx, 0, Math.max(0, n - state.view.count));
    renderAll();
    return;
  }
  if (inside) {
    const scales = makeScales();
    state.hover = { index: clamp(scales.idxOfX(x), 0, state.data.candles.length - 1), y };
    drawChart();
    updateTooltip(e, x, y);
  } else if (state.hover) {
    state.hover = null;
    el.crosshair.style.display = "none";
    el.tooltip.classList.add("hidden");
    drawChart();
  }
});

window.addEventListener("mouseup", (e) => {
  if (state.selecting) {
    finishSelection();
    state.selecting = null;
  }
  state.drag = null;
});

function updateTooltip(e, x, y) {
  const hit = state.markersCache.find(m => x >= m.x0 && x <= m.x1 && y >= m.y0 && y <= m.y1);
  if (hit) {
    el.tooltip.textContent = hit.tooltip;
    el.tooltip.style.left = (e.clientX + 12) + "px";
    el.tooltip.style.top = (e.clientY + 12) + "px";
    el.tooltip.classList.remove("hidden");
  } else {
    el.tooltip.classList.add("hidden");
  }
}

function finishSelection() {
  const scales = makeScales();
  const { x0, x1 } = state.selecting;
  let i0 = scales.idxOfX(Math.min(x0, x1));
  let i1 = scales.idxOfX(Math.max(x0, x1));
  i0 = clamp(i0, 0, state.data.candles.length - 1);
  i1 = clamp(i1, 0, state.data.candles.length - 1);
  if (i1 - i0 < 3) { state.selectedRange = null; renderAll(); return; }
  state.selectedRange = { start_idx: i0, end_idx: i1 };
  toggleSelectMode(false);
  openBuilder("shape");
  drawShapePreview();
  renderAll();
}

// ═══════════════════════════════════════════════════════════════════════
// 커스텀 패턴 빌더 모달
// ═══════════════════════════════════════════════════════════════════════
const FIELD_OPTIONS = [
  ["close", "종가"], ["open", "시가"], ["high", "고가"], ["low", "저가"], ["volume", "거래량"],
  ["body", "몸통크기"], ["range", "전체범위"], ["upper_wick", "윗꼬리"], ["lower_wick", "아랫꼬리"], ["body_pct", "몸통비율"],
];
const OP_OPTIONS = [[">", ">"], ["<", "<"], [">=", "≥"], ["<=", "≤"], ["==", "="], ["!=", "≠"]];

function toggleSelectMode(force) {
  state.selectMode = force !== undefined ? force : !state.selectMode;
  el.selectModeBtn.classList.toggle("primary-btn", state.selectMode);
  el.selectHint.textContent = state.selectMode ? "차트를 드래그해서 구간을 선택하세요" : "";
}
el.selectModeBtn.addEventListener("click", () => toggleSelectMode());

function openBuilder(tab) {
  el.builderModal.classList.remove("hidden");
  switchBuilderTab(tab || state.activeBuilderTab);
}
function closeBuilder() { el.builderModal.classList.add("hidden"); }
document.getElementById("closeBuilderBtn").addEventListener("click", closeBuilder);
document.getElementById("openBuilderBtn").addEventListener("click", () => openBuilder("rule"));

document.querySelectorAll('#builderModal [data-btab]').forEach(btn => {
  btn.addEventListener("click", () => switchBuilderTab(btn.dataset.btab));
});
function switchBuilderTab(tab) {
  state.activeBuilderTab = tab;
  document.querySelectorAll('#builderModal [data-btab]').forEach(b => b.classList.toggle("active", b.dataset.btab === tab));
  document.getElementById("ruleBuilder").classList.toggle("hidden", tab !== "rule");
  document.getElementById("shapeBuilder").classList.toggle("hidden", tab !== "shape");
}

document.querySelectorAll('.tab[data-tab]').forEach(btn => {
  btn.addEventListener("click", () => {
    state.activeListTab = btn.dataset.tab;
    document.querySelectorAll('.tab[data-tab]').forEach(b => b.classList.toggle("active", b === btn));
    renderPatternList();
  });
});

// ── 규칙 빌더 ────────────────────────────────────────────────────────────
function candleRuleHtml(offset) {
  return `
    <div class="candle-rule-box" data-offset="${offset}">
      <div class="crb-head">
        <span>오프셋 <input type="number" class="offset-input" value="${offset}" max="0"></span>
        <button class="rm-candle-btn">캔들 삭제</button>
      </div>
      <div class="cond-list"></div>
      <button class="add-cond-btn ghost-btn">+ 조건 추가</button>
    </div>`;
}
function condRowHtml() {
  return `
    <div class="cond-row">
      <select class="field-sel">${FIELD_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <select class="op-sel">${OP_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <select class="vtype-sel"><option value="number">숫자</option><option value="field">다른 필드</option></select>
      <input class="value-input" value="0" />
      <button class="rm-btn">✕</button>
    </div>`;
}
function bindCondRow(rowEl) {
  rowEl.querySelector(".rm-btn").addEventListener("click", () => rowEl.remove());
  const vtypeSel = rowEl.querySelector(".vtype-sel");
  const valueInput = rowEl.querySelector(".value-input");
  vtypeSel.addEventListener("change", () => {
    if (vtypeSel.value === "field") {
      const sel = document.createElement("select");
      sel.className = "value-input field-value";
      sel.innerHTML = FIELD_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
      valueInput.replaceWith(sel);
    } else {
      const inp = document.createElement("input");
      inp.className = "value-input";
      inp.value = "0";
      rowEl.querySelector(".value-input").replaceWith(inp);
    }
  });
}
function addCandleRule(offset) {
  const box = document.createElement("div");
  box.innerHTML = candleRuleHtml(offset);
  const node = box.firstElementChild;
  document.getElementById("ruleCandles").appendChild(node);
  node.querySelector(".rm-candle-btn").addEventListener("click", () => node.remove());
  node.querySelector(".add-cond-btn").addEventListener("click", () => addCondRow(node));
  addCondRow(node);
}
function addCondRow(candleNode) {
  const wrap = document.createElement("div");
  wrap.innerHTML = condRowHtml();
  const rowEl = wrap.firstElementChild;
  candleNode.querySelector(".cond-list").appendChild(rowEl);
  bindCondRow(rowEl);
}
document.getElementById("addCandleBtn").addEventListener("click", () => addCandleRule(0));

function collectRuleDefinition() {
  const candleBoxes = document.querySelectorAll("#ruleCandles .candle-rule-box");
  const candles = [];
  for (const box of candleBoxes) {
    const offset = parseInt(box.querySelector(".offset-input").value, 10) || 0;
    const conditions = [];
    for (const row of box.querySelectorAll(".cond-row")) {
      const field = row.querySelector(".field-sel").value;
      const op = row.querySelector(".op-sel").value;
      const value_type = row.querySelector(".vtype-sel").value;
      const valEl = row.querySelector(".value-input");
      const value = value_type === "number" ? parseFloat(valEl.value) : valEl.value;
      conditions.push({ field, op, value_type, value });
    }
    candles.push({ offset: Math.min(0, offset), conditions });
  }
  return candles;
}

document.getElementById("saveRuleBtn").addEventListener("click", async () => {
  const name = document.getElementById("ruleName").value.trim();
  const direction = document.getElementById("ruleDirection").value;
  const candles = collectRuleDefinition();
  const errBox = document.getElementById("ruleError");
  errBox.textContent = "";
  if (!name) { errBox.textContent = "패턴 이름을 입력하세요."; return; }
  if (!candles.length || candles.some(c => !c.conditions.length)) { errBox.textContent = "캔들과 조건을 최소 1개 이상 추가하세요."; return; }
  try {
    await api("/patterns/custom/rule", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, direction, candles }),
    });
    closeBuilder();
    await loadCustomPatterns();
    if (state.data) await loadAnalysis();
  } catch (e) { errBox.textContent = e.message; }
});

// ── 모양 빌더 ────────────────────────────────────────────────────────────
function drawShapePreview() {
  const canvas = document.getElementById("shapePreview");
  const pctx = canvas.getContext("2d");
  pctx.clearRect(0, 0, canvas.width, canvas.height);
  document.getElementById("saveShapeBtn").disabled = true;
  if (!state.selectedRange) return;
  const { start_idx, end_idx } = state.selectedRange;
  const closes = state.data.candles.slice(start_idx, end_idx + 1).map(c => c.c);
  const lo = Math.min(...closes), hi = Math.max(...closes);
  pctx.strokeStyle = "#ffd700"; pctx.lineWidth = 2; pctx.beginPath();
  closes.forEach((v, i) => {
    const x = (i / (closes.length - 1)) * (canvas.width - 10) + 5;
    const y = canvas.height - 10 - ((v - lo) / (hi - lo || 1)) * (canvas.height - 20);
    if (i === 0) pctx.moveTo(x, y); else pctx.lineTo(x, y);
  });
  pctx.stroke();
  document.getElementById("saveShapeBtn").disabled = false;
}

document.getElementById("saveShapeBtn").addEventListener("click", async () => {
  const name = document.getElementById("shapeName").value.trim();
  const direction = document.getElementById("shapeDirection").value;
  const errBox = document.getElementById("shapeError");
  errBox.textContent = "";
  if (!name) { errBox.textContent = "패턴 이름을 입력하세요."; return; }
  if (!state.selectedRange) { errBox.textContent = "먼저 차트에서 구간을 선택하세요."; return; }
  try {
    await api("/patterns/custom/shape", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name, direction, market: state.market, symbol: state.symbol, interval: state.interval, limit: state.limit,
        start_idx: state.selectedRange.start_idx, end_idx: state.selectedRange.end_idx,
      }),
    });
    closeBuilder();
    state.selectedRange = null;
    await loadCustomPatterns();
    if (state.data) await loadAnalysis();
  } catch (e) { errBox.textContent = e.message; }
});

// ═══════════════════════════════════════════════════════════════════════
// 상단 컨트롤 바인딩
// ═══════════════════════════════════════════════════════════════════════
el.market.addEventListener("change", () => {
  state.market = el.market.value;
  populateSymbolSelect();
  el.customSymbol.value = "";
});
el.loadBtn.addEventListener("click", () => {
  state.symbol = el.symbol.value;
  state.interval = el.interval.value;
  loadAnalysis();
});
el.interval.addEventListener("change", () => { state.interval = el.interval.value; });

for (const [id, key] of [
  ["tgEma", "ema"], ["tgVwap", "vwap"], ["tgBb", "bb"], ["tgFib", "fib"],
  ["tgRf", "rf"], ["tgFbb", "fbb"], ["tgCandlePat", "candlePat"], ["tgChartPat", "chartPat"], ["tgCustomPat", "customPat"],
]) {
  document.getElementById(id).addEventListener("change", (e) => {
    state.toggles[key] = e.target.checked;
    if (state.data) drawChart();
  });
}

window.addEventListener("resize", () => { if (state.data) renderAll(); });

// ═══════════════════════════════════════════════════════════════════════
// 초기화
// ═══════════════════════════════════════════════════════════════════════
(async function init() {
  addCandleRule(0);
  await loadMarkets();
  await loadCustomPatterns();
  await loadWatchlist();
  await loadAnalysis();
})();
