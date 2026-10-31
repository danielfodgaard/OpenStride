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

function fullRender(planFile, status, options) {
  try {
    renderPlan(planFile, options);
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

function matchSelect(activity, planFile) {
  const match = el("select");
  match.setAttribute("aria-label", "Planned session");
  match.add(new Option("Unplanned", "0:-1"));
  for (const week of planFile.plan.weeks) {
    week.sessions.forEach((session, index) => {
      if ((session.modality ?? "run") !== (activity.modality ?? "run")) return;
      match.add(new Option(`${session.date} ${session.title}`, `${week.week_number}:${index}`));
    });
  }
  match.value = `${activity.match.week_number}:${activity.match.session_index}`;
  return match;
}

function chosenMatch(select) {
  const [week_number, session_index] = select.value.split(":").map(Number);
  return { week_number, session_index };
}

function rpeSelect() {
  const rpe = el("select");
  rpe.setAttribute("aria-label", "RPE");
  rpe.add(new Option("RPE not given", "0"));
  for (let i = 1; i <= 10; i += 1) rpe.add(new Option(String(i), String(i)));
  return rpe;
}

function manualControls(entry, planFile) {
  const box = el("details");
  box.append(el("summary", "Edit"));
  const status = statusLine();
  const note = el("input");
  note.value = entry.note;
  note.setAttribute("aria-label", "Note");
  const duration = el("input");
  duration.type = "number";
  duration.min = "0";
  duration.value = entry.duration_min;
  duration.setAttribute("aria-label", "Duration in minutes");
  const rpe = rpeSelect();
  rpe.value = String(entry.rpe);
  const match = matchSelect(entry, planFile);
  const path = `/api/manual/${encodeURIComponent(entry.id)}`;
  const save = button("Save", status, async () => {
    await api(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: note.value, duration_min: Number(duration.value), rpe: Number(rpe.value), match: chosenMatch(match) }),
    });
    await route();
  });
  const remove = button("Delete", status, async () => {
    if (!confirm(`Delete "${entry.title}"?`)) return;
    await api(path, { method: "DELETE" });
    await route();
  });
  box.append(note, duration, rpe, match, save, remove, status);
  return box;
}

// Edit a run's note, RPE and match, or re-run its review.
function activityControls(activity, planFile) {
  const box = el("details");
  box.append(el("summary", "Edit"));
  const status = statusLine();
  const note = el("input");
  note.value = activity.user_note;
  note.setAttribute("aria-label", "Note");
  const rpe = rpeSelect();
  rpe.value = String(activity.rpe);
  const match = matchSelect(activity, planFile);
  const path = `/api/activities/${encodeURIComponent(activity.id)}`;
  const save = button("Save", status, async () => {
    await api(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_note: note.value, rpe: Number(rpe.value), match: chosenMatch(match) }),
    });
    await route();
  });
  const rerun = button("Re-run review", status, async () => {
    await api(`${path}/review`, { method: "POST" });
    await route();
  }, "Reviewing…");
  box.append(note, rpe, match, save, rerun, status);
  return box;
}

function importSection(status) {
  const files = el("input");
  files.type = "file";
  files.multiple = true;
  files.accept = ".fit,.gpx";
  files.id = "run-files";
  const note = el("textarea");
  note.rows = 2;
  note.cols = 50;
  note.id = "run-note";
  const rpe = el("select");
  rpe.id = "run-rpe";
  rpe.add(new Option("RPE not given", "0"));
  for (let i = 1; i <= 10; i += 1) rpe.add(new Option(String(i), String(i)));
  const labelled = (text, control) => {
    const label = el("label", `${text} `);
    label.htmlFor = control.id;
    const line = el("p");
    line.append(label, control);
    return line;
  };
  const upload = button("Upload", status, async () => {
    if (!files.files.length) throw new Error("Choose at least one .fit or .gpx file.");
    const body = new FormData();
    for (const file of files.files) body.append("files", file);
    body.append("user_note", note.value);
    body.append("rpe", rpe.value);
    const results = await api("/api/activities", { method: "POST", body });
    await route();
    document.getElementById("import-status").textContent = results
      .map((r) => r.message ?? `${r.file}: imported`).join("; ");
  }, "Importing…");
  const section = el("section");
  status.id = "import-status";
  const uploadLine = el("p");
  uploadLine.append(upload);
  section.append(el("h2", "Import runs"), labelled("Files", files), labelled("Note", note), labelled("RPE", rpe), uploadLine, status);
  return section;
}

