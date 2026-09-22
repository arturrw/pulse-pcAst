"""The single page of the web app: markup, style and script in one string (no external files, no external addresses).

Safety rules for this page: everything that comes from the machine (process names, file paths, model answers) is put on
the screen with textContent / createTextNode, never as HTML, and the server's content policy blocks every script that
does not carry the per-response nonce. The text is English only."""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pulse</title>
<style nonce="{{NONCE}}">
:root{--bg:#f4f4f1;--card:#fff;--ink:#1c1c1a;--mute:#6b6b66;--line:#e2e2dc;--acc:#2f6fdb;--acc-ink:#fff;--acc-tint:#e4ecfa;--ok:#2a7d46;--warn:#b25b00;--bad:#c23b32;--chip:#eceae4}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecece8;--mute:#9a9a94;--line:#33332f;--acc:#6ea0ff;--acc-ink:#0d0d0c;--acc-tint:#243350;--ok:#5fcf8b;--warn:#ffb15c;--bad:#ff8a80;--chip:#2a2a27}}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,"Segoe UI",sans-serif;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:12px;padding:10px 20px;border-bottom:1px solid var(--line);background:var(--card)}
header h1{font-size:17px;margin:0;flex:1}header .dot{width:10px;height:10px;border-radius:50%;background:var(--mute)}
button,select,input,textarea{font:inherit;color:inherit}
select{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:5px 8px}
button{background:var(--chip);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer;transition:background-color .15s,border-color .15s,transform .12s,box-shadow .15s,opacity .15s}
button:hover:not(:disabled){border-color:var(--acc);transform:translateY(-1px);box-shadow:0 3px 10px rgba(0,0,0,.16)}
button:active:not(:disabled){transform:translateY(0) scale(.96);box-shadow:none;transition-duration:.05s}
button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible,th:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
button.primary{background:var(--acc);color:var(--acc-ink);border-color:var(--acc)}button:disabled{opacity:.5;cursor:default}
.shell{flex:1;display:flex;overflow:hidden;position:relative}
.sidebar{width:76px;flex-shrink:0;overflow:hidden;background:var(--card);border-right:1px solid var(--line);display:flex;flex-direction:column;gap:2px;padding:10px 6px;position:relative;transition:width .28s cubic-bezier(.4,0,.2,1),padding .28s cubic-bezier(.4,0,.2,1)}
.shell.collapsed .sidebar{width:0;padding-left:0;padding-right:0;border-right:0}
.sidebar .ink{position:absolute;left:6px;right:6px;border-radius:8px;background:var(--chip);pointer-events:none;transition:top .28s cubic-bezier(.4,0,.2,1),height .28s cubic-bezier(.4,0,.2,1)}
.sidebtn{position:relative;z-index:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;width:100%;padding:11px 2px;border:0;border-radius:8px;background:none;color:var(--ink);flex-shrink:0}
.sidebtn:hover:not(:disabled){background:var(--chip);transform:none;box-shadow:none}
.sidebtn.on{color:var(--acc)}
.edge{position:absolute;top:0;bottom:0;left:76px;width:16px;display:flex;align-items:center;justify-content:center;z-index:5;transition:left .28s cubic-bezier(.4,0,.2,1)}
.shell.collapsed .edge{left:0}
.side-toggle{opacity:0;width:20px;height:38px;padding:0;border-radius:0 8px 8px 0;border:1px solid var(--line);border-left:0;background:var(--card);display:flex;align-items:center;justify-content:center;cursor:pointer;transition:opacity .18s ease}
.side-toggle svg{transition:transform .28s cubic-bezier(.4,0,.2,1)}
.shell.collapsed .side-toggle svg{transform:rotate(180deg)}
.edge:hover .side-toggle,.side-toggle:focus-visible{opacity:1}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
main{flex:1;overflow:auto;padding:18px 20px 40px}
main>*{max-width:1100px;width:100%;margin:0 auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.card h3{margin:0 0 4px;font-size:13px;color:var(--mute);font-weight:500}.card .big{font-size:20px;font-weight:600;line-height:1.25}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mute{color:var(--mute)}
.badge{display:inline-block;font-size:12px;border-radius:999px;padding:1px 9px;border:1px solid currentColor}
.tag{display:inline-block;font-size:12px;border-radius:999px;padding:2px 9px;background:var(--chip);margin:3px 6px 0 0}
.tag.acc{background:var(--acc-tint);color:var(--acc)}
section{margin-top:22px}section>h2{font-size:15px;margin:0 0 8px}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:8px;display:flex;gap:12px;align-items:flex-start}
.item .body{flex:1;min-width:0}.item .title{font-weight:600}.item .detail{color:var(--mute);font-size:13px;overflow-wrap:anywhere}
iframe{display:block;width:100%;height:600px;border:0;background:transparent;opacity:0;transition:opacity .35s ease}iframe.ready{opacity:1}
.askwrap{display:grid;grid-template-columns:230px 1fr;gap:16px;align-items:start}.convo{min-width:0}
.chats{display:flex;flex-direction:column;gap:6px;position:sticky;top:0;min-width:0}.chatrow{display:flex;align-items:center;gap:6px;padding:7px 10px;border:1px solid var(--line);border-radius:10px;background:var(--card);cursor:pointer}
.chatrow:hover{border-color:var(--acc)}.chatrow.on{border-color:var(--acc);background:var(--chip)}.chatrow .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chatrow .x{border:0;background:none;padding:0 6px;color:var(--mute)}.chatrow .x:hover{color:var(--bad)}
.vrow{display:grid;grid-template-columns:minmax(120px,1.2fr) 1fr 1fr minmax(130px,1.5fr);gap:2px 14px;padding:9px 0;border-bottom:1px solid var(--line);align-items:start}
.vrow .lbl{display:none}.vrow.head{color:var(--mute);font-weight:500;font-size:13px;padding:6px 0}.vrow .txt{grid-column:1/-1}
.progs{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.progs button{font-size:13px}.progs button.on{border-color:var(--acc)}.addgame .row{margin:8px 0}
@media (max-width:640px){.vrow{grid-template-columns:1fr 1fr}.vrow.head{display:none}.vrow .nm{order:1}.vrow .res{order:2;justify-self:end}.vrow .av{order:3}.vrow .lo{order:4}.vrow .txt{order:5}.vrow .lbl{display:block}}
.gamecard{cursor:pointer}.gamecard:hover{border-color:var(--acc)}.back{margin-bottom:6px}
@media (max-width:700px){.sidebar{position:absolute;top:0;bottom:0;z-index:6;box-shadow:2px 0 16px rgba(0,0,0,.25)}}
@media (max-width:760px){.askwrap{grid-template-columns:1fr}.chats{position:static}}
.chat{display:flex;flex-direction:column;gap:10px;min-height:280px}.msg{max-width:85%;padding:9px 13px;border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.me{align-self:flex-end;background:var(--acc);color:var(--acc-ink)}.msg.bot{align-self:flex-start;background:var(--card);border:1px solid var(--line)}
.tools{font-size:12px;color:var(--mute);margin-top:4px}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
textarea,input[type=text]{flex:1;min-width:180px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 10px 6px 0;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}th{color:var(--mute);font-weight:500}
code{background:var(--chip);padding:1px 6px;border-radius:5px;overflow-wrap:anywhere}.chips button{font-size:13px}.err{color:var(--bad);margin:8px 0}
.card.link,.gamecard{cursor:pointer;transition:transform .16s ease,border-color .16s,box-shadow .16s}.card.link:hover,.gamecard:hover{border-color:var(--acc);transform:translateY(-2px);box-shadow:0 6px 16px rgba(0,0,0,.18)}.card.link:active,.gamecard:active{transform:scale(.985)}.small{font-size:12px}
.item.col{flex-direction:column;gap:0;padding:0}.item.col.done{opacity:.65}
.head{display:flex;gap:12px;align-items:flex-start;padding:10px 14px;cursor:pointer;width:100%}.head.static{cursor:default}.head:hover{background:var(--chip);border-radius:10px}.head.static:hover{background:none}
.head .body{flex:1;min-width:0}
.more{display:none;padding:2px 14px 12px}.more.open{display:block;animation:rise .22s ease both}.explain p{margin:6px 0}.tech{margin:8px 0;font-family:ui-monospace,Consolas,monospace}
.grp{font-size:14px;margin:18px 0 8px;color:var(--mute)}
.fields{padding:0 14px 12px;display:flex;flex-direction:column;gap:10px}.field{display:flex;flex-direction:column;gap:3px}.field>label{font-weight:600;font-size:13px}.field select,.field input[type=time]{align-self:flex-start;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:5px 8px}
.row.tight{margin:0}
.upd{display:none;align-items:center;gap:12px;padding:8px 20px;background:var(--chip);border-bottom:1px solid var(--line)}.upd.show{display:flex;animation:drop .28s ease both}.upd .grow{flex:1}
.verdict{font-size:22px;font-weight:700;margin:4px 0}.verdict.ok{color:var(--ok)}.verdict.warn{color:var(--warn)}.verdict.bad{color:var(--bad)}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes drop{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
#view>*{animation:rise .26s ease both}#view>*:nth-child(2){animation-delay:.04s}#view>*:nth-child(3){animation-delay:.08s}#view>*:nth-child(n+4){animation-delay:.12s}
.msg{animation:rise .2s ease both}.chatrow,.item,.head{transition:background-color .15s,border-color .15s}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.001ms!important;animation-delay:0s!important;transition-duration:.001ms!important}}
</style></head><body>
<header><span class="dot" id="dot"></span><h1>Pulse</h1></header>
<div class="upd" id="upd"></div>
<div class="shell" id="shell">
<aside class="sidebar" id="sidebar"></aside>
<div class="edge"><button class="side-toggle" id="sideToggle" type="button" aria-label="Toggle menu" title="Toggle menu"></button></div>
<main id="view"></main>
</div>
<script nonce="{{NONCE}}">
"use strict";
const $ = (id) => document.getElementById(id);
function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const k in props || {}) {
    if (k === "class") e.className = props[k];
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), props[k]);
    else if (k === "style") e.style.cssText = props[k];          // through the style object: an inline style attribute is blocked by the page policy
    else if (k === "checked" || k === "value" || k === "disabled") e[k] = props[k];
    else e.setAttribute(k, props[k]);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
