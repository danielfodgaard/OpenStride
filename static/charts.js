// Plain inline SVG charts. Every chart comes with a table of the same numbers.
import { el, main, table } from "./render/ui.js";
import { dayMs, paceText, roundKm, timeText } from "./render/format.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const MS_PER_DAY = 86400000;
const HEIGHT = 280;
const MARGIN = { top: 14, right: 12, bottom: 28, left: 40 };
const BAR_SLOT = 25;  // narrowest week that still fits its percentage; more weeks scroll sideways
const COLORS = { fitness: "var(--accent)", fatigue: "var(--load-atl)", form: "var(--ink-2)", grid: "var(--grid)", axis: "var(--ink-3)" };

export function svgEl(tag, attributes, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function newSvg(width, height, label) {
  return svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": label });
}

// Charts are drawn at the size they show at, so 11 px labels stay 11 px on a phone.
function availableWidth(share = 1) {
  const inner = main.clientWidth - 66;  // page and card padding
  return Math.max(260, Math.round(share * inner));
}

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

// Horizontal grid lines with their values on the left.
function gridLines(svg, ticks, toY, left, right, format = String) {
  for (const tick of ticks) {
    svg.append(svgEl("line", { x1: left, x2: right, y1: toY(tick), y2: toY(tick), stroke: COLORS.grid }));
    svg.append(svgEl("text", { x: left - 6, y: toY(tick) + 4, "text-anchor": "end", "font-size": 11 }, format(tick)));
  }
}

// items: [{ label, color, kind: "bar" | "line" | "dash" }]
export function legend(items) {
  const box = el("div");
  box.className = "legend";
  for (const { label, color, kind = "bar" } of items) {
    const key = el("span");
    const swatch = el("i");
    swatch.className = kind;
    swatch.style.background = kind === "bar" ? color : "";
    swatch.style.borderColor = color;
    key.append(swatch, label);
    box.append(key);
  }
  return box;
}

function chartBox(title, plot, items, headers, rows) {
  const box = el("div");
  box.className = "chart";
  const numbers = el("details");
  numbers.append(el("summary", "Table"), table(headers, rows));
  box.append(el("h3", title), plot, legend(items), numbers);
  return box;
}

// A hover and touch tooltip. `textAt` turns an x in SVG units into text, or null for none.
function addTip(box, svg, width, textAt) {
  const tip = el("div");
  tip.className = "tip";
  tip.hidden = true;
  box.append(tip);
  const hide = () => { tip.hidden = true; };
  const show = ({ clientX, clientY }) => {
    const rect = svg.getBoundingClientRect();
    const text = textAt(((clientX - rect.left) / rect.width) * width);
    if (!text) return hide();
    tip.textContent = text;
    tip.hidden = false;
    const outer = box.getBoundingClientRect();
    tip.style.left = `${Math.max(0, Math.min(clientX - outer.left - tip.offsetWidth / 2, outer.width - tip.offsetWidth))}px`;
    tip.style.top = `${Math.max(0, clientY - outer.top - tip.offsetHeight - 14)}px`;
    return undefined;
  };
  svg.addEventListener("mousemove", show);
  svg.addEventListener("mouseleave", hide);
  svg.addEventListener("touchstart", (event) => { if (event.touches[0]) show(event.touches[0]); }, { passive: true });
  svg.addEventListener("touchmove", (event) => { if (event.touches[0]) show(event.touches[0]); }, { passive: true });
  svg.addEventListener("touchend", hide);
}

// series: [{ name, color, points: [{x, y}], dashed }], all on the one y-axis.
function drawSeriesChart({ title, series, format = String, marker, xMax, headers, rows, tipAt }) {
  const filled = series.filter((s) => s.points.length);
  if (!filled.length) {
    const box = el("div");
    box.append(el("h3", title), el("p", "No data yet."));
    return box;
  }
  const width = availableWidth();
  const xs = filled.flatMap((s) => s.points.map((p) => p.x));
  const left = Math.min(...xs);
  const right = Math.max(...xs, xMax ?? -Infinity, left + 4 * MS_PER_DAY);  // room for the date labels
  const xSpan = right - left || 1;
  const toX = (x) => MARGIN.left + ((x - left) / xSpan) * (width - MARGIN.left - MARGIN.right);
  const ys = filled.flatMap((s) => s.points.map((p) => p.y));
  const ticks = niceTicks(Math.min(...ys), Math.max(...ys));
  const low = ticks[0];
  const span = ticks[ticks.length - 1] - low;
  const toY = (y) => HEIGHT - MARGIN.bottom - ((y - low) / span) * (HEIGHT - MARGIN.top - MARGIN.bottom);
  const svg = newSvg(width, HEIGHT, title);

  gridLines(svg, ticks, toY, MARGIN.left, width - MARGIN.right, format);
  const labels = width < 420 ? 3 : 4;
  for (let i = 0; i <= labels; i += 1) {
    const x = left + (xSpan * i) / labels;
    const anchor = i === labels ? "end" : i ? "middle" : "start";
    svg.append(svgEl("text", { x: toX(x), y: HEIGHT - 8, "text-anchor": anchor, "font-size": 11 }, monthDay(x)));
  }
  if (marker && marker.x >= left && marker.x <= right) {
    const x = toX(marker.x);
    svg.append(svgEl("line", { x1: x, x2: x, y1: MARGIN.top, y2: HEIGHT - MARGIN.bottom, stroke: COLORS.axis, "stroke-dasharray": "2 3" }));
    svg.append(svgEl("text", { x: x - 4, y: MARGIN.top + 10, "text-anchor": "end", "font-size": 11 }, marker.label));
  }
  for (const line of filled) {
    svg.append(svgEl("polyline", {
      points: line.points.map((p) => `${toX(p.x).toFixed(1)},${toY(p.y).toFixed(1)}`).join(" "),
      fill: "none", stroke: line.color, "stroke-width": 2, "stroke-linejoin": "round",
      ...(line.dashed ? { "stroke-dasharray": "5 4" } : {}),
    }));
  }
  const cross = svgEl("line", { y1: MARGIN.top, y2: HEIGHT - MARGIN.bottom, stroke: COLORS.axis, "stroke-dasharray": "3 3", visibility: "hidden" });
  svg.append(cross);
  const items = filled.map((s) => ({ label: s.name, color: s.color, kind: s.dashed ? "dash" : "line" }));
  const box = chartBox(title, svg, items, headers, rows);
  addTip(box, svg, width, (svgX) => {
    const hit = tipAt(left + ((svgX - MARGIN.left) / (width - MARGIN.left - MARGIN.right)) * xSpan);
    cross.setAttribute("visibility", hit ? "visible" : "hidden");
    if (!hit) return null;
    cross.setAttribute("x1", toX(hit.x));
    cross.setAttribute("x2", toX(hit.x));
    return hit.text;
  });
  svg.addEventListener("mouseleave", () => cross.setAttribute("visibility", "hidden"));
  return box;
}

// A bar with a rounded top, anchored square on the baseline.
function barPath(x, y, width, base) {
  const r = Math.min(3, width / 2, (base - y) / 2);
  return `M${x} ${base}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + width - r}Q${x + width} ${y} ${x + width} ${y + r}V${base}Z`;
}

// bars: [{ label, planned, actual (null for weeks not started), percent, notes: [..], tip }]
// Each bar reads as a progress bar: grey is the plan, the accent fill is what's done.
function drawBarChart({ title, bars, headers, rows }) {
  const margin = { top: 20, right: 0, left: 28 };
  const height = HEIGHT + 10;
  const width = Math.max(availableWidth(), margin.left + margin.right + bars.length * BAR_SLOT);
  const slot = (width - margin.left - margin.right) / bars.length;
  const hasNotes = slot >= 30 && bars.some((b) => b.notes.length);  // narrower, they live in the tooltip and table
  const bottom = 22 + (hasNotes ? 22 : 0);
  const ticks = niceTicks(0, Math.max(1, ...bars.flatMap((b) => [b.planned, b.actual ?? 0])));
  const top = ticks[ticks.length - 1];
  const toY = (y) => height - bottom - (y / top) * (height - margin.top - bottom);
  const svg = newSvg(width, height, title);
  const barWidth = Math.min(slot * 0.68, 44);
  gridLines(svg, ticks, toY, margin.left, width - margin.right);
  const wide = slot >= 76;  // room for "S 2/2 · X 1/1" on one line
  bars.forEach((bar, i) => {
    const middle = margin.left + slot * (i + 0.5);
    const x = middle - barWidth / 2;
    svg.append(svgEl("path", { d: barPath(x, toY(bar.planned), barWidth, toY(0)), fill: "var(--bar)" }));
    if (bar.actual) svg.append(svgEl("path", { d: barPath(x, toY(bar.actual), barWidth, toY(0)), fill: "var(--accent)" }));
    if (bar.actual > bar.planned) {
      svg.append(svgEl("line", {
        x1: x - 3, x2: x + barWidth + 3, y1: toY(bar.planned), y2: toY(bar.planned), stroke: "var(--ink)", "stroke-width": 1.5,
      }));
    }
    if (bar.percent !== null) {
      svg.append(svgEl("text", {
        x: middle, y: toY(Math.max(bar.planned, bar.actual)) - 5, "text-anchor": "middle", "font-size": slot < 30 ? 9 : 10,
      }, `${bar.percent}%`));
    }
    svg.append(svgEl("text", { x: middle, y: height - bottom + 14, "text-anchor": "middle", "font-size": 11 }, bar.label));
    if (hasNotes) (wide ? [bar.notes.join(" · ")] : bar.notes).forEach((note, line) => svg.append(svgEl("text", {
      x: middle, y: height - bottom + 26 + line * 10, "text-anchor": "middle", "font-size": 9,
    }, note)));
  });
  const scroller = el("div");
  scroller.className = "chart-scroll";
  scroller.append(svg);
  const items = [{ label: "Planned km", color: "var(--bar)" }, { label: "Completed km", color: "var(--accent)" }];
  if (bars.some((b) => b.actual > b.planned)) items.push({ label: "Planned level", color: "var(--ink)", kind: "line" });
  const box = chartBox(title, scroller, items, headers, rows);
  addTip(box, svg, width, (svgX) => bars[Math.floor((svgX - margin.left) / slot)]?.tip ?? null);
  return box;
}

const signed = (value) => `${Math.round(value) < 0 ? "−" : "+"}${Math.abs(Math.round(value))}`;
const formText = (tsb) => (Math.round(tsb) < 0 ? `−${Math.abs(Math.round(tsb))}` : Math.abs(Math.round(tsb)));

function snapshotText({ ctl, atl, tsb, ctl_7d_change: change }) {
  return `Fitness ${Math.round(ctl)} (${signed(change)} this week), fatigue ${Math.round(atl)}, form ${formText(tsb)}`;
}

function weekNotes(week) {
  const notes = [];
  if (week.planned_strength || week.completed_strength) notes.push(`S ${week.completed_strength}/${week.planned_strength}`);
  if (week.planned_cross || week.completed_cross) notes.push(`X ${week.completed_cross}/${week.planned_cross}`);
  return notes;
}

// A week counts once it has started, or as soon as it holds any km, whatever their date.
export const notStarted = (week, asOf) => week.start_date > asOf && !week.actual_km;

function weekPercent(week, asOf) {
  return notStarted(week, asOf) || !week.planned_km ? null : Math.round((week.actual_km / week.planned_km) * 100);
}

function weekTip(week, asOf) {
  const km = notStarted(week, asOf) ? `${roundKm(week.planned_km)} km planned`
    : `${roundKm(week.actual_km)} of ${roundKm(week.planned_km)} km${week.planned_km ? ` (${weekPercent(week, asOf)}%)` : ""}`;
  return [`Week ${week.week_number}`, km, `${week.completed_sessions} of ${week.planned_sessions} sessions`, ...weekNotes(week)].join(" · ");
}

export function drawProgressCharts(metrics, raceDate) {
  const days = metrics.days.map((d) => ({ ...d, x: dayMs(d.date) }));
  const line = (name, color, key, dashed) => ({ name, color, dashed, points: days.map((d) => ({ x: d.x, y: d[key] })) });
  const fitness = drawSeriesChart({
    title: "Fitness & fatigue",
    series: [
      line("Fitness (CTL)", COLORS.fitness, "ctl"),
      line("Fatigue (ATL)", COLORS.fatigue, "atl"),
      line("Form (TSB)", COLORS.form, "tsb", true),
    ],
    marker: { x: dayMs(raceDate), label: "Race" },
    xMax: dayMs(raceDate),
    headers: ["Date", "Load", "CTL", "ATL", "TSB", "km"],
    rows: metrics.days.map((d) => [d.date, d.load, d.ctl, d.atl, d.tsb, d.km]),
    tipAt: (x) => {
      const day = days.reduce((best, d) => (Math.abs(d.x - x) < Math.abs(best.x - x) ? d : best), days[0]);
      if (Math.abs(day.x - x) > MS_PER_DAY) return null;  // past the last day there's nothing to read
      return { x: day.x, text: `${day.date} · Fitness ${Math.round(day.ctl)} · Fatigue ${Math.round(day.atl)} · Form ${formText(day.tsb)}` };
    },
  });

  const volume = drawBarChart({
    title: "Weekly volume",
    bars: metrics.weeks.map((w) => ({
      label: `W${w.week_number}`,
      planned: w.planned_km,
      actual: notStarted(w, metrics.as_of) ? null : w.actual_km,
      percent: weekPercent(w, metrics.as_of),
      notes: weekNotes(w),
      tip: weekTip(w, metrics.as_of),
    })),
    headers: ["Week", "Start", "Planned km", "Actual km", "%", "Sessions", "Strength", "Cross", "Cross min"],
    rows: metrics.weeks.map((w) => {
      const percent = weekPercent(w, metrics.as_of);
      return [
        w.week_number, w.start_date, w.planned_km, notStarted(w, metrics.as_of) ? "" : w.actual_km, percent === null ? "" : `${percent}%`,
        `${w.completed_sessions}/${w.planned_sessions}`, `${w.completed_strength}/${w.planned_strength}`,
        `${w.completed_cross}/${w.planned_cross}`, `${w.actual_cross_min}/${w.planned_cross_min}`,
      ];
    }),
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

  const svg = newSvg(size, size, "Route map");
  const barLength = (1 / 111.32 / span) * inner;  // one km of latitude
  if (barLength < inner) {
    svg.append(svgEl("line", { x1: pad, y1: 292, x2: pad + barLength, y2: 292, stroke: "var(--ink-3)", "stroke-width": 2 }));
    svg.append(svgEl("text", { x: pad + barLength + 6, y: 296, "font-size": 11 }, "1 km"));
  }
  svg.append(svgEl("polyline", {
    points: located.map((_, n) => place(n).map((v) => v.toFixed(1)).join(",")).join(" "),
    fill: "none", stroke: "var(--route)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
  }));
  const [startX, startY] = place(0);
  const [endX, endY] = place(located.length - 1);
  svg.append(svgEl("circle", { cx: startX, cy: startY, r: 5, fill: "var(--accent)", stroke: "var(--panel)", "stroke-width": 2 }));
  svg.append(svgEl("rect", { x: endX - 5, y: endY - 5, width: 10, height: 10, fill: "var(--route)", stroke: "var(--panel)", "stroke-width": 2 }));
  const marker = svgEl("circle", { r: 6, fill: "var(--panel)", stroke: "var(--route)", "stroke-width": 2.5, visibility: "hidden" });
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

const CHART_H_PACE = 170;
const CHART_H_HR = 150;
const CHART_LEFT = 44;
const CHART_RIGHT = 10;

function distanceChart({ width, points, index, height, xAxis, maxKm, lo, hi, invert, ticks, format, color, band, label }) {
  const bottom = xAxis ? 26 : 8;
  const top = 10;
  const svg = newSvg(width, height, label);
  const x = (km) => CHART_LEFT + (km / maxKm) * (width - CHART_LEFT - CHART_RIGHT);
  const share = (v) => (v - lo) / (hi - lo);
  const y = (v) => (invert ? top + share(v) * (height - top - bottom) : height - bottom - share(v) * (height - top - bottom));
  const clamp = (v) => Math.max(lo, Math.min(hi, v));
  if (band) {
    const [y1, y2] = [y(clamp(band[0])), y(clamp(band[1]))];
    svg.append(svgEl("rect", {
      x: CHART_LEFT, y: Math.min(y1, y2), width: width - CHART_LEFT - CHART_RIGHT, height: Math.abs(y2 - y1),
      fill: color, "fill-opacity": 0.12,
    }));
  }
  gridLines(svg, ticks, y, CHART_LEFT, width - CHART_RIGHT, format);
  if (xAxis) {
    const step = Math.max(1, Math.ceil(maxKm / (width < 420 ? 6 : 10)));
    for (let km = 0; km <= maxKm; km += step) {
      svg.append(svgEl("text", { x: x(km), y: height - 6, "text-anchor": "middle", "font-size": 11 }, km));
    }
    svg.append(svgEl("text", { x: width - CHART_RIGHT, y: height - 6, "text-anchor": "end", "font-size": 11 }, "km"));
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
  const cross = svgEl("line", { y1: top, y2: height - bottom, stroke: COLORS.axis, "stroke-dasharray": "3 3", visibility: "hidden" });
  const dot = svgEl("circle", { r: 4.5, fill: color, stroke: "var(--panel)", "stroke-width": 2, visibility: "hidden" });
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

function hoverLabel(width) {
  const box = svgEl("rect", { width: 150, height: 40, rx: 4, fill: "var(--ink)" });
  const first = svgEl("text", { style: "fill: var(--bg)", "font-size": 12 });
  const second = svgEl("text", { style: "fill: var(--bg)", "font-size": 12 });
  const group = svgEl("g", { visibility: "hidden" });
  group.append(box, first, second);
  return {
    group,
    show(p, svgX) {
      const left = Math.min(Math.max(svgX - 75, CHART_LEFT), width - CHART_RIGHT - 150);
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
  box.className = "charts-pair";
  // Route takes a third of the row on a laptop, so the charts take what's left of it.
  const width = matchMedia("(min-width: 900px)").matches && route ? availableWidth(2 / 3) - 8 : availableWidth();
  const charts = [];

  if (paces.length) {
    // A few seconds of walking shouldn't flatten the whole chart.
    const low = Math.min(quantile(paces, 0.01), band?.[0] ?? Infinity);
    const high = Math.max(quantile(paces, 0.99), band?.[1] ?? -Infinity);
    const lo = Math.floor(low / 15) * 15 - 15;
    const hi = Math.ceil(high / 15) * 15 + 15;
    charts.push(distanceChart({
      width, points, index: 2, height: CHART_H_PACE, xAxis: !heartRates.length, maxKm, lo, hi, invert: true,
      ticks: ticksFor(lo, hi, [15, 30, 60, 120, 300, 600]), format: paceText, color: "var(--pace)", band, label: "Pace over distance",
    }));
  }
  if (heartRates.length) {
    const lo = Math.floor((heartRates[0] - 5) / 10) * 10;
    const hi = Math.ceil((heartRates[heartRates.length - 1] + 5) / 10) * 10;
    charts.push(distanceChart({
      width, points, index: 3, height: CHART_H_HR, xAxis: true, maxKm, lo, hi, invert: false,
      ticks: ticksFor(lo, hi, [10, 20, 40]), format: String, color: "var(--hr)", label: "Heart rate over distance",
    }));
  }
  if (!charts.length) return null;
  const label = hoverLabel(width);
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
    route?.mark(-1);
  };
  const move = (clientX) => {
    const rect = charts[0].svg.getBoundingClientRect();
    const svgX = ((clientX - rect.left) / rect.width) * width;
    const km = ((svgX - CHART_LEFT) / (width - CHART_LEFT - CHART_RIGHT)) * maxKm;
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
