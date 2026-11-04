// Hand edits: the session menu, the toast with Undo, and the request the workout page's plan controls share. Code applies these, no coach.
import { api, button, editMemo, el, input, labelled, muted, notice, planPath, postJson, refresh, statusLine, textarea, toast } from "./ui.js";
import { DAYS, addDays, dateText } from "./format.js";

async function undo(id) {
  document.querySelector("[data-undoable]")?.remove();
  await api(`/api/plans/${encodeURIComponent(id)}`);  // the latest ETag: Undo may come after a reload
  await api(`/api/plans/${encodeURIComponent(id)}/undo`, { method: "POST", plan: id });
  toast("Undone");
  refresh();
}

function actionButton(text, action) {
  const b = el("button", text);
  b.type = "button";
  b.addEventListener("click", () => action().catch((error) => toast(error.message)));
  return b;
}

export async function sendEdit(id, body) {
  const result = await postJson(`/api/plans/${encodeURIComponent(id)}/edits`, body, id);
  const note = body.note?.trim();
  const ask = actionButton("Ask the coach to check this week", async () => {
    editMemo.chatDraft = `I changed the plan by hand: ${result.summary}.${note ? ` Reason: ${note}.` : ""} Can you check the rest of the week?`;
    if (location.hash === planPath(id)) refresh();
    else location.hash = planPath(id);
  });
  const rereview = body.activity_id ? ["Re-run the review to judge it against this"] : [];
  toast([result.summary, ...result.warnings, ...rereview].join(" · "), actionButton("Undo", () => undo(id)), ask);
  editMemo.warnings = result.warnings;
  refresh();
}

