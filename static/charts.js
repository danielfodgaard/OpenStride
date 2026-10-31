// Plain inline SVG charts. Every chart comes with a table of the same numbers.
import { el, table } from "./render/ui.js";
import { paceText, timeText } from "./render/format.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const MS_PER_DAY = 86400000;
const WIDTH = 760;
const HEIGHT = 300;
const MARGIN = { top: 16, right: 64, bottom: 36, left: 64 };
const COLORS = { blue: "#1f5fbf", red: "#c0392b", green: "#2e7d32", grey: "#888888" };

function svgEl(tag, attributes, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function newSvg(height, label) {
  return svgEl("svg", { viewBox: `0 0 ${WIDTH} ${height}`, width: WIDTH, height, role: "img", "aria-label": label });
}

const dayMs = (isoDate) => Date.parse(`${isoDate}T00:00:00Z`);
const monthDay = (ms) => new Date(ms).toISOString().slice(5, 10);

// 4-6 round tick values covering min..max.
function niceTicks(min, max) {
  const low = min === max ? min - 1 : min;
  const high = min === max ? max + 1 : max;
  const raw = (high - low) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => (Math.ceil(high / s) - Math.floor(low / s)) <= 5);
  const start = Math.floor(low / step) * step;
  const ticks = [];
  for (let value = start; value < high + step * 0.999; value += step) ticks.push(Number(value.toFixed(6)));
  return ticks;
}

function tableToggle(headers, rows) {
  const box = el("details");
  box.append(el("summary", "Table"), table(headers, rows));
  return box;
}

function chartBox(title, svg, legend, headers, rows) {
  const box = el("div");
  box.append(el("h3", title), svg, el("p", legend), tableToggle(headers, rows));
  return box;
}

// series: [{ name, color, points: [{x, y}], axis: "left"|"right", dashed, dots }]
// options: { title, leftFormat, rightFormat, marker: {x, label}, xMax, headers, rows }
function drawSeriesChart({ title, series, leftFormat = String, rightFormat = String, marker, xMax, headers, rows }) {
  const filled = series.filter((s) => s.points.length);
  if (!filled.length) {
    const box = el("div");
    box.append(el("h3", title), el("p", "No data yet."));
    return box;
  }
  const xs = filled.flatMap((s) => s.points.map((p) => p.x));
  const left = Math.min(...xs);
  const right = Math.max(...xs, xMax ?? -Infinity, left + 4 * MS_PER_DAY);  // room for 5 date labels
  const xSpan = right - left || 1;
  const toX = (x) => MARGIN.left + ((x - left) / xSpan) * (WIDTH - MARGIN.left - MARGIN.right);
  const scaleFor = (axis) => {
    const ys = filled.filter((s) => s.axis === axis).flatMap((s) => s.points.map((p) => p.y));
    if (!ys.length) return null;
    const ticks = niceTicks(Math.min(...ys), Math.max(...ys));
    const low = ticks[0];
    const span = ticks[ticks.length - 1] - low;
    return { ticks, toY: (y) => HEIGHT - MARGIN.bottom - ((y - low) / span) * (HEIGHT - MARGIN.top - MARGIN.bottom) };
  };
  const scales = { left: scaleFor("left"), right: scaleFor("right") };
  const svg = newSvg(HEIGHT, title);

  for (const [axis, format, labelX, anchor] of [["left", leftFormat, MARGIN.left - 6, "end"], ["right", rightFormat, WIDTH - MARGIN.right + 6, "start"]]) {
    if (!scales[axis]) continue;
    for (const tick of scales[axis].ticks) {
      const y = scales[axis].toY(tick);
      if (axis === "left" || !scales.left) {
        svg.append(svgEl("line", { x1: MARGIN.left, x2: WIDTH - MARGIN.right, y1: y, y2: y, stroke: "#dddddd" }));
      }
      svg.append(svgEl("text", { x: labelX, y: y + 4, "text-anchor": anchor, "font-size": 11 }, format(tick)));
    }
  }
  for (let i = 0; i <= 4; i += 1) {
    const x = left + (xSpan * i) / 4;
    svg.append(svgEl("text", { x: toX(x), y: HEIGHT - 12, "text-anchor": "middle", "font-size": 11 }, monthDay(x)));
  }
  svg.append(svgEl("line", {
    x1: MARGIN.left, x2: MARGIN.left, y1: MARGIN.top, y2: HEIGHT - MARGIN.bottom, stroke: "#333333",
  }));
  svg.append(svgEl("line", {
    x1: MARGIN.left, x2: WIDTH - MARGIN.right, y1: HEIGHT - MARGIN.bottom, y2: HEIGHT - MARGIN.bottom, stroke: "#333333",
  }));
  if (marker && marker.x >= left && marker.x <= right) {
    const x = toX(marker.x);
    svg.append(svgEl("line", { x1: x, x2: x, y1: MARGIN.top, y2: HEIGHT - MARGIN.bottom, stroke: "#000000", "stroke-dasharray": "2 3" }));
    svg.append(svgEl("text", { x: x - 4, y: MARGIN.top + 10, "text-anchor": "end", "font-size": 11 }, marker.label));
  }
  for (const line of filled) {
    const { toY } = scales[line.axis];
    const path = line.points.map((p) => `${toX(p.x).toFixed(1)},${toY(p.y).toFixed(1)}`).join(" ");
    svg.append(svgEl("polyline", {
      points: path, fill: "none", stroke: line.color, "stroke-width": line.dashed ? 1 : 2,
      ...(line.dashed ? { "stroke-dasharray": "5 4" } : {}),
    }));
    if (line.dots) {
      for (const p of line.points) svg.append(svgEl("circle", { cx: toX(p.x), cy: toY(p.y), r: 3, fill: line.color }));
    }
  }
  const legend = filled.map((s) => `${s.name}${s.axis === "right" && scales.left ? " (right axis)" : ""}${s.dashed ? " (dashed)" : ""}`).join(" · ");
  return chartBox(title, svg, legend, headers, rows);
}