async function api(path, body) {
  const opt = { credentials: "same-origin" }; if (body !== undefined) { opt.method = "POST"; opt.headers = { "Content-Type": "application/json" }; opt.body = JSON.stringify(body); }
  const r = await fetch("/api/" + path, opt); let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status)); return j;
}
const view = () => $("view");
const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
const TABS = [["overview", "Overview"], ["ask", "Ask"], ["findings", "Findings"], ["timeline", "What happened"], ["games", "Games"], ["setup", "Setup"]];
let tab = TABS.some((x) => x[0] === location.hash.slice(1)) ? location.hash.slice(1) : "overview";
let pendingAsk = null; let gen = 0;
const err = (e) => h("p", { class: "err" }, String(e.message || e));
const SEV = { high: ["Serious", "bad"], medium: ["Worth a look", "warn"], low: ["Minor", "mute"] };
const sev = (s) => h("span", { class: "badge " + (SEV[s] || SEV.low)[1] }, (SEV[s] || SEV.low)[0]);

// Small line icons for the sidebar, built straight from SVG primitives (no images, no external files).
const SVGNS = "http://www.w3.org/2000/svg";
const ICONS = {
  overview: [["rect", { x: 3, y: 3, width: 8, height: 8, rx: 2 }], ["rect", { x: 13, y: 3, width: 8, height: 5, rx: 2 }], ["rect", { x: 13, y: 10, width: 8, height: 11, rx: 2 }], ["rect", { x: 3, y: 13, width: 8, height: 8, rx: 2 }]],
  ask: [["rect", { x: 3, y: 5, width: 18, height: 12, rx: 3 }], ["path", { d: "M8 17l-2 3v-3" }], ["line", { x1: 7, y1: 9, x2: 17, y2: 9 }], ["line", { x1: 7, y1: 13, x2: 13, y2: 13 }]],
  findings: [["path", { d: "M12 3l7 3v5c0 5-3.2 8-7 10-3.8-2-7-5-7-10V6l7-3Z" }], ["path", { d: "M9 12l2 2 4-4" }]],
  timeline: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M12 7v5l3.5 2" }]],
  games: [["rect", { x: 2, y: 7, width: 20, height: 10, rx: 5 }], ["line", { x1: 7, y1: 10, x2: 7, y2: 14 }], ["line", { x1: 5, y1: 12, x2: 9, y2: 12 }], ["circle", { cx: 15, cy: 10.5, r: 1.1 }], ["circle", { cx: 17.5, cy: 13, r: 1.1 }]],
  setup: [["line", { x1: 4, y1: 6, x2: 20, y2: 6 }], ["circle", { cx: 9, cy: 6, r: 2 }], ["line", { x1: 4, y1: 12, x2: 20, y2: 12 }], ["circle", { cx: 15, cy: 12, r: 2 }], ["line", { x1: 4, y1: 18, x2: 20, y2: 18 }], ["circle", { cx: 9, cy: 18, r: 2 }]],
  refresh: [["path", { d: "M20 11A8 8 0 1 0 19 15" }], ["path", { d: "M20 5v6h-6" }]],
  chevron: [["path", { d: "M14 6l-6 6 6 6" }]],
};
function icon(name, size) {
  const s = document.createElementNS(SVGNS, "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("width", size || 20); s.setAttribute("height", size || 20);
  s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor"); s.setAttribute("stroke-width", "1.8");
  s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.setAttribute("aria-hidden", "true");
  for (const [tag, attrs] of ICONS[name] || []) { const el = document.createElementNS(SVGNS, tag); for (const k in attrs) el.setAttribute(k, attrs[k]); s.append(el); }
  return s;
}
function iconBtn(name, label, onclick) { return h("button", { class: "row tight", style: "gap:6px", onclick, title: label }, icon(name, 16), label); }

function go(name) { tab = name; history.replaceState(null, "", "#" + name); show(); }
function moveSideInk(animate) {
  const bar = $("sidebar"), on = bar.querySelector("button.on"), ink = bar.querySelector(".ink"); if (!on || !ink) return;
  if (!animate) ink.style.transition = "none";
  ink.style.top = on.offsetTop + "px"; ink.style.height = on.offsetHeight + "px";
  if (!animate) { ink.getBoundingClientRect(); ink.style.transition = ""; }
}
function drawSidebar() {
  const bar = $("sidebar"); const first = !bar.querySelector("button");
  if (first) bar.append(...TABS.map(([k, label]) => {
    const b = h("button", { class: "sidebtn", onclick: () => go(k), title: label, "aria-label": label }, icon(k), h("span", { class: "sr-only" }, label));
    b.dataset.tab = k; return b;
  }), h("span", { class: "ink" }));
  bar.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  moveSideInk(!first);
}
window.addEventListener("resize", () => moveSideInk(false));
const shellEl = $("shell");
let sidebarCollapsed = false; try { sidebarCollapsed = localStorage.getItem("pulse.sidebar") === "1"; } catch (e) {}
if (sidebarCollapsed) shellEl.classList.add("collapsed");
$("sideToggle").append(icon("chevron", 16));
$("sideToggle").addEventListener("click", () => {
  const collapsed = shellEl.classList.toggle("collapsed");
  try { localStorage.setItem("pulse.sidebar", collapsed ? "1" : "0"); } catch (e) {}
  setTimeout(() => moveSideInk(false), 300);
});
function show() {
  drawSidebar(); const mine = ++gen; fill(view(), h("p", { class: "mute" }, "Loading…"));
  const out = (...k) => { if (mine === gen) fill(view(), ...k); };
  ({ overview, ask, findings, timeline, games, setup })[tab](out).catch((e) => out(err(e)));
}
function askAbout(text) { pendingAsk = text; go("ask"); }
const toggle = (e) => e.currentTarget.nextSibling.classList.toggle("open");

// A block that explains itself in plain words: what it is, why it is here, what to do.
function explainBox(x) {
  return h("div", { class: "explain" },
    h("p", {}, h("b", {}, "What is it? "), x.what), x.why ? h("p", {}, h("b", {}, "Why is it here? "), x.why) : null,
    x.todo ? h("p", {}, h("b", {}, "What should I do? "), x.todo) : null,
    x.ask ? h("div", { class: "row" }, h("button", { onclick: () => askAbout(x.ask) }, "Ask the assistant about this")) : null);
}

// The report is a separate page shown inside this one; it is as tall as its content, so the page has the only scrollbar.
const PERIODS = [[6, "Last 6 hours"], [24, "Last 24 hours"], [72, "Last 3 days"], [168, "Last 7 days"]];
let period = 24; try { const p = +localStorage.getItem("pulse.period"); if (PERIODS.some((x) => x[0] === p)) period = p; } catch (e) {}

// Tables of the report: click a heading to sort by that column (again to reverse). Dates, times, numbers and sizes sort as such.
const UNITS = { B: 1, KB: 1e3, MB: 1e6, GB: 1e9, TB: 1e12 };
function sortValue(text) {
  const t = text.trim(); let m;
  if ((m = t.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/))) return { n: Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)), t };
  if ((m = t.match(/^(\d{1,2}):(\d{2})$/))) return { n: +m[1] * 60 + +m[2], t };
  if ((m = t.match(/^(-?\d+(?:[.,]\d+)?)\s*(TB|GB|MB|KB|B)?(?![A-Za-z])/i))) { let n = parseFloat(m[1].replace(",", ".")); if (m[2]) n *= UNITS[m[2].toUpperCase()]; return { n, t }; }
  return { n: null, t };
}
function makeSortable(doc) {
  doc.querySelectorAll("table").forEach((table) => {
    const head = table.rows[0]; if (!head || table.rows.length < 3 || ![...head.cells].every((c) => c.tagName === "TH")) return;
    [...head.cells].forEach((th, col) => {
      if (!th.textContent.trim()) return;
      th.classList.add("sortable"); th.tabIndex = 0; th.setAttribute("role", "button");
      const sort = () => {
        const body = [...table.rows].slice(1); const cur = th.getAttribute("aria-sort");
        const keyed = body.map((tr, i) => ({ tr, i, v: sortValue(tr.cells[col] ? tr.cells[col].textContent : "") }));
        const numeric = keyed.every((k) => k.v.n !== null || k.v.t === "");
        const dir = cur === "ascending" ? "descending" : cur === "descending" ? "ascending" : (numeric ? "descending" : "ascending");
        [...head.cells].forEach((c) => c.removeAttribute("aria-sort")); th.setAttribute("aria-sort", dir);
        const cmp = (x, y) => numeric ? (x.v.n ?? -Infinity) - (y.v.n ?? -Infinity) : x.v.t.localeCompare(y.v.t, undefined, { numeric: true, sensitivity: "base" });
        keyed.sort((x, y) => (dir === "ascending" ? 1 : -1) * cmp(x, y) || x.i - y.i);
        keyed.forEach((k) => k.tr.parentNode.appendChild(k.tr));
      };
      th.addEventListener("click", sort); th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
    });
  });
}

