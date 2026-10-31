// The only renderer.
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const COLUMNS = ["Date", "Day", "Type", "Title", "Distance", "Duration", "Target pace", "Details"];
const ACTUAL_COLUMNS = [...COLUMNS, "Actual", "Review"];

// Text always goes through textContent: plan files can come from anywhere.
function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

function table(headers, rows) {
  const t = el("table");
  t.border = 1;
  const headRow = t.createTHead().insertRow();
  for (const h of headers) headRow.append(el("th", h));
  const body = t.createTBody();
  for (const cells of rows) {
    const row = body.insertRow();
    for (const cell of cells) {
      const td = el("td");
      td.append(cell);
      row.append(td);
    }
  }
  return t;
}

function amount(item) {
  if (item.distance_km) return `${item.distance_km.toFixed(1)} km`;
  return item.duration_min ? `${item.duration_min} min` : "";
}

function stepText(step) {
  if (step.reps || step.load) {  // a strength exercise; absent in v1 and v2 files
    return `${step.repeat} × ${step.reps} ${step.description}${step.load ? ` — ${step.load}` : ""}`;
  }
  const reps = step.repeat > 1 ? `${step.repeat} × ` : "";
  const size = [`${reps}${amount(step)}`.trim(), step.target_pace].filter(Boolean).join(" @ ");
  return [size, step.description].filter(Boolean).join(" — ");
}

function details(session) {
  const cell = el("div", session.description);
  if (session.steps.length) {
    const list = el("ol");
    for (const step of session.steps) list.append(el("li", stepText(step)));
    cell.append(list);
  }
  return cell;
}

// UTC so that daylight saving can't shift a date.
function addDays(isoDate, days) {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function formatDuration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const rest = String(seconds % 60).padStart(2, "0");
  return hours ? `${hours}:${minutes}:${rest}` : `${Number(minutes)}:${rest}`;
}

function actualText(activity) {
  if (activity.manual) {
    return ["Done", activity.duration_min ? `${activity.duration_min} min` : "",
      activity.distance_km ? `${activity.distance_km} km` : "", activity.rpe ? `RPE ${activity.rpe}` : ""].filter(Boolean).join(" · ");
  }
  const parts = [`${activity.distance_km.toFixed(2)} km`, formatDuration(activity.moving_time_s), activity.avg_pace];
  if (activity.avg_hr) parts.push(`${activity.avg_hr} bpm`);
  return parts.join(" · ");
}

// One activity per line: a session can have several, e.g. a run split in two.
function actualCell(activities, controls) {
  const cell = el("div");
  for (const activity of activities) {
    cell.append(el("div", actualText(activity)));
    const note = activity.manual ? activity.note : activity.user_note;
    if (note) cell.append(el("div", `Note: ${note}`));
    if (activity.rpe && !activity.manual) cell.append(el("div", `RPE ${activity.rpe}`));
    if (controls) cell.append(controls(activity));
  }
  return cell;
}

function reviewCell(activities) {
  const cell = el("div");
  for (const { review, summary } of activities) {
    if (summary) cell.append(el("div", summary));
    if (!review) continue;
    const box = el("details");
    box.append(el("summary", `${review.review.completion} — ${review.review.headline}`));
    const points = el("ul");
    for (const text of review.review.observations) points.append(el("li", text));
    for (const text of review.review.flags) points.append(el("li", `Flag: ${text}`));
    box.append(points);
    cell.append(box);
  }
  return cell;
}

function weekRows(week, activities, controls) {
  const onWeekDay = (i) => activities.filter((a) => a.date === addDays(week.start_date, i) && a.match.session_index === -1);
  const unplannedRow = (activity, i) => [
    activity.date, DAYS[i], "unplanned", activity.manual ? activity.title : activity.source_file, "", "", "", "", actualCell([activity], controls), reviewCell([activity]),
  ];
  return DAYS.flatMap((day, i) => {
    const sessions = week.sessions.filter((s) => s.day === day);
    const rows = sessions.map((s) => {
      const matched = activities.filter((a) => a.match.week_number === week.week_number
        && a.match.session_index === week.sessions.indexOf(s));
      return [
        s.date,
        s.day,
        s.type,
        s.title,
        s.distance_km ? `${s.distance_km} km` : "",
        s.duration_min ? `${s.duration_min} min` : "",
        s.target_pace,
        details(s),
        actualCell(matched, controls),
        reviewCell(matched),
      ];
    });
    if (!rows.length) rows.push([addDays(week.start_date, i), day, "Rest", "", "", "", "", "", "", ""]);
    return [...rows, ...onWeekDay(i).map((a) => unplannedRow(a, i))];
  });
}

// `actuals` adds the Actual and Review columns; `activities` are this plan's imported runs.
// `controls(activity)` optionally returns edit controls for a run.
function renderPlan(planFile, { actuals = false, activities = [], controls } = {}) {
  if (!["1.0", "2.0", "3.0"].includes(planFile?.schema_version)) {
    throw new Error(`Unsupported plan file: schema_version is ${planFile?.schema_version}, expected 1.0, 2.0 or 3.0`);
  }
  const { meta, request, paces, plan } = planFile;
  const nodes = [
    el("h1", request.goal_description),
    el("p", `${request.race_distance_km} km — goal ${request.goal_time ?? "best effort"} — race on ${request.race_date}`),
  ];
  if (plan.warnings.length) {
    const list = el("ul");
    for (const warning of plan.warnings) list.append(el("li", warning));
    nodes.push(list);
  }
  nodes.push(el("p", plan.summary));
  nodes.push(table(["Zone", "Pace"], [
    ["Easy", paces.easy],
    ["Marathon", paces.marathon],
    ["Threshold", paces.threshold],
    ["Interval", paces.interval],
    ["Repetition", paces.repetition],
    ["Race pace", paces.race_pace],
  ]));
  nodes.push(el("p", `Current VDOT: ${paces.current_vdot} — Goal VDOT: ${paces.goal_vdot ?? "none"}`));
  for (const week of plan.weeks) {
    nodes.push(el("h2", `Week ${week.week_number} — ${week.phase} — ${week.start_date} — ${week.planned_km} km`));
    nodes.push(el("p", week.focus));
    const rows = weekRows(week, activities, controls);
    nodes.push(actuals ? table(ACTUAL_COLUMNS, rows) : table(COLUMNS, rows.map((row) => row.slice(0, COLUMNS.length))));
  }
  const planDays = new Set(plan.weeks.flatMap((week) => DAYS.map((_, i) => addDays(week.start_date, i))));
  const outside = activities.filter((a) => !planDays.has(a.date));
  if (outside.length) {
    nodes.push(el("h2", "Runs outside the plan dates"));
    nodes.push(table(["Date", "Title", "Actual", "Review"], outside.map((a) => [
      a.date, a.manual ? a.title : a.source_file, actualCell([a], controls), reviewCell([a]),
    ])));
  }
  nodes.push(el("p", `Model: ${meta.model} — created ${meta.created_at} — tokens: ${meta.usage.input_tokens} in, ${meta.usage.output_tokens} out`));
  document.getElementById("plan").replaceChildren(...nodes);
}