// One exercise in the logbook form.
function exerciseRow(list, step = {}) {
  const row = el("p");
  row.className = "exercise";
  const field = (name, type, value) => {
    const input = el("input");
    input.type = type;
    input.name = name;
    input.value = value;
    input.setAttribute("aria-label", name);
    return input;
  };
  const remove = el("button", "Remove");
  remove.type = "button";
  remove.addEventListener("click", () => row.remove());
  row.append(
    field("exercise", "text", step.description ?? ""), " sets ",
    field("sets", "number", step.repeat ?? 3), " reps ",
    field("reps", "number", step.reps ?? 10), " load ",
    field("load", "text", step.load ?? ""), " ", remove,
  );
  list.append(row);
}

function readExercises(list) {
  return [...list.querySelectorAll(".exercise")].map((row) => {
    const value = (name) => row.querySelector(`[name=${name}]`).value;
    return {
      repeat: Number(value("sets")), description: value("exercise"), distance_km: 0, duration_min: 0,
      target_pace: "", reps: Number(value("reps")), load: value("load"),
    };
  }).filter((step) => step.description.trim());
}

function logSection(planFile, today) {
  const section = el("section");
  const status = statusLine();
  const labelled = (text, control, id) => {
    control.id = id;
    const label = el("label", `${text} `);
    label.htmlFor = id;
    const line = el("p");
    line.append(label, control);
    return line;
  };
  const input = (type) => {
    const node = el("input");
    node.type = type;
    return node;
  };
  const date = input("date");
  date.value = today;
  date.max = today;
  const modality = el("select");
  modality.add(new Option("Strength", "strength"));
  modality.add(new Option("Cross-training", "cross"));
  modality.add(new Option("Run", "run"));
  const title = input("text");
  const duration = input("number");
  duration.min = "0";
  const distance = input("number");
  distance.min = "0";
  distance.step = "any";
  const rpe = rpeSelect();
  const note = input("text");
  const exercises = el("div");
  const exerciseBox = el("div");
  const addExercise = el("button", "Add exercise");
  addExercise.type = "button";
  addExercise.addEventListener("click", () => exerciseRow(exercises));
  exerciseBox.append(el("p", "Exercises"), exercises, addExercise);

  // Prefill from the planned session that day, when there is one.
  const prefill = () => {
    const session = planFile.plan.weeks.flatMap((w) => w.sessions)
      .find((s) => s.date === date.value && (s.modality ?? "run") === modality.value);
    title.value = session?.title ?? "";
    duration.value = session?.duration_min || "";
    distance.value = session?.distance_km || "";
    exercises.replaceChildren();
    exerciseBox.hidden = modality.value !== "strength";
    if (modality.value === "strength") for (const step of session?.steps ?? []) exerciseRow(exercises, step);
  };
  date.addEventListener("change", prefill);
  modality.addEventListener("change", prefill);
  prefill();

  const save = button("Log session", status, async () => {
    if (!date.value) throw new Error("Choose a date.");
    await postJson(`/api/plans/${encodeURIComponent(planFile.meta.id)}/manual`, {
      date: date.value,
      modality: modality.value,
      title: title.value,
      duration_min: Math.round(Number(duration.value)),
      distance_km: Number(distance.value),
      rpe: Number(rpe.value),
      note: note.value,
      details: modality.value === "strength" ? readExercises(exercises) : [],
    });
    await route();
  });
  const saveLine = el("p");
  saveLine.append(save);
  section.append(
    el("h2", "Log a session"),
    labelled("Date", date, "log-date"),
    labelled("Type", modality, "log-modality"),
    labelled("Title", title, "log-title"),
    labelled("Duration (min)", duration, "log-duration"),
    labelled("Distance (km)", distance, "log-distance"),
    labelled("RPE", rpe, "log-rpe"),
    labelled("Note", note, "log-note"),
    exerciseBox,
    saveLine,
    status,
  );
  return section;
}

