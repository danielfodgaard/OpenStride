// Cards: each type is drawn one way, from data the server built. Nothing here computes training facts.
import { barChart, lineChart, routeMap, runCharts } from "./charts.js";
import { amount, chip, dateText, duration, el, km, table, timeText } from "./format.js";
import { markdown } from "./md.js";

const STATE = {
  done: ["Done", "good"], missed: ["Missed", "critical"], today: ["Today", "warning"], upcoming: ["", ""],
  skipped: ["Skipped", ""], replaced: ["Did something else", ""],
};
const KIND = { added: ["Added", "good"], removed: ["Removed", "critical"], moved: ["Moved", "warning"], changed: ["Changed", "warning"], skipped: ["Skipped", ""] };
const SPORT = { run: "Run", ride: "Ride", swim: "Swim", strength: "Strength", walk: "Walk", hike: "Hike", row: "Row", ski: "Ski", other: "Workout" };

function head(title, sub) {
  const box = el("div", null, "card-head");
  box.append(el("h3", title));
  if (sub) box.append(el("p", sub, "muted"));
  return box;
}

function stat(label, value, unit) {
  const box = el("div", null, "stat");
  const v = el("span", value, "v");
  if (unit) v.append(el("small", unit));
  box.append(el("span", label, "k"), v);
  return box;
}

function strip(...stats) {
  const box = el("div", null, "strip");
  box.append(...stats.filter(Boolean));
  return box;
}

function activityLine(activity) {
  return [SPORT[activity.sport] || activity.sport, km(activity.distance_km), duration(activity.moving_time_s), activity.avg_pace, activity.avg_hr ? `${activity.avg_hr} bpm` : ""].filter(Boolean).join(" · ");
}

function sessionRow(session, ctx) {
  const row = el("button", null, `row ${session.state}`);
  row.type = "button";
  const left = el("span", null, "row-main");
  left.append(el("span", `${session.day} ${dateText(session.date, { day: "numeric", month: "short" })}`, "row-day"), el("span", session.title, "row-title"));
  const meta = el("span", null, "row-meta");
  const done = session.activities?.[0];
  meta.append(el("span", done ? activityLine(done) : amount(session)));
  const [label, tone] = STATE[session.state] || ["", ""];
  if (label) meta.append(chip(label, tone));
  row.append(left, meta);
  row.addEventListener("click", () => (done ? ctx.peek("activity", { key: done.key }) : ctx.peek("session", { id: session.id })));
  return row;
}

// The sessions of one week, each row opening the session or the workout done for it.
function weekRows(data, ctx) {
  const list = el("div", null, "rows");
  for (const session of data.sessions) list.append(sessionRow(session, ctx));
  for (const extra of data.unplanned) {
    const row = el("button", null, "row unplanned");
    row.type = "button";
    const left = el("span", null, "row-main");
    left.append(el("span", dateText(extra.date, { weekday: "short", day: "numeric", month: "short" }), "row-day"), el("span", extra.name || SPORT[extra.sport] || "Activity", "row-title"));
    const meta = el("span", null, "row-meta");
    meta.append(el("span", activityLine(extra)), chip("Not in the plan"));
    row.append(left, meta);
    row.addEventListener("click", () => ctx.peek("activity", { key: extra.key }));
    list.append(row);
  }
  return list;
}

function week(data, ctx) {
  const box = el("div", null, "card-body");
  box.append(head(`Week ${data.week_number} of ${data.of} · ${data.phase}`, `${dateText(data.start_date)} to ${dateText(data.end_date)}${data.focus ? ` · ${data.focus}` : ""}`));
  box.append(strip(stat("Running", `${data.done_km} / ${data.planned_km}`, "km"), stat("Sessions", `${data.sessions_done} / ${data.sessions_planned}`)));
  box.append(weekRows(data, ctx));
  return box;
}

