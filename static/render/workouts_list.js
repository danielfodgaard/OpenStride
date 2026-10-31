// The Workouts page: importing, logging and every recorded workout, newest first.
import {
  actions, activePlan, api, button, chip, el, foldable, garminStatus, input, labelled, link, main, notice, postJson, refresh,
  rowLinks, rpeSelect, stale, statusLine, table, textarea, toast, viewToken,
} from "./ui.js";
import { clockText, monthHeading, shortDate, stepText, timeText } from "./format.js";

let importReport = "";  // survives the re-render after an upload
let syncReport = "";

const UNOFFICIAL_WARNING = "Garmin sync uses an unofficial connection: it can break without notice and is a grey area under Garmin's terms.";

function garminSection(garmin) {
  const section = el("section");
  const status = statusLine();
  status.textContent = syncReport;
  syncReport = "";
  const sync = button("Sync from Garmin", status, async () => {
    const result = await api("/api/garmin/sync", { method: "POST" });
    const count = result.imported.length;
    toast(`${count} ${count === 1 ? "run" : "runs"} imported, ${result.matched} matched, ${result.unplanned} unplanned`);
    syncReport = result.warnings.join(" ");
    refresh();
  }, "Syncing…", "primary");
  const last = el("span", garmin.last_sync_at ? `Last sync ${new Date(garmin.last_sync_at).toLocaleString()}` : "Not synced yet");
  last.className = "muted";
  section.append(actions(sync, last));
  if (garmin.last_error) section.append(notice(garmin.last_error, "critical"));
  section.append(status);
  return section;
}

function importSection(open) {
  const startOpen = open || Boolean(importReport);
  const status = statusLine();
  status.id = "import-status";
  status.textContent = importReport;
  importReport = "";
  const files = input("file");
  files.value = "";
  files.multiple = true;
  files.accept = ".fit,.gpx";
  files.id = "run-files";
  const note = textarea(2, 50);
  const rpe = rpeSelect();
  const upload = button("Upload", status, async () => {
    if (!files.files.length) throw new Error("Choose at least one .fit or .gpx file.");
    const body = new FormData();
    for (const file of files.files) body.append("files", file);
    body.append("user_note", note.value);
    body.append("rpe", rpe.value);
    const results = await api("/api/activities", { method: "POST", body });
    const [summary, ...details] = reportParts(results);
    toast(summary);
    importReport = details.join(". ");
    refresh();
  }, "Importing…", "primary");
  return foldable("Import runs", startOpen, labelled("Files", files), labelled("Note", note), labelled("RPE", rpe), actions(upload), status);
}

function reportParts(results) {
  const imported = results.filter((r) => r.status === "imported");
  const matched = imported.filter((r) => r.activity.match.session_index >= 0).length;
  const count = imported.length;
  const summary = `${count} ${count === 1 ? "run" : "runs"} imported, ${matched} matched, ${count - matched} unplanned`;
  return [summary, ...results.flatMap((r) => [r.message, ...(r.warnings ?? [])]).filter(Boolean)];
}

