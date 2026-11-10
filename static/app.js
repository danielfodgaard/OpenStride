// The page: one stream to read, one box to write in. It draws what the server sends and computes nothing.
import { LIVE, renderCard } from "./cards.js";
import { amount, chip, dateText, el, timeText } from "./format.js";
import { markdown } from "./md.js";

const $ = (id) => document.getElementById(id);
const events = $("events"), live = $("live"), streamBox = $("stream"), peek = $("peek"), drawer = $("drawer");
const state = { first: null, last: 0, busy: false, files: [], liveCards: new Map(), source: null, retry: 1000, typing: null };

// --- talking to the server ---------------------------------------------------------------------
async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("The server can't be reached.");
  }
  if (response.status === 401) { location.href = "/login"; throw new Error("Log in again."); }
  const offline = response.headers.get("X-OpenStride-Offline") === "1";
  setOffline(offline || Boolean(response.headers.get("X-OpenStride-Saved-At")) && !navigator.onLine);
  let body = null;
  try { body = await response.json(); } catch { /* no body */ }
  if (!response.ok) throw new Error(body?.detail || body?.error || `The server answered ${response.status}.`);
  return body;
}

const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function setOffline(offline) {
  const banner = $("offline-banner");
  banner.hidden = !offline;
  if (offline) banner.textContent = "Offline: showing what this device saved. Messages wait until the server is back.";
}

function toast(text) {
  const node = el("div", text, "toast");
  $("toasts").append(node);
  setTimeout(() => node.remove(), 5000);
}

// --- cards -------------------------------------------------------------------------------------
const ctx = {
  post,
  send: (text) => sendMessage(text),
  refresh: () => { refreshLive(); loadGlance(); },
  peek: async (type, args) => {
    peek.replaceChildren(el("p", "Loading…", "muted"));
    if (!peek.open) peek.showModal();
    try {
      const data = await api(`/api/card?type=${type}&args=${encodeURIComponent(JSON.stringify(args || {}))}`);
      const close = el("button", "Close", "quiet close");
      close.type = "button";
      close.addEventListener("click", () => peek.close());
      peek.replaceChildren(close, renderCard(type, data, ctx));
    } catch (error) {
      peek.replaceChildren(el("p", error.message, "notice"));
    }
  },
};
peek.addEventListener("click", (event) => { if (event.target === peek) peek.close(); });

async function fillCard(box, type, args, data) {
  try {
    const built = data || await api(`/api/card?type=${type}&args=${encodeURIComponent(JSON.stringify(args || {}))}`);
    box.replaceChildren(renderCard(type, built, ctx));
  } catch (error) {
    if (!box.childElementCount || box.firstElementChild.classList.contains("muted")) box.replaceChildren(el("p", error.message, "muted"));
  }
}

function cardEvent(event, type, args, data) {
  const box = el("section", null, `card card-${type}`);
  box.append(el("p", "Loading…", "muted"));
  fillCard(box, type, args, data);
  if (LIVE.has(type) && !data) state.liveCards.set(event.id, { box, type, args });
  return box;
}

function refreshLive() {
  for (const { box, type, args } of state.liveCards.values()) if (box.isConnected) fillCard(box, type, args);
}

// --- the stream --------------------------------------------------------------------------------
function renderEvent(event) {
  if (event.type === "message") {
    const row = el("div", null, `message ${event.role}`);
    if (event.role === "coach" && event.origin && !["athlete", "system"].includes(event.origin)) row.append(el("p", `${event.origin.replaceAll("_", " ")} · ${timeText(event.at)}`, "origin"));
    row.append(event.role === "coach" ? markdown(event.text) : el("p", event.text));
    for (const name of event.attachments || []) row.append(chip(name));
    return row;
  }
  if (event.type === "card") return cardEvent(event, event.card, event.args, event.data);
  if (event.type === "proposal") return cardEvent(event, "proposal", { id: event.proposal_id });
  if (event.type === "notice") return el("p", event.text, `stream-note ${event.level || ""}`);
  if (event.type === "turn" && event.state === "failed") return el("p", event.error || "That turn failed.", "stream-note warning");
  if (event.type === "turn" && event.state === "stopped") return el("p", "Stopped.", "stream-note");
  return null;
}

function nearBottom() {
  return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
}

function scrollDown(force = false) {
  if (force || nearBottom()) requestAnimationFrame(() => window.scrollTo({ top: document.documentElement.scrollHeight }));
}