// Moments where the PC behaved unlike its own recent past. If the user was not doing anything then, the assistant looks into it.
function unusualPart() {
  const host = h("div", {});
  const item = (p) => h("div", { class: "item" }, h("span", { class: "badge warn" }, "Unusual"),
    h("div", { class: "body" }, h("div", { class: "title" }, p.title),
      h("div", { class: "mute small" }, p.started + " \u00b7 " + plural(p.minutes, "minute", "minutes")),
      h("div", { class: "detail" }, p.details.join(" \u00b7 ")),
      p.during_game ? h("span", { class: "tag acc" }, "while " + p.during_game + " was running") : null),
    h("button", { onclick: () => askAbout(p.question) }, "What could it be?"));
  const load = async () => {
    try {
      const d = await api("anomalies?hours=" + period);
      fill(host, d.periods.length ? h("section", {}, h("h2", {}, "Unusual moments"),
        h("p", { class: "mute small" }, "Stretches where the PC did not behave like it did in the hours before, shaded on the charts below. Not a diagnosis: if you were not doing anything then, ask the assistant to look into it."),
        d.periods.slice(0, 6).map(item)) : null);
    } catch (e) { fill(host); }
  };
  return { host, load };
}

function reportPart() {
  const frame = h("iframe", { sandbox: "allow-same-origin", title: "report" });
  const note = h("p", { class: "mute" }, "Loading the report\u2026"); const un = unusualPart();
  const fit = () => { try { const b = frame.contentDocument && frame.contentDocument.body; if (b) frame.style.height = Math.ceil(b.getBoundingClientRect().height) + 4 + "px"; } catch (e) {} };
  frame.addEventListener("load", () => { note.hidden = true; try { makeSortable(frame.contentDocument); } catch (e) {} fit(); setTimeout(fit, 400); frame.classList.add("ready"); });
  new ResizeObserver(fit).observe(frame);
  const load = () => { note.hidden = false; frame.classList.remove("ready"); frame.src = "/report?hours=" + period + "&embed=1"; un.load(); };
  const sel = h("select", { onchange: () => { period = +sel.value; try { localStorage.setItem("pulse.period", String(period)); } catch (e) {} load(); } }, PERIODS.map(([v, label]) => h("option", { value: String(v) }, label)));
  sel.value = String(period); load();
  return h("div", {}, un.host, h("section", {}, h("div", { class: "row" }, h("h2", { style: "margin:0;flex:1" }, "Report"), sel), note, frame));
}

