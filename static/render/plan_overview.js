// A plan's overview page, and the draft actions it shares with the generator.
import {
  actions, activePlan, api, breadcrumb, bulletList, button, chip, downloadButton, editMemo, el, foldable, fullPlanVersionCheck, labelled,
  line, link, main, notice, planPath, postJson, refresh, stale, statStrip, statusLine, table, textarea, viewToken,
} from "./ui.js";
import { DAYS, amountText, dateText, roundKm, shortDate } from "./format.js";
import { drawProgressCharts } from "../charts.js";

function sessionText(session) {
  if (!session) return "Rest";
  return `${session.type}: ${session.title}, ${amountText(session)}${session.target_pace ? ` @ ${session.target_pace}` : ""}`;
}

// Old vs. new session per day for the affected weeks; changed rows get a *.
function diffTable(planFile, proposal) {
  const onDay = (week, day) => week.sessions.filter((s) => s.day === day).map(sessionText).join("; ") || "Rest";
  const rows = proposal.result.weeks.flatMap((newWeek) => {
    const oldWeek = planFile.plan.weeks.find((w) => w.week_number === newWeek.week_number);
    return DAYS.map((day) => {
      const before = onDay(oldWeek, day);
      const after = onDay(newWeek, day);
      return [before === after ? "" : "*", newWeek.week_number, day, before, after];
    });
  });
  return table(["", "Week", "Day", "Old", "New"], rows);
}

// `bases` maps a version to its plan: a proposal's diff is against the version it was made from.
function proposalView(proposal, planFile, bases, readOnly = false) {
  const box = el("div");
  const status = statusLine();
  box.className = "field";
  const { recommendation, rationale, changes } = proposal.result;
  box.append(el("h3", recommendation === "no_change" ? "No change recommended" : "Suggested changes"));
  box.append(el("p", `Weeks ${proposal.scope_weeks.join(", ")} — ${proposal.status}`), el("p", rationale));
  if (recommendation === "change") {
    box.append(bulletList(changes.map((c) => `Week ${c.week_number} ${c.day}: ${c.summary}`)));
    if (proposal.warnings.length) box.append(el("p", "Worth knowing before you accept:"), bulletList(proposal.warnings));
    box.append(diffTable(bases[proposal.based_on_version] ?? planFile, proposal));
    if (!proposal.acceptable) box.append(el("p", "This proposal can't be accepted."));
    if (proposal.acceptable && proposal.status === "pending" && !readOnly) {
      const path = `/api/proposals/${encodeURIComponent(proposal.proposal_id)}`;
      const plan = planFile.meta.id;
      box.append(actions(
        button("Accept", status, async () => {
          await api(`${path}/accept`, { method: "POST", plan });
          refresh();
        }, "Working…", "primary"),
        button("Reject", status, async () => {
          await api(`${path}/reject`, { method: "POST", plan });
          refresh();
        }),
      ));
    }
  }
  box.append(status);
  return box;
}

function versionLine({ meta }) {
  return line(`${meta.status} · version ${meta.version} · `, link("All versions", `${planPath(meta.id)}/versions`));
}

function recentEdits({ meta }) {
  const lines = meta.history.slice(-5).reverse().map((h) => `v${h.version} · ${h.summary || (h.proposal_id ? "Coach proposal" : h.rationale)}`);
  return lines.length ? [bulletList(lines)] : [];
}

function adaptSection(planFile, proposals, bases) {
  const status = statusLine();
  const scope = el("select");
  scope.id = "adapt-scope";
  scope.add(new Option("This week", "this_week"));
  scope.add(new Option("Next 2 weeks", "next_2_weeks"));
  scope.add(new Option("Rest of plan", "rest_of_plan"));
  const context = textarea(2, 50);
  context.id = "adapt-context";
  const prefilled = editMemo.adaptContext;
  editMemo.adaptContext = "";
  context.value = prefilled;
  const suggest = button("Suggest plan changes", status, async () => {
    await postJson(`/api/plans/${encodeURIComponent(planFile.meta.id)}/proposals`, {
      scope: scope.value,
      user_context: context.value,
    });
    refresh();
  }, "Thinking… this can take a minute");
  const latest = proposals.find((p) => p.origin !== "chat");
  const pending = proposals.some((p) => p.origin !== "chat" && p.status === "pending");
  const section = foldable(
    "Adapt plan",
    pending || Boolean(prefilled),
    versionLine(planFile),
    ...recentEdits(planFile),
    labelled("Scope", scope),
    labelled("Context", context),
    actions(suggest),
    status,
  );
  if (latest) section.append(proposalView(latest, planFile, bases));
  return section;
}