function addEvent(event, { prepend = false } = {}) {
  if (event.id && !prepend) {
    if (event.id <= state.last) return;
    state.last = event.id;
  }
  if (event.id && (state.first === null || event.id < state.first)) state.first = event.id;
  const node = renderEvent(event);
  if (!node) return;
  node.dataset.id = event.id || "";
  const stick = nearBottom();
  if (prepend) events.prepend(node);
  else events.append(node);
  if (!prepend && stick) scrollDown(true);
}

async function loadHistory() {
  const data = await api("/api/stream?limit=80");
  events.replaceChildren();
  state.last = 0;
  state.first = null;
  for (const event of data.events) addEvent(event);
  $("earlier").hidden = !(state.first && state.first > 1);
  setBusy(data.busy);
  if (!data.events.length) events.append(emptyState());
  scrollDown(true);
}

function emptyState() {
  const box = el("section", null, "card welcome");
  box.append(el("h3", "Welcome to OpenStride"));
  box.append(el("p", "Everything here happens in this conversation. Tell your coach about your next race and how you train, ask about a run, or ask for a change. The only thing that waits for your approval is a change to your plan."));
  const actions = el("div", null, "actions");
  for (const text of ["I want to train for a race", "What do you know about my training so far?"]) {
    const button = el("button", text);
    button.type = "button";
    button.addEventListener("click", () => sendMessage(text));
    actions.append(button);
  }
  box.append(actions);
  return box;
}

$("earlier").addEventListener("click", async () => {
  const data = await api(`/api/stream?limit=80&before=${state.first}`);
  const height = document.documentElement.scrollHeight;
  for (const event of data.events.reverse()) addEvent(event, { prepend: true });
  $("earlier").hidden = !(state.first && state.first > 1) || !data.events.length;
  window.scrollTo({ top: document.documentElement.scrollHeight - height + window.scrollY });
});

// The reply as it is written, and what the wait is for.
function setBusy(busy) {
  state.busy = busy;
  $("stop").hidden = !busy;
  $("send").hidden = busy;
  if (busy) showTyping("Working…");
  else clearTyping();
}

function showTyping(status) {
  if (!state.typing) {
    live.hidden = false;
    const text = el("div", null, "message coach streaming");
    const line = el("p", "", "status");
    live.replaceChildren(text, line);
    state.typing = { text, line, raw: "" };
  }
  if (status) state.typing.line.textContent = status;
  scrollDown();
}

function clearTyping() {
  state.typing = null;
  live.hidden = true;
  live.replaceChildren();
}

function connect() {
  const source = new EventSource("/api/events");
  state.source = source;
  source.addEventListener("open", async () => {
    state.retry = 1000;
    setOffline(false);
    try {  // whatever was missed while the connection was down
      const data = await api(`/api/stream?after=${state.last}&limit=500`);
      events.querySelector(".welcome")?.remove();
      for (const event of data.events) addEvent(event);
      setBusy(data.busy);
    } catch { /* the next open tries again */ }
  });
  const on = (name, handler) => source.addEventListener(name, (message) => handler(JSON.parse(message.data)));
  for (const kind of ["message", "card", "proposal", "notice"]) {
    on(kind, (event) => {
      events.querySelector(".welcome")?.remove();
      if (kind === "message" && event.role === "coach" && state.typing) { state.typing.raw = ""; state.typing.text.replaceChildren(); }
      addEvent(event);
    });
  }
  on("turn", (event) => {
    if (event.state === "started") setBusy(true);
    else { addEvent(event); clearTyping(); }
  });
  on("busy", (event) => setBusy(event.busy));
  on("status", (event) => showTyping(event.text));
  on("delta", (event) => {
    showTyping();
    state.typing.raw += event.text;
    state.typing.text.textContent = state.typing.raw.trim() === "SILENT" ? "" : state.typing.raw.trimStart();
    scrollDown();
  });
  on("reset", () => { if (state.typing) { state.typing.raw = ""; state.typing.text.replaceChildren(); } });
  on("plan_changed", () => ctx.refresh());
  on("data_changed", () => { ctx.refresh(); if (!drawer.hidden) loadDrawer(); });
  source.addEventListener("error", () => {
    source.close();
    setTimeout(connect, state.retry);
    state.retry = Math.min(state.retry * 2, 30000);
  });
}

// --- the composer ------------------------------------------------------------------------------
const message = $("message");

function grow() {
  message.style.height = "auto";
  message.style.height = `${Math.min(message.scrollHeight, 200)}px`;
}