// Every week of the plan as a fold, with only the current one open.
function allWeeks(weeks, ctx) {
  const box = el("div", null, "weeks");
  for (const data of weeks) {
    const fold = el("details", null, `week-fold${data.current ? " current" : ""}`);
    fold.open = data.current;
    const past = data.end_date < new Date().toISOString().slice(0, 10) || data.sessions_done > 0;
    const volume = past || data.current ? `${data.done_km} / ${data.planned_km} km` : `${data.planned_km} km`;
    const summary = el("summary");
    const title = el("span", `Week ${data.week_number} · ${data.phase}`, "week-title");
    const meta = el("span", `${dateText(data.start_date, { day: "numeric", month: "short" })} · ${volume} · ${past || data.current ? `${data.sessions_done} of ` : ""}${data.sessions_planned} sessions`, "week-meta");
    summary.append(title, meta);
    if (data.current) summary.append(chip("Now", "warning"));
    fold.append(summary);
    if (data.focus) fold.append(el("p", data.focus, "muted"));
    fold.append(weekRows(data, ctx));
    box.append(fold);
  }
  return box;
}

function steps(session) {
  if (!session.steps?.length) return null;
  const strength = session.modality === "strength";
  const rows = session.steps.map((s) => strength
    ? [s.description, `${s.repeat} × ${s.reps || ""}`.trim(), s.load]
    : [s.repeat > 1 ? `${s.repeat} ×` : "", s.description, s.distance_km ? km(s.distance_km) : s.duration_min ? `${s.duration_min} min` : "", s.target_pace]);
  return table(strength ? ["Exercise", "Sets × reps", "Load"] : ["", "Step", "Amount", "Target"], rows);
}

function session(data, ctx) {
  const box = el("div", null, "card-body");
  const [label, tone] = STATE[data.state] || ["", ""];
  const title = head(data.title, `${data.day} ${dateText(data.date)}${data.week_number ? ` · week ${data.week_number}` : ""} · ${data.type.replace("_", " ")}`);
  if (label) title.append(chip(label, tone));
  box.append(title);
  if (amount(data)) box.append(el("p", amount(data), "lead"));
  if (data.description) box.append(el("p", data.description));
  const plan = steps(data);
  if (plan) box.append(plan);
  if (data.edit_note) box.append(el("p", data.edit_note, "muted"));
  if (data.moved_from) box.append(el("p", `First planned for ${dateText(data.moved_from)}.`, "muted"));
  for (const activity of data.activities || []) {
    const open = el("button", `Done: ${activityLine(activity)}`, "quiet");
    open.type = "button";
    open.addEventListener("click", () => ctx.peek("activity", { key: activity.key }));
    box.append(open);
  }
  return box;
}

function activity(data) {
  const box = el("div", null, "card-body");
  box.append(head(data.name || SPORT[data.sport] || "Activity", `${dateText(data.date, { weekday: "long", day: "numeric", month: "long" })} · ${SPORT[data.sport] || data.sport}`));
  if (data.partial) box.append(el("p", data.partial_reason, "notice"));
  box.append(strip(
    data.distance_km ? stat("Distance", String(Number(data.distance_km.toFixed(2))), "km") : null,
    stat("Time", duration(data.moving_time_s)),
    data.avg_pace ? stat("Pace", data.avg_pace.replace("/km", ""), "/km") : null,
    data.avg_hr ? stat(data.max_hr ? "HR avg / max" : "Heart rate", `${data.avg_hr}${data.max_hr ? ` / ${data.max_hr}` : ""}`) : null,
    data.load !== null && data.load !== undefined ? stat("Load", String(Math.round(data.load))) : null,
    data.rpe ? stat("RPE", String(data.rpe), "/ 10") : null,
  ));
  if (data.planned) box.append(el("p", `Planned: ${data.planned.title}${amount(data.planned) ? ` (${amount(data.planned)})` : ""}`, "muted"));
  else box.append(el("p", "Not a planned session.", "muted"));
  if (data.series?.points?.length) {
    const top = el("div", null, "run-top");
    const route = routeMap(data.series.points);
    const charts = el("div", null, "run-charts");
    if (route) top.append(route);
    top.append(charts);
    box.append(top);
    runCharts(charts, data.series.points, data.planned);
  }
  if (data.intervals?.length) {
    const fold = el("details");
    fold.append(el("summary", `Laps and intervals (${data.intervals.length})`));
    fold.append(table(["", "Distance", "Time", "Pace", "HR"], data.intervals.map((lap, i) => [lap.label || lap.type || String(i + 1), km(lap.distance_km), duration(lap.moving_time_s), lap.avg_pace, lap.avg_hr || ""])));
    box.append(fold);
  }
  if (data.review) {
    box.append(el("h4", "Review"));
    box.append(markdown(data.review));
  }
  if (data.athlete_note) {
    box.append(el("h4", "Your note"));
    box.append(markdown(data.athlete_note));
  }
  return box;
}

