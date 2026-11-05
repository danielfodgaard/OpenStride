// Hash router, plus the views that aren't a plan page or a workout page.
import { chatView } from "./render/chat.js";
import { coachDataView } from "./render/coach_data.js";
import { garminView } from "./render/garmin.js";
import {
  actions, activePlan, api, breadcrumb, button, buttonLink, chip, downloadButton, el, garminStatus, input,
  labelled, line, link, main, nextRoute,
  planPath, postJson, rowLink, stale, statusLine, table, viewToken,
} from "./render/ui.js";
import { dateText } from "./render/format.js";
import { daysToGo, draftActions, planBasics, planOverview, weekSummary } from "./render/plan_overview.js";
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
  // Checkbox groups come back as a RadioNodeList, so they are skipped here and filled below.
  for (const [name, value] of Object.entries(request)) {
    if (fields[name] instanceof HTMLElement) fields[name].value = value ?? "";
  }
  for (const box of fields.available_days) box.checked = request.available_days.includes(box.value);
}

async function newView(draftId) {
  const token = viewToken();
  const form = document.getElementById("plan-form").content.firstElementChild.cloneNode(true);
  const status = statusLine();
  const preview = el("div");
  preview.className = "field";
  form.elements.race_date.value = defaultRaceDate();
  const card = el("section");
  card.append(form, status);
  main.replaceChildren(el("h1", "New plan"), card, preview);

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
  fillForm(form, planFile.request);
  const review = el("section");
  review.append(el("h2", "Draft preview"), draftActions(planFile, status, form, formRequest), actions(downloadButton(planFile, status)));
  preview.append(
    review,
    planBasics(planFile),
    line(link("View weeks", `${planPath(draftId)}/weeks`)),
  );
}

const planRow = (plan) => rowLink(planPath(plan.id), plan.goal_description, `Race ${dateText(plan.race_date)} · version ${plan.version}`, chip(plan.status));

function planGroup(title, plans) {
  const section = el("section");
  const list = el("div");
  list.className = "plan-list";
  list.append(...plans.map(planRow));
  section.append(el("h2", title), list);
  return section;
}

async function activePlanCard(plan) {
  const encoded = encodeURIComponent(plan.id);
  const [status, metrics] = await Promise.all([api(`/api/plans/${encoded}/status`), api(`/api/plans/${encoded}/metrics`)]);
  const section = el("section");
  const goal = link(plan.goal_description, planPath(plan.id));
  goal.className = "big";
  const where = status.week_number ? `Week ${status.week_number} of ${status.weeks_total} · ` : "";
  section.append(
    el("h2", "Active plan"),
    goal,
    el("p", `Race ${dateText(plan.race_date)} · ${daysToGo(status.days_to_race)}`),
    el("p", `${where}${weekSummary(status, metrics)}`),
    actions(buttonLink("Open overview", planPath(plan.id))),
  );
  return section;
}

async function plansView() {
  const token = viewToken();
  const plans = await api("/api/plans");
  const active = plans.find((p) => p.status === "active");
  const activeCard = active ? await activePlanCard(active) : null;
  if (stale(token)) return;
  const drafts = plans.filter((p) => p.status === "draft");
  const past = plans.filter((p) => p.status === "archived").sort((a, b) => b.created_at.localeCompare(a.created_at));
  const status = statusLine();
  const importInput = input("file");
  importInput.value = "";
  importInput.accept = ".json,application/json";
  importInput.dataset.online = "";
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
  const more = el("section");
  more.append(line(link("Create a plan", "#/new")), labelled("Import plan file", importInput), status);
  main.replaceChildren(
    el("h1", "All plans"),
    ...(plans.length ? [] : [el("p", "No plans yet.")]),
    ...(activeCard ? [activeCard] : []),
    ...(drafts.length ? [planGroup("Drafts", drafts)] : []),
    ...(past.length ? [planGroup("Past plans", past)] : []),
    more,
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
        await api(`/api/plans/${encoded}/restore/${version}`, { method: "POST", plan: id });
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
    el("h1", `Version ${planFile.meta.version}, read-only`),
    planBasics(planFile),
    table(["Week", "Date", "Day", "Type", "Session", "Planned", "Target"], rows),
  );
}

async function homeView() {
  const active = await activePlan();
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
    else if (page === "garmin") await garminView();
    else if (page === "chat") await chatView();
    else if (page === "coach") await coachDataView();
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

function markTab() {
  const tab = location.hash.split("/")[1] || "workouts";
  for (const a of document.querySelectorAll("#tabs a")) {
    if (a.hash.split("/")[1] === tab) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
}

function currentTheme() {
  return document.documentElement.dataset.theme ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function showTheme() {
  const toggle = document.getElementById("theme-toggle");
  const next = currentTheme() === "dark" ? "light" : "dark";
  toggle.textContent = next === "dark" ? "Dark" : "Light";
  toggle.setAttribute("aria-label", `Switch to ${next} theme`);
  document.querySelector("meta[name=theme-color]").content = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
}

// TODO(v4.1): a way back to following the system theme once one is picked.
function setUpTheme() {
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const theme = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("openstride:theme", theme);
    } catch {
      // Blocked storage only costs remembering the choice.
    }
    showTheme();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", showTheme);
  showTheme();
}

async function renderFooter() {
  const footer = document.getElementById("footer");
  const backup = link("Download backup", "/api/backup");
  backup.dataset.online = "";
  footer.append(backup, link("Garmin settings", "#/garmin"));
  if (!await garminStatus()) return;
  const secrets = input("checkbox");
  secrets.addEventListener("change", () => {
    backup.href = secrets.checked ? "/api/backup?include_secrets=1" : "/api/backup";
  });
  const tick = el("label");
  tick.className = "tick";
  tick.append(secrets, "Include secrets");
  footer.append(tick);
}

// Fills the cache, so the active plan opens offline even if this device never visited its pages.
async function warmCache() {
  if (!navigator.onLine) return;
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller) {
    await new Promise((resolve) => { navigator.serviceWorker.addEventListener("controllerchange", resolve, { once: true }); });
  }
  const active = await activePlan();
  const paths = ["/api/activities", "/api/plans", "/api/today", "/api/memory"];
  if (active) {
    const plan = `/api/plans/${encodeURIComponent(active.meta.id)}`;
    paths.push(`/api/activities?plan_id=${encodeURIComponent(active.meta.id)}`, plan);
    for (const part of ["status", "metrics", "usage", "proposals", "chat", "progress"]) paths.push(`${plan}/${part}`);
  }
  await Promise.all(paths.map((path) => fetch(path).catch(() => null)));
}

window.addEventListener("hashchange", () => { markTab(); route(); });
setUpTheme();
renderFooter();
markTab();
route();
// Not there on plain http: the app still works, it just can't be installed.
if ("serviceWorker" in navigator) {
  // After a deploy the first load still runs the cached modules while the new worker installs. Once it takes over,
  // reload, so the page never mixes a new module with an old one from the cache.
  const updating = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (updating) location.reload(); });
  navigator.serviceWorker.register("/sw.js").then(warmCache).catch(() => null);
}
