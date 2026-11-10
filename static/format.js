// Small formatters and the one element builder the page uses.
export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function chip(text, tone = "") {
  return el("span", text, `chip ${tone}`.trim());
}

const DAY = { weekday: "short", day: "numeric", month: "short" };

export function dateText(iso, options = DAY) {
  if (!iso) return "";
  return new Date(`${String(iso).slice(0, 10)}T12:00:00`).toLocaleDateString("en-GB", options);
}

export function timeText(iso) {
  return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function km(value) {
  return value ? `${Number(value.toFixed(1))} km` : "";
}

export function duration(seconds) {
  if (!seconds) return "";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function paceText(secondsPerKm) {
  if (!secondsPerKm) return "";
  const total = Math.round(secondsPerKm);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// What a planned session asks for, in a few words.
export function amount(session) {
  const parts = [];
  if (session.distance_km) parts.push(km(session.distance_km));
  else if (session.duration_min) parts.push(`${Number(session.duration_min)} min`);
  if (session.target_pace) parts.push(session.target_pace);
  return parts.join(" · ");
}

export function table(headers, rows, numeric = []) {
  const t = el("table");
  const head = el("tr");
  for (const header of headers) head.append(el("th", header));
  const thead = el("thead");
  thead.append(head);
  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    row.forEach((cell, index) => {
      const td = el("td");
      if (cell instanceof Node) td.append(cell);
      else td.textContent = cell ?? "";
      if (numeric.includes(index) || typeof cell === "number") td.className = "num";
      tr.append(td);
    });
    body.append(tr);
  }
  t.append(thead, body);
  const wrap = el("div", null, "table-wrap");
  wrap.tabIndex = 0;
  wrap.append(t);
  return wrap;
}