function activities(data, ctx) {
  const box = el("div", null, "card-body");
  box.append(head("Recent activities"));
  const list = el("div", null, "rows");
  for (const item of data.activities) {
    const row = el("button", null, "row");
    row.type = "button";
    const left = el("span", null, "row-main");
    left.append(el("span", dateText(item.date), "row-day"), el("span", item.name || SPORT[item.sport] || "Activity", "row-title"));
    const meta = el("span", null, "row-meta");
    meta.append(el("span", activityLine(item)));
    if (!item.session_id) meta.append(chip("Not in the plan"));
    row.append(left, meta);
    row.addEventListener("click", () => ctx.peek("activity", { key: item.key }));
    list.append(row);
  }
  if (!data.activities.length) list.append(el("p", "No activities yet.", "muted"));
  box.append(list);
  return box;
}

function fitness(data) {
  const box = el("div", null, "card-body");
  const latest = data.latest;
  box.append(head("Fitness and fatigue", `As of ${dateText(latest.date)}`));
  box.append(strip(stat("Fitness", String(latest.fitness)), stat("Fatigue", String(latest.fatigue)), stat("Form", String(latest.form)), latest.ramp !== null && latest.ramp !== undefined ? stat("Ramp", String(latest.ramp), "/ week") : null));
  const chart = el("div");
  const x = data.days.map((d) => d.date.slice(5));
  lineChart(chart, {
    x, label: "Fitness, fatigue and form",
    series: [
      { name: "Fitness", values: data.days.map((d) => d.fitness), color: "var(--accent)" },
      { name: "Fatigue", values: data.days.map((d) => d.fatigue), color: "var(--load-atl)" },
      { name: "Form", values: data.days.map((d) => d.form), color: "var(--ink-2)", dash: true },
    ],
    marks: data.race_date ? [{ x: data.race_date.slice(5), label: "Race" }] : [],
  });
  box.append(chart);
  return box;
}

function volume(data) {
  const box = el("div", null, "card-body");
  box.append(head("Weekly volume", "Planned against done, running km"));
  const chart = el("div");
  barChart(chart, {
    x: data.weeks.map((w) => `W${w.week_number}`), label: "Weekly running volume",
    series: [
      { name: "Planned", values: data.weeks.map((w) => w.planned_km), color: "var(--bar)" },
      { name: "Done", values: data.weeks.map((w) => (w.future ? null : w.done_km)), color: "var(--accent)" },
    ],
    yFormat: (v) => `${v}`,
  });
  box.append(chart);
  const fold = el("details");
  fold.append(el("summary", "Table"));
  fold.append(table(["Week", "Phase", "Planned km", "Done km", "Sessions"], data.weeks.map((w) => [`${w.week_number}${w.current ? " (now)" : ""}`, w.phase, w.planned_km, w.future ? "" : w.done_km, w.future ? `${w.sessions_planned}` : `${w.sessions_done} / ${w.sessions_planned}`])));
  box.append(fold);
  return box;
}