async function overview(out) {
  const s = await api("status"); const c = s.collector, w = s.windows, a = s.startup, p = s.processes;
  const card = (title, cls, big, small, to) => h("div", { class: "card link", onclick: () => go(to) }, h("h3", {}, title), h("div", { class: "big " + cls }, big), h("div", { class: "mute" }, small));
  const names = { collect: "Recorder", alerts: "Alerts", digest: "Summary" };
  const jobs = Object.entries(s.jobs).map(([k, v]) => h("span", { class: "badge " + (v ? "ok" : "mute"), style: "margin-right:6px" }, names[k] + ": " + (v ? "on" : "off")));
  $("dot").style.background = c.recording ? "var(--ok)" : "var(--bad)";
  out(h("div", { class: "grid" },
      card("Recording", c.recording ? "ok" : "bad", c.recording ? "Recording" : "Not recording", (c.seconds_since_last_sample == null ? "" : "last sample " + Math.round(c.seconds_since_last_sample) + " s ago · ") + c.hours_recorded_24h.toFixed(0) + " of the last 24 h recorded", "setup"),
      w.available ? card("Windows & antivirus", w.open ? (w.high ? "bad" : "warn") : "ok", w.open ? w.open + " to look at" : "All clear", (w.realtime_protection ? "antivirus is on" : "antivirus is OFF") + (w.accepted ? " · " + w.accepted + " accepted by you" : ""), "findings") : null,
      a.available ? card("Programs that start by themselves", a.new ? (a.high ? "bad" : "warn") : "ok", a.new ? a.new + " new" : "Nothing new", "since " + a.baseline, "findings") : null,
      p.available ? card("Running programs", p.flagged ? "bad" : "ok", p.flagged ? p.flagged + " look unusual" : "Nothing unusual", "", "findings") : null,
      s.disk ? card("Disk space", s.disk.free_gb < 30 ? "bad" : "ok", Math.round(s.disk.free_gb) + " GB free", "on " + s.disk.name, "setup") : null,
      h("div", { class: "card link", onclick: () => go("setup") }, h("h3", {}, "Background helpers"), h("div", {}, jobs))),
    reportPart());
}

let chatId = null;   // the open conversation (null = a new one, created when the first question is sent)
async function ask(out) {
  const st = (await api("setup")).ollama;
  const log = h("div", { class: "chat" }); const input = h("textarea", { rows: "2", placeholder: "Ask something…" }); const btn = h("button", { class: "primary" }, "Send");
  const side = h("div", { class: "chats" });
  const add = (who, text, used) => { const m = h("div", { class: "msg " + who }, text); log.append(used && used.length ? h("div", {}, m, h("div", { class: "tools" }, "checked: " + used.join(", "))) : m); return m; };
  const drawList = async () => {
    const d = await api("chats");
    fill(side, h("button", { class: "primary", onclick: () => open(null) }, "+ New chat"), d.chats.map((c) => {
      const x = h("button", { class: "x", title: "Delete this chat" }, "×");
      x.addEventListener("click", async (e) => { e.stopPropagation();
        if (!x.dataset.sure) { x.dataset.sure = "1"; x.textContent = "Delete?"; return; }
        try { await api("chat_delete", { id: c.id }); } catch (er) {}
        if (c.id === chatId) await open(null); else await drawList(); });
      return h("div", { class: "chatrow" + (c.id === chatId ? " on" : ""), title: c.title, onclick: () => { if (c.id !== chatId) open(c.id); } }, h("span", { class: "t" }, c.title), x);
    }));
  };
  async function open(id) {
    chatId = id; fill(log);
    if (id) { try { const c = await api("chat?id=" + encodeURIComponent(id)); c.log.forEach((m) => add(m.who, m.text, m.used)); } catch (e) { chatId = null; } }
    else await api("reset", {});
    await drawList(); input.focus();
  }
  async function send(text) {
    text = (text || input.value).trim(); if (!text) return; input.value = ""; add("me", text);
    btn.disabled = true; const wait = add("bot", "Thinking…");
    try { const r = await api("ask", { message: text, chat: chatId }); wait.remove(); add("bot", r.answer, r.tools); chatId = r.chat; await drawList(); }
    catch (e) { wait.remove(); log.append(err(e)); }
    btn.disabled = false; input.focus();
  }
  btn.addEventListener("click", () => send()); input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  out(h("p", { class: "mute" }, "Ask about this PC in your own words: load, disks, temperature, programs, antivirus, game FPS. The assistant only reads, it changes nothing."),
    st.running && st.model_ready ? null : h("p", { class: "err" }, st.hint),
    h("div", { class: "askwrap" }, side, h("div", { class: "convo" },
      h("div", { class: "row chips" }, ["How much free space do I have?", "Was anything unusual in the last day?", "Is my antivirus on?", "What starts by itself?"].map((x) => h("button", { onclick: () => send(x) }, x))), log,
      h("div", { class: "row" }, input, btn))));
  if (pendingAsk) { const q = pendingAsk; pendingAsk = null; chatId = null; await api("reset", {}); await drawList(); send(q); }
  else if (chatId) await open(chatId); else await drawList();
}