// bars: [{ label, planned, actual (null for future weeks), notes: [..] }]
function drawBarChart({ title, bars, headers, rows }) {
  const height = HEIGHT + 24;
  const bottom = MARGIN.bottom + 24;
  const ticks = niceTicks(0, Math.max(1, ...bars.flatMap((b) => [b.planned, b.actual ?? 0])));
  const top = ticks[ticks.length - 1];
  const toY = (y) => height - bottom - (y / top) * (height - MARGIN.top - bottom);
  const svg = newSvg(height, title);
  const slot = (WIDTH - MARGIN.left - MARGIN.right) / bars.length;
  const barWidth = Math.min(slot * 0.4, 24);
  for (const tick of ticks) {
    svg.append(svgEl("line", { x1: MARGIN.left, x2: WIDTH - MARGIN.right, y1: toY(tick), y2: toY(tick), stroke: "#dddddd" }));
    svg.append(svgEl("text", { x: MARGIN.left - 6, y: toY(tick) + 4, "text-anchor": "end", "font-size": 11 }, tick));
  }
  const small = slot < 34 ? 7 : 9;
  const wide = slot >= 60;  // room for "S 2/2 · X 1/1" on one line
  bars.forEach((bar, i) => {
    const middle = MARGIN.left + slot * (i + 0.5);
    const addBar = (x, value, color) => svg.append(svgEl("rect", {
      x, y: toY(value), width: barWidth, height: toY(0) - toY(value), fill: color,
    }));
    addBar(middle - barWidth, bar.planned, COLORS.grey);
    if (bar.actual !== null) addBar(middle, bar.actual, COLORS.blue);
    svg.append(svgEl("text", { x: middle, y: height - bottom + 13, "text-anchor": "middle", "font-size": 10 }, bar.label));
    (wide ? [bar.notes.join(" · ")] : bar.notes).forEach((note, line) => svg.append(svgEl("text", {
      x: middle, y: height - bottom + 25 + line * 10, "text-anchor": "middle", "font-size": small,
    }, note)));
  });
  return chartBox(title, svg, "Planned km (grey) · Actual km (blue)", headers, rows);
}

const signed = (value) => `${value < 0 ? "−" : "+"}${Math.abs(Math.round(value))}`;

export function snapshotText({ ctl, atl, tsb, ctl_7d_change: change }) {
  const form = tsb < 0 ? `−${Math.abs(Math.round(tsb))}` : Math.round(tsb);
  return `Fitness ${Math.round(ctl)} (${signed(change)} this week), fatigue ${Math.round(atl)}, form ${form}`;
}

