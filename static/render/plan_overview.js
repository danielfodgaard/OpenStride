// A plan's overview page, and the draft actions it shares with the generator.
import {
  api, breadcrumb, bulletList, button, downloadButton, el, fullPlanVersionCheck, labelled, line, link, main,
  planPath, postJson, refresh, stale, statusLine, table, textarea, viewToken,
} from "./ui.js";
import { DAYS, amountText, dateText } from "./format.js";
import { drawProgressCharts, snapshotText } from "../charts.js";

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
      box.append(line(
        button("Accept", status, async () => {
          await api(`${path}/accept`, { method: "POST" });
          refresh();
        }),
        " ",
        button("Reject", status, async () => {
          await api(`${path}/reject`, { method: "POST" });
          refresh();
        }),
      ));
    }
  }
  box.append(status);
  return box;
}

function adaptSection(planFile, proposals, bases) {
  const section = el("section");
  const status = statusLine();
  const scope = el("select");
  scope.id = "adapt-scope";
  scope.add(new Option("This week", "this_week"));
  scope.add(new Option("Next 2 weeks", "next_2_weeks"));
  scope.add(new Option("Rest of plan", "rest_of_plan"));
  const context = textarea(2, 50);
  context.id = "adapt-context";
  const suggest = button("Suggest plan changes", status, async () => {
    await postJson(`/api/plans/${encodeURIComponent(planFile.meta.id)}/proposals`, {
      scope: scope.value,
      user_context: context.value,
    });
    refresh();
  }, "Thinking… this can take a minute");
  section.append(el("h2", "Adapt plan"), labelled("Scope", scope), labelled("Context", context), line(suggest), status);
  const latest = proposals.find((p) => p.origin !== "chat");
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
    await postJson(`/api/plans/${encoded}/chat`, { text: text.value });
    refresh();
  }, "The coach is thinking…");
  const clear = button("Clear chat", status, async () => {
    if (!confirm("Clear the chat? The coach loses this history.")) return;
    await api(`/api/plans/${encoded}/chat/clear`, { method: "POST" });
    refresh();
  });
  section.append(labelled("Message", text), line(send, " ", clear), status);
  return section;
}

function summaryView({ summary }) {
  const box = el("div");
  box.append(el("h3", summary.headline), el("p", `Status: ${summary.on_track}`), bulletList(summary.observations));
  if (summary.suggestions.length) box.append(el("p", "Suggestions (never applied automatically):"), bulletList(summary.suggestions));
  return box;
}

function progressSection(planFile, progress, readOnly) {
  const section = el("section");
  const status = statusLine();
  const headers = ["Week", "Planned km", "Actual km", "Sessions planned", "Sessions done", "As planned", "Partly", "Missed targets", "Unplanned"];
  const row = (label, r) => [label, r.planned_km, r.actual_km, r.sessions_planned, r.sessions_completed,
    r.as_planned, r.partly, r.missed_targets, r.unplanned];
  const summary = el("div");
  if (progress.latest_summary) summary.append(summaryView(progress.latest_summary));
  section.append(el("h2", "Progress summary"));
  if (!readOnly) {
    const ask = button("Progress summary", status, async () => {
      const result = await api(`/api/plans/${encodeURIComponent(planFile.meta.id)}/progress/summary`, { method: "POST" });
      summary.replaceChildren(summaryView(result));
    }, "Summarising…");
    section.append(line(ask), status);
  }
  section.append(summary, el("h3", "Planned vs actual"), table(headers, [...progress.weeks.map((r) => row(r.week_number, r)), row("Total", progress.totals)]));
  return section;
}

function usageFooter(usage) {
  const cost = usage.unpriced_models.length ? `$${usage.cost_usd} plus ${usage.unpriced_models.join(", ")}` : `$${usage.cost_usd}`;
  return el("p", `Models: ${usage.models.join(", ")} — ${usage.input_tokens} tokens in, ${usage.output_tokens} out — about ${cost} so far`);
}

function headerLines(planFile, status) {
  const { request, meta } = planFile;
  const toGo = status.days_to_race >= 0 ? `${status.days_to_race} days to go` : "race day has passed";
  const lines = [
    el("h1", request.goal_description),
    el("p", `${request.race_distance_km} km — goal ${request.goal_time ?? "best effort"} — race on ${dateText(request.race_date)} (${toGo})`),
  ];
  const encoded = encodeURIComponent(meta.id);
  lines.push(line(`${meta.status} · version ${meta.version} `, link("All versions", `#/plans/${encoded}/versions`)));
  return lines;
}