// ---- findings
const RANK = { high: 0, medium: 1, low: 2 };
let sortBy = "severity", showAccepted = true;
async function findings(out) {
  const d = await api("findings"); const list = d.findings;
  const box = h("div", {}); const summary = h("span", { class: "mute" });
  const draw = () => {
    const shown = list.filter((f) => showAccepted || !f.accepted);
    const order = (a, b) => (a.accepted - b.accepted) || ((RANK[a.severity] ?? 3) - (RANK[b.severity] ?? 3)) || a.title.localeCompare(b.title);
    shown.sort(sortBy === "severity" ? order : (a, b) => a.source.localeCompare(b.source) || order(a, b));
    const rows = []; let last = null;
    for (const f of shown) {
      if (sortBy === "source" && f.source !== last) { rows.push(h("h2", { class: "grp" }, f.source)); last = f.source; }
      rows.push(findingCard(f, draw));
    }
    fill(box, shown.length ? rows : h("p", { class: "mute" }, "Nothing here. All clear."));
    const counts = ["high", "medium", "low"].map((k) => [k, list.filter((f) => !f.accepted && f.severity === k).length]).filter((x) => x[1]);
    summary.textContent = counts.length ? counts.map(([k, n]) => n + " " + SEV[k][0].toLowerCase()).join(" · ") : "No open findings";
  };
  const sortSel = h("select", { onchange: () => { sortBy = sortSel.value; draw(); } }, h("option", { value: "severity" }, "Most serious first"), h("option", { value: "source" }, "By source"));
  sortSel.value = sortBy;
  const acc = h("input", { type: "checkbox", checked: showAccepted, onchange: () => { showAccepted = acc.checked; draw(); } });
  out(h("p", { class: "mute" }, "Things the checks noticed. Click one to see what it means in plain words. Accept what you know is fine, so it stops being flagged."),
      h("div", { class: "row" }, h("span", { class: "mute" }, "Sort:"), sortSel, h("label", { class: "mute" }, acc, " show accepted"), summary), box);
  draw();
}
function findingCard(f, redraw) {
  const detail = h("div", { class: "more" });
  const head = h("div", { class: "head", onclick: () => detail.classList.toggle("open") },
    sev(f.severity), h("div", { class: "body" }, h("div", { class: "title" }, f.title), h("div", { class: "detail" }, f.accepted ? "Accepted by you" + (f.accepted_note ? ": " + f.accepted_note : "") : (f.source + (f.detail ? " · " + f.detail : "")))), h("span", { class: "mute" }, "▾"));
  const actions = h("div", { class: "row" });
  const forget = () => fill(actions, h("button", { onclick: async (e) => { e.target.disabled = true; try { await api("forget", { id: f.id }); f.accepted = false; f.accepted_note = ""; redraw(); } catch (x) { fill(actions, err(x)); } } }, "Stop accepting"));
  const start = () => fill(actions, h("button", { onclick: () => {
      const note = h("input", { type: "text", placeholder: "Why is this fine? (optional)" });
      const ok = h("button", { class: "primary", onclick: async () => { ok.disabled = true; try { await api("ack", { id: f.id, note: note.value }); f.accepted = true; f.accepted_note = note.value; redraw(); } catch (x) { fill(actions, err(x), h("button", { onclick: start }, "Try again")); } } }, "Accept");
      fill(actions, note, ok, h("button", { onclick: start }, "Cancel")); note.focus(); } }, "Accept"));
  if (f.id == null) fill(actions, h("span", { class: "mute" }, "Information only: it goes away by itself when the program closes."));
  else if (f.accepted) forget(); else start();
  fill(detail, explainBox(f.explain), h("div", { class: "detail tech" }, f.detail || ""), actions);
  return h("div", { class: "item col" + (f.accepted ? " done" : "") }, head, detail);
}

// ---- what happened
const KIND = { metric: "Load", process: "New program", network: "New connection", autostart: "Starts by itself", gap: "No data", alert: "Notification", game: "Game", windows: "Windows", defender: "Antivirus" };
const METRIC_LABEL = { cpu_percent: "Processor", ram_percent: "Memory", gpu_temp_c: "Graphics card temperature", gpu_util_percent: "Graphics card load" };
async function timeline(out) {
  const when = h("input", { type: "text", value: "now", placeholder: "now" });
  const mins = h("select", {}, [10, 30, 60, 180].map((m) => h("option", { value: String(m) }, "± " + (m >= 60 ? m / 60 + " h" : m + " min"))));
  mins.value = "30"; const res = h("div", {});
  const run = async () => {
    fill(res, h("p", { class: "mute" }, "Looking…"));
    try {
      const r = await api("timeline?when=" + encodeURIComponent(when.value) + "&minutes=" + encodeURIComponent(mins.value));
      const rows = Object.entries(r.metrics).map(([k, m]) => { const info = r.metric_info[k];
        return h("div", { class: "item col" }, h("div", { class: "head", onclick: toggle }, h("div", { class: "body" }, h("div", { class: "title" }, METRIC_LABEL[k] || k), h("div", { class: "detail" }, "average " + m.window_avg.toFixed(0) + ", highest " + m.window_max.toFixed(0) + (m.before_avg_3h == null ? "" : " · normally about " + m.before_avg_3h.toFixed(0)))), h("span", { class: "mute" }, "▾")),
          h("div", { class: "more" }, h("p", {}, info.what), info.todo ? h("p", {}, info.todo) : null)); });
      const procs = r.heaviest_processes.map((p) => h("div", { class: "item col" }, h("div", { class: "head", onclick: toggle }, h("div", { class: "body" }, h("div", { class: "title" }, p.name), h("div", { class: "detail" }, "used about " + p.avg_cpu_percent.toFixed(0) + "% of the processor")), h("span", { class: "mute" }, "▾")), h("div", { class: "more" }, explainBox(p.explain))));
      const evs = r.timeline.map((e) => h("div", { class: "item col" }, h("div", { class: "head", onclick: toggle }, h("code", {}, e.time), h("span", { class: "badge mute" }, KIND[e.type] || e.type), h("div", { class: "body" }, e.text), h("span", { class: "mute" }, "▾")), h("div", { class: "more" }, explainBox(e.explain))));
      fill(res, h("p", { class: "mute" }, r.window.from + " → " + r.window.to),
        rows.length ? h("section", {}, h("h2", {}, "How busy the PC was"), rows) : null,
        procs.length ? h("section", {}, h("h2", {}, "Busiest programs"), procs) : null,
        h("section", {}, h("h2", {}, "What changed"), evs.length ? evs : h("p", { class: "mute" }, "Nothing notable changed in this window.")));
    } catch (e) { fill(res, err(e)); }
  };
  const preset = (label, text) => h("button", { onclick: () => { when.value = text; run(); } }, label);
  when.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  out(h("p", { class: "mute" }, "Did the PC lag, freeze or restart? Pick the moment and see what it was doing then: how busy it was, which programs were working, and what changed. Click any line to see what it means."),
    h("div", { class: "row chips" }, preset("Right now", "now"), preset("An hour ago", "60 minutes ago"), preset("Yesterday evening", "yesterday 21:00"), preset("Last night", "yesterday 23:00")),
    h("div", { class: "row" }, h("span", { class: "mute" }, "Moment:"), when, mins, h("button", { class: "primary", onclick: run }, "Show")),
    h("p", { class: "mute small" }, "You can type a time like 14:03, yesterday 21:30, 2026-09-20 14:03 or 45 minutes ago."), res);
  run();
}

