// The workout detail page. One renderer for a planned session, a recorded workout, or both.
import {
  api, breadcrumb, bulletList, button, el, input, line, link, main, patchJson, planPath, refresh,
  rpeSelect, stale, statusLine, table, viewToken,
} from "./ui.js";
import { amountText, clockText, dateText, expandSteps, paceText, stepText, targetBand, timeText, utcText } from "./format.js";
import { drawDistanceCharts, drawRoute } from "../charts.js";

const OFF_TARGET_S = 5;

function isOffTarget(pace, target) {
  const band = targetBand(target);
  if (!band) return false;
  const margin = target.includes("-") ? OFF_TARGET_S : 0;  // a single pace's band is already ±5 s
  return pace < band[0] - margin || pace > band[1] + margin;
}

function plannedSection(session) {
  const section = el("section");
  section.append(el("h2", "Planned"));
  const facts = [session.type, amountText(session), session.target_pace].filter(Boolean);
  section.append(el("p", facts.join(" · ")), el("p", session.description));
  if (session.steps.length) {
    const steps = el("ol");
    for (const step of session.steps) steps.append(el("li", stepText(step)));
    section.append(steps);
  }
  return section;
}

function summarySection(activity, session) {
  const section = el("section");
  section.append(el("h2", "Summary"));
  const stats = [
    ["Distance", `${activity.distance_km.toFixed(2)} km`],
    ["Moving time", timeText(activity.moving_time_s)],
    ["Avg pace", activity.avg_pace || "–"],
    ["Avg HR", activity.avg_hr ? `${activity.avg_hr} bpm` : "–"],
    ["Max HR", activity.max_hr ? `${activity.max_hr} bpm` : "–"],
    ["Cadence", activity.avg_cadence_spm ? `${activity.avg_cadence_spm} spm` : "–"],
    ["Elevation gain", `${Math.round(activity.elevation_gain_m)} m`],
  ];
  section.append(table(stats.map(([name]) => name), [stats.map(([, value]) => value)]));
  const notes = [];
  const band = session && !session.steps.length ? targetBand(session.target_pace) : null;
  if (band && activity.distance_km) {
    const pace = activity.moving_time_s / activity.distance_km;
    notes.push(`${pace >= band[0] && pace <= band[1] ? "Within" : "Outside"} planned pace range`);
  }
  notes.push(`File ${activity.source_file}`);
  if (activity.start_utc) notes.push(`Stored start ${utcText(activity.start_utc)}`);
  section.append(el("p", notes.join(" · ")));
  return section;
}

function reviewSection(activity, editable) {
  const section = el("section");
  section.append(el("h2", "Review"));
  const review = activity.review?.review;
  if (review) {
    section.append(el("p", `${review.completion} — ${review.headline}`), bulletList(review.observations));
    if (review.flags.length) section.append(el("p", "Flags"), bulletList(review.flags));
    section.append(el("p", `Effort: ${review.effort.replaceAll("_", " ")}`));
  } else {
    section.append(el("p", "No review yet."));
  }
  if (!editable) {
    if (activity.user_note) section.append(el("p", `Note: ${activity.user_note}`));
    if (activity.rpe) section.append(el("p", `RPE ${activity.rpe}`));
    return section;
  }
  const status = statusLine();
  const path = `/api/activities/${encodeURIComponent(activity.id)}`;
  const note = input("text", activity.user_note);
  note.setAttribute("aria-label", "Note");
  note.size = 50;
  const rpe = rpeSelect(activity.rpe);
  section.append(
    line("Note ", note, " ", rpe, " ", button("Save", status, async () => {
      await patchJson(path, { user_note: note.value, rpe: Number(rpe.value) });
      refresh();
    })),
    line(button("Re-run review", status, async () => {
      await api(`${path}/review`, { method: "POST" });
      refresh();
    }, "Reviewing…")),
    status,
  );
  return section;
}

function chartsSections(activity, series, session) {
  if (!series) {
    if (activity.has_raw) return [];
    return [el("p", "Re-import the file to see route and charts")];
  }
  const nodes = [];
  const route = drawRoute(series.points);
  if (route) {
    const section = el("section");
    section.append(el("h2", "Route"), route.svg, el("p", "Start ● · Finish ■"));
    nodes.push(section);
  }
  const band = session && !session.steps.length ? targetBand(session.target_pace) : null;
  const charts = drawDistanceCharts(series.points, band, route);
  if (charts) {
    const section = el("section");
    const parts = ["Pace, min/km, faster is higher"];
    if (band) parts.push("shaded band is the planned range");
    if (series.points.some((p) => p[3] !== null)) parts.push("heart rate, bpm, below");
    section.append(el("h2", "Pace and heart rate over distance"), charts, el("p", parts.join(" · ")));
    nodes.push(section);
  }
  return nodes;
}

