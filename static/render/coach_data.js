// The Coach data page: the coach's notes and what the LLM calls have cost. Kept off the plan page on purpose.
import { actions, activePlan, api, button, el, labelled, main, putJson, refresh, stale, statStrip, statusLine, textarea, viewToken } from "./ui.js";

// The coach's notes: the one thing that carries across plans. The coach writes them; you can correct them.
function notesSection(notes) {
  const status = statusLine();
  const text = textarea(16);
  text.value = notes.content || notes.template;
  const save = button("Save notes", status, async () => {
    await putJson("/api/memory", { content: text.value });
    refresh();
  }, "Saving…", "primary");
  const section = el("section");
  section.append(
    el("h2", "What your coach remembers"),
    el("p", notes.updated_at ? `Last updated ${notes.updated_at} by the ${notes.updated_by}.` : "Nothing written yet. The coach fills this in as you talk; you can also write it yourself."),
    labelled("Notes", text),
    actions(save),
    status,
  );
  if (notes.versions.length) {
    const pick = el("select");
    for (const stamp of notes.versions) pick.add(new Option(stamp, stamp));
    section.append(labelled("Earlier version", pick), actions(button("Restore this version", status, async () => {
      await api(`/api/memory/restore/${encodeURIComponent(pick.value)}`, { method: "POST" });
      refresh();
    })));
  }
  return section;
}

function usageSection(plan, usage) {
  const section = el("section");
  section.append(el("h2", "What the coaching has cost"));
  if (!usage) {
    section.append(el("p", "No active plan, so nothing to add up yet."));
    return section;
  }
  const cost = usage.unpriced_models.length ? `$${usage.cost_usd} plus ${usage.unpriced_models.join(", ")}` : `$${usage.cost_usd}`;
  section.append(
    el("p", `For the active plan "${plan.request.goal_description}": plan drafts, proposals, run reviews and the coach's replies.`),
    statStrip([
      ["Tokens in", usage.input_tokens.toLocaleString()],
      ["Of which cached", usage.cached_tokens.toLocaleString()],
      ["Tokens out", usage.output_tokens.toLocaleString()],
      ["About", cost],
    ]),
    el("p", `Models: ${usage.models.join(", ")}. Cached tokens are read at a tenth of the input price.`),
  );
  return section;
}

export async function coachDataView() {
  const token = viewToken();
  const [notes, plan] = await Promise.all([api("/api/memory"), activePlan()]);
  const usage = plan ? await api(`/api/plans/${encodeURIComponent(plan.meta.id)}/usage`) : null;
  if (stale(token)) return;
  main.replaceChildren(notesSection(notes), usageSection(plan, usage));
}
