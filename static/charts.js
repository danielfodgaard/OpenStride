// Plain inline SVG charts, drawn at the size they show at so labels stay readable on a phone.
import { el, paceText } from "./format.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const MARGIN = { top: 12, right: 12, bottom: 26, left: 42 };
export const PALETTE = ["var(--accent)", "var(--load-atl)", "var(--pace)", "var(--hr)", "var(--good)", "var(--ink-2)"];

export function svgEl(tag, attributes = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function niceTicks(min, max) {
  const low = min === max ? min - 1 : min;
  const high = min === max ? max + 1 : max;
  const raw = (high - low) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => Math.ceil(high / s) - Math.floor(low / s) <= 5);
  const ticks = [];
  for (let value = Math.floor(low / step) * step; value < high + step * 0.999; value += step) ticks.push(Number(value.toFixed(6)));
  return ticks;
}

export function legend(items) {
  const box = el("div", null, "legend");
  for (const { label, color, kind = "line" } of items) {
    const key = el("span");
    const swatch = el("i", null, kind);
    swatch.style.background = kind === "bar" ? color : "";
    swatch.style.borderColor = color;
    key.append(swatch, label);
    box.append(key);
  }
  return box;
}

function frame(container, minWidth = 300, height = 220) {
  const width = Math.max(minWidth, Math.min(Math.round(container.clientWidth || 640), 900));
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img" });
  return { svg, width, height, left: MARGIN.left, right: width - MARGIN.right, top: MARGIN.top, bottom: height - MARGIN.bottom };
}

function grid(f, ticks, toY, format = String) {
  for (const tick of ticks) {
    f.svg.append(svgEl("line", { x1: f.left, x2: f.right, y1: toY(tick), y2: toY(tick), stroke: "var(--grid)" }));
    f.svg.append(svgEl("text", { x: f.left - 6, y: toY(tick) + 4, "text-anchor": "end", "font-size": 11 }, format(tick)));
  }
}

function xLabels(f, labels, toX, every) {
  labels.forEach((label, index) => {
    if (index % every === 0) f.svg.append(svgEl("text", { x: toX(index), y: f.bottom + 16, "text-anchor": "middle", "font-size": 11 }, label));
  });
}

function tooltip(box, f, count, toX, textAt) {
  const tip = el("div", null, "tip");
  tip.hidden = true;
  const cursor = svgEl("line", { y1: f.top, y2: f.bottom, stroke: "var(--ink-3)", "stroke-dasharray": "2 3", visibility: "hidden" });
  f.svg.append(cursor);
  const move = (event) => {
    const rect = f.svg.getBoundingClientRect();
    const x = ((event.touches ? event.touches[0].clientX : event.clientX) - rect.left) * (f.width / rect.width);
    let best = 0;
    for (let i = 1; i < count; i += 1) if (Math.abs(toX(i) - x) < Math.abs(toX(best) - x)) best = i;
    const text = textAt(best);
    if (!text) return;
    cursor.setAttribute("x1", toX(best));
    cursor.setAttribute("x2", toX(best));
    cursor.setAttribute("visibility", "visible");
    tip.textContent = text;
    tip.hidden = false;
    tip.style.left = `${Math.min(Math.max(toX(best) * (rect.width / f.width) - 60, 0), rect.width - 150)}px`;
    tip.style.top = "4px";
  };
  const leave = () => { tip.hidden = true; cursor.setAttribute("visibility", "hidden"); };
  f.svg.addEventListener("mousemove", move);
  f.svg.addEventListener("touchstart", move, { passive: true });
  f.svg.addEventListener("touchmove", move, { passive: true });
  f.svg.addEventListener("mouseleave", leave);
  box.append(tip);
}

function path(values, toX, toY) {
  let d = "", pen = false;
  values.forEach((value, index) => {
    if (value === null || value === undefined || Number.isNaN(value)) { pen = false; return; }
    d += `${pen ? "L" : "M"}${toX(index).toFixed(1)} ${toY(value).toFixed(1)}`;
    pen = true;
  });
  return d;
}