function weekNotes(week) {
  const notes = [];
  if (week.planned_strength || week.completed_strength) notes.push(`S ${week.completed_strength}/${week.planned_strength}`);
  if (week.planned_cross || week.completed_cross) notes.push(`X ${week.completed_cross}/${week.planned_cross}`);
  return notes;
}

export function drawProgressCharts(metrics, raceDate) {
  const days = metrics.days.map((d) => ({ ...d, x: dayMs(d.date) }));
  const line = (name, color, key) => ({ name, color, axis: "left", points: days.map((d) => ({ x: d.x, y: d[key] })) });
  const fitness = drawSeriesChart({
    title: "Fitness & fatigue",
    series: [line("Fitness (CTL)", COLORS.blue, "ctl"), line("Fatigue (ATL)", COLORS.red, "atl"), line("Form (TSB)", COLORS.green, "tsb")],
    marker: { x: dayMs(raceDate), label: "Race" },
    xMax: dayMs(raceDate),
    headers: ["Date", "Load", "CTL", "ATL", "TSB", "km"],
    rows: metrics.days.map((d) => [d.date, d.load, d.ctl, d.atl, d.tsb, d.km]),
  });

  const volume = drawBarChart({
    title: "Weekly volume",
    bars: metrics.weeks.map((w) => ({
      label: `W${w.week_number}`,
      planned: w.planned_km,
      actual: w.start_date > metrics.as_of ? null : w.actual_km,
      notes: weekNotes(w),
    })),
    headers: ["Week", "Start", "Planned km", "Actual km", "Sessions", "Strength", "Cross", "Cross min"],
    rows: metrics.weeks.map((w) => [
      w.week_number, w.start_date, w.planned_km, w.start_date > metrics.as_of ? "" : w.actual_km,
      `${w.completed_sessions}/${w.planned_sessions}`, `${w.completed_strength}/${w.planned_strength}`,
      `${w.completed_cross}/${w.planned_cross}`, `${w.actual_cross_min}/${w.planned_cross_min}`,
    ]),
  });

  return [fitness, el("p", snapshotText(metrics.current)), volume];
}

// The route, in the style of the reference page: latitude-corrected, start dot, finish square.
export function drawRoute(points) {
  const located = points.map((p, i) => ({ lat: p[4], lon: p[5], i })).filter((p) => p.lat !== null && p.lon !== null);
  if (located.length < 2) return null;
  const k = Math.cos((located[0].lat * Math.PI) / 180);
  const xs = located.map((p) => p.lon * k);
  const ys = located.map((p) => -p.lat);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const span = Math.max(maxX - minX, maxY - minY) || 1e-6;
  const size = 300;
  const pad = 22;
  const inner = size - 2 * pad;
  const left = pad + (inner - ((maxX - minX) / span) * inner) / 2;
  const top = pad + (inner - ((maxY - minY) / span) * inner) / 2;
  const place = (n) => [left + ((xs[n] - minX) / span) * inner, top + ((ys[n] - minY) / span) * inner];

  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: "img", "aria-label": "Route map" });
  const barLength = (1 / 111.32 / span) * inner;  // one km of latitude
  if (barLength < inner) {
    svg.append(svgEl("line", { x1: pad, y1: 292, x2: pad + barLength, y2: 292, stroke: "#7a8a95", "stroke-width": 2 }));
    svg.append(svgEl("text", { x: pad + barLength + 6, y: 296, "font-size": 11 }, "1 km"));
  }
  svg.append(svgEl("polyline", {
    points: located.map((_, n) => place(n).map((v) => v.toFixed(1)).join(",")).join(" "),
    fill: "none", stroke: "#15212a", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
  }));
  const [startX, startY] = place(0);
  const [endX, endY] = place(located.length - 1);
  svg.append(svgEl("circle", { cx: startX, cy: startY, r: 5, fill: "#0d7a87", stroke: "#ffffff", "stroke-width": 2 }));
  svg.append(svgEl("rect", { x: endX - 5, y: endY - 5, width: 10, height: 10, fill: "#b8325c", stroke: "#ffffff", "stroke-width": 2 }));
  const marker = svgEl("circle", { r: 6, fill: "#ffffff", stroke: "#15212a", "stroke-width": 2.5, visibility: "hidden" });
  svg.append(marker);

  const byPoint = new Map(located.map((p, n) => [p.i, n]));
  return {
    svg,
    mark(pointIndex) {
      const n = byPoint.get(pointIndex);
      if (n === undefined) return marker.setAttribute("visibility", "hidden");
      const [x, y] = place(n);
      marker.setAttribute("cx", x);
      marker.setAttribute("cy", y);
      return marker.setAttribute("visibility", "visible");
    },
    clear: () => marker.setAttribute("visibility", "hidden"),
  };
}

