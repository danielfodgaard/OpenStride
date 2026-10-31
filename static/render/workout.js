// The workout detail page. One renderer for a planned session, a recorded workout, or both.
import {
  USB_HELP, actions, activePlan, api, breadcrumb, bulletList, button, buttonLink, chip, el, fitButton, garminStatus, input, labelled, line,
  link, main, notice, patchJson, planPath, refresh, rpeSelect, stale, statStrip, statusLine, table, toast, viewToken,
} from "./ui.js";
import {
  amountText, clockText, dateText, expandSteps, paceText, shortDate, stepText, targetBand, timeText, utcText,
} from "./format.js";
import { drawDistanceCharts, drawRoute, legend } from "../charts.js";
import { activityButtons, sessionMenu, sessionTitle } from "./session_edit.js";

const OFF_TARGET_S = 5;

function isOffTarget(pace, target) {
  const band = targetBand(target);
  if (!band) return false;
  const margin = target.includes("-") ? OFF_TARGET_S : 0;  // a single pace's band is already ±5 s
  return pace < band[0] - margin || pace > band[1] + margin;
}

function plannedSection(session, menu) {
  const section = el("section");
  const heading = el("div");
  heading.className = "section-head";
  heading.append(el("h2", "Planned"));
  if (menu) heading.append(menu);
  section.append(heading);
  const facts = [session.type, amountText(session), session.target_pace].filter(Boolean);
  const summary = el("p");
  summary.append(sessionTitle(session, facts.join(" · ")));
  section.append(summary, el("p", session.description));
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
  section.append(statStrip([
    ["Distance", activity.distance_km.toFixed(2), "km"],
    ["Moving time", timeText(activity.moving_time_s)],
    ["Avg pace", activity.avg_pace || "–"],
    ["Avg HR", activity.avg_hr || "–", activity.avg_hr ? "bpm" : ""],
    ["Max HR", activity.max_hr || "–", activity.max_hr ? "bpm" : ""],
    ["Cadence", activity.avg_cadence_spm || "–", activity.avg_cadence_spm ? "spm" : ""],
    ["Elevation gain", Math.round(activity.elevation_gain_m), "m"],
  ]));
  const notes = [];
  const band = session && !session.steps.length ? targetBand(session.target_pace) : null;
  if (band && activity.distance_km) {
    const pace = activity.moving_time_s / activity.distance_km;
    notes.push(`${pace >= band[0] && pace <= band[1] ? "Within" : "Outside"} planned pace range`);
  }
  notes.push(`File ${activity.source_file}`);
  if (activity.start_utc) notes.push(`Stored start ${utcText(activity.start_utc)}`);
  const facts = el("p", notes.join(" · "));
  facts.className = "muted";
  section.append(facts);
  return section;
}

function reviewSection(activity, editable) {
  const section = el("section");
  section.append(el("h2", "Review"));
  const review = activity.review?.review;
  if (review) {
    section.append(line(chip(review.completion), ` ${review.headline}`), bulletList(review.observations));
    if (review.flags.length) section.append(el("h4", "Flags"), bulletList(review.flags));
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
  const rpe = rpeSelect(activity.rpe);
  const fields = el("div");
  fields.className = "field-row";
  fields.append(labelled("Note", note), labelled("RPE", rpe));
  section.append(
    fields,
    actions(
      button("Save", status, async () => {
        await patchJson(path, { user_note: note.value, rpe: Number(rpe.value) });
        refresh();
      }),
      button("Re-run review", status, async () => {
        await api(`${path}/review`, { method: "POST" });
        refresh();
      }, "Reviewing…"),
    ),
    status,
  );
  return section;
}

// Route beside the charts on a laptop, stacked on a phone.
function chartsSections(activity, series, session) {
  if (!series) {
    if (activity.has_raw) return [];
    return [el("p", "Re-import the file to see route and charts")];
  }
  const top = el("div");
  top.className = "workout-top";
  const route = drawRoute(series.points);
  if (route) {
    const section = el("section");
    section.append(el("h2", "Route"), route.svg, legend([
      { label: "Start", color: "var(--accent)" }, { label: "Finish", color: "var(--route)" },
    ]));
    top.append(section);
  }
  const band = session && !session.steps.length ? targetBand(session.target_pace) : null;
  const charts = drawDistanceCharts(series.points, band, route);
  if (charts) {
    const section = el("section");
    const items = [{ label: "Pace, min/km, faster is higher", color: "var(--pace)", kind: "line" }];
    if (band) items.push({ label: "Planned range", color: "color-mix(in srgb, var(--pace) 20%, transparent)" });
    if (series.points.some((p) => p[3] !== null)) items.push({ label: "Heart rate, bpm", color: "var(--hr)", kind: "line" });
    section.append(el("h2", "Pace and heart rate over distance"), charts, legend(items));
    top.append(section);
  }
  return top.childElementCount ? [top] : [];
}

function paceBar(width) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", 84);
  svg.setAttribute("height", 8);
  svg.setAttribute("aria-hidden", "true");
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("width", width);
  rect.setAttribute("height", 6);
  rect.setAttribute("fill", "var(--pace)");
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
  select.add(new Option("Unplanned", "0:-1"));
  for (const week of plan.plan.weeks) {
    week.sessions.forEach((session, index) => {
      if ((session.modality ?? "run") !== workout.modality) return;
      select.add(new Option(`${session.date} ${session.title}`, `${week.week_number}:${index}`));
    });
  }
  select.value = `${workout.match.week_number}:${workout.match.session_index}`;
  const path = workout.manual ? `/api/manual/${encodeURIComponent(workout.id)}` : `/api/activities/${encodeURIComponent(workout.id)}`;
  const box = el("div");
  box.className = "field";
  box.append(labelled("Change match", select), actions(button("Save match", status, async () => {
    const [week_number, session_index] = select.value.split(":").map(Number);
    await patchJson(path, { match: { week_number, session_index } });
    refresh();
  })));
  return box;
}