function statusSection(status, metrics) {
  const section = el("section");
  let where = `Week ${status.week_number} of ${status.weeks_total} · ${status.phase} phase`;
  if (!status.started) where = `Starts in week 1 of ${status.weeks_total}`;
  else if (status.finished) where = "Plan finished";
  else if (!status.week_number) where = "Between plan weeks";
  const facts = [
    `Running: ${status.km_actual} of ${status.km_planned} km planned to date`,
    `Sessions: ${status.sessions_completed} of ${status.sessions_planned} planned to date`,
  ];
  if (status.strength_planned) facts.push(`Strength: ${status.strength_completed} of ${status.strength_planned}`);
  if (status.cross_planned) facts.push(`Cross-training: ${status.cross_completed} of ${status.cross_planned}`);
  section.append(el("h2", "Status"), el("p", where), bulletList(facts), el("p", snapshotText(metrics.current)));
  return section;
}

function nextUpSection(planFile, next, today) {
  const section = el("section");
  section.append(el("h2", "Next up"));
  if (!next) {
    section.append(el("p", "No sessions left on this plan."));
    return section;
  }
  const href = `${planPath(planFile.meta.id)}/sessions/${next.week_number}/${next.index}`;
  const when = next.date === today ? "Today" : dateText(next.date);
  const planned = [next.type, amountText(next), next.target_pace].filter(Boolean).join(" · ");
  section.append(line(link(`${when}: ${next.title}`, href)), el("p", planned));
  if (next.done) {
    const actual = [next.actual_km ? `${next.actual_km} km` : "", `${next.actual_min} min`, next.actual_hr ? `${next.actual_hr} bpm` : ""];
    section.append(el("p", `Done: ${actual.filter(Boolean).join(" · ")}`));
  }
  return section;
}


// Draft actions. `form` is present on the generator page, where field edits count too.
export function draftActions(planFile, status, form, formRequest) {
  const encoded = encodeURIComponent(planFile.meta.id);
  const feedback = textarea(3, 50);
  feedback.id = "feedback";
  const accept = button("Accept plan", status, async () => {
    const active = await fetch("/api/plans/active").then((r) => (r.ok ? r.json() : null));
    if (active && !confirm(`This replaces your current plan '${active.request.goal_description}'`)) return;
    await api(`/api/plans/${encoded}/accept`, { method: "POST" });
    location.hash = planPath(planFile.meta.id);
  });
  const regenerate = button("Regenerate with feedback", status, async () => {
    if (!feedback.value.trim()) throw new Error("Write some feedback first.");
    await postJson(`/api/plans/${encoded}/regenerate`, {
      feedback: feedback.value.trim(),
      request: form ? formRequest(form) : null,
    });
    refresh();
  }, "Regenerating… this takes about a minute");
  const discard = button("Discard", status, async () => {
    if (!confirm("Discard this draft?")) return;
    await api(`/api/plans/${encoded}`, { method: "DELETE" });
    location.hash = "#/plans";
  });
  const box = el("div");
  box.append(line(accept, " ", discard), labelled("Feedback", feedback), line(regenerate));
  if (!form) box.append(line(link("Edit the form fields for this draft", `#/new/${encoded}`)));
  return box;
}

// What used to sit above the weekly tables: warnings, summary and pace zones.
export function planBasics(planFile) {
  const { plan, paces } = planFile;
  const box = el("div");
  if (plan.warnings.length) box.append(bulletList(plan.warnings));
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
  const nodes = [breadcrumb(["All plans", "#/plans"], [planFile.request.goal_description]), ...headerLines(planFile, status)];
  const weeksLink = line(link("View weeks", `${planPath(id)}/weeks`));
  if (isDraft) {
    nodes.push(el("p", "Draft"), draftActions(planFile, actionStatus), actionStatus, planBasics(planFile), weeksLink);
  } else {
    if (!isActive) nodes.push(el("p", "Archived"));
    nodes.push(
      statusSection(status, metrics),
      nextUpSection(planFile, status.next, status.today),
      ...drawProgressCharts(metrics, planFile.request.race_date),
      weeksLink,
      chatSection(planFile, messages, proposals, bases, !isActive),
      progressSection(planFile, progress, !isActive),
    );
    if (isActive) nodes.push(adaptSection(planFile, proposals, bases));
  }
  nodes.push(line(downloadButton(planFile, actionStatus)), usageFooter(usage));
  main.replaceChildren(...nodes);
}