function chatMessage(message, proposals, planFile, bases, readOnly) {
  const box = el("div");
  box.append(el("h4", `${message.role === "user" ? "You" : "Coach"} — ${message.created_at}`));
  for (const paragraph of message.text.split(/\n+/)) box.append(el("p", paragraph));
  if (message.flags.length) box.append(bulletList(message.flags));
  const proposal = proposals.find((p) => p.proposal_id === message.proposal_id);
  if (proposal) box.append(proposalView(proposal, planFile, bases, readOnly));
  return box;
}

function chatSection(planFile, messages, proposals, bases, readOnly) {
  const section = el("section");
  const encoded = encodeURIComponent(planFile.meta.id);
  section.append(el("h2", "Coach chat"));
  for (const message of messages) section.append(chatMessage(message, proposals, planFile, bases, readOnly));
  if (!messages.length) section.append(el("p", readOnly ? "No chat on this plan." : "Ask your coach about the plan or your training."));
  if (readOnly) return section;

  const status = statusLine();
  const text = textarea(3, 60);
  text.id = "chat-text";
  const send = button("Send", status, async () => {
    if (!text.value.trim()) throw new Error("Write a message first.");
    await postJson(`/api/plans/${encoded}/chat`, { text: text.value }, planFile.meta.id);
    refresh();
  }, "The coach is thinking…", "primary");
  const clear = button("Clear chat", status, async () => {
    if (!confirm("Clear the chat? The coach loses this history.")) return;
    await api(`/api/plans/${encoded}/chat/clear`, { method: "POST", plan: planFile.meta.id });
    refresh();
  });
  section.append(labelled("Message", text), actions(send, clear), status);
  return section;
}

function summaryView({ summary }) {
  const box = el("div");
  box.append(el("h3", summary.headline), el("p", `Status: ${summary.on_track}`), bulletList(summary.observations));
  if (summary.suggestions.length) box.append(el("p", "Suggestions (never applied automatically):"), bulletList(summary.suggestions));
  return box;
}

function progressSection(planFile, progress, readOnly) {
  const status = statusLine();
  const headers = ["Week", "Planned km", "Actual km", "Sessions planned", "Sessions done", "Skipped", "Replaced", "As planned", "Partly", "Missed targets", "Unplanned"];
  const row = (label, r) => [label, r.planned_km, r.actual_km, r.sessions_planned, r.sessions_completed, r.sessions_skipped, r.sessions_replaced,
    r.as_planned, r.partly, r.missed_targets, r.unplanned];
  const summary = el("div");
  if (progress.latest_summary) summary.append(summaryView(progress.latest_summary));
  const section = foldable("Progress summary", false);
  if (!readOnly) {
    const ask = button("Progress summary", status, async () => {
      const result = await api(`/api/plans/${encodeURIComponent(planFile.meta.id)}/progress/summary`, { method: "POST" });
      summary.replaceChildren(summaryView(result));
    }, "Summarising…");
    section.append(actions(ask), status);
  }
  section.append(summary, el("h3", "Planned vs actual"), table(headers, [...progress.weeks.map((r) => row(r.week_number, r)), row("Total", progress.totals)]));
  return section;
}

function usageFooter(usage) {
  const cost = usage.unpriced_models.length ? `$${usage.cost_usd} plus ${usage.unpriced_models.join(", ")}` : `$${usage.cost_usd}`;
  const text = el("p", `Models: ${usage.models.join(", ")} — ${usage.input_tokens} tokens in, ${usage.output_tokens} out — about ${cost} so far`);
  text.className = "muted";
  return text;
}

