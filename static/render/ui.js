// Small DOM and API helpers shared by every view.
export const main = document.getElementById("main");

let routeToken = 0;
export const nextRoute = () => {
  routeToken += 1;
  savedAt = null;
};
// A view keeps its token; a slow fetch can then see that the user has moved on.
export const viewToken = () => routeToken;
export const stale = (token) => token !== routeToken;
export const refresh = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

export const planPath = (id) => `#/plans/${encodeURIComponent(id)}`;

export const USB_HELP = "Connect the watch by USB and copy the file into GARMIN/NewFiles. It appears under workouts after you unplug.";

// Text always goes through textContent: plan files and notes can hold anything.
export function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

export function line(...children) {
  const p = el("p");
  p.append(...children);
  return p;
}

export function actions(...children) {
  const box = el("div");
  box.className = "actions";
  box.append(...children);
  return box;
}

const CHIP_KINDS = { as_planned: "good", partly: "warning", missed_targets: "critical", active: "good", draft: "warning" };
const CHIP_TEXT = { as_planned: "As planned", partly: "Partly", missed_targets: "Missed targets", unplanned: "Unplanned",
  active: "Active", draft: "Draft", archived: "Archived", done: "Done" };

// Status always reads as text; the colour only backs it up.
export function chip(value, kind = CHIP_KINDS[value]) {
  const span = el("span", CHIP_TEXT[value] ?? value);
  span.className = kind ? `chip ${kind}` : "chip";
  return span;
}

// stats: [[label, value, unit]]
export function statStrip(stats) {
  const strip = el("div");
  strip.className = "strip";
  for (const [label, value, unit] of stats) {
    const stat = el("div");
    stat.className = "stat";
    const name = el("span", label);
    name.className = "k";
    const number = el("span", value);
    number.className = "v";
    if (unit) number.append(el("small", unit));
    stat.append(name, number);
    strip.append(stat);
  }
  return strip;
}

export function foldable(title, open, ...children) {
  const box = el("details");
  box.className = "fold";
  box.open = open;
  const summary = el("summary");
  summary.append(el("h2", title));
  box.append(summary, ...children);
  return box;
}

const escapeHtml = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const inline = (text) => escapeHtml(text)
  .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
  .replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/(^|\s)\*(\S.*?\S|\S)\*(?=[\s.,;:!?]|$)/g, "$1<em>$2</em>");
const LIST_LINE = /^\s*(?:[-*•]|\d+[.)])\s+/;

