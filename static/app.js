// Hash router, plus the views that aren't a plan page or a workout page.
import {
  api, breadcrumb, button, downloadButton, el, fullPlanVersionCheck, input, labelled, line, link, main, nextRoute,
  planPath, postJson, stale, statusLine, table, viewToken,
} from "./render/ui.js";
import { draftActions, planBasics, planOverview } from "./render/plan_overview.js";
import { weeksView } from "./render/weeks.js";
import { sessionView, workoutView } from "./render/workout.js";
import { workoutsView } from "./render/workouts_list.js";

// A fixed date would drift out of the allowed 3-24 week window.
function defaultRaceDate() {
  const date = new Date();
  date.setDate(date.getDate() + 70 + (7 - date.getDay()) % 7);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function formRequest(form) {
  const data = new FormData(form);
  const distance = data.get("race_distance");
  return {
    goal_description: data.get("goal_description"),
    race_distance_km: Number(distance === "custom" ? data.get("custom_distance") : distance),
    goal_time: data.get("goal_time").trim() || null,
    race_date: data.get("race_date"),
    recent_race_distance_km: Number(data.get("recent_race_distance_km")),
    recent_race_time: data.get("recent_race_time").trim(),
    current_weekly_km: Number(data.get("current_weekly_km")),
    longest_recent_run_km: Number(data.get("longest_recent_run_km")),
    runs_per_week: Number(data.get("runs_per_week")),
    available_days: data.getAll("available_days"),
    long_run_day: data.get("long_run_day"),
    experience: data.get("experience"),
    notes: data.get("notes"),
    strength_sessions_per_week: Number(data.get("strength_sessions_per_week")),
    cross_training: data.get("cross_training").trim(),
    cross_sessions_per_week: Number(data.get("cross_sessions_per_week")),
    equipment: data.get("equipment").trim(),
    hr_max: Number(data.get("hr_max")),
    hr_rest: Number(data.get("hr_rest")),
    age: Number(data.get("age")),
  };
}

function fillForm(form, request) {
  const fields = form.elements;
  const preset = [...fields.race_distance.options].find((o) => Number(o.value) === request.race_distance_km);
  fields.race_distance.value = preset ? preset.value : "custom";
  fields.custom_distance.value = preset ? "" : request.race_distance_km;
  for (const name of ["goal_description", "race_date", "recent_race_distance_km", "recent_race_time",
    "current_weekly_km", "longest_recent_run_km", "runs_per_week", "long_run_day", "experience", "notes",
    "strength_sessions_per_week", "cross_training", "cross_sessions_per_week", "equipment", "hr_max", "hr_rest", "age"]) {
    fields[name].value = request[name];
  }
  fields.goal_time.value = request.goal_time ?? "";
  for (const box of fields.available_days) box.checked = request.available_days.includes(box.value);
}

async function newView(draftId) {
  const token = viewToken();
  const form = document.getElementById("plan-form").content.firstElementChild.cloneNode(true);
  const status = statusLine();
  const preview = el("div");
  form.elements.race_date.value = defaultRaceDate();
  main.replaceChildren(el("h1", "New plan"), form, status, preview);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    status.textContent = "Generating… this takes about a minute";
    try {
      const planFile = await postJson("/api/plan", formRequest(form));
      status.textContent = "";
      location.hash = `#/new/${encodeURIComponent(planFile.meta.id)}`;
    } catch (error) {
      status.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  if (!draftId) return;
  const encoded = encodeURIComponent(draftId);
  const planFile = await api(`/api/plans/${encoded}`);
  if (stale(token)) return;
  if (planFile.meta.status !== "draft") {
    location.replace(planPath(draftId));
    return;
  }
  fullPlanVersionCheck(planFile);
  fillForm(form, planFile.request);
  preview.append(
    el("h2", "Draft preview"),
    draftActions(planFile, status, form, formRequest),
    line(downloadButton(planFile, status)),
    planBasics(planFile),
    line(link("View weeks", `${planPath(draftId)}/weeks`)),
  );
}

async function plansView() {
  const token = viewToken();
  const plans = await api("/api/plans");
  if (stale(token)) return;
  const ordered = [...plans.filter((p) => p.status === "active"), ...plans.filter((p) => p.status !== "active")];
  const status = statusLine();
  const rows = ordered.map((p) => [
    p.goal_description, p.race_date, p.status === "active" ? "Active" : p.status, p.version, p.created_at,
    link("Open", planPath(p.id)),
  ]);
  const importInput = input("file");
  importInput.value = "";
  importInput.accept = ".json,application/json";
  importInput.addEventListener("change", async () => {
    const file = importInput.files[0];
    if (!file) return;
    status.textContent = "Importing…";
    const body = new FormData();
    body.append("file", file);
    try {
      const planFile = await api("/api/plans/import", { method: "POST", body });
      location.hash = planPath(planFile.meta.id);
    } catch (error) {
      status.textContent = error.message;
    }
  });
  main.replaceChildren(
    el("h1", "All plans"),
    plans.length ? table(["Goal", "Race date", "Status", "Version", "Created", ""], rows) : el("p", "No plans yet."),
    line(link("Create a plan", "#/new")),
    labelled("Import plan file", importInput),
    status,
  );
}

async function versionsView(id) {
  const token = viewToken();
  const encoded = encodeURIComponent(id);
  const planFile = await api(`/api/plans/${encoded}`);
  const versions = await api(`/api/plans/${encoded}/versions`);
  if (stale(token)) return;
  const status = statusLine();
  const rows = versions.map(({ version, history }) => {
    const view = el("div");
    view.append(link("View", `${planPath(id)}/versions/${version}`));
    if (planFile.meta.status === "active" && version !== planFile.meta.version) {
      view.append(" ", button("Restore", status, async () => {
        await api(`/api/plans/${encoded}/restore/${version}`, { method: "POST" });
        location.hash = planPath(id);
      }));
    }
    return [version, history[0]?.accepted_at ?? planFile.meta.accepted_at, history[0]?.rationale ?? "Accepted plan", view];
  });
  main.replaceChildren(
    breadcrumb(["All plans", "#/plans"], [planFile.request.goal_description, planPath(id)], ["Versions"]),
    el("h1", "Plan versions"),
    table(["Version", "Accepted", "Why", ""], rows),
    status,
  );
}

async function versionView(id, version) {
  const token = viewToken();
  const planFile = await api(`/api/plans/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}`);
  fullPlanVersionCheck(planFile);
  if (stale(token)) return;
  const rows = planFile.plan.weeks.flatMap((week) => week.sessions.map((s) => [
    week.week_number, s.date, s.day, s.type, s.title, s.distance_km ? `${s.distance_km} km` : `${s.duration_min} min`, s.target_pace,
  ]));
  main.replaceChildren(
    breadcrumb(
      ["All plans", "#/plans"],
      [planFile.request.goal_description, planPath(id)],
      ["Versions", `${planPath(id)}/versions`],
      [`Version ${planFile.meta.version}`],
    ),
    el("p", `Version ${planFile.meta.version}, read-only`),
    planBasics(planFile),
    table(["Week", "Date", "Day", "Type", "Session", "Planned", "Target"], rows),
  );
}

async function homeView() {
  const active = await fetch("/api/plans/active").then((r) => (r.ok ? r.json() : null));
  location.replace(active ? planPath(active.meta.id) : "#/workouts");
}

async function route() {
  nextRoute();
  const [path, search] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const [page, id, section, a, b] = parts;
  try {
    if (!page) await homeView();
    else if (page === "workouts" && !id) await workoutsView(new URLSearchParams(search));
    else if (page === "workouts") await workoutView(id);
    else if (page === "new") await newView(id);
    else if (page === "plans" && !id) await plansView();
    else if (page === "plans" && !section) await planOverview(id);
    else if (page === "plans" && section === "weeks") await weeksView(id, a);
    else if (page === "plans" && section === "sessions") await sessionView(id, a, b);
    else if (page === "plans" && section === "versions" && !a) await versionsView(id);
    else if (page === "plans" && section === "versions") await versionView(id, a);
    else main.replaceChildren(el("p", "Page not found."));
  } catch (error) {
    main.replaceChildren(el("p", error.message));
  }
}

window.addEventListener("hashchange", route);
route();