function sheet(title, ...children) {
  const dialog = el("dialog");
  dialog.className = "sheet";
  const close = el("button", "Close");
  close.type = "button";
  close.addEventListener("click", () => dialog.close());
  dialog.append(el("h3", title), ...children, close);
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

// A sheet row: like `button`, and it closes the sheet it sits in once the action went through.
function option(text, status, action, kind = "") {
  const b = button(text, status, async () => {
    await action();
    b.closest("dialog")?.close();
  }, "Working…", `sheet-row ${kind}`.trim());
  return b;
}

function moveChoices(planFile, week, session, today, status, send) {
  const race = planFile.request.race_date;
  const box = el("div");
  box.className = "day-row";
  DAYS.forEach((day, i) => {
    const date = addDays(week.start_date, i);
    if (date >= today && date < race && date !== session.date) {
      box.append(option(day, status, () => send({ kind: "move", to_date: date })));
    }
  });
  const picker = input("date");
  picker.min = today;
  picker.max = addDays(race, -1);
  const other = el("div");
  other.className = "field-row";
  other.append(labelled("Another date", picker), option("Move", status, async () => {
    if (!picker.value) throw new Error("Pick a date first.");
    await send({ kind: "move", to_date: picker.value });
  }));
  return [box, other];
}

function swapChoices(planFile, week, index, today, status, send) {
  const box = el("details");
  box.append(el("summary", "Swap with…"));
  for (const candidate of planFile.plan.weeks) {
    if (candidate.week_number !== week.week_number && candidate.week_number !== week.week_number + 1) continue;
    candidate.sessions.forEach((s, i) => {
      const self = candidate === week && i === index;
      if (self || s.date < today || s.type === "race" || s.status !== "planned") return;
      box.append(option(`${dateText(s.date)}: ${s.title}`, status, () => send({
        kind: "swap", other: { week: candidate.week_number, index: i },
      })));
    });
  }
  return box;
}

const RUN_TYPES = ["easy", "long", "tempo", "intervals", "recovery"];

// Rewrite the session itself. Only what differs is sent; the server drops interval steps when the amount or pace changes.
function changeForm(session, status, send) {
  const box = el("details");
  box.append(el("summary", "Change…"));
  const fields = {
    title: input("text", session.title),
    distance_km: input("number", session.distance_km),
    duration_min: input("number", session.duration_min),
    target_pace: input("text", session.target_pace),
    description: textarea(3),
  };
  fields.description.value = session.description;
  fields.distance_km.step = fields.duration_min.step = "0.5";
  fields.distance_km.min = fields.duration_min.min = "0";
  if (session.modality === "run") {
    fields.type = el("select");
    fields.type.append(...RUN_TYPES.map((t) => new Option(t, t, false, t === session.type)));
  }
  const row = el("div");
  row.className = "field-row";
  const labels = { title: "Title", type: "Type", distance_km: "Distance (km)", duration_min: "Duration (min)", target_pace: "Target pace", description: "Description" };
  row.append(...Object.entries(fields).map(([key, control]) => labelled(labels[key], control)));
  if (session.steps.length) row.append(muted("Changing the distance, duration or pace turns the steps into one steady block.", "p"));
  box.append(row, option("Save changes", status, () => send({
    kind: "change",
    changes: Object.fromEntries(Object.entries(fields).map(([key, control]) => [key, control.type === "number" ? Number(control.value) : control.value])),
  })));
  return box;
}

export function sessionMenu(planFile, week, index) {
  const session = week.sessions[index];
  const b = el("button", "⋯");
  b.type = "button";
  b.className = "menu-button";
  b.dataset.online = "";
  b.setAttribute("aria-label", `Options for ${session.title}`);
  b.addEventListener("click", async (event) => {
    event.stopPropagation();  // the row around it is a link
    const { today } = await api("/api/today");
    const status = statusLine();
    const note = input("text");
    const send = (body) => sendEdit(planFile.meta.id, { session: { week: week.week_number, index }, note: note.value, ...body });
    const nodes = session.status === "skipped"
      ? [option("Unskip", status, () => send({ kind: "unskip" }))]
      : [
        el("h4", "Move to…"), ...moveChoices(planFile, week, session, today, status, send),
        swapChoices(planFile, week, index, today, status, send),
        ...(session.type === "race" || session.date < today ? [] : [changeForm(session, status, send)]),
        option("Skip", status, () => send({ kind: "skip" })),
        ...(session.type === "race" || session.date < today ? [] : [option("Delete from the plan", status, () => send({ kind: "delete" }), "danger")]),
      ];
    sheet(`${session.day}: ${session.title}`, ...nodes, labelled("Note (optional)", note), status);
  });
  return b;
}

export function sessionTitle(session, content) {
  const box = el("span");
  const title = el("span");
  title.append(content);
  if (session.status !== "planned") title.className = "struck";
  box.append(title);
  if (session.status === "skipped") box.append(" ", muted("Skipped"));
  if (session.status === "replaced") box.append(" ", muted("Replaced"));
  if (session.moved_from) box.append(" ", muted(`moved from ${dateText(session.moved_from).slice(0, 3)}`));
  return box;
}

export function editWarnings() {
  const warnings = editMemo.warnings.map((text) => notice(text, "warning"));
  editMemo.warnings = [];
  return warnings;
}

// Laptop only: dropping on a session swaps, dropping on a free day moves.
let dragged = null;
const canDrag = () => matchMedia("(hover: hover) and (min-width: 900px)").matches;

export function dragSource(row, week, index) {
  if (!canDrag()) return;
  row.draggable = true;
  row.addEventListener("dragstart", (event) => {
    dragged = { week: week.week_number, index };
    event.dataTransfer.effectAllowed = "move";
  });
  row.addEventListener("dragend", () => { dragged = null; });
}

export function dropTarget(row, id, body) {
  if (!canDrag()) return;
  row.addEventListener("dragover", (event) => {
    if (dragged) {
      event.preventDefault();
      row.classList.add("drop-here");
    }
  });
  row.addEventListener("dragleave", () => row.classList.remove("drop-here"));
  row.addEventListener("drop", (event) => {
    event.preventDefault();
    row.classList.remove("drop-here");
    if (!dragged) return;
    const session = dragged;
    sendEdit(id, { session, ...body }).catch((error) => toast(error.message));
  });
}