function showAttachments() {
  const box = $("attachments");
  box.hidden = !state.files.length;
  box.replaceChildren(...state.files.map((file, index) => {
    const item = chip(`${file.name} ×`);
    item.role = "button";
    item.tabIndex = 0;
    item.title = "Remove";
    item.addEventListener("click", () => { state.files.splice(index, 1); showAttachments(); });
    return item;
  }));
}

async function sendMessage(text) {
  const body = new FormData();
  body.append("text", text || "");
  for (const file of state.files) body.append("files", file);
  const sent = state.files;
  state.files = [];
  showAttachments();
  try {
    await api("/api/messages", { method: "POST", body });
    setBusy(true);
    scrollDown(true);
    return true;
  } catch (error) {
    state.files = sent;
    showAttachments();
    toast(error.message);
    return false;
  }
}

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = message.value.trim();
  if (!text && !state.files.length) return;
  message.value = "";
  grow();
  if (!(await sendMessage(text))) { message.value = text; grow(); }
});
message.addEventListener("input", grow);
message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && window.matchMedia("(pointer: fine)").matches) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
$("attach").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", (event) => {
  state.files.push(...event.target.files);
  event.target.value = "";
  showAttachments();
});
document.addEventListener("dragover", (event) => event.preventDefault());
document.addEventListener("drop", (event) => {
  event.preventDefault();
  if (event.dataTransfer?.files?.length) { state.files.push(...event.dataTransfer.files); showAttachments(); }
});
$("stop").addEventListener("click", () => post("/api/turn/stop").catch((error) => toast(error.message)));

// --- the glance header -------------------------------------------------------------------------
function tile(label, value, sub, onClick) {
  const button = el("button", null, "tile");
  button.type = "button";
  button.append(el("span", label, "k"), el("span", value, "v"));
  if (sub) button.append(el("span", sub, "s"));
  button.addEventListener("click", onClick);
  return button;
}

async function loadGlance() {
  let data;
  try { data = await api("/api/glance"); } catch { return; }
  const box = $("glance");
  const tiles = [];
  if (data.has_plan) {
    const today = data.sessions_today;
    const first = today[0];
    const label = !today.length ? "Rest day" : today.map((s) => s.title).join(" + ");
    const state_ = first ? { done: "done", skipped: "skipped", replaced: "did something else" }[first.state] || amount(first) : "";
    tiles.push(tile("Today", label, state_, () => ctx.peek("week", { week: "this" })));
    if (data.next_hard) tiles.push(tile("Next key session", data.next_hard.title, dateText(data.next_hard.date), () => ctx.peek("session", { id: data.next_hard.id })));
    if (data.week) tiles.push(tile(`Week ${data.week.week_number} of ${data.week.of}`, `${data.week.done_km} / ${data.week.planned_km} km`, `${data.week.sessions_done} of ${data.week.sessions_planned} sessions`, () => ctx.peek("volume", {})));
  }
  if (data.form) tiles.push(tile("Form", String(data.form.value), `fitness ${data.form.fitness}`, () => ctx.peek("fitness", {})));
  if (data.has_plan) tiles.push(tile("Race", data.days_to_race >= 0 ? `${data.days_to_race} days` : "Done", data.goal, () => ctx.peek("plan", {})));
  if (data.pending_proposals?.length) tiles.push(tile("Waiting for you", `${data.pending_proposals.length} proposal${data.pending_proposals.length > 1 ? "s" : ""}`, "to approve or reject", () => ctx.peek("proposal", { id: data.pending_proposals[0] })));
  box.hidden = !tiles.length;
  box.replaceChildren(...tiles);
}

// --- the data drawer: read-only, for trust -----------------------------------------------------
const TIER = { gated: ["Needs your approval", "warning"], recorded: ["Record", ""], server: ["App", ""], authored: ["Coach", "good"] };

async function openFile(path) {
  const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
  const view = el("div", null, "card-body");
  const [label, tone] = TIER[data.tier] || ["", ""];
  const title = el("div", null, "card-head");
  title.append(el("h3", data.path), chip(label, tone));
  view.append(title);
  if (data.image) {
    const image = el("img");
    image.src = `/api/raw?path=${encodeURIComponent(data.path)}`;
    image.alt = data.path;
    view.append(image);
  } else if (data.path.endsWith(".md")) {
    view.append(markdown(data.text, { headings: true, imageBase: data.path.split("/").slice(0, -1).join("/") }));
  } else {
    let text = data.text;
    if (data.path.endsWith(".json")) { try { text = JSON.stringify(JSON.parse(text), null, 1); } catch { /* as it is */ } }
    view.append(el("pre", text));
  }
  if (data.truncated) view.append(el("p", "The file is longer; this is its start.", "muted"));
  if (data.log?.length) {
    const fold = el("details");
    fold.append(el("summary", `History (${data.log.length})`));
    const list = el("ul");
    for (const entry of data.log) list.append(el("li", `${timeText(entry.at)}: ${entry.message}`));
    fold.append(list);
    view.append(fold);
  }
  const close = el("button", "Close", "quiet close");
  close.type = "button";
  close.addEventListener("click", () => peek.close());
  peek.replaceChildren(close, view);
  if (!peek.open) peek.showModal();
}