function sessionText(session) {
  if (!session) return "Rest";
  const amount = session.distance_km ? `${session.distance_km} km` : `${session.duration_min} min`;
  return `${session.type}: ${session.title}, ${amount} @ ${session.target_pace}`;
}

function sessionsOn(week, day) {
  return week.sessions.filter((s) => s.day === day);
}

// Old vs. new session per day for the affected weeks; changed rows get a *.
function diffTable(planFile, proposal) {
  const rows = proposal.result.weeks.flatMap((newWeek) => {
    const oldWeek = planFile.plan.weeks.find((w) => w.week_number === newWeek.week_number);
    return DAYS.map((day) => {
      const before = sessionsOn(oldWeek, day).map(sessionText).join("; ") || "Rest";
      const after = sessionsOn(newWeek, day).map(sessionText).join("; ") || "Rest";
      return [before === after ? "" : "*", newWeek.week_number, day, before, after];
    });
  });
  return table(["", "Week", "Day", "Old", "New"], rows);
}

function proposalView(proposal, planFile, readOnly = false) {
  const box = el("div");
  const status = statusLine();
  const { recommendation, rationale, changes } = proposal.result;
  box.append(el("h3", recommendation === "no_change" ? "No change recommended" : "Suggested changes"));
  box.append(el("p", `Weeks ${proposal.scope_weeks.join(", ")} — ${proposal.status}`));
  box.append(el("p", rationale));
  if (recommendation === "change") {
    const list = el("ul");
    for (const change of changes) list.append(el("li", `Week ${change.week_number} ${change.day}: ${change.summary}`));
    box.append(list, diffTable(planFile, proposal));
    if (!proposal.acceptable) box.append(el("p", "This proposal can't be accepted."));
    if (proposal.acceptable && proposal.status === "pending" && !readOnly) {
      const path = `/api/proposals/${encodeURIComponent(proposal.proposal_id)}`;
      const actions = el("p");
      actions.append(
        button("Accept", status, async () => {
          await api(`${path}/accept`, { method: "POST" });
          await route();
        }),
        " ",
        button("Reject", status, async () => {
          await api(`${path}/reject`, { method: "POST" });
          await route();
        }),
      );
      box.append(actions);
    }
  }
  box.append(status);
  return box;
}

function adaptSection(planFile, proposals) {
  const section = el("section");
  const status = statusLine();
  const scope = el("select");
  scope.id = "adapt-scope";
  scope.add(new Option("This week", "this_week"));
  scope.add(new Option("Next 2 weeks", "next_2_weeks"));
  scope.add(new Option("Rest of plan", "rest_of_plan"));
  const context = el("textarea");
  context.id = "adapt-context";
  context.rows = 2;
  context.cols = 50;
  const scopeLabel = el("label", "Scope ");
  scopeLabel.htmlFor = scope.id;
  const contextLabel = el("label", "Context ");
  contextLabel.htmlFor = context.id;
  const suggest = button("Suggest plan changes", status, async () => {
    await postJson(`/api/plans/${encodeURIComponent(planFile.meta.id)}/proposals`, {
      scope: scope.value,
      user_context: context.value,
    });
    await route();
  }, "Thinking… this can take a minute");
  const scopeLine = el("p");
  scopeLine.append(scopeLabel, scope);
  const contextLine = el("p");
  contextLine.append(contextLabel, context);
  const suggestLine = el("p");
  suggestLine.append(suggest);
  section.append(el("h2", "Adapt plan"), scopeLine, contextLine, suggestLine, status);
  if (proposals.length) section.append(proposalView(proposals[0], planFile));
  return section;
}

