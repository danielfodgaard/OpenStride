// The weeks page: one foldable block per week, and a linked row per session.
import {
  api, breadcrumb, el, fullPlanVersionCheck, line, link, main, planPath, rowLinks, stale, table, viewToken,
} from "./ui.js";
import { DAYS, actualText, addDays, amountText, dateText } from "./format.js";
import { planBasics } from "./plan_overview.js";

const storageKey = (id) => `openstride:weeks:${id}`;

function remembered(id) {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey(id)));
    return Array.isArray(saved) ? saved : null;
  } catch {
    return null;
  }
}

function remember(id, numbers) {
  try {
    localStorage.setItem(storageKey(id), JSON.stringify(numbers));
  } catch {
    // Blocked storage only costs the remembered folds.
  }
}

function sessionRows(week, id, workouts) {
  const rows = [];
  DAYS.forEach((day, i) => {
    const date = addDays(week.start_date, i);
    const sessions = week.sessions.filter((s) => s.day === day);
    for (const session of sessions) {
      const index = week.sessions.indexOf(session);
      const href = `${planPath(id)}/sessions/${week.week_number}/${index}`;
      const matched = workouts.filter((w) => w.match.week_number === week.week_number && w.match.session_index === index);
      rows.push({
        href,
        cells: [
          date, day, session.type, link(session.title, href), amountText(session), session.target_pace,
          matched.map(actualText).join("; "),
          matched.map((w) => w.review?.review.completion ?? "").filter(Boolean).join("; "),
        ],
      });
    }
    if (!sessions.length) rows.push({ cells: [date, day, "Rest", "", "", "", "", ""] });
    for (const workout of workouts.filter((w) => w.date === date && w.match.session_index === -1)) {
      const href = `#/workouts/${encodeURIComponent(workout.id)}`;
      rows.push({
        href,
        cells: [date, day, "unplanned", link(workout.title, href), "", "", actualText(workout), workout.review?.review.completion ?? ""],
      });
    }
  });
  return rows;
}

function weekBlock(week, volume, id, workouts, today, open, onToggle) {
  const box = el("details");
  box.open = open;
  box.dataset.week = week.week_number;
  const facts = [`Week ${week.week_number}`, week.phase, dateText(week.start_date), `${week.planned_km} km`];
  if (volume && week.start_date <= today) {
    facts.push(`${volume.actual_km} km done`, `${volume.completed_sessions}/${volume.planned_sessions} sessions`);
  }
  box.append(el("summary", facts.join(" · ")), el("p", week.focus));
  const rows = sessionRows(week, id, workouts);
  const body = table(["Date", "Day", "Type", "Session", "Planned", "Target", "Actual", "Review"], rows.map((r) => r.cells));
  const lines = body.querySelector("tbody").rows;
  rows.forEach((row, i) => { if (row.href) rowLinks(lines[i], row.href); });
  box.append(body);
  box.addEventListener("toggle", onToggle);
  return box;
}

export async function weeksView(id, openWeek) {
  const token = viewToken();
  const encoded = encodeURIComponent(id);
  const planFile = await api(`/api/plans/${encoded}`);
  fullPlanVersionCheck(planFile);
  const metrics = await api(`/api/plans/${encoded}/metrics`);
  const workouts = planFile.meta.status === "draft" ? [] : await api(`/api/activities?plan_id=${encoded}`);
  if (stale(token)) return;

  const today = metrics.as_of;
  const current = planFile.plan.weeks.find((w) => w.start_date <= today && today <= addDays(w.start_date, 6));
  const open = new Set(openWeek ? [Number(openWeek)] : remembered(id) ?? (current ? [current.week_number] : []));
  const blocks = [];
  const saveFolds = () => remember(id, blocks.filter((b) => b.open).map((b) => Number(b.dataset.week)));
  for (const week of planFile.plan.weeks) {
    const volume = metrics.weeks.find((v) => v.week_number === week.week_number);
    blocks.push(weekBlock(week, volume, id, workouts, today, open.has(week.week_number), saveFolds));
  }
  main.replaceChildren(
    breadcrumb(["All plans", "#/plans"], [planFile.request.goal_description, planPath(id)], ["Weeks"]),
    el("h1", "Weeks"),
    planBasics(planFile),
    ...blocks,
    line(link("Back to overview", planPath(id))),
  );
}