export const daysToGo = (days) => (days >= 0 ? `${days} days to go` : "race day has passed");

function headerLines(planFile, status) {
  const { request, meta } = planFile;
  const head = el("div");
  head.className = "page-head";
  const sub = el("p", `${request.race_distance_km} km · goal ${request.goal_time ?? "best effort"} · race on ${dateText(request.race_date)} (${daysToGo(status.days_to_race)}) `);
  sub.className = "sub";
  sub.append(chip(meta.status));
  head.append(el("h1", request.goal_description), sub);
  return head;
}

function toDateSection(status) {
  const section = el("section");
  const stats = [
    ["Running to date", `${status.km_actual} / ${status.km_planned}`, "km"],
    ["Sessions to date", `${status.sessions_completed} / ${status.sessions_planned}`],
  ];
  if (status.strength_planned) stats.push(["Strength", `${status.strength_completed} / ${status.strength_planned}`]);
  if (status.cross_planned) stats.push(["Cross-training", `${status.cross_completed} / ${status.cross_planned}`]);
  section.append(el("h2", "To date"), statStrip(stats));
  return section;
}

function doneText(session) {
  const actual = [session.actual_km ? `${session.actual_km} km` : "", `${session.actual_min} min`, session.actual_pace, session.actual_hr ? `${session.actual_hr} bpm` : ""];
  return actual.filter(Boolean).join(" · ");
}

function sessionRow(planFile, session, when, facts, done) {
  const row = link("", `${planPath(planFile.meta.id)}/sessions/${session.week_number}/${session.index}`);
  row.className = "row-link";
  const title = el("span", `${when}: ${session.title}`);
  title.className = "row-title";
  const meta = el("span", facts);
  meta.className = "row-meta";
  row.append(title, meta);
  if (done) row.append(chip("done"));
  return row;
}

// The plan's week to show: the current one, week 1 before the start, none once it's over.
function shownWeek(status) {
  if (status.finished) return 0;
  return status.started ? status.week_number : 1;
}

export function weekSummary(status, metrics) {
  const number = shownWeek(status);
  const week = metrics.weeks.find((w) => w.week_number === number);
  if (!week) return status.finished ? "Plan finished" : "Between plan weeks";
  return `${roundKm(week.actual_km)} of ${roundKm(week.planned_km)} km · ${week.completed_sessions} of ${week.planned_sessions} sessions`;
}

function thisWeekSection(planFile, status, metrics) {
  const section = el("section");
  const number = shownWeek(status);
  const week = planFile.plan.weeks.find((w) => w.week_number === number);
  const facts = week ? `Week ${number} of ${status.weeks_total} · ${week.phase} · ${weekSummary(status, metrics)}` : weekSummary(status, metrics);
  const lead = link(facts, `${planPath(planFile.meta.id)}/weeks${week ? `/${number}` : ""}`);
  lead.className = "big";
  section.append(el("h2", "This week"), lead);
  if (!status.started) section.append(el("p", `Starts ${shortDate(planFile.plan.weeks[0].start_date)}`));
  const list = el("div");
  list.className = "plan-list";
  const { next, today, today_done: todayDone } = status;
  if (todayDone) list.append(sessionRow(planFile, todayDone, "Today", doneText(todayDone), true));
  if (next) {
    const planned = [next.type, amountText(next), next.target_pace].filter(Boolean).join(" · ");
    list.append(sessionRow(planFile, next, next.date === today ? "Today" : shortDate(next.date), planned));
  } else {
    list.append(el("p", "No sessions left on this plan."));
  }
  section.append(list);
  return section;
}