// Round values spaced so that 3-6 ticks cover lo..hi.
function ticksFor(lo, hi, steps) {
  const step = steps.find((s) => (hi - lo) / s <= 5) ?? steps[steps.length - 1];
  const ticks = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t);
  return ticks;
}

function quantile(sorted, share) {
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * share))];
}

const CHART_W = 640;
const CHART_LEFT = 44;
const CHART_RIGHT = 10;

function distanceChart({ points, index, height, xAxis, maxKm, lo, hi, invert, ticks, format, color, band, label }) {
  const bottom = xAxis ? 26 : 8;
  const top = 10;
  const svg = svgEl("svg", { viewBox: `0 0 ${CHART_W} ${height}`, width: CHART_W, height, role: "img", "aria-label": label });
  const x = (km) => CHART_LEFT + (km / maxKm) * (CHART_W - CHART_LEFT - CHART_RIGHT);
  const share = (v) => (v - lo) / (hi - lo);
  const y = (v) => (invert ? top + share(v) * (height - top - bottom) : height - bottom - share(v) * (height - top - bottom));
  const clamp = (v) => Math.max(lo, Math.min(hi, v));
  if (band) {
    const [y1, y2] = [y(clamp(band[0])), y(clamp(band[1]))];
    svg.append(svgEl("rect", {
      x: CHART_LEFT, y: Math.min(y1, y2), width: CHART_W - CHART_LEFT - CHART_RIGHT, height: Math.abs(y2 - y1),
      fill: color, "fill-opacity": 0.12,
    }));
  }
  for (const tick of ticks) {
    svg.append(svgEl("line", { x1: CHART_LEFT, x2: CHART_W - CHART_RIGHT, y1: y(tick), y2: y(tick), stroke: "#e9eef0" }));
    svg.append(svgEl("text", { x: CHART_LEFT - 8, y: y(tick) + 4, "text-anchor": "end", "font-size": 11 }, format(tick)));
  }
  if (xAxis) {
    const step = Math.max(1, Math.ceil(maxKm / 10));
    for (let km = 0; km <= maxKm; km += step) {
      svg.append(svgEl("text", { x: x(km), y: height - 6, "text-anchor": "middle", "font-size": 11 }, km));
    }
    svg.append(svgEl("text", { x: CHART_W - CHART_RIGHT, y: height - 6, "text-anchor": "end", "font-size": 11 }, "km"));
  }
  // A stop breaks the line rather than drawing across it.
  let segment = [];
  const flush = () => {
    if (segment.length > 1) {
      svg.append(svgEl("polyline", { points: segment.join(" "), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }));
    }
    segment = [];
  };
  for (const p of points) {
    if (p[index] === null) flush();
    else segment.push(`${x(p[0]).toFixed(1)},${y(clamp(p[index])).toFixed(1)}`);
  }
  flush();
  const cross = svgEl("line", { y1: top, y2: height - bottom, stroke: "#7a8a95", "stroke-dasharray": "3 3", visibility: "hidden" });
  const dot = svgEl("circle", { r: 4.5, fill: color, stroke: "#ffffff", "stroke-width": 2, visibility: "hidden" });
  svg.append(cross, dot);
  return {
    svg,
    show(p) {
      cross.setAttribute("x1", x(p[0]));
      cross.setAttribute("x2", x(p[0]));
      cross.setAttribute("visibility", "visible");
      if (p[index] === null) return dot.setAttribute("visibility", "hidden");
      dot.setAttribute("cx", x(p[0]));
      dot.setAttribute("cy", y(clamp(p[index])));
      return dot.setAttribute("visibility", "visible");
    },
    hide() {
      cross.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
    },
  };
}