function paces(p) {
  return table(["Easy", "Marathon", "Threshold", "Interval", "Repetition", "Race"], [[p.easy, p.marathon, p.threshold, p.interval, p.repetition, p.race_pace]]);
}

function plan(data, ctx) {
  const box = el("div", null, "card-body");
  box.append(head(data.goal, `Race ${dateText(data.race_date, { weekday: "short", day: "numeric", month: "long", year: "numeric" })} · ${data.days_to_go} days to go${data.goal_time ? ` · goal ${data.goal_time}` : ""}`));
  if (data.summary) box.append(el("p", data.summary));
  for (const warning of data.warnings || []) box.append(el("p", warning, "notice"));
  box.append(el("p", data.phases.map((p) => `${p.phase} ${p.from_week === p.to_week ? `w${p.from_week}` : `w${p.from_week}–${p.to_week}`}`).join("  ·  "), "phases"));
  box.append(el("h4", "Reference paces"), paces(data.paces));
  if (data.all_weeks?.length) {
    box.append(el("h4", `All ${data.all_weeks.length} weeks`));
    box.append(allWeeks(data.all_weeks, ctx));
  } else {
    box.append(el("h4", "This week"));
    box.append(week(data.this_week, ctx));
  }
  if (data.versions?.length > 1) {
    const fold = el("details");
    fold.append(el("summary", `Plan history (${data.versions.length})`));
    const list = el("ul");
    for (const version of data.versions) list.append(el("li", `${timeText(version.at)}: ${version.message}`));
    fold.append(list);
    box.append(fold);
  }
  return box;
}

function brief(s) {
  return `${s.day} ${dateText(s.date, { day: "numeric", month: "short" })}: ${s.title}${amount(s) ? ` (${amount(s)})` : ""}`;
}

function diffEntry(entry) {
  const row = el("div", null, `diff ${entry.kind}`);
  const [label, tone] = KIND[entry.kind];
  row.append(chip(label, tone));
  const text = el("div", null, "diff-text");
  if (entry.kind === "added") text.append(el("p", brief(entry.after)));
  else if (entry.kind === "removed") text.append(el("p", brief(entry.before), "struck"));
  else {
    text.append(el("p", brief(entry.before), "struck"));
    text.append(el("p", brief(entry.after)));
  }
  row.append(text);
  return row;
}