// Draft actions. `form` is present on the generator page, where field edits count too.
export function draftActions(planFile, status, form, formRequest) {
  const encoded = encodeURIComponent(planFile.meta.id);
  const feedback = textarea(3, 50);
  feedback.id = "feedback";
  const accept = button("Accept plan", status, async () => {
    const active = await activePlan();
    if (active && !confirm(`This replaces your current plan '${active.request.goal_description}'`)) return;
    await api(`/api/plans/${encoded}/accept`, { method: "POST", plan: planFile.meta.id });
    location.hash = planPath(planFile.meta.id);
  }, "Working…", "primary");
  const regenerate = button("Regenerate with feedback", status, async () => {
    if (!feedback.value.trim()) throw new Error("Write some feedback first.");
    await postJson(`/api/plans/${encoded}/regenerate`, {
      feedback: feedback.value.trim(),
      request: form ? formRequest(form) : null,
    }, planFile.meta.id);
    refresh();
  }, "Regenerating… this takes about a minute");
  const discard = button("Discard", status, async () => {
    if (!confirm("Discard this draft?")) return;
    await api(`/api/plans/${encoded}`, { method: "DELETE" });
    location.hash = "#/plans";
  }, "Working…", "danger");
  const box = el("div");
  box.className = "field";
  box.append(actions(accept, discard), labelled("Feedback", feedback), actions(regenerate));
  if (!form) box.append(line(link("Edit the form fields for this draft", `#/new/${encoded}`)));
  return box;
}

// What used to sit above the weekly tables: warnings, summary and pace zones.
export function planBasics(planFile) {
  const { plan, paces } = planFile;
  const box = el("section");
  for (const warning of plan.warnings) box.append(notice(warning));
  box.append(el("p", plan.summary), table(["Zone", "Pace"], [
    ["Easy", paces.easy],
    ["Marathon", paces.marathon],
    ["Threshold", paces.threshold],
    ["Interval", paces.interval],
    ["Repetition", paces.repetition],
    ["Race pace", paces.race_pace],
  ]), el("p", `Current VDOT: ${paces.current_vdot} — Goal VDOT: ${paces.goal_vdot ?? "none"}`));
  return box;
}

export async function planOverview(id) {
  const token = viewToken();
  const encoded = encodeURIComponent(id);
  const planFile = await api(`/api/plans/${encoded}`);
  fullPlanVersionCheck(planFile);
  const isDraft = planFile.meta.status === "draft";
  const isActive = planFile.meta.status === "active";
  const [status, metrics, usage] = await Promise.all([
    api(`/api/plans/${encoded}/status`),
    api(`/api/plans/${encoded}/metrics`),
    api(`/api/plans/${encoded}/usage`),
  ]);
  const proposals = isDraft ? [] : await api(`/api/plans/${encoded}/proposals`);
  const messages = isDraft ? [] : await api(`/api/plans/${encoded}/chat`);
  const progress = isDraft ? null : await api(`/api/plans/${encoded}/progress`);
  const bases = {};
  for (const version of new Set(proposals.map((p) => p.based_on_version))) {
    bases[version] = await api(`/api/plans/${encoded}/versions/${version}`).catch(() => null);
  }
  if (stale(token)) return;

  const actionStatus = statusLine();
  const nodes = [breadcrumb(["All plans", "#/plans"], [planFile.request.goal_description]), headerLines(planFile, status)];
  const weeksLink = line(link("View weeks", `${planPath(id)}/weeks`));
  if (isDraft) {
    const draft = el("section");
    draft.append(draftActions(planFile, actionStatus), actionStatus);
    nodes.push(draft, planBasics(planFile), weeksLink, versionLine(planFile));
  } else {
    const [fitness, snapshot, volume] = drawProgressCharts(metrics, planFile.request.race_date);
    const fitnessCard = el("section");
    fitnessCard.append(fitness, snapshot);
    const volumeCard = el("section");
    volumeCard.append(volume);
    nodes.push(
      thisWeekSection(planFile, status, metrics),
      toDateSection(status),
      fitnessCard,
      volumeCard,
      weeksLink,
      chatSection(planFile, messages, proposals, bases, !isActive),
      progressSection(planFile, progress, !isActive),
      isActive ? adaptSection(planFile, proposals, bases) : versionLine(planFile),
    );
  }
  nodes.push(actions(downloadButton(planFile, actionStatus)), usageFooter(usage));
  main.replaceChildren(...nodes);
}