function chatMessage(message, proposals, planFile, readOnly) {
  const box = el("div");
  box.append(el("h4", `${message.role === "user" ? "You" : "Coach"} — ${message.created_at}`));
  for (const paragraph of message.text.split(/\n+/)) box.append(el("p", paragraph));
  if (message.flags.length) {
    const flags = el("ul");
    for (const flag of message.flags) flags.append(el("li", flag));
    box.append(flags);
  }
  const proposal = proposals.find((p) => p.proposal_id === message.proposal_id);
  if (proposal) box.append(proposalView(proposal, planFile, readOnly));
  return box;
}

function chatSection(planFile, messages, proposals, readOnly) {
  const section = el("section");
  const encoded = encodeURIComponent(planFile.meta.id);
  section.append(el("h2", "Coach chat"));
  for (const message of messages) section.append(chatMessage(message, proposals, planFile, readOnly));
  if (!messages.length) section.append(el("p", readOnly ? "No chat on this plan." : "Ask your coach about the plan or your training."));
  if (readOnly) return section;

  const status = statusLine();
  const text = el("textarea");
  text.id = "chat-text";
  text.rows = 3;
  text.cols = 60;
  const label = el("label", "Message ");
  label.htmlFor = text.id;
  const send = button("Send", status, async () => {
    if (!text.value.trim()) throw new Error("Write a message first.");
    await postJson(`/api/plans/${encoded}/chat`, { text: text.value });
    await route();
  }, "The coach is thinking…");
  const clear = button("Clear chat", status, async () => {
    if (!confirm("Clear the chat? The coach loses this history.")) return;
    await api(`/api/plans/${encoded}/chat/clear`, { method: "POST" });
    await route();
  });
  const line = el("p");
  line.append(label, text);
  const actions = el("p");
  actions.append(send, " ", clear);
  section.append(line, actions, status);
  return section;
}

function summaryView({ summary }) {
  const box = el("div");
  box.append(el("h3", summary.headline), el("p", `Status: ${summary.on_track}`));
  const observations = el("ul");
  for (const text of summary.observations) observations.append(el("li", text));
  const suggestions = el("ul");
  for (const text of summary.suggestions) suggestions.append(el("li", text));
  box.append(observations);
  if (summary.suggestions.length) box.append(el("p", "Suggestions (never applied automatically):"), suggestions);
  return box;
}

function progressSection(planFile, progress, metrics) {
  const section = el("section");
  const status = statusLine();
  const headers = ["Week", "Planned km", "Actual km", "Sessions planned", "Sessions done", "As planned", "Partly", "Missed targets", "Unplanned"];
  const row = (label, r) => [label, r.planned_km, r.actual_km, r.sessions_planned, r.sessions_completed,
    r.as_planned, r.partly, r.missed_targets, r.unplanned];
  const summary = el("div");
  if (progress.latest_summary) summary.append(summaryView(progress.latest_summary));
  const ask = button("Progress summary", status, async () => {
    const result = await api(`/api/plans/${encodeURIComponent(planFile.meta.id)}/progress/summary`, { method: "POST" });
    summary.replaceChildren(summaryView(result));
  }, "Summarising…");
  const askLine = el("p");
  askLine.append(ask);
  section.append(
    el("h2", "Progress"),
    ...drawProgressCharts(metrics, planFile.request.race_date),
    el("h3", "Planned vs actual"),
    table(headers, [...progress.weeks.map((r) => row(r.week_number, r)), row("Total", progress.totals)]),
    askLine,
    status,
    summary,
  );
  return section;
}

function usageFooter(usage) {
  const cost = usage.unpriced_models.length ? `$${usage.cost_usd} plus ${usage.unpriced_models.join(", ")}` : `$${usage.cost_usd}`;
  return el("p", `Models: ${usage.models.join(", ")} — ${usage.input_tokens} tokens in, ${usage.output_tokens} out — about ${cost} so far`);
}