function hoverLabel() {
  const box = svgEl("rect", { width: 150, height: 40, rx: 4, fill: "#15212a" });
  const first = svgEl("text", { fill: "#ffffff", "font-size": 12 });
  const second = svgEl("text", { fill: "#ffffff", "font-size": 12 });
  const group = svgEl("g", { visibility: "hidden" });
  group.append(box, first, second);
  return {
    group,
    show(p, svgX) {
      const left = Math.min(Math.max(svgX - 75, CHART_LEFT), CHART_W - CHART_RIGHT - 150);
      group.setAttribute("transform", `translate(${left},12)`);
      first.setAttribute("x", 8);
      first.setAttribute("y", 16);
      second.setAttribute("x", 8);
      second.setAttribute("y", 32);
      first.textContent = `${p[0].toFixed(2)} km · ${timeText(p[1])}`;
      second.textContent = `${p[2] === null ? "–" : `${paceText(p[2])}/km`} · ${p[3] ?? "–"} bpm`;
      group.setAttribute("visibility", "visible");
    },
    hide: () => group.setAttribute("visibility", "hidden"),
  };
}

// Pace above heart rate on one distance axis, never two y-axes. Hovering either moves a shared
// crosshair and tooltip, and the marker on `route`.
export function drawDistanceCharts(points, band, route) {
  const paces = points.map((p) => p[2]).filter((v) => v !== null).sort((a, b) => a - b);
  const heartRates = points.map((p) => p[3]).filter((v) => v !== null).sort((a, b) => a - b);
  const maxKm = points[points.length - 1][0] || 1;
  const box = el("div");
  const charts = [];

  if (paces.length) {
    // A few seconds of walking shouldn't flatten the whole chart.
    const low = Math.min(quantile(paces, 0.01), band?.[0] ?? Infinity);
    const high = Math.max(quantile(paces, 0.99), band?.[1] ?? -Infinity);
    const lo = Math.floor(low / 15) * 15 - 15;
    const hi = Math.ceil(high / 15) * 15 + 15;
    charts.push(distanceChart({
      points, index: 2, height: 170, xAxis: !heartRates.length, maxKm, lo, hi, invert: true,
      ticks: ticksFor(lo, hi, [15, 30, 60, 120, 300, 600]), format: paceText, color: "#0d7a87", band, label: "Pace over distance",
    }));
  }
  if (heartRates.length) {
    const lo = Math.floor((heartRates[0] - 5) / 10) * 10;
    const hi = Math.ceil((heartRates[heartRates.length - 1] + 5) / 10) * 10;
    charts.push(distanceChart({
      points, index: 3, height: 150, xAxis: true, maxKm, lo, hi, invert: false,
      ticks: ticksFor(lo, hi, [10, 20, 40]), format: String, color: "#b8325c", label: "Heart rate over distance",
    }));
  }
  if (!charts.length) return null;
  const label = hoverLabel();
  charts[0].svg.append(label.group);
  for (const chart of charts) box.append(chart.svg);

  const nearest = (km) => {
    let best = 0;
    for (let i = 1; i < points.length; i += 1) {
      if (Math.abs(points[i][0] - km) < Math.abs(points[best][0] - km)) best = i;
    }
    return best;
  };
  const hide = () => {
    for (const chart of charts) chart.hide();
    label.hide();
    route?.clear();
  };
  const move = (clientX) => {
    const rect = charts[0].svg.getBoundingClientRect();
    const svgX = ((clientX - rect.left) / rect.width) * CHART_W;
    const km = ((svgX - CHART_LEFT) / (CHART_W - CHART_LEFT - CHART_RIGHT)) * maxKm;
    if (km < 0 || km > maxKm) return hide();
    const best = nearest(km);
    for (const chart of charts) chart.show(points[best]);
    label.show(points[best], svgX);
    return route?.mark(best);
  };
  box.addEventListener("mousemove", (event) => move(event.clientX));
  box.addEventListener("mouseleave", hide);
  box.addEventListener("touchstart", (event) => { if (event.touches[0]) move(event.touches[0].clientX); }, { passive: true });
  box.addEventListener("touchmove", (event) => { if (event.touches[0]) move(event.touches[0].clientX); }, { passive: true });
  box.addEventListener("touchend", hide);
  return box;
}