// ---- games: a library of games; opening one shows only its own recordings
let gameOpen = null, gamesNote = "";
async function games(out) {
  const d = await api("games"); const recs = d.recordings.filter((r) => !r.error);
  const game = d.games.find((g) => g.id === gameOpen);
  if (!game) { gameOpen = null; return library(out, d); }
  gamePage(out, game, recs.filter((r) => r.game.toLowerCase() === game.id.toLowerCase()), d);
}
function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
// Pick or type a program name; the game joins the library at once and can be recorded right away.
function addGamePanel(startOpen, done) {
  const el = h("div", { class: "card addgame more" + (startOpen ? " open" : "") });
  const input = h("input", { type: "text", placeholder: "Program name, e.g. Hades2.exe" });
  const msg = h("div", {}); const progs = h("div", { class: "progs" }); let loaded = false;
  let all = [];
  const draw = () => {
    const q = input.value.trim().toLowerCase(); const shown = all.filter((x) => !q || x.process.toLowerCase().includes(q)).slice(0, 24);
    fill(progs, shown.length ? shown.map((x) => h("button", { class: x.process.toLowerCase() === q ? "on" : "", title: x.game_like ? "Looks like an installed game" : "", onclick: () => { input.value = x.process; draw(); } }, (x.game_like ? "★ " : "") + x.process))
      : h("span", { class: "mute small" }, all.length ? "Nothing running matches. You can still add it by name." : "Nothing found."));
  };
  input.addEventListener("input", draw);
  const load = async () => {
    if (loaded) return; loaded = true; fill(progs, h("span", { class: "mute small" }, "Looking at what is running…"));
    try { all = (await api("programs")).programs; draw(); } catch (e) { fill(progs, err(e)); }
  };
  const act = async (record) => {
    fill(msg); if (!input.value.trim()) { fill(msg, h("p", { class: "err" }, "Pick a program from the list or type its name.")); return; }
    try {
      await api("game_add", { process: input.value.trim() });
      if (record) { const r = await api("game_record", { process: input.value.trim() }); gamesNote = r.message; }
      done();
    } catch (e) { fill(msg, err(e)); }
  };
  fill(el, h("div", { class: "row", style: "justify-content:space-between" }, h("p", { style: "margin:0" }, h("b", {}, "Add a game")), iconBtn("refresh", "Refresh the list", () => { loaded = false; load(); })),
    h("p", { class: "mute small" }, "Start the game first: it appears in the list below (games installed by Steam and similar come first, marked ★). If you just launched it, press “Refresh the list”. Or type the program name, as shown in Task Manager under Details."),
    progs, h("div", { class: "row" }, input, h("button", { onclick: () => act(false) }, "Add to library"), h("button", { class: "primary", onclick: () => act(true) }, "Add and record a session")), msg);
  return { el, open: () => { el.classList.add("open"); load(); input.focus(); }, toggle: () => { el.classList.toggle("open"); if (el.classList.contains("open")) load(); } };
}
function library(out, d) {
  const list = d.games; const panel = addGamePanel(!list.length, () => go("games")); const note = gamesNote; gamesNote = "";
  if (!list.length) panel.open();
  out(h("div", { class: "row" }, h("p", { class: "mute", style: "margin:0;flex:1" }, "Your games. Open one to see how it ran, in plain words."), iconBtn("refresh", "Refresh", () => go("games")), h("button", { class: "primary", onclick: () => panel.toggle() }, "+ Add a game")),
    note ? h("div", { class: "card" }, note) : null, panel.el,
    list.length ? h("div", { class: "grid", style: "margin-top:12px" }, list.map((g) => h("div", { class: "card gamecard", onclick: () => { gameOpen = g.id; go("games"); } },
      h("div", { class: "big" }, g.name),
      h("div", { class: "mute" }, [g.sessions ? plural(g.sessions, "play session", "play sessions") : null, g.runs ? plural(g.runs, "benchmark run", "benchmark runs") : null].filter(Boolean).join(" · ") || "No recordings yet"),
      h("div", { class: "mute small" }, g.last ? "Last recorded " + g.last : "Open it to record a session")))) : h("p", { class: "mute" }, "No games yet. Add the first one above."));
}
function gamePage(out, game, recs, d) {
  const numbers = (r) => h("div", { class: "grid" }, r.numbers.map((n) => h("div", { class: "card" }, h("h3", {}, n.label), h("div", { class: "big" }, String(n.value)), h("div", { class: "mute small" }, n.hint))));
  async function openRec(rec, host) {
    fill(host, h("p", { class: "mute" }, "Analysing…"));
    try {
      const r = await api("game?name=" + encodeURIComponent(rec.name));
      const other = h("select", {}, h("option", { value: "" }, "Compare with…"), recs.filter((x) => x.name !== rec.name).map((x) => h("option", { value: x.name }, x.name + " (" + x.recorded + ")")));
      const cmp = h("div", {});
      other.addEventListener("change", async () => { if (!other.value) return fill(cmp);
        try { const c = await api("compare?a=" + encodeURIComponent(rec.name) + "&b=" + encodeURIComponent(other.value));
          fill(cmp, h("div", { class: "card" }, h("div", { class: "big" }, c.verdict), h("p", {}, "Compared with " + other.value + ": " + c.text), h("p", { class: "mute small" }, c.note))); } catch (e) { fill(cmp, err(e)); } });
      fill(host, h("div", { class: "verdict " + r.tone }, r.verdict), h("p", {}, r.text + " (" + r.minutes + " min recorded)"), numbers(r), r.limiter ? h("p", {}, r.limiter) : null,
        recs.length > 1 ? h("div", { class: "row" }, other) : null, cmp);
    } catch (e) { fill(host, err(e)); }
  }
  const row = (r) => {
    const host = h("div", { class: "more" }); const toggle = () => { const on = host.classList.toggle("open"); if (on && !host.dataset.done) { host.dataset.done = "1"; openRec(r, host); } };
    return { el: h("div", { class: "item col" }, h("div", { class: "head", onclick: toggle }, h("div", { class: "body" }, h("div", { class: "title" }, r.name), h("div", { class: "detail" }, r.recorded)), h("span", { class: "mute" }, "▾")), host), toggle };
  };
  const group = (title, items, blurb) => {
    if (!items.length) return null;
    let all = false; const box = h("div", {});
    const draw = () => fill(box, (all ? items : items.slice(0, 6)).map((r) => row(r).el), !all && items.length > 6 ? h("button", { onclick: () => { all = true; draw(); } }, "Show all " + items.length) : null);
    draw();
    return h("section", {}, h("h2", {}, title + " (" + items.length + ")"), h("p", { class: "mute small" }, blurb), box);
  };
  // Benchmark runs come in batches (one test session with several variants, each repeated): one table per batch.
  const sessions = recs.filter((r) => r.kind !== "benchmark run"), runs = recs.filter((r) => r.kind === "benchmark run");
  const batches = [], byTag = new Map(), loose = [];
  for (const r of runs) {
    if (!r.batch) { loose.push(r); continue; }
    if (!byTag.has(r.batch)) { const b = { tag: r.batch, runs: [] }; byTag.set(r.batch, b); batches.push(b); }
    byTag.get(r.batch).runs.push(r);
  }
  const pct = (x) => (x == null ? "" : (x > 0 ? "+" : "") + x.toFixed(0) + "%");
  const batchRow = (b) => {
    const host = h("div", { class: "more" });
    const load = async () => {
      fill(host, h("p", { class: "mute" }, "Analysing " + b.runs.length + " runs…"));
      try {
        const d = await api("batch?game=" + encodeURIComponent(game.id) + "&batch=" + encodeURIComponent(b.tag));
        const rows = d.variants.map((v) => h("div", { class: "vrow" },
          h("div", { class: "nm" }, h("b", {}, v.variant), h("div", { class: "mute small" }, plural(v.runs, "run", "runs"))),
          h("div", { class: "av" }, h("div", { class: "lbl mute small" }, "Average FPS"), Math.round(v.avg_fps) + " FPS", h("div", { class: "mute small" }, v.reference ? "reference" : pct(v.avg_change))),
          h("div", { class: "lo" }, h("div", { class: "lbl mute small" }, "Worst 1% FPS"), Math.round(v.low1_fps) + " FPS", h("div", { class: "mute small" }, pct(v.low1_change))),
          h("div", { class: "res" }, h("span", { class: "badge " + v.tone }, v.verdict)),
          h("div", { class: "txt mute small" }, v.text)));
        const singles = h("div", { class: "more" });
        fill(singles, b.runs.map((r) => row(r).el));
        fill(host, h("div", { class: "vrow head" }, h("div", {}, "Variant"), h("div", {}, "Average FPS"), h("div", {}, "Worst 1% FPS"), h("div", {}, "Result")), rows,
          h("p", { class: "mute small" }, d.note),
          h("button", { onclick: () => singles.classList.toggle("open") }, "Show the " + b.runs.length + " single runs"), singles);
      } catch (e) { fill(host, err(e)); }
    };
    const variants = new Set(b.runs.map((r) => r.variant)).size;
    return h("div", { class: "item col" }, h("div", { class: "head", onclick: () => { const on = host.classList.toggle("open"); if (on && !host.dataset.done) { host.dataset.done = "1"; load(); } } },
      h("div", { class: "body" }, h("div", { class: "title" }, b.tag), h("div", { class: "detail" }, plural(variants, "variant", "variants") + " · " + plural(b.runs.length, "run", "runs") + " · " + b.runs[0].recorded)), h("span", { class: "mute" }, "▾")), host);
  };
  const batchSection = () => {
    if (!batches.length) return null;
    let all = false; const box = h("div", {});
    const draw = () => fill(box, (all ? batches : batches.slice(0, 6)).map(batchRow), !all && batches.length > 6 ? h("button", { onclick: () => { all = true; draw(); } }, "Show all " + batches.length) : null);
    draw();
    return h("section", {}, h("h2", {}, "Benchmark tests (" + batches.length + ")"),
      h("p", { class: "mute small" }, "Repeated runs of the same scene with different settings. Open one to see which setting helped, hurt or made no real difference."), box);
  };
  const latest = h("div", {}); const recMsg = h("div", {});
  const record = h("button", { class: "primary", onclick: async () => {
    fill(recMsg, h("p", { class: "mute" }, "Starting…"));
    try { const r = await api("game_record", { process: game.id }); fill(recMsg, h("div", { class: "card" }, r.message)); } catch (e) { fill(recMsg, err(e)); } } }, "Record a session");
  const remove = game.tracked ? h("button", { onclick: async () => { try { await api("game_remove", { process: game.id }); gameOpen = null; go("games"); } catch (e) { fill(recMsg, err(e)); } } }, "Remove from my list") : null;
  const refresh = iconBtn("refresh", "Refresh", () => go("games"));
  out(h("button", { class: "back", onclick: () => { gameOpen = null; go("games"); } }, "← All games"),
    h("h2", { style: "margin:8px 0 2px;font-size:20px" }, game.name),
    h("p", { class: "mute" }, recs.length ? [plural(recs.length, "recording", "recordings"), "last one " + recs[0].recorded].join(" · ") : "No recordings yet"),
    h("div", { class: "row" }, record, remove, refresh),
    h("p", { class: "mute small" }, "Finished a session? Press “Refresh” to check for the new recording."), recMsg,
    recs.length ? [
      h("section", {}, h("h2", {}, "Latest recording"), h("p", { class: "mute small" }, recs[0].name), latest),
      batchSection(),
      group("Play sessions", sessions, "Normal play, recorded while you played."),
      group("Other test runs", loose, "Single runs that do not belong to a batch.")]
    : h("div", { class: "card" }, h("p", {}, "Nothing recorded for this game yet."),
        h("p", { class: "mute" }, d.presentmon ? "Press “Record a session”, allow the administrator prompt and start the game. Close the game when you are done: the recording appears here."
          : "Recording needs PresentMon, which is not installed yet. Press “Record a session” to see where to get it.")));
  if (recs.length) openRec(recs[0], latest);
}