async function versionsView(id) {
  const token = routeToken;
  const encoded = encodeURIComponent(id);
  const planFile = await api(`/api/plans/${encoded}`);
  const versions = await api(`/api/plans/${encoded}/versions`);
  if (token !== routeToken) return;
  const status = statusLine();
  const rows = versions.map(({ version, history }) => {
    const view = el("div");
    view.append(link("View", `#/plans/${encoded}/versions/${version}`));
    if (planFile.meta.status === "active" && version !== planFile.meta.version) {
      view.append(" ", button("Restore", status, async () => {
        await api(`/api/plans/${encoded}/restore/${version}`, { method: "POST" });
        location.hash = `#/plans/${encoded}`;
      }));
    }
    return [version, history[0]?.accepted_at ?? planFile.meta.accepted_at, history[0]?.rationale ?? "Accepted plan", view];
  });
  const back = el("p");
  back.append(link("Back to plan", `#/plans/${encoded}`));
  main.replaceChildren(el("h1", "Plan versions"), back, table(["Version", "Accepted", "Why", ""], rows), status);
}

async function versionView(id, version) {
  const token = routeToken;
  const encoded = encodeURIComponent(id);
  const planFile = await api(`/api/plans/${encoded}/versions/${encodeURIComponent(version)}`);
  if (token !== routeToken) return;
  const status = statusLine();
  const back = el("p");
  back.append(link("All versions", `#/plans/${encoded}/versions`));
  main.replaceChildren(el("p", `Version ${planFile.meta.version}, read-only`), back, status, planDiv());
  fullRender(planFile, status);
}

async function planView(id) {
  const token = routeToken;
  const encoded = encodeURIComponent(id);
  const planFile = await api(`/api/plans/${encoded}`);
  const isDraft = planFile.meta.status === "draft";
  const activities = isDraft ? [] : await api(`/api/activities?plan_id=${encoded}`);
  const isActive = planFile.meta.status === "active";
  const proposals = isDraft ? [] : await api(`/api/plans/${encoded}/proposals`);
  const messages = isDraft ? [] : await api(`/api/plans/${encoded}/chat`);
  const progress = isActive ? await api(`/api/plans/${encoded}/progress`) : null;
  const metrics = isActive ? await api(`/api/plans/${encoded}/metrics`) : null;
  const usage = await api(`/api/plans/${encoded}/usage`);
  if (token !== routeToken) return;
  const status = statusLine();
  const { status: planStatus } = planFile.meta;
  const nodes = [];
  if (planStatus === "draft") {
    nodes.push(el("p", "Draft"), draftActions(planFile, status));
  } else {
    if (planStatus === "archived") nodes.push(el("p", "Archived"));
    const actions = el("p");
    actions.append(`version ${planFile.meta.version} `, link("All versions", `#/plans/${encoded}/versions`), " ", downloadButton(planFile, status));
    nodes.push(actions);
  }
  nodes.push(status, planDiv());
  if (isActive) nodes.push(
    importSection(statusLine()),
    logSection(planFile, metrics.as_of),
    chatSection(planFile, messages, proposals, false),
    adaptSection(planFile, proposals),
    progressSection(planFile, progress, metrics),
  );
  if (planStatus === "archived") nodes.push(chatSection(planFile, messages, proposals, true));
  nodes.push(usageFooter(usage));
  main.replaceChildren(...nodes);
  fullRender(planFile, status, {
    actuals: !isDraft,
    activities,
    controls: isActive
      ? (activity) => (activity.manual ? manualControls(activity, planFile) : activityControls(activity, planFile))
      : undefined,
  });
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
    else if (parts[0] === "plans" && parts.length === 2) await planView(parts[1]);
    else if (parts[0] === "plans" && parts.length === 3 && parts[2] === "versions") await versionsView(parts[1]);
    else if (parts[0] === "plans" && parts.length === 4 && parts[2] === "versions") await versionView(parts[1], parts[3]);
    else main.replaceChildren(el("p", "Page not found."));
  } catch (error) {
    main.replaceChildren(el("p", error.message));
  }
}

window.addEventListener("hashchange", route);
route();