function deleteControl(workout, status) {
  const box = actions();
  const path = workout.manual ? `/api/manual/${encodeURIComponent(workout.id)}` : `/api/activities/${encodeURIComponent(workout.id)}`;
  const ask = button("Delete workout", status, async () => {
    const sure = button("Yes, delete it", status, async () => {
      await api(path, { method: "DELETE" });
      location.hash = "#/workouts";
    }, "Deleting…", "danger");
    const keep = button("Keep it", status, async () => box.replaceChildren(ask));
    box.replaceChildren("Delete this workout for good? ", sure, keep);
  }, "Working…", "danger");
  box.append(ask);
  return box;
}

function manualSummary(entry, session) {
  const nodes = [];
  const section = el("section");
  section.append(el("h2", "Summary"));
  const stats = [["Type", entry.modality], ["Duration", entry.duration_min, "min"], ["RPE", entry.rpe || "–"]];
  if (entry.distance_km) stats.push(["Distance", entry.distance_km, "km"]);
  section.append(statStrip(stats));
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
  return nodes;
}

function manualEdit(entry) {
  const section = el("section");
  const status = statusLine();
  const note = input("text", entry.note);
  const duration = input("number", entry.duration_min);
  duration.min = "0";
  const rpe = rpeSelect(entry.rpe);
  const fields = el("div");
  fields.className = "field-row";
  fields.append(labelled("Note", note), labelled("Minutes", duration), labelled("RPE", rpe));
  section.append(el("h2", "Edit"), fields, actions(button("Save", status, async () => {
    await patchJson(`/api/manual/${encodeURIComponent(entry.id)}`, {
      note: note.value, duration_min: Number(duration.value), rpe: Number(rpe.value),
    });
    refresh();
  })), status);
  return section;
}

// Download and Send for a planned run nobody has done yet.
function watchSection(plan, place, session, garmin) {
  const section = el("section");
  const status = statusLine();
  const buttons = actions(fitButton(plan.meta.id, place.week, place.index, session, status));
  if (garmin?.can_push_workouts) {
    const path = `/api/plans/${encodeURIComponent(plan.meta.id)}/sessions/${place.week}/${place.index}/send-to-garmin`;
    buttons.append(button("Send to Garmin", status, async () => {
      const { scheduled_date: date } = await api(path, { method: "POST" });
      toast(`Sent to Garmin for ${shortDate(date)}`);
    }, "Sending…", "primary"));
  }
  const help = el("p", USB_HELP);
  help.className = "muted";
  section.append(el("h2", "To your watch"), buttons, help, status);
  return section;
}

