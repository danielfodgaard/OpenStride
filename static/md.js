// Light Markdown to DOM: paragraphs, headings, lists, bold, italic, code, links, images and pipe tables.
// Text is set with textContent throughout, so nothing an agent or a file says can become markup.
import { el } from "./format.js";

const INLINE = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|!\[[^\]]*\]\([^)\s]+\)|\[[^\]]+\]\([^)\s]+\))/g;

function inline(parent, text, imageBase) {
  for (const part of text.split(INLINE)) {
    if (!part) continue;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) parent.append(el("strong", part.slice(2, -2)));
    else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) parent.append(el("code", part.slice(1, -1)));
    else if (/^!\[[^\]]*\]\([^)\s]+\)$/.test(part)) {
      const [, alt, src] = part.match(/^!\[([^\]]*)\]\(([^)\s]+)\)$/);
      if (imageBase && !/^[a-z]+:|^\//i.test(src) && !src.includes("..")) {
        const image = el("img");
        image.alt = alt;
        image.loading = "lazy";
        image.src = `/api/raw?path=${encodeURIComponent(`${imageBase}/${src}`)}`;
        parent.append(image);
      } else parent.append(alt);
    } else if (/^\[[^\]]+\]\([^)\s]+\)$/.test(part)) {
      const [, label, href] = part.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
      if (/^https?:\/\//i.test(href)) {
        const link = el("a", label);
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        parent.append(link);
      } else parent.append(label);
    } else if (part.startsWith("*") && part.endsWith("*") && part.length > 2) parent.append(el("em", part.slice(1, -1)));
    else parent.append(part);
  }
}

const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());

export function markdown(text, { imageBase = "", headings = false } = {}) {
  const root = el("div", null, "md");
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    if (/^```/.test(line)) {
      const code = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      i += 1;
      root.append(el("pre", code.join("\n")));
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const node = el(headings ? `h${Math.min(heading[1].length + 1, 4)}` : "p");
      if (!headings) node.className = "md-heading";
      inline(node, heading[2], imageBase);
      root.append(node);
      i += 1;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
      const table = el("table");
      const head = el("tr");
      for (const cell of cells(line)) { const th = el("th"); inline(th, cell, imageBase); head.append(th); }
      const thead = el("thead"); thead.append(head);
      const body = el("tbody");
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        const tr = el("tr");
        for (const cell of cells(lines[i])) { const td = el("td"); inline(td, cell, imageBase); tr.append(td); }
        body.append(tr);
        i += 1;
      }
      table.append(thead, body);
      const wrap = el("div", null, "table-wrap");
      wrap.append(table);
      root.append(wrap);
      continue;
    }
    const bullet = /^\s*([-*•]|\d+[.)])\s+/;
    if (bullet.test(line)) {
      const list = el(/^\s*\d/.test(line) ? "ol" : "ul");
      while (i < lines.length && bullet.test(lines[i])) {
        const item = el("li");
        inline(item, lines[i].replace(bullet, ""), imageBase);
        list.append(item);
        i += 1;
      }
      root.append(list);
      continue;
    }
    const paragraph = [];
    while (i < lines.length && lines[i].trim() && !bullet.test(lines[i]) && !/^(#{1,4})\s|^```/.test(lines[i])) paragraph.push(lines[i++]);
    const p = el("p");
    inline(p, paragraph.join(" "), imageBase);
    root.append(p);
  }
  return root;
}
