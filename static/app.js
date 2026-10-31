// Hash router and views. All plan rendering goes through render.js.
const main = document.getElementById("main");
let routeToken = 0;  // lets a slow view notice the user has moved on

async function api(path, options) {
  const response = await fetch(path, options);
  if (response.ok) return response.status === 204 ? null : response.json();
  const body = await response.json().catch(() => null);
  if (Array.isArray(body?.detail)) {
    throw new Error(body.detail.map((d) => `${d.loc.slice(1).join(".") || "request"}: ${d.msg}`).join("; "));
  }
  throw new Error(body?.detail ?? `Request failed (${response.status})`);
}

const postJson = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

function link(text, href) {
  const a = el("a", text);
  a.href = href;
  return a;
}

// A button that shows progress and errors on `status` and can't be double-clicked.
function button(text, status, action, busyText = "Working…") {
  const b = el("button", text);
  b.type = "button";
  b.addEventListener("click", async () => {
    b.disabled = true;
    status.textContent = busyText;
    try {
      await action();
      status.textContent = "";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      b.disabled = false;
    }
  });
  return b;
}

function statusLine() {
  const p = el("p");
  p.setAttribute("role", "status");
  return p;
}

function downloadButton(planFile, status) {
  return button("Download JSON", status, async () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(planFile, null, 2)], { type: "application/json" }));
    a.download = `${planFile.meta.id}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
}

function planDiv() {
  const div = el("div");
  div.id = "plan";
  return div;
}

function fullRender(planFile, status) {
  try {
    renderPlan(planFile);
  } catch (error) {
    status.textContent = `Could not show plan: ${error.message}`;
  }
}

// Form

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
  };
}

function fillForm(form, request) {
  const fields = form.elements;
  const preset = [...fields.race_distance.options].find((o) => Number(o.value) === request.race_distance_km);
  fields.race_distance.value = preset ? preset.value : "custom";
  fields.custom_distance.value = preset ? "" : request.race_distance_km;
  for (const name of ["goal_description", "race_date", "recent_race_distance_km", "recent_race_time",
    "current_weekly_km", "longest_recent_run_km", "runs_per_week", "long_run_day", "experience", "notes"]) {
    fields[name].value = request[name];
  }
  fields.goal_time.value = request.goal_time ?? "";
  for (const box of fields.available_days) box.checked = request.available_days.includes(box.value);
}

// Draft actions. `form` is present on the generator page, where field edits count too.
async function acceptPlan(id) {
  const active = await fetch("/api/plans/active").then((r) => (r.ok ? r.json() : null));
  if (active && !confirm(`This replaces your current plan '${active.request.goal_description}'`)) return;
  await api(`/api/plans/${encodeURIComponent(id)}/accept`, { method: "POST" });
  location.hash = `#/plans/${encodeURIComponent(id)}`;
}

function draftActions(planFile, status, form) {
  const id = encodeURIComponent(planFile.meta.id);
  const feedback = el("textarea");
  feedback.rows = 3;
  feedback.cols = 50;
  feedback.id = "feedback";
  const label = el("label", "Feedback ");
  label.htmlFor = "feedback";

  const regenerate = button("Regenerate with feedback", status, async () => {
    if (!feedback.value.trim()) throw new Error("Write some feedback first.");
    await postJson(`/api/plans/${id}/regenerate`, {
      feedback: feedback.value.trim(),
      request: form ? formRequest(form) : null,
    });
    await route();
  }, "Regenerating… this takes about a minute");

  const discard = button("Discard", status, async () => {
    if (!confirm("Discard this draft?")) return;
    await api(`/api/plans/${id}`, { method: "DELETE" });
    location.hash = "#/plans";
  });

  const box = el("div");
  const actions = el("p");
  actions.append(button("Accept plan", status, () => acceptPlan(planFile.meta.id)), " ", discard, " ", downloadButton(planFile, status));
  const feedbackLine = el("p");
  feedbackLine.append(label, feedback, " ", regenerate);
  box.append(actions, feedbackLine);
  if (!form) {
    const edit = el("p");
    edit.append(link("Edit the form fields for this draft", `#/new/${id}`));
    box.append(edit);
  }
  return box;
}

// Views

async function newView(draftId) {
  const token = routeToken;
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
  const planFile = await api(`/api/plans/${encodeURIComponent(draftId)}`);
  if (token !== routeToken) return;
  if (planFile.meta.status !== "draft") {
    location.replace(`#/plans/${encodeURIComponent(draftId)}`);
    return;
  }
  fillForm(form, planFile.request);
  preview.append(el("h2", "Draft preview"), draftActions(planFile, status, form), planDiv());
  fullRender(planFile, status);
}

async function plansView() {
  const token = routeToken;
  const plans = await api("/api/plans");
  if (token !== routeToken) return;
  const status = statusLine();
  const rows = plans.map((p) => [
    p.goal_description, p.race_date, p.status, p.version, p.created_at,
    link("Open", `#/plans/${encodeURIComponent(p.id)}`),
  ]);
  const importInput = el("input");
  importInput.type = "file";
  importInput.accept = ".json,application/json";
  importInput.id = "import-file";
  importInput.addEventListener("change", async () => {
    const file = importInput.files[0];
    if (!file) return;
    status.textContent = "Importing…";
    const body = new FormData();
    body.append("file", file);
    try {
      const planFile = await api("/api/plans/import", { method: "POST", body });
      location.hash = `#/plans/${encodeURIComponent(planFile.meta.id)}`;
    } catch (error) {
      status.textContent = error.message;
    }
  });
  const importLabel = el("label", "Import plan file ");
  importLabel.htmlFor = "import-file";
  const importLine = el("p");
  importLine.append(importLabel, importInput);
  const createLine = el("p");
  createLine.append(link("Create a plan", "#/new"));
  main.replaceChildren(
    el("h1", "All plans"),
    plans.length ? table(["Goal", "Race date", "Status", "Version", "Created", ""], rows) : el("p", "No plans yet."),
    createLine,
    importLine,
    status,
  );
}

async function planView(id) {
  const token = routeToken;
  const planFile = await api(`/api/plans/${encodeURIComponent(id)}`);
  if (token !== routeToken) return;
  const status = statusLine();
  const { status: planStatus } = planFile.meta;
  const nodes = [];
  if (planStatus === "draft") {
    nodes.push(el("p", "Draft"), draftActions(planFile, status));
  } else {
    if (planStatus === "archived") nodes.push(el("p", "Archived"));
    const actions = el("p");
    actions.append(downloadButton(planFile, status));
    nodes.push(actions);
  }
  nodes.push(status, planDiv());
  main.replaceChildren(...nodes);
  fullRender(planFile, status);
}

async function homeView() {
  const active = await fetch("/api/plans/active").then((r) => (r.ok ? r.json() : null));
  location.replace(active ? `#/plans/${encodeURIComponent(active.meta.id)}` : "#/plans");
}

async function route() {
  routeToken += 1;
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  try {
    if (parts.length === 0) await homeView();
    else if (parts[0] === "new") await newView(parts[1]);
    else if (parts[0] === "plans" && parts.length === 1) await plansView();
    else if (parts[0] === "plans") await planView(parts[1]);
    else main.replaceChildren(el("p", "Page not found."));
  } catch (error) {
    main.replaceChildren(el("p", error.message));
  }
}

window.addEventListener("hashchange", route);
route();