function exerciseRow(list, step = {}) {
  const row = el("p");
  row.className = "exercise";
  const field = (name, type, value) => {
    const node = input(type, value);
    node.name = name;
    node.setAttribute("aria-label", name);
    return node;
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

function logSection(activePlan, today, prefillDate, open) {
  const status = statusLine();
  const date = input("date", prefillDate || today);
  date.id = "log-date";
  date.max = today;
  const modality = el("select");
  modality.id = "log-modality";
  modality.add(new Option("Strength", "strength"));
  modality.add(new Option("Cross-training", "cross"));
  modality.add(new Option("Run", "run"));
  const title = input("text");
  title.id = "log-title";
  const duration = input("number");
  duration.id = "log-duration";
  duration.min = "0";
  const distance = input("number");
  distance.id = "log-distance";
  distance.min = "0";
  distance.step = "any";
  const rpe = rpeSelect();
  rpe.id = "log-rpe";
  const note = input("text");
  note.id = "log-note";
  const exercises = el("div");
  const exerciseBox = el("div");
  const addExercise = el("button", "Add exercise");
  addExercise.type = "button";
  addExercise.addEventListener("click", () => exerciseRow(exercises));
  exerciseBox.className = "field";
  exerciseBox.append(el("h4", "Exercises"), exercises, actions(addExercise));

  // Prefill from the planned session that day, when there is one.
  const prefill = () => {
    const sessions = activePlan?.plan.weeks.flatMap((w) => w.sessions) ?? [];
    const session = sessions.find((s) => s.date === date.value && (s.modality ?? "run") === modality.value);
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
    const path = activePlan ? `/api/plans/${encodeURIComponent(activePlan.meta.id)}/manual` : "/api/manual";
    await postJson(path, {
      date: date.value,
      modality: modality.value,
      title: title.value,
      duration_min: Math.round(Number(duration.value)),
      distance_km: Number(distance.value),
      rpe: Number(rpe.value),
      note: note.value,
      details: modality.value === "strength" ? readExercises(exercises) : [],
    }, activePlan?.meta.id);
    toast("Session logged");
    refresh();
  }, "Working…", "primary");
  return foldable(
    "Log a session",
    open,
    labelled("Date", date),
    labelled("Type", modality),
    labelled("Title", title),
    labelled("Duration (min)", duration),
    labelled("Distance (km)", distance),
    labelled("RPE", rpe),
    labelled("Note", note),
    exerciseBox,
    actions(save),
    status,
  );
}

function workoutRow(item) {
  const href = `#/workouts/${encodeURIComponent(item.id)}`;
  const review = el("span", item.review ? ` ${item.review.review.headline}` : item.summary ?? "");
  if (item.review) review.prepend(chip(item.review.review.completion));
  const when = `${shortDate(item.date)} ${clockText(item.start_time)}`.trim();  // the month heading has the year
  if (item.manual) {
    return { href, cells: [when, link(item.title, href), item.modality, item.distance_km ? `${item.distance_km} km` : "",
      `${item.duration_min} min`, item.rpe ? `RPE ${item.rpe}` : "", "", item.match_label, review] };
  }
  return { href, cells: [when, link(item.title, href), item.type, `${item.distance_km.toFixed(2)} km`,
    timeText(item.moving_time_s), item.avg_pace, item.avg_hr ? `${item.avg_hr} bpm` : "", item.match_label, review] };
}

function listSection(items) {
  const section = el("section");
  section.append(el("h2", "All workouts"));
  if (!items.length) section.append(el("p", "No workouts yet. Import a .fit or .gpx file, or log a session."));
  const months = new Map();
  for (const item of items) {
    const heading = monthHeading(item.date);
    months.set(heading, [...(months.get(heading) ?? []), workoutRow(item)]);
  }
  for (const [heading, rows] of months) {
    const body = table(
      ["When", "Workout", "Type", "Distance", "Time", "Pace", "HR", "Plan", "Review"],
      rows.map((row) => row.cells),
    );
    [...body.querySelector("tbody").rows].forEach((row, i) => rowLinks(row, rows[i].href));
    section.append(el("h3", heading), body);
  }
  return section;
}

export async function workoutsView(query) {
  const token = viewToken();
  const [items, active, { today }, garmin] = await Promise.all([
    api("/api/activities"),
    activePlan().then((plan) => (plan ? api(`/api/plans/${encodeURIComponent(plan.meta.id)}`) : null)),  // this read holds the ETag
    api("/api/today"),
    garminStatus(),
  ]);
  if (stale(token)) return;
  const open = query.get("open");
  main.replaceChildren(
    el("h1", "Workouts"),
    ...(garmin ? [garminSection(garmin)] : []),
    importSection(open === "import"),
    logSection(active, today, query.get("date"), open === "log"),
    listSection(items),
  );
}