// ---- setup
const SECS = { 10: "10 seconds", 15: "15 seconds", 30: "30 seconds", 60: "1 minute", 120: "2 minutes", 300: "5 minutes" };
const MINS = { 5: "5 minutes", 15: "15 minutes", 30: "30 minutes", 60: "1 hour", 120: "2 hours", 360: "6 hours" };
async function setup(out) {
  const s = await api("setup"); const cfg = s.settings; const msg = h("div", { class: "mute" });
  const say = async (fn) => { msg.textContent = "Working…"; try { const r = await fn(); msg.textContent = r.ok === false ? "Failed: " + (r.output || "") : "Done"; return r.ok !== false; } catch (e) { msg.textContent = "Failed: " + e.message; return false; } };
  const pick = (map, choices, value) => { const el = h("select", {}, choices.map((c) => h("option", { value: String(c) }, map[c] || String(c)))); el.value = String(value); return el; };
  const field = (label, el, hint) => h("div", { class: "field" }, h("label", {}, label), el, hint ? h("div", { class: "mute small" }, hint) : null);
  const jobCard = (j, title, blurb, fields, collect) => {
    const installed = !!j.state;
    const buttons = h("div", { class: "row" },
      h("button", { class: "primary", onclick: async () => {
        const values = collect();
        const ok = installed ? await say(() => api("settings", { settings: values })) : await say(() => api("job", { task: j.name, action: "install", settings: values }));
        if (ok) setup(out); } }, installed ? "Save changes" : "Turn on"),
      installed ? h("button", { onclick: async () => { if (confirm("Turn this off?") && await say(() => api("job", { task: j.name, action: "remove" }))) setup(out); } }, "Turn off") : null);
    return h("div", { class: "item col" }, h("div", { class: "head static" }, h("span", { class: "badge " + (installed ? "ok" : "mute") }, installed ? "On" : "Off"), h("div", { class: "body" }, h("div", { class: "title" }, title), h("div", { class: "detail" }, blurb))),
      h("div", { class: "fields" }, fields, buttons));
  };
  const job = Object.fromEntries(s.jobs.map((j) => [j.name, j]));
  const cInt = pick(SECS, s.choices.collect_interval, cfg.collect_interval);
  const aInt = pick(MINS, s.choices.alerts_interval, cfg.alerts_interval);
  const aSev = h("select", {}, h("option", { value: "high" }, "Only serious problems"), h("option", { value: "medium" }, "Serious problems and things worth a look")); aSev.value = cfg.alerts_min_severity;
  const qOn = h("input", { type: "checkbox", checked: cfg.quiet_on }); const qFrom = h("input", { type: "time", value: cfg.quiet_from }); const qTo = h("input", { type: "time", value: cfg.quiet_to });
  const dAt = h("input", { type: "time", value: cfg.digest_time });
  out(h("p", { class: "mute" }, "Small helpers that run in the background. Turn on what you want and set it to fit your day."),
    h("section", {}, h("h2", {}, "Background helpers"),
      jobCard(job.collect, "Recorder", "Notes how busy the PC is, which programs run and what they connect to. Everything else here builds on this history.",
        field("Record every", cInt), () => ({ collect_interval: +cInt.value })),
      jobCard(job.alerts, "Problem alerts", "Shows a Windows notification when something needs attention.",
        [field("Check every", aInt), field("Tell me about", aSev), field("Quiet hours", h("div", { class: "row tight" }, h("label", {}, qOn, " stay silent from "), qFrom, " to ", qTo), "Nothing is shown in this time. Anything that comes up meanwhile is shown afterwards.")],
        () => ({ alerts_interval: +aInt.value, alerts_min_severity: aSev.value, quiet_on: qOn.checked, quiet_from: qFrom.value, quiet_to: qTo.value })),
      jobCard(job.digest, "Daily summary", "One notification a day with what happened in the last 24 hours. If the PC is off at that time, it shows when you switch it on.",
        field("Send at", dAt, "Pick a time when you are up."), () => ({ digest_time: dAt.value })), msg),
    h("section", {}, h("h2", {}, "Assistant (Ollama)"), h("div", { class: "item" }, h("span", { class: "badge " + (s.ollama.running ? "ok" : "bad") }, s.ollama.running ? "Running" : "Not running"), h("div", { class: "body" }, h("div", { class: "title" }, "Model " + s.model), h("div", { class: "detail" }, s.ollama.hint || "Ready to answer.")))),
    h("section", {}, h("h2", {}, "Try it"), h("div", { class: "row" }, h("button", { onclick: () => say(() => api("notify", {})) }, "Send a test notification"), h("button", { onclick: () => say(() => api("digest", {})) }, "Run the summary now")),
      h("p", { class: "mute" }, "Traffic per program needs administrator rights. In a terminal opened as administrator run:"), h("p", {}, h("code", {}, s.netstats_command))),
    h("section", {}, h("h2", {}, "Where your data is"), h("code", {}, s.data_folder)));
}