async function loadDrawer() {
  const [status, listing, log] = await Promise.all([api("/api/status"), api("/api/files"), api("/api/log?limit=15")]);
  const box = el("div", null, "drawer-body");
  box.append(el("h2", "Your data"));
  const provider = status.provider;
  const facts = el("ul", null, "facts");
  facts.append(el("li", provider.connected ? `Provider: ${provider.name}${provider.last_sync_at ? `, synced ${timeText(provider.last_sync_at)}` : ""}` : "No provider connected yet (see docs/SETUP.md)"));
  if (provider.error) facts.append(el("li", `Last sync failed: ${provider.error}`));
  facts.append(el("li", `Agent: ${status.runtime}; shell sandbox ${status.sandbox}`));
  const usage = status.usage;
  facts.append(el("li", `This month: $${usage.cost_usd.toFixed(2)} over ${usage.turns} turns${usage.monthly_cap_usd ? ` (cap $${usage.monthly_cap_usd})` : ""}`));
  box.append(facts);
  const actions = el("div", null, "actions");
  if (provider.connected) {
    const sync = el("button", "Sync now");
    sync.type = "button";
    sync.addEventListener("click", async () => {
      sync.disabled = true;
      try { const result = await post("/api/sync"); toast(`Synced: ${result.new.length} new, ${result.updated.length} updated.`); } catch (error) { toast(error.message); }
      sync.disabled = false;
      loadDrawer();
    });
    actions.append(sync);
  }
  const backup = el("a", "Download backup", "button");
  backup.href = "/api/backup";
  actions.append(backup);
  box.append(actions);

  const groups = new Map();
  for (const file of listing.files) {
    const top = file.path.includes("/") ? file.path.split("/")[0] : "(top)";
    if (!groups.has(top)) groups.set(top, []);
    groups.get(top).push(file);
  }
  for (const [name, files] of groups) {
    const fold = el("details");
    const [label, tone] = TIER[files[0].tier] || ["", ""];
    const summary = el("summary", `${name} (${files.length}) `);
    summary.append(chip(label, tone));
    fold.append(summary);
    const list = el("ul", null, "files");
    for (const file of files.slice(-200).reverse()) {
      const link = el("button", file.path.split("/").slice(name === "(top)" ? 0 : 1).join("/"), "link");
      link.type = "button";
      link.addEventListener("click", () => openFile(file.path).catch((error) => toast(error.message)));
      const item = el("li");
      item.append(link);
      list.append(item);
    }
    fold.append(list);
    box.append(fold);
  }
  const history = el("details");
  history.append(el("summary", "Latest changes"));
  const list = el("ul");
  for (const entry of log.log) list.append(el("li", `${timeText(entry.at)}: ${entry.message}`));
  history.append(list);
  box.append(history);
  box.append(el("p", `OpenStride ${status.version}. Everything here is a file on your own machine, in a git repository.`, "muted"));
  drawer.replaceChildren(box);
}

$("data-button").addEventListener("click", () => {
  drawer.hidden = !drawer.hidden;
  $("data-button").setAttribute("aria-expanded", String(!drawer.hidden));
  document.body.classList.toggle("drawer-open", !drawer.hidden);
  if (!drawer.hidden) loadDrawer().catch((error) => drawer.replaceChildren(el("p", error.message, "notice")));
});

// --- theme, service worker, start --------------------------------------------------------------
const themeButton = $("theme-toggle");
function showTheme() {
  const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  themeButton.textContent = dark ? "Light" : "Dark";
  document.querySelector('meta[name="theme-color"]').content = dark ? "#0e1418" : "#f4f6f7";
}
themeButton.addEventListener("click", () => {
  const dark = themeButton.textContent === "Dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try { localStorage.setItem("openstride:theme", dark ? "dark" : "light"); } catch { /* private mode */ }
  showTheme();
  refreshLive();  // charts take their colours when drawn
});
showTheme();

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
window.addEventListener("online", () => setOffline(false));

loadHistory().catch((error) => events.replaceChildren(el("p", error.message, "notice"))).finally(connect);
loadGlance();
