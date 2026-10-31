// Small DOM and API helpers shared by every view.
export const main = document.getElementById("main");

let routeToken = 0;
export const nextRoute = () => { routeToken += 1; };
// A view keeps its token; a slow fetch can then see that the user has moved on.
export const viewToken = () => routeToken;
export const stale = (token) => token !== routeToken;
export const refresh = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

export const planPath = (id) => `#/plans/${encodeURIComponent(id)}`;

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

export function link(text, href) {
  const a = el("a", text);
  a.href = href;
  return a;
}

export function bulletList(items) {
  const list = el("ul");
  for (const item of items) list.append(el("li", item));
  return list;
}

export function table(headers, rows) {
  const t = el("table");
  t.border = 1;
  const headRow = t.createTHead().insertRow();
  for (const h of headers) headRow.append(el("th", h));
  const body = t.createTBody();
  for (const cells of rows) {
    const row = body.insertRow();
    for (const cell of cells) {
      const td = el("td");
      td.append(cell);
      row.append(td);
    }
  }
  return t;
}

// Makes a whole table row open `href`. The link inside stays for keyboards and screen readers.
export function rowLinks(row, href) {
  row.addEventListener("click", (event) => {
    if (!event.target.closest("a, button, summary, input, select")) location.hash = href;
  });
}

export function breadcrumb(...crumbs) {
  const p = el("p");
  crumbs.forEach(([text, href], i) => {
    if (i) p.append(" › ");
    p.append(href ? link(text, href) : text);
  });
  return p;
}

export async function api(path, options) {
  const response = await fetch(path, options);
  if (response.ok) return response.status === 204 ? null : response.json();
  const body = await response.json().catch(() => null);
  if (Array.isArray(body?.detail)) {
    throw new Error(body.detail.map((d) => `${d.loc.slice(1).join(".") || "request"}: ${d.msg}`).join("; "));
  }
  throw new Error(body?.detail ?? `Request failed (${response.status})`);
}

const jsonRequest = (method, path, body) => api(path, {
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
export const postJson = (path, body) => jsonRequest("POST", path, body);
export const patchJson = (path, body) => jsonRequest("PATCH", path, body);

export function statusLine() {
  const p = el("p");
  p.setAttribute("role", "status");
  return p;
}

// A button that shows progress and errors on `status` and can't be double-clicked.
export function button(text, status, action, busyText = "Working…") {
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

let nextControlId = 0;
export function labelled(text, control) {
  nextControlId += 1;
  control.id = control.id || `control-${nextControlId}`;
  const label = el("label", `${text} `);
  label.htmlFor = control.id;
  return line(label, control);
}

export function input(type, value = "") {
  const node = el("input");
  node.type = type;
  node.value = value;
  return node;
}

export function textarea(rows, cols) {
  const node = el("textarea");
  node.rows = rows;
  node.cols = cols;
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

export function fullPlanVersionCheck(planFile) {
  if (!["1.0", "2.0", "3.0"].includes(planFile?.schema_version)) {
    throw new Error(`Unsupported plan file: schema_version is ${planFile?.schema_version}, expected 1.0, 2.0 or 3.0`);
  }
}

export function downloadButton(planFile, status) {
  return button("Download JSON", status, async () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(planFile, null, 2)], { type: "application/json" }));
    a.download = `${planFile.meta.id}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
}
