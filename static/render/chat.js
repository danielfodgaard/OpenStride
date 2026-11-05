// The Coach page: the conversation about the active plan, on its own tab. A plan page shows an old plan's chat read-only.
import {
  actions, activePlan, api, bulletList, button, editMemo, el, foldable, labelled, line, link, main, muted, planPath, postStream,
  refresh, richText, stale, statusLine, table, textarea, viewToken,
} from "./ui.js";
import { DAYS, amountText } from "./format.js";

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

function chatMessage(message, proposals, planFile, bases, readOnly) {
  const box = el("div");
  box.append(el("h4", `${message.role === "user" ? "You" : "Coach"} — ${message.created_at}`));
  box.append(...richText(message.text));
  if (message.flags.length) box.append(bulletList(message.flags));
  if (message.memory_updated) box.append(line(muted("The coach updated "), link("its notes about you", "#/coach"), muted(".")));
  const proposal = proposals.find((p) => p.proposal_id === message.proposal_id);
  if (proposal) box.append(proposalView(proposal, planFile, bases, readOnly));
  return box;
}

// The conversation: its messages with their proposals, and the composer unless the plan is read-only.
export function chatSection(planFile, messages, proposals, bases, readOnly) {
  const encoded = encodeURIComponent(planFile.meta.id);
  const section = el("section");
  const render = (message) => chatMessage(message, proposals, planFile, bases, readOnly);
  // Only the latest exchange stays open; the rest folds away so the page doesn't grow with the conversation.
  const earlier = messages.slice(0, -2);
  if (earlier.length) section.append(foldable(`Earlier in this conversation (${earlier.length})`, false, ...earlier.map(render)));
  section.append(...messages.slice(-2).map(render));
  if (!messages.length) section.append(el("p", readOnly ? "No chat on this plan." : "Ask about your training, or ask for a plan change. The coach sees your plan, every run and its own notes about you."));
  if (readOnly) return section;

  const status = statusLine();
  const text = textarea(3);
  text.value = editMemo.chatDraft;
  editMemo.chatDraft = "";
  const composer = labelled("Message", text);
  const send = button("Send", status, async () => {
    if (!text.value.trim()) throw new Error("Write a message first.");
    // The exchange shows as it streams; the reload at the end draws it properly, Markdown and all.
    const you = el("div");
    you.append(el("h4", "You"), el("p", text.value));
    const live = el("p", "");
    live.className = "streaming";
    const progress = muted("Sending…", "p");  // what the coach is doing, and for how long, until its words arrive
    const coach = el("div");
    coach.append(el("h4", "Coach"), progress, live);
    section.insertBefore(you, composer);
    section.insertBefore(coach, composer);
    const started = Date.now();
    let stage = "Sending…";
    const show = () => { progress.textContent = `${stage} ${Math.round((Date.now() - started) / 1000)} s`; };
    const ticker = setInterval(show, 1000);
    try {
      await postStream(`/api/plans/${encoded}/chat`, { text: text.value }, planFile.meta.id, (words) => {
        live.textContent += words;
        progress.hidden = true;
      }, (what) => {
        stage = what;
        progress.hidden = false;
        show();
      });
    } finally {
      clearInterval(ticker);
    }
    refresh();
  }, "The coach is thinking…", "primary");
  const clear = button("New conversation", status, async () => {
    if (!confirm("Start a new conversation? The coach keeps its notes about you; this conversation is archived, not shown again.")) return;
    await api(`/api/plans/${encoded}/chat/clear`, { method: "POST", plan: planFile.meta.id });
    refresh();
  });
  section.append(composer, actions(send, clear), status);
  return section;
}

export async function chatView() {
  const token = viewToken();
  const active = await activePlan();
  if (!active) {
    main.replaceChildren(el("h1", "Your coach"), line("The coach talks about your active plan. ", link("Accept a plan", "#/plans"), " to start a conversation."));
    return;
  }
  const encoded = encodeURIComponent(active.meta.id);
  const planFile = await api(`/api/plans/${encoded}`);  // by id, so its ETag is on hand for a proposal
  const [messages, proposals] = await Promise.all([api(`/api/plans/${encoded}/chat`), api(`/api/plans/${encoded}/proposals`)]);
  const bases = {};
  for (const version of new Set(proposals.map((p) => p.based_on_version))) {
    bases[version] = await api(`/api/plans/${encoded}/versions/${version}`).catch(() => null);
  }
  if (stale(token)) return;
  const about = line(muted("About "), link(planFile.request.goal_description, planPath(active.meta.id)));
  about.className = "sub";
  main.replaceChildren(el("h1", "Your coach"), about, chatSection(planFile, messages, proposals, bases, false));
}