function proposal(data, ctx) {
  const box = el("div", null, "card-body proposal");
  const title = head(data.kind === "new_plan" ? "A new plan, for your approval" : "A plan change, for your approval", `${data.author === "analyst" ? "Built by the analyst" : "From your coach"} · ${timeText(data.created_at)}`);
  box.append(title);
  box.append(markdown(data.rationale));
  if (data.plan) {
    box.append(el("p", `${data.plan.goal} · race ${dateText(data.plan.race_date)} · ${data.plan.weeks} weeks`, "lead"));
    if (data.plan.summary) box.append(el("p", data.plan.summary));
    box.append(paces(data.plan.paces));
  }
  for (const warning of data.warnings) box.append(el("p", `To weigh: ${warning}`, "notice"));
  const fold = el("details");
  const count = data.diff.reduce((n, w) => n + w.entries.length, 0);
  fold.open = data.kind !== "new_plan" && count <= 12;
  fold.append(el("summary", data.kind === "new_plan" ? `All ${data.diff.length} weeks` : `${count} change${count === 1 ? "" : "s"} in ${data.diff.length} week${data.diff.length === 1 ? "" : "s"}`));
  for (const w of data.diff) {
    const volumeNote = w.planned_km_before !== null && w.planned_km_before !== w.planned_km_after ? ` · ${w.planned_km_before} → ${w.planned_km_after} km` : ` · ${w.planned_km_after} km`;
    fold.append(el("h4", `Week ${w.week_number} · ${w.phase} · from ${dateText(w.start_date)}${volumeNote}`));
    for (const entry of w.entries) fold.append(diffEntry(entry));
  }
  box.append(fold);
  if (data.status === "pending") {
    const actions = el("div", null, "actions");
    const approve = el("button", "Approve", "primary");
    const reject = el("button", "Reject");
    const note = el("input");
    note.placeholder = "Why not? (optional, goes to your coach)";
    note.hidden = true;
    note.setAttribute("aria-label", "Why you reject this");
    const status = el("p", "", "muted");
    approve.type = reject.type = "button";
    const act = async (path, body) => {
      approve.disabled = reject.disabled = true;
      status.textContent = "Working…";
      try {
        await ctx.post(`/api/proposals/${encodeURIComponent(data.id)}/${path}`, body);
      } catch (error) {
        status.textContent = error.message;
      }
      ctx.refresh();
    };
    approve.addEventListener("click", () => act("approve"));
    reject.addEventListener("click", () => {
      if (note.hidden) { note.hidden = false; note.focus(); reject.textContent = "Confirm reject"; return; }
      act("reject", { note: note.value });
    });
    actions.append(approve, reject);
    box.append(actions, note, status, el("p", "Nothing changes unless you approve.", "muted"));
  } else {
    const tone = { approved: "good", rejected: "critical", stale: "" }[data.status] || "";
    const line = el("p");
    line.append(chip({ approved: "Approved", rejected: "Rejected", stale: "Out of date" }[data.status] || data.status, tone));
    if (data.decided_at) line.append(` ${timeText(data.decided_at)}`);
    box.append(line);
    if (data.decision_note) box.append(el("p", data.decision_note, "muted"));
  }
  return box;
}

function report(data) {
  const box = el("div", null, "card-body");
  box.append(head("Analyst report"));
  const fold = el("details");
  fold.open = true;
  fold.append(el("summary", "Read the report"));
  fold.append(markdown(data.markdown, { imageBase: `reports/${data.id}`, headings: true }));
  box.append(fold);
  if (data.files.length) box.append(el("p", `With it: ${data.files.join(", ")}`, "muted"));
  return box;
}

function tableCard(data) {
  const box = el("div", null, "card-body");
  if (data.title) box.append(head(data.title));
  box.append(table(data.columns, data.rows));
  if (data.note) box.append(el("p", data.note, "muted"));
  return box;
}

function chartCard(data) {
  const box = el("div", null, "card-body");
  box.append(head(data.title || "Chart", [data.y_label, data.x_label].filter(Boolean).join(" by ")));
  const chart = el("div");
  const draw = data.kind === "bar" ? barChart : lineChart;
  draw(chart, { x: data.x.map(String), series: data.series, yFormat: (v) => String(Number(Number(v).toFixed(2))), label: data.title });
  box.append(chart);
  if (data.note) box.append(el("p", data.note, "muted"));
  return box;
}

function choice(data, ctx) {
  const box = el("div", null, "card-body");
  box.append(el("p", data.question, "lead"));
  const actions = el("div", null, "actions");
  for (const option of data.options) {
    const button = el("button", option);
    button.type = "button";
    button.addEventListener("click", () => {
      for (const other of actions.querySelectorAll("button")) other.disabled = true;
      ctx.send(option);
    });
    actions.append(button);
  }
  box.append(actions);
  return box;
}

const RENDER = { week, session, activity, activities, fitness, volume, plan, proposal, report, table: tableCard, chart: chartCard, choice };
export const LIVE = new Set(["week", "plan", "volume", "fitness", "proposal", "activities", "session"]);

export function renderCard(type, data, ctx) {
  const draw = RENDER[type];
  return draw ? draw(data, ctx) : el("p", `(a ${type} card this version of the page can't draw)`, "muted");
}