// Updates: only inside the desktop app (the shell checks for a signed release and installs it).
const shell = window.__TAURI_INTERNALS__;
let updTry = 0;
async function checkUpdate() {
  if (!shell) return;
  let u = null;
  try { u = await shell.invoke("check_update"); updTry = 0; }
  catch (e) { if (updTry < 3) setTimeout(checkUpdate, [60, 300, 900][updTry++] * 1000); return; }   // no network yet (autostart at logon): try again soon
  const box = $("upd");
  if (!u) { box.classList.remove("show"); return; }
  const later = h("button", { onclick: () => box.classList.remove("show") }, "Later");
  const go = h("button", { class: "primary", onclick: async () => {
    go.disabled = true; later.disabled = true; text.textContent = "Downloading and installing Pulse " + u.version + "… it restarts by itself.";
    try { await shell.invoke("install_update"); } catch (e) { text.textContent = "The update failed: " + (e && e.message || e); go.disabled = false; later.disabled = false; }
  } }, "Update now");
  const text = h("span", { class: "grow" }, "Pulse " + u.version + " is available.");
  box.replaceChildren(text, go, later); box.classList.add("show");
}
checkUpdate(); setInterval(checkUpdate, 6 * 3600 * 1000);

async function beat() { try { const st = await api("status"); $("dot").style.background = st.collector.recording ? "var(--ok)" : "var(--bad)"; } catch (e) {} }
setInterval(() => { api("ping", {}).catch(() => {}); }, 15000); api("ping", {}).catch(() => {}); beat(); setInterval(beat, 60000);
show();
</script></body></html>
"""


def render_page(nonce: str) -> str:
    return PAGE.replace("{{NONCE}}", nonce)
