// The weeks page: one foldable block per week, and a linked row per session.
import {
  USB_HELP, actions, api, breadcrumb, button, chip, el, fitButton, fullPlanVersionCheck, garminStatus, line, link, main,
  planPath, refresh, rowLinks, stale, statusLine, table, toast, viewToken,
} from "./ui.js";
import { DAYS, actualText, addDays, amountText, dateText, roundKm } from "./format.js";
import { planBasics } from "./plan_overview.js";
import { notStarted } from "../charts.js";

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

function completionChips(workouts) {
  const box = el("span");
  for (const workout of workouts) if (workout.review) box.append(chip(workout.review.review.completion));
  return box;
}

function sendResult({ sent, failed }, status) {
  toast(`${sent} ${sent === 1 ? "session" : "sessions"} sent to Garmin`);
  if (failed.length) status.textContent = failed.map((f) => `${f.session}: ${f.error}`).join("; ");
}

// Runs still to come this week can go to the watch; strength and cross sessions can't.
function watchBlock(id, week, workouts, today, garmin, changed) {
  const undone = week.sessions
    .map((session, index) => ({ session, index }))
    .filter(({ session, index }) => (session.modality ?? "run") === "run" && session.date >= today
      && !workouts.some((w) => w.match.week_number === week.week_number && w.match.session_index === index));
  if (!undone.length) return null;
  const status = statusLine();
  const path = `/api/plans/${encodeURIComponent(id)}/weeks/${week.week_number}/send-to-garmin`;
  const box = el("div");
  box.className = "field";
  const help = el("p", USB_HELP);
  help.className = "muted";
  box.append(el("h4", "To your watch"), actions(...undone.map(({ session, index }) => (
    fitButton(id, week.week_number, index, session, status, `${session.day}: ${session.title} .fit`)
  ))), help);
  if (garmin?.can_push_workouts) {
    const send = actions(button("Send week to Garmin", status, async () => {
      sendResult(await api(path, { method: "POST" }), status);
    }, "Sending…", "primary"));
    if (changed > 0) {
      send.append(button("Re-send changed sessions", status, async () => {
        sendResult(await api(`${path}?changed_only=true`, { method: "POST" }), status);
        refresh();
      }, "Sending…"));
    }
    box.append(send);
  }
  box.append(status);
  return box;
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
          completionChips(matched),
        ],
      });
    }
    if (!sessions.length) rows.push({ cells: [date, day, "Rest", "", "", "", "", ""] });
    for (const workout of workouts.filter((w) => w.date === date && w.match.session_index === -1)) {
      const href = `#/workouts/${encodeURIComponent(workout.id)}`;
      rows.push({
        href,
        cells: [date, day, "unplanned", link(workout.title, href), "", "", actualText(workout), completionChips([workout])],
      });
    }
  });
  return rows;
}

function weekBlock(week, volume, id, workouts, today, open, onToggle, watch) {
  const box = el("details");
  box.className = "fold";
  box.open = open;
  box.dataset.week = week.week_number;
  const facts = [`Week ${week.week_number}`, week.phase, dateText(week.start_date), `${week.planned_km} km`];
  if (volume && !notStarted(volume, today)) {
    facts.push(`${roundKm(volume.actual_km)} km done`, `${volume.completed_sessions}/${volume.planned_sessions} sessions`);
  }
  box.append(el("summary", facts.join(" · ")), el("p", week.focus));
  const rows = sessionRows(week, id, workouts);
  const body = table(["Date", "Day", "Type", "Session", "Planned", "Target", "Actual", "Review"], rows.map((r) => r.cells));
  const lines = body.querySelector("tbody").rows;
  rows.forEach((row, i) => { if (row.href) rowLinks(lines[i], row.href); });
  box.append(body);
  if (watch) box.append(watch);
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
  const active = planFile.meta.status === "active";
  const garmin = active ? await garminStatus() : null;
  const changes = garmin?.can_push_workouts ? await Promise.all(planFile.plan.weeks.map((w) => (
    api(`/api/plans/${encoded}/weeks/${w.week_number}/garmin`).catch(() => null)
  ))) : [];
  if (stale(token)) return;

  const today = metrics.as_of;
  const current = planFile.plan.weeks.find((w) => w.start_date <= today && today <= addDays(w.start_date, 6));
  const open = new Set(openWeek ? [Number(openWeek)] : remembered(id) ?? (current ? [current.week_number] : []));
  const blocks = [];
  const saveFolds = () => remember(id, blocks.filter((b) => b.open).map((b) => Number(b.dataset.week)));
  planFile.plan.weeks.forEach((week, i) => {
    const volume = metrics.weeks.find((v) => v.week_number === week.week_number);
    const watch = active ? watchBlock(id, week, workouts, today, garmin, changes[i]?.changed ?? 0) : null;
    blocks.push(weekBlock(week, volume, id, workouts, today, open.has(week.week_number), saveFolds, watch));
  });
  main.replaceChildren(
    breadcrumb(["All plans", "#/plans"], [planFile.request.goal_description, planPath(id)], ["Weeks"]),
    el("h1", "Weeks"),
    planBasics(planFile),
    ...blocks,
    line(link("Back to overview", planPath(id))),
  );
}