function paceBar(width) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", 84);
  svg.setAttribute("height", 8);
  svg.setAttribute("aria-hidden", "true");
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("width", width);
  rect.setAttribute("height", 6);
  rect.setAttribute("fill", "#0d7a87");
  svg.append(rect);
  return svg;
}

function lapsSection(activity, session) {
  const steps = session?.steps ?? [];
  const expanded = expandSteps(steps);
  const matchesSteps = steps.length > 0 && activity.laps.length === expanded.length;
  const useLaps = activity.laps.length > 0 && (matchesSteps || !steps.length);
  const rows = useLaps ? activity.laps : activity.km_splits;
  if (!rows.length) return null;

  const paces = rows.map((lap) => (lap.distance_km ? lap.time_s / lap.distance_km : 0));
  const known = paces.filter(Boolean);
  const fastest = Math.min(...known);
  const slowest = Math.max(...known);
  let elapsed = 0;
  const cells = rows.map((lap, i) => {
    elapsed += lap.time_s;
    const width = slowest === fastest ? 50 : 20 + ((slowest - paces[i]) / (slowest - fastest)) * 60;
    const pace = el("span", lap.pace ? `${paceText(paces[i])} ` : "– ");
    pace.append(paceBar(width.toFixed(0)));
    const row = [lap.index, `${lap.distance_km.toFixed(2)} km`, timeText(lap.time_s), pace, lap.avg_hr || "–", timeText(elapsed)];
    if (matchesSteps) {
      const step = expanded[i];
      row.push([step.target_pace, step.description].filter(Boolean).join(" — "));
      row.push(isOffTarget(paces[i], step.target_pace) ? "off target" : "");
    }
    return row;
  });
  const headers = ["Lap", "Distance", "Time", "Pace", "Avg HR", "Elapsed"];
  if (matchesSteps) headers.push("Target", "");
  const section = el("section");
  section.append(el("h2", useLaps ? "Laps" : "Km splits"), table(headers, cells));
  return section;
}

function matchControl(workout, plan, status) {
  const select = el("select");
  select.setAttribute("aria-label", "Planned session");
  select.add(new Option("Unplanned", "0:-1"));
  for (const week of plan.plan.weeks) {
    week.sessions.forEach((session, index) => {
      if ((session.modality ?? "run") !== workout.modality) return;
      select.add(new Option(`${session.date} ${session.title}`, `${week.week_number}:${index}`));
    });
  }
  select.value = `${workout.match.week_number}:${workout.match.session_index}`;
  const path = workout.manual ? `/api/manual/${encodeURIComponent(workout.id)}` : `/api/activities/${encodeURIComponent(workout.id)}`;
  return line("Change match ", select, " ", button("Save match", status, async () => {
    const [week_number, session_index] = select.value.split(":").map(Number);
    await patchJson(path, { match: { week_number, session_index } });
    refresh();
  }));
}

function deleteControl(workout, status) {
  const box = el("span");
  const path = workout.manual ? `/api/manual/${encodeURIComponent(workout.id)}` : `/api/activities/${encodeURIComponent(workout.id)}`;
  const ask = button("Delete workout", status, async () => {
    const sure = button("Yes, delete it", status, async () => {
      await api(path, { method: "DELETE" });
      location.hash = "#/workouts";
    });
    const keep = button("Keep it", status, async () => box.replaceChildren(ask));
    box.replaceChildren("Delete this workout for good? ", sure, " ", keep);
  });
  box.append(ask);
  return box;
}

function manualDetail(entry, session, editable) {
  const nodes = [];
  const section = el("section");
  section.append(el("h2", "Summary"));
  const stats = [["Type", entry.modality], ["Duration", `${entry.duration_min} min`], ["RPE", entry.rpe || "–"]];
  if (entry.distance_km) stats.push(["Distance", `${entry.distance_km} km`]);
  section.append(table(stats.map(([name]) => name), [stats.map(([, value]) => value)]));
  if (entry.note) section.append(el("p", `Note: ${entry.note}`));
  nodes.push(section);
  if (entry.details.length) {
    const planned = session?.steps ?? [];
    const rows = Array.from({ length: Math.max(planned.length, entry.details.length) }, (_, i) => [
      planned[i] ? stepText(planned[i]) : "", entry.details[i] ? stepText(entry.details[i]) : "",
    ]);
    const exercises = el("section");
    exercises.append(el("h2", "Exercises"), table(["Planned", "Logged"], rows));
    nodes.push(exercises);
  }
  if (editable) {
    const status = statusLine();
    const note = input("text", entry.note);
    note.setAttribute("aria-label", "Note");
    const duration = input("number", entry.duration_min);
    duration.min = "0";
    duration.setAttribute("aria-label", "Duration in minutes");
    const rpe = rpeSelect(entry.rpe);
    nodes.push(line("Note ", note, " Minutes ", duration, " ", rpe, " ", button("Save", status, async () => {
      await patchJson(`/api/manual/${encodeURIComponent(entry.id)}`, {
        note: note.value, duration_min: Number(duration.value), rpe: Number(rpe.value),
      });
      refresh();
    })), status);
  }
  return nodes;
}