// `plan` may be null, as may `session` or `activity`, but not both of those.
function renderWorkout({ plan, session, activity, series, place, matchPlan, also = [], garmin, workouts = [] }) {
  const nodes = [];
  const date = session?.date ?? activity.date;
  const title = session?.title ?? activity.title;
  const when = [dateText(date), activity?.start_time ? clockText(activity.start_time) : ""].filter(Boolean).join(" ");
  const weekLink = place ? link(`Week ${place.week}`, `${planPath(plan.meta.id)}/weeks/${place.week}`) : "Unplanned";
  const head = el("div");
  head.className = "page-head";
  const sub = line(`${when} · `, weekLink);
  sub.className = "sub";
  head.append(el("h1", title), sub);
  nodes.push(head);
  for (const warning of activity?.warnings ?? []) nodes.push(notice(warning));
  const editable = activity && (!activity.plan_id || plan?.meta.status === "active");

  const canEdit = matchPlan?.meta.status === "active";
  const editButtons = () => (canEdit ? activityButtons(matchPlan, activity, workouts) : []);
  if (!activity) {
    const week = plan.plan.weeks.find((w) => w.week_number === place.week);
    const menu = plan.meta.status === "active" && session.type !== "race" ? sessionMenu(plan, week, place.index) : null;
    if (session) nodes.push(plannedSection(session, menu));
    if (plan.meta.status === "active") {
      if ((session.modality ?? "run") === "run" && session.status === "planned") nodes.push(watchSection(plan, place, session, garmin));
      nodes.push(actions(
        buttonLink("Import run", `#/workouts?open=import&date=${session.date}`),
        buttonLink("Log session", `#/workouts?open=log&date=${session.date}`),
      ));
    }
  } else if (activity.manual) {
    nodes.push(...manualSummary(activity, session));
    if (session) nodes.push(plannedSection(session));
    if (editable) nodes.push(manualEdit(activity));
    if (activity.plan_id) nodes.push(...editButtons());
  } else {
    nodes.push(...chartsSections(activity, series, session));
    const laps = lapsSection(activity, session);
    if (laps) nodes.push(laps);
    nodes.push(summarySection(activity, session));
    if (session) nodes.push(plannedSection(session));
    nodes.push(reviewSection(activity, editable), ...editButtons());
  }
  for (const other of also) nodes.push(line("Also on this session: ", link(other.title, `#/workouts/${encodeURIComponent(other.id)}`)));

  const status = statusLine();
  if (place) nodes.push(actions(buttonLink("Back to week", `${planPath(plan.meta.id)}/weeks/${place.week}`)));
  if (activity) {
    const section = el("section");
    section.append(el("h2", "Actions"));
    if (editable && matchPlan) section.append(matchControl(activity, matchPlan, status));
    section.append(deleteControl(activity, status), status);
    nodes.push(section);
  }
  return nodes;
}

const planFor = async (id) => (id ? api(`/api/plans/${encodeURIComponent(id)}`) : null);
const seriesFor = async (activity) => (
  activity?.has_series ? api(`/api/activities/${encodeURIComponent(activity.id)}/series`) : null
);

export async function workoutView(id) {
  const token = viewToken();
  const activity = await api(`/api/activities/${encodeURIComponent(id)}`);
  const [plan, series] = await Promise.all([planFor(activity.plan_id), seriesFor(activity)]);
  const active = plan ? null : await activePlan();
  const matchPlan = plan ?? await planFor(active?.meta.id);  // by id, so its ETag is on hand for an edit
  const workouts = matchPlan?.meta.status === "active" ? await api(`/api/activities?plan_id=${encodeURIComponent(matchPlan.meta.id)}`) : [];
  if (stale(token)) return;
  const { week_number: week, session_index: index } = activity.match;
  const session = index >= 0 ? plan?.plan.weeks.find((w) => w.week_number === week)?.sessions[index] : null;
  main.replaceChildren(
    breadcrumb(["Workouts", "#/workouts"], [activity.title]),
    ...renderWorkout({ plan, session, activity, series, place: session ? { week } : null, matchPlan, workouts }),
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
  const garmin = activity ? null : await garminStatus();
  if (stale(token)) return;
  main.replaceChildren(
    breadcrumb(
      ["All plans", "#/plans"],
      [plan.request.goal_description, planPath(id)],
      ["Weeks", `${planPath(id)}/weeks`],
      [`Week ${week.week_number} ${session.day}`],
    ),
    ...renderWorkout({
      plan, session, activity, series, place: { week: week.week_number, index: Number(index) }, matchPlan: plan,
      also: matched.slice(0, -1), garmin,
    }),
  );
}
