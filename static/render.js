// The only renderer. A classic script, not a module: modules don't load from file://,
// and the sample plan must open with no server.
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const COLUMNS = ["Date", "Day", "Type", "Title", "Distance", "Duration", "Target pace", "Details"];

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
  const reps = step.repeat > 1 ? `${step.repeat} × ` : "";
  return `${reps}${amount(step)} @ ${step.target_pace} — ${step.description}`;
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

function weekRows(week) {
  return DAYS.flatMap((day, i) => {
    const sessions = week.sessions.filter((s) => s.day === day);
    if (!sessions.length) return [[addDays(week.start_date, i), day, "Rest", "", "", "", "", ""]];
    return sessions.map((s) => [
      s.date,
      s.day,
      s.type,
      s.title,
      s.distance_km ? `${s.distance_km} km` : "",
      s.duration_min ? `${s.duration_min} min` : "",
      s.target_pace,
      details(s),
    ]);
  });
}

function renderPlan(planFile) {
  if (planFile?.schema_version !== "1.0") {
    throw new Error(`Unsupported plan file: schema_version is ${planFile?.schema_version}, expected 1.0`);
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
    nodes.push(table(COLUMNS, weekRows(week)));
  }
  nodes.push(el("p", `Model: ${meta.model} — created ${meta.created_at} — tokens: ${meta.usage.input_tokens} in, ${meta.usage.output_tokens} out`));
  document.getElementById("plan").replaceChildren(...nodes);
}