// `plan` may be null, as may `session` or `activity`, but not both of those.
function renderWorkout({ plan, session, activity, series, place, matchPlan, also = [] }) {
  const nodes = [];
  const date = session?.date ?? activity.date;
  const title = session?.title ?? activity.title;
  nodes.push(el("h1", title));
  const when = [dateText(date), activity?.start_time ? clockText(activity.start_time) : ""].filter(Boolean).join(" ");
  const weekLink = place ? link(`Week ${place.week}`, `${planPath(plan.meta.id)}/weeks/${place.week}`) : "Unplanned";
  nodes.push(line(`${when} · `, weekLink));
  if (session) nodes.push(plannedSection(session));

  if (!activity) {
    if (plan.meta.status === "active") {
      nodes.push(line(
        link("Import run", `#/workouts?date=${session.date}`), " · ",
        link("Log session", `#/workouts?date=${session.date}`),
      ));
    }
  } else if (activity.manual) {
    const editable = !activity.plan_id || plan?.meta.status === "active";
    nodes.push(...manualDetail(activity, session, editable));
  } else {
    const editable = !activity.plan_id || plan?.meta.status === "active";
    nodes.push(summarySection(activity, session), reviewSection(activity, editable));
    nodes.push(...chartsSections(activity, series, session));
    const laps = lapsSection(activity, session);
    if (laps) nodes.push(laps);
  }
  for (const other of also) nodes.push(line("Also on this session: ", link(other.title, `#/workouts/${encodeURIComponent(other.id)}`)));

  const status = statusLine();
  if (place) nodes.push(line(link("Back to week", `${planPath(plan.meta.id)}/weeks/${place.week}`)));
  if (activity) {
    const editable = !activity.plan_id || plan?.meta.status === "active";
    if (editable && matchPlan) nodes.push(matchControl(activity, matchPlan, status));
    nodes.push(line(deleteControl(activity, status)), status);
  }
  return nodes;
}

const planFor = async (id) => (id ? api(`/api/plans/${encodeURIComponent(id)}`) : null);
const activePlan = () => fetch("/api/plans/active").then((r) => (r.ok ? r.json() : null));
const seriesFor = async (activity) => (
  activity?.has_series ? api(`/api/activities/${encodeURIComponent(activity.id)}/series`) : null
);

export async function workoutView(id) {
  const token = viewToken();
  const activity = await api(`/api/activities/${encodeURIComponent(id)}`);
  const [plan, series] = await Promise.all([planFor(activity.plan_id), seriesFor(activity)]);
  const matchPlan = plan ?? await activePlan();
  if (stale(token)) return;
  const { week_number: week, session_index: index } = activity.match;
  const session = index >= 0 ? plan?.plan.weeks.find((w) => w.week_number === week)?.sessions[index] : null;
  main.replaceChildren(
    breadcrumb(["Workouts", "#/workouts"], [activity.title]),
    ...renderWorkout({ plan, session, activity, series, place: session ? { week } : null, matchPlan }),
  );
}

export async function sessionView(id, weekNumber, index) {
  const token = viewToken();
  const encoded = encodeURIComponent(id);
  const plan = await api(`/api/plans/${encoded}`);
  const week = plan.plan.weeks.find((w) => w.week_number === Number(weekNumber));
  const session = week?.sessions[Number(index)];
  if (!session) {
    main.replaceChildren(el("p", "That session isn't on this plan."));
    return;
  }
  const matched = plan.meta.status === "draft" ? [] : (await api(`/api/activities?plan_id=${encoded}`))
    .filter((w) => w.match.week_number === week.week_number && w.match.session_index === Number(index));
  // The list is newest first; the earliest run counts as the session and the rest are linked.
  const first = matched[matched.length - 1];
  const activity = first ? await api(`/api/activities/${encodeURIComponent(first.id)}`) : null;
  const series = await seriesFor(activity);
  if (stale(token)) return;
  main.replaceChildren(
    breadcrumb(
      ["All plans", "#/plans"],
      [plan.request.goal_description, planPath(id)],
      ["Weeks", `${planPath(id)}/weeks`],
      [`Week ${week.week_number} ${session.day}`],
    ),
    ...renderWorkout({
      plan, session, activity, series, place: { week: week.week_number }, matchPlan: plan, also: matched.slice(0, -1),
    }),
  );
}