// series: [{ name, values, color?, dash? }]; x: labels. Drawn when the box is in the page (it needs its width).
export function lineChart(box, { x, series, yFormat = (v) => String(v), marks = [], invert = false, label = "" }) {
  const draw = () => {
    box.replaceChildren();
    const f = frame(box);
    f.svg.setAttribute("aria-label", label);
    const all = series.flatMap((s) => s.values.filter((v) => v !== null && v !== undefined));
    if (!all.length) { box.append(el("p", "No data to draw.", "muted")); return; }
    const ticks = niceTicks(Math.min(...all), Math.max(...all));
    const [low, high] = [ticks[0], ticks[ticks.length - 1]];
    const toX = (i) => f.left + (x.length > 1 ? (i / (x.length - 1)) * (f.right - f.left) : (f.right - f.left) / 2);
    const scale = (v) => (v - low) / (high - low || 1);
    const toY = (v) => (invert ? f.top + scale(v) * (f.bottom - f.top) : f.bottom - scale(v) * (f.bottom - f.top));
    grid(f, ticks, toY, yFormat);
    xLabels(f, x, toX, Math.max(1, Math.ceil(x.length / Math.max(3, Math.floor((f.right - f.left) / 70)))));
    if (low < 0 && high > 0) f.svg.append(svgEl("line", { x1: f.left, x2: f.right, y1: toY(0), y2: toY(0), stroke: "var(--ink-3)" }));
    for (const mark of marks) {
      const index = x.indexOf(mark.x);
      if (index < 0) continue;
      f.svg.append(svgEl("line", { x1: toX(index), x2: toX(index), y1: f.top, y2: f.bottom, stroke: "var(--ink-3)", "stroke-dasharray": "3 3" }));
      f.svg.append(svgEl("text", { x: toX(index) - 4, y: f.top + 10, "text-anchor": "end", "font-size": 11 }, mark.label));
    }
    series.forEach((s, index) => {
      const color = s.color || PALETTE[index % PALETTE.length];
      f.svg.append(svgEl("path", { d: path(s.values, toX, toY), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", ...(s.dash ? { "stroke-dasharray": "5 4" } : {}) }));
    });
    box.append(f.svg);
    tooltip(box, f, x.length, toX, (i) => `${x[i]}  ${series.map((s) => `${s.name} ${s.values[i] === null || s.values[i] === undefined ? "–" : yFormat(s.values[i])}`).join("  ")}`);
    if (series.length > 1 || series[0].name) box.append(legend(series.map((s, i) => ({ label: s.name, color: s.color || PALETTE[i % PALETTE.length], kind: s.dash ? "dash" : "line" }))));
  };
  box.classList.add("chart");
  requestAnimationFrame(draw);
}

export function barChart(box, { x, series, yFormat = (v) => String(v), label = "" }) {
  const draw = () => {
    box.replaceChildren();
    const f = frame(box, Math.max(300, x.length * 26 + MARGIN.left + MARGIN.right));
    f.svg.setAttribute("aria-label", label);
    const all = series.flatMap((s) => s.values.filter((v) => v !== null && v !== undefined));
    const ticks = niceTicks(Math.min(0, ...all), Math.max(1, ...all));
    const [low, high] = [ticks[0], ticks[ticks.length - 1]];
    const slot = (f.right - f.left) / x.length;
    const toX = (i) => f.left + slot * (i + 0.5);
    const toY = (v) => f.bottom - ((v - low) / (high - low || 1)) * (f.bottom - f.top);
    grid(f, ticks, toY, yFormat);
    xLabels(f, x, toX, Math.max(1, Math.ceil(x.length / Math.max(3, Math.floor((f.right - f.left) / 44)))));
    const width = Math.max(3, (slot * 0.7) / series.length);
    series.forEach((s, seriesIndex) => {
      const color = s.color || PALETTE[seriesIndex % PALETTE.length];
      s.values.forEach((value, index) => {
        if (value === null || value === undefined) return;
        const top = toY(Math.max(value, 0)), base = toY(Math.min(value, 0));
        f.svg.append(svgEl("rect", { x: toX(index) - (width * series.length) / 2 + seriesIndex * width, y: top, width: width - 1, height: Math.max(base - top, 0.5), fill: color, rx: 1.5 }));
      });
    });
    const scroll = el("div", null, "chart-scroll");
    scroll.append(f.svg);
    box.append(scroll);
    tooltip(scroll, f, x.length, toX, (i) => `${x[i]}  ${series.map((s) => `${s.name} ${s.values[i] === null || s.values[i] === undefined ? "–" : yFormat(s.values[i])}`).join("  ")}`);
    box.append(legend(series.map((s, i) => ({ label: s.name, color: s.color || PALETTE[i % PALETTE.length], kind: "bar" }))));
  };
  box.classList.add("chart");
  requestAnimationFrame(draw);
}

// A run's recording: pace and heart rate over distance. points rows: [km, seconds, pace_s_per_km, hr, lat, lon, altitude, cadence]
export function runCharts(box, points, planned) {
  const rows = points.filter((p) => p[0] !== null);
  if (rows.length < 2) return;
  const x = rows.map((p) => `${p[0].toFixed(1)} km`);
  const band = planned ? [...String(planned.target_pace || "").matchAll(/(\d+):(\d\d)/g)].map((m) => Number(m[1]) * 60 + Number(m[2])) : [];
  const paces = rows.map((p) => (p[2] && p[2] < 900 ? p[2] : null));
  if (paces.some((v) => v !== null)) {
    const pace = el("div");
    const series = [{ name: "Pace /km", values: paces, color: "var(--pace)" }];
    for (const target of band.slice(0, 2)) series.push({ name: `Target ${paceText(target)}`, values: rows.map(() => target), color: "var(--ink-3)", dash: true });
    lineChart(pace, { x, series, yFormat: paceText, invert: true, label: "Pace over distance" });
    box.append(pace);
  }
  const hr = rows.map((p) => p[3] || null);
  if (hr.some((v) => v !== null)) {
    const heart = el("div");
    lineChart(heart, { x, series: [{ name: "Heart rate", values: hr, color: "var(--hr)" }], yFormat: (v) => String(Math.round(v)), label: "Heart rate over distance" });
    box.append(heart);
  }
}

export function routeMap(points) {
  const track = points.filter((p) => p[4] !== null && p[5] !== null && p[4] !== undefined);
  if (track.length < 10) return null;
  const lats = track.map((p) => p[4]), lons = track.map((p) => p[5]);
  const [minLat, maxLat, minLon, maxLon] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
  const squeeze = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
  const w = (maxLon - minLon) * squeeze || 1e-6, h = maxLat - minLat || 1e-6;
  const size = 220, scale = (size - 20) / Math.max(w, h);
  const d = track.map((p, i) => `${i ? "L" : "M"}${(10 + (p[5] - minLon) * squeeze * scale).toFixed(1)} ${(size - 10 - (p[4] - minLat) * scale).toFixed(1)}`).join("");
  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: "img", "aria-label": "Route", class: "route" });
  svg.append(svgEl("path", { d, fill: "none", stroke: "var(--route)", "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  svg.append(svgEl("circle", { cx: d.match(/M([\d.]+)/)[1], cy: d.match(/M[\d.]+ ([\d.]+)/)[1], r: 4, fill: "var(--good)" }));
  return svg;
}
