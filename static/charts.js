// Plain inline SVG charts. Every chart comes with a table of the same numbers.
const SVG_NS = "http://www.w3.org/2000/svg";
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
  const svg = svgEl("svg", { viewBox: `0 0 ${WIDTH} ${height}`, width: "100%", role: "img", "aria-label": label });
  svg.style.maxWidth = `${WIDTH}px`;
  return svg;
}

const dayMs = (isoDate) => Date.parse(`${isoDate}T00:00:00Z`);
const monthDay = (ms) => new Date(ms).toISOString().slice(5, 10);
const paceText = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;

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

function leastSquares(points) {
  const n = points.length;
  if (n < 2) return null;
  const meanX = points.reduce((sum, p) => sum + p.x, 0) / n;
  const meanY = points.reduce((sum, p) => sum + p.y, 0) / n;
  const spread = points.reduce((sum, p) => sum + (p.x - meanX) ** 2, 0);
  if (!spread) return null;
  const slope = points.reduce((sum, p) => sum + (p.x - meanX) * (p.y - meanY), 0) / spread;
  return { slope, intercept: meanY - slope * meanX };
}

function trendPoints(points) {
  const fit = leastSquares(points);
  if (!fit) return [];
  const xs = points.map((p) => p.x);
  return [Math.min(...xs), Math.max(...xs)].map((x) => ({ x, y: fit.slope * x + fit.intercept }));
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
  const right = Math.max(...xs, xMax ?? -Infinity);
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
  // Short ranges would repeat a date, so fewer ticks then.
  const xLabels = new Set();
  for (let i = 0; i <= 4; i += 1) {
    const x = left + (xSpan * i) / 4;
    if (xLabels.has(monthDay(x))) continue;
    xLabels.add(monthDay(x));
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
  bars.forEach((bar, i) => {
    const middle = MARGIN.left + slot * (i + 0.5);
    const addBar = (x, value, color) => svg.append(svgEl("rect", {
      x, y: toY(value), width: barWidth, height: toY(0) - toY(value), fill: color,
    }));
    addBar(middle - barWidth, bar.planned, COLORS.grey);
    if (bar.actual !== null) addBar(middle, bar.actual, COLORS.blue);
    svg.append(svgEl("text", { x: middle, y: height - bottom + 13, "text-anchor": "middle", "font-size": 10 }, bar.label));
    bar.notes.forEach((note, line) => svg.append(svgEl("text", {
      x: middle, y: height - bottom + 25 + line * 10, "text-anchor": "middle", "font-size": small,
    }, note)));
  });
  return chartBox(title, svg, "Planned km (grey) · Actual km (blue)", headers, rows);
}

const signed = (value) => `${value < 0 ? "−" : "+"}${Math.abs(Math.round(value))}`;

function snapshotText({ ctl, atl, tsb, ctl_7d_change: change }) {
  const form = tsb < 0 ? `−${Math.abs(Math.round(tsb))}` : Math.round(tsb);
  return `Fitness ${Math.round(ctl)} (${signed(change)} this week), fatigue ${Math.round(atl)}, form ${form}`;
}

function weekNotes(week) {
  const notes = [];
  if (week.planned_strength || week.completed_strength) notes.push(`S ${week.completed_strength}/${week.planned_strength}`);
  if (week.planned_cross_min || week.actual_cross_min) notes.push(`X ${Math.round(week.actual_cross_min)}/${Math.round(week.planned_cross_min)}`);
  return notes;
}

function drawProgressCharts(metrics, raceDate) {
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
    headers: ["Week", "Start", "Planned km", "Actual km", "Sessions", "Strength", "Cross min"],
    rows: metrics.weeks.map((w) => [
      w.week_number, w.start_date, w.planned_km, w.start_date > metrics.as_of ? "" : w.actual_km,
      `${w.completed_sessions}/${w.planned_sessions}`, `${w.completed_strength}/${w.planned_strength}`,
      `${w.actual_cross_min}/${w.planned_cross_min}`,
    ]),
  });

  const easy = metrics.easy_runs.map((p) => ({ ...p, x: dayMs(p.date) }));
  const pacePoints = easy.map((p) => ({ x: p.x, y: p.avg_pace_s }));
  const hrPoints = easy.filter((p) => p.avg_hr).map((p) => ({ x: p.x, y: p.avg_hr }));
  const paceHr = drawSeriesChart({
    title: "Easy-run pace and heart rate",
    series: [
      { name: "Pace, min/km", color: COLORS.blue, axis: "left", dots: true, points: pacePoints },
      { name: "Pace trend", color: COLORS.blue, axis: "left", dashed: true, points: trendPoints(pacePoints) },
      { name: "Heart rate, bpm", color: COLORS.red, axis: "right", dots: true, points: hrPoints },
      { name: "HR trend", color: COLORS.red, axis: "right", dashed: true, points: trendPoints(hrPoints) },
    ],
    leftFormat: paceText,
    headers: ["Date", "Type", "km", "Pace", "HR"],
    rows: easy.map((p) => [p.date, p.type, p.distance_km, `${paceText(p.avg_pace_s)}/km`, p.avg_hr || ""]),
  });

  const efficiencyPoints = easy.filter((p) => p.efficiency).map((p) => ({ x: p.x, y: p.efficiency }));
  const efficiency = drawSeriesChart({
    title: "Efficiency (metres per heartbeat)",
    series: [
      { name: "Efficiency", color: COLORS.green, axis: "left", dots: true, points: efficiencyPoints },
      { name: "Trend", color: COLORS.green, axis: "left", dashed: true, points: trendPoints(efficiencyPoints) },
    ],
    headers: ["Date", "Type", "Metres per heartbeat"],
    rows: easy.filter((p) => p.efficiency).map((p) => [p.date, p.type, p.efficiency]),
  });

  return [fitness, el("p", snapshotText(metrics.current)), volume, paceHr, efficiency];
}