// The coach's light Markdown: paragraphs, bullet and numbered lists, bold, code. Headings become bold lines.
export function richText(text) {
  const nodes = [];
  let list = null;
  for (const line of text.split("\n")) {
    if (LIST_LINE.test(line)) {
      const numbered = /^\s*\d/.test(line);
      if (!list || list.tagName !== (numbered ? "OL" : "UL")) nodes.push(list = el(numbered ? "ol" : "ul"));
      const item = el("li");
      item.innerHTML = inline(line.replace(LIST_LINE, ""));
      list.append(item);
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    const p = el("p");
    const heading = line.match(/^#+\s+(.*)/);
    p.innerHTML = heading ? `<strong>${inline(heading[1])}</strong>` : inline(line);
    nodes.push(p);
  }
  return nodes;
}

export function notice(text, kind = "") {
  const p = el("p", text);
  p.className = `notice ${kind}`.trim();
  return p;
}

export function muted(text, tag = "span") {
  const node = el(tag, text);
  node.className = "muted";
  return node;
}

// A whole-row link with a title, a meta line and an optional chip.
export function rowLink(href, title, meta, chipNode) {
  const row = link("", href);
  row.className = "row-link";
  const titleNode = el("span", title);
  titleNode.className = "row-title";
  const metaNode = el("span", meta);
  metaNode.className = "row-meta";
  row.append(titleNode, metaNode);
  if (chipNode) row.append(chipNode);
  return row;
}

// Filled by the edit that just happened, for the page that renders next.
export const editMemo = { warnings: [], chatDraft: "" };

// Per-device memory for folds and the like. Blocked storage only costs what was remembered.
export function recall(key) {
  try {
    return JSON.parse(localStorage.getItem(`openstride:${key}`));
  } catch {
    return null;
  }
}

export function remember(key, value) {
  try {
    localStorage.setItem(`openstride:${key}`, JSON.stringify(value));
  } catch {
    // nothing to do
  }
}

export function toast(text, ...buttons) {
  const item = el("div", text);
  item.className = "toast";
  const shelf = document.getElementById("toasts");
  if (buttons.length) {
    item.dataset.undoable = "";
    shelf.querySelector("[data-undoable]")?.remove();  // its Undo would no longer be the last edit
    item.append(actions(...buttons));
  }
  shelf.append(item);
  setTimeout(() => item.remove(), buttons.length ? 15000 : 5000);
}

export function link(text, href) {
  const a = el("a", text);
  a.href = href;
  return a;
}

export function buttonLink(text, href) {
  const a = link(text, href);
  a.className = "button";
  return a;
}

export function bulletList(items) {
  const list = el("ul");
  for (const item of items) list.append(el("li", item));
  return list;
}

const NUMERIC = /^[\d:.,/–-]+ ?(km|min|bpm|spm|m|\/km|%)?$/;

// The table scrolls inside its own box on a phone, never the page.
export function table(headers, rows) {
  const t = el("table");
  const headRow = t.createTHead().insertRow();
  for (const h of headers) headRow.append(el("th", h));
  const body = t.createTBody();
  for (const cells of rows) {
    const row = body.insertRow();
    for (const cell of cells) {
      const td = el("td");
      td.append(cell);
      const text = String(cell?.textContent ?? cell);
      if (NUMERIC.test(text)) td.className = "num";
      else if (text.length > 28) td.className = "wrap";
      row.append(td);
    }
  }
  const wrap = el("div");
  wrap.className = "table-wrap";
  wrap.tabIndex = 0;
  wrap.append(t);
  return wrap;
}

// Makes a whole table row open `href`. The link inside stays for keyboards and screen readers.
export function rowLinks(row, href) {
  row.classList.add("linked");
  row.addEventListener("click", (event) => {
    if (!event.target.closest("a, button, summary, input, select")) location.hash = href;
  });
}

export function breadcrumb(...crumbs) {
  const p = el("p");
  p.className = "crumbs";
  crumbs.forEach(([text, href], i) => {
    if (i) p.append(" › ");
    p.append(href ? link(text, href) : text);
  });
  return p;
}

const OFFLINE_HELP = "Import, sync, chat, reviews, plan changes and new plans need the server.";
let cachedView = false;  // the service worker answered from its cache
let savedAt = null;  // the oldest copy on this page

function showOffline() {
  const offline = cachedView || !navigator.onLine;
  const banner = document.getElementById("offline-banner");
  const from = savedAt ? `showing data from ${new Date(savedAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}` : "showing saved data";
  banner.hidden = !offline;
  banner.textContent = `Offline: ${from}. ${OFFLINE_HELP}`;
  document.body.classList.toggle("offline", offline);
  for (const control of document.querySelectorAll("[data-online]")) {
    control.disabled = offline;
    control.title = offline ? "Needs the server" : "";
  }
}

function noteResponse(response) {
  const copy = response.headers.get("X-OpenStride-Saved-At");
  cachedView = Boolean(copy) || response.headers.has("X-OpenStride-Offline");
  if (copy && (!savedAt || copy < savedAt)) savedAt = copy;
  showOffline();
}

new MutationObserver(showOffline).observe(main, { childList: true, subtree: true });
window.addEventListener("offline", showOffline);
window.addEventListener("online", () => {
  cachedView = false;
  showOffline();
  refresh();
});

const etags = {};  // plan id -> ETag of the last copy we read

async function send(path, options = {}) {
  const { plan, ...init } = options;
  if (plan && etags[plan]) init.headers = { ...init.headers, "If-Match": etags[plan] };
  const response = await fetch(path, init).catch(() => {
    cachedView = true;
    showOffline();
    throw new Error("Offline: this page isn't saved on this device yet.");
  });
  noteResponse(response);
  if (response.status === 401) {
    location.href = "/login";
    throw new Error("Please sign in.");
  }
  if (response.ok) return response;
  if (response.status === 501) throw new Error("Garmin not set up");
  const body = await response.json().catch(() => null);
  if (response.status === 409 && body?.error === "plan_changed") {
    // TODO(v4.1): keep what was typed (a chat message, say) across this reload.
    toast("This plan changed on another device. Reloaded.");
    refresh();
    throw new Error("This plan changed on another device. Reloaded.");
  }
  if (Array.isArray(body?.detail)) {
    throw new Error(body.detail.map((d) => `${d.loc.slice(1).join(".") || "request"}: ${d.msg}`).join("; "));
  }
  throw new Error(body?.detail ?? `Request failed (${response.status})`);
}

// `options.plan` names the plan a write applies to, so a stale copy gets refused rather than overwriting.
export async function api(path, options) {
  const response = await send(path, options);
  const planId = path.match(/^\/api\/plans\/([^/?]+)$/)?.[1];
  const etag = response.headers.get("ETag");
  if (planId && etag) etags[decodeURIComponent(planId)] = etag;
  return response.status === 204 ? null : response.json();
}

export const activePlan = () => api("/api/plans/active").catch(() => null);

// null means Garmin is off or not set up: nothing to show.
export async function garminStatus() {
  const status = await api("/api/garmin/status").catch(() => null);
  return status && (status.sync !== "off" || status.push !== "off") ? status : null;
}

async function downloadFile(path, filename) {
  const response = await send(path);
  saveBlob(await response.blob(), filename);
}

// The .fit download for a planned run. On 501 it shows "Garmin not set up" on `status`.
export function fitButton(planId, weekNumber, index, session, status, text = "Download workout .fit") {
  const path = `/api/plans/${encodeURIComponent(planId)}/sessions/${weekNumber}/${index}/workout.fit`;
  const name = `${session.date}-${session.title.replace(/[^\w]+/g, "-")}.fit`;
  return button(text, status, () => downloadFile(path, name), "Preparing…");
}

function saveBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

const jsonRequest = (method, path, body, plan) => api(path, {
  method,
  plan,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
export const postJson = (path, body, plan) => jsonRequest("POST", path, body, plan);
export const patchJson = (path, body) => jsonRequest("PATCH", path, body);
export const putJson = (path, body) => jsonRequest("PUT", path, body);

export function statusLine() {
  const p = el("p");
  p.setAttribute("role", "status");
  return p;
}

// A button that shows progress and errors on `status` and can't be double-clicked.
export function button(text, status, action, busyText = "Working…", kind = "") {
  const b = el("button", text);
  b.type = "button";
  b.className = kind;
  b.dataset.online = "";
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

let nextControlId = 0;
export function labelled(text, control) {
  nextControlId += 1;
  control.id = control.id || `control-${nextControlId}`;
  const label = el("label", text);
  label.htmlFor = control.id;
  const field = el("div");
  field.className = "field";
  field.append(label, control);
  return field;
}

export function input(type, value = "") {
  const node = el("input");
  node.type = type;
  node.value = value;
  return node;
}

export function textarea(rows) {
  const node = el("textarea");
  node.rows = rows;
  return node;
}

export function rpeSelect(value = 0) {
  const rpe = el("select");
  rpe.setAttribute("aria-label", "RPE");
  rpe.add(new Option("RPE not given", "0"));
  for (let i = 1; i <= 10; i += 1) rpe.add(new Option(String(i), String(i)));
  rpe.value = String(value);
  return rpe;
}

export function downloadButton(planFile, status) {
  const b = button("Download JSON", status, async () => {
    saveBlob(new Blob([JSON.stringify(planFile, null, 2)], { type: "application/json" }), `${planFile.meta.id}.json`);
  });
  delete b.dataset.online;  // works from the copy on screen
  return b;
}
