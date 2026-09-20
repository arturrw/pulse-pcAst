"""The single page of the web app: markup, style and script in one string (no external files, no external addresses).

Safety rules for this page: everything that comes from the machine (process names, file paths, model answers) is put on
the screen with textContent / createTextNode, never as HTML, and the server's content policy blocks every script that
does not carry the per-response nonce. Texts are in English and Russian; the language follows the browser and can be
switched in the header."""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>pcassist</title>
<style nonce="{{NONCE}}">
:root{--bg:#f4f4f1;--card:#fff;--ink:#1c1c1a;--mute:#6b6b66;--line:#e2e2dc;--acc:#2f6fdb;--acc-ink:#fff;--ok:#2a7d46;--warn:#b25b00;--bad:#c23b32;--chip:#eceae4}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecece8;--mute:#9a9a94;--line:#33332f;--acc:#6ea0ff;--acc-ink:#0d0d0c;--ok:#5fcf8b;--warn:#ffb15c;--bad:#ff8a80;--chip:#2a2a27}}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,"Segoe UI",sans-serif;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:12px;padding:10px 20px;border-bottom:1px solid var(--line);background:var(--card)}
header h1{font-size:17px;margin:0;flex:1}header .dot{width:10px;height:10px;border-radius:50%;background:var(--mute)}
button,select,input,textarea{font:inherit;color:inherit}
select{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:5px 8px}
button{background:var(--chip);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer}button:hover{border-color:var(--acc)}
button.primary{background:var(--acc);color:var(--acc-ink);border-color:var(--acc)}button:disabled{opacity:.5;cursor:default}
nav{display:flex;gap:4px;padding:8px 20px 0;background:var(--card);border-bottom:1px solid var(--line);flex-wrap:wrap}
nav button{border:0;border-bottom:3px solid transparent;border-radius:6px 6px 0 0;background:none;padding:8px 14px}
nav button.on{border-bottom-color:var(--acc);font-weight:600}
main{flex:1;overflow:auto;padding:18px 20px 40px;max-width:1100px;width:100%;margin:0 auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.card h3{margin:0 0 4px;font-size:13px;color:var(--mute);font-weight:500}.card .big{font-size:20px;font-weight:600;line-height:1.25}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mute{color:var(--mute)}
.badge{display:inline-block;font-size:12px;border-radius:999px;padding:1px 9px;border:1px solid currentColor}
section{margin-top:22px}section>h2{font-size:15px;margin:0 0 8px}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:8px;display:flex;gap:12px;align-items:flex-start}
.item .body{flex:1;min-width:0}.item .title{font-weight:600}.item .detail{color:var(--mute);font-size:13px;overflow-wrap:anywhere}
iframe{width:100%;height:1500px;border:1px solid var(--line);border-radius:12px;background:var(--card)}
.chat{display:flex;flex-direction:column;gap:10px;min-height:280px}.msg{max-width:85%;padding:9px 13px;border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.me{align-self:flex-end;background:var(--acc);color:var(--acc-ink)}.msg.bot{align-self:flex-start;background:var(--card);border:1px solid var(--line)}
.tools{font-size:12px;color:var(--mute);margin-top:4px}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
textarea,input[type=text]{flex:1;min-width:180px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 10px 6px 0;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}th{color:var(--mute);font-weight:500}
code{background:var(--chip);padding:1px 6px;border-radius:5px;overflow-wrap:anywhere}.chips button{font-size:13px}.err{color:var(--bad);margin:8px 0}
</style></head><body>
<header><span class="dot" id="dot"></span><h1>pcassist</h1><select id="lang" aria-label="language"><option value="en">English</option><option value="ru">Русский</option></select><button id="quit"></button></header>
<nav id="tabs"></nav><main id="view"></main>
<script nonce="{{NONCE}}">
"use strict";
const TXT = {
 en:{tabs:{overview:"Overview",ask:"Ask",findings:"Findings",timeline:"What happened",games:"Games",setup:"Setup"},quit:"Quit",
  recording:"Recording",winDef:"Windows & Defender",autostart:"Autostart",procs:"Processes",disk:"Disk",jobs:"Background jobs",
  recOk:"Recording",recBad:"Not recording",lastSample:"last sample {s} s ago",hours:"{h} of the last 24 h recorded",
  open:"{n} open",none:"nothing open",accepted:"{n} accepted by you",rtOn:"real-time protection on",rtOff:"real-time protection OFF",
  newN:"{n} new",noNew:"nothing new",since:"since {d}",flagged:"{n} flagged",noFlag:"nothing flagged",free:"{g} GB free on {d}",
  jobOn:"on",jobOff:"not installed",loading:"Loading…",report:"Report (last 24 h)",
  askHint:"Ask about this PC: load, disks, temperature, processes, autostart, Defender, game FPS. It only reads.",send:"Send",reset:"New chat",
  askPlaceholder:"Ask something…",thinking:"Thinking…",usedTools:"used: {t}",
  examples:["How much free space do I have?","Was anything unusual in the last day?","Is my antivirus on?","What starts by itself?","What happened at 14:00?"],
  sev:{high:"high",medium:"medium",low:"low"},accept:"Accept",forget:"Forget",acceptedFor:"accepted {d}",why:"Why do you accept it? (optional)",
  ok:"OK",cancel:"Cancel",noFindings:"No findings.",infoOnly:"information only",acceptedList:"Accepted risks",
  whenLabel:"Moment",whenHint:"14:03, yesterday 21:30, 2026-09-20 14:03 or 45 minutes ago",window:"± minutes",show:"Show",
  metricsHdr:"Metrics in the window",heaviest:"Heaviest processes",timelineHdr:"Timeline",emptyTl:"Nothing on the timeline.",
  recordings:"Recordings",noRec:"No recordings yet. Record a game with PresentMon (see the README).",report1:"Report",compare:"Compare",
  before:"before",after:"after",jobsHdr:"Background jobs",install:"Install",remove:"Remove",confirmInstall:"Install this background job?",
  confirmRemove:"Remove this background job?",ollamaHdr:"Assistant (Ollama)",running:"running",notRunning:"not running",model:"model {m}",
  tools:"Tools",testNote:"Send a test notification",digestNow:"Run the digest now",netstats:"Traffic per process needs administrator rights. In a terminal opened as administrator run:",
  dataFolder:"Data folder",copied:"Copied",done:"Done",failed:"Failed",state:{Running:"running",Ready:"ready",Disabled:"disabled"}},
 ru:{tabs:{overview:"Обзор",ask:"Спросить",findings:"Находки",timeline:"Что происходило",games:"Игры",setup:"Настройка"},quit:"Выйти",
  recording:"Запись",winDef:"Windows и Defender",autostart:"Автозапуск",procs:"Процессы",disk:"Диск",jobs:"Фоновые задачи",
  recOk:"Идёт запись",recBad:"Запись не идёт",lastSample:"последний замер {s} с назад",hours:"записано {h} из последних 24 ч",
  open:"открыто: {n}",none:"ничего не открыто",accepted:"принято вами: {n}",rtOn:"защита в реальном времени включена",rtOff:"защита в реальном времени ВЫКЛЮЧЕНА",
  newN:"новых: {n}",noNew:"ничего нового",since:"с {d}",flagged:"помечено: {n}",noFlag:"ничего не помечено",free:"свободно {g} ГБ на {d}",
  jobOn:"включена",jobOff:"не установлена",loading:"Загрузка…",report:"Отчёт (последние 24 ч)",
  askHint:"Спросите про этот ПК: нагрузка, диски, температура, процессы, автозапуск, Defender, FPS в играх. Только чтение.",send:"Отправить",reset:"Новый чат",
  askPlaceholder:"Спросите что-нибудь…",thinking:"Думаю…",usedTools:"использовано: {t}",
  examples:["Сколько свободного места на дисках?","Было ли что-то странное за последние сутки?","Включён ли антивирус?","Что запускается само?","Что происходило в 14:00?"],
  sev:{high:"высокая",medium:"средняя",low:"низкая"},accept:"Принять",forget:"Снять",acceptedFor:"принято {d}",why:"Почему вы это принимаете? (необязательно)",
  ok:"ОК",cancel:"Отмена",noFindings:"Находок нет.",infoOnly:"только информация",acceptedList:"Принятые риски",
  whenLabel:"Момент",whenHint:"14:03, вчера 21:30, 2026-09-20 14:03 или 45 минут назад",window:"± минут",show:"Показать",
  metricsHdr:"Метрики в этом окне",heaviest:"Самые тяжёлые процессы",timelineHdr:"Шкала времени",emptyTl:"На шкале ничего нет.",
  recordings:"Записи",noRec:"Записей пока нет. Запишите игру через PresentMon (см. README).",report1:"Отчёт",compare:"Сравнить",
  before:"до",after:"после",jobsHdr:"Фоновые задачи",install:"Установить",remove:"Удалить",confirmInstall:"Установить эту фоновую задачу?",
  confirmRemove:"Удалить эту фоновую задачу?",ollamaHdr:"Ассистент (Ollama)",running:"работает",notRunning:"не запущена",model:"модель {m}",
  tools:"Инструменты",testNote:"Отправить тестовое уведомление",digestNow:"Сформировать сводку сейчас",netstats:"Трафик по процессам требует прав администратора. В терминале от имени администратора выполните:",
  dataFolder:"Папка данных",copied:"Скопировано",done:"Готово",failed:"Не удалось",state:{Running:"работает",Ready:"готова",Disabled:"отключена"}}
};
let lang = localStorage.getItem("pca-lang") || ((navigator.language || "en").slice(0, 2) === "ru" ? "ru" : "en");
const t = (k, v) => { let s = k.split(".").reduce((o, p) => (o ? o[p] : undefined), TXT[lang]); if (typeof s !== "string") return s === undefined ? k : s; for (const x in v || {}) s = s.split("{" + x + "}").join(String(v[x])); return s; };
const $ = (id) => document.getElementById(id);
const stateText = (x) => { const v = t("state." + x); return v === "state." + x ? x : v; };
function h(tag, props, ...kids) { const e = document.createElement(tag); for (const k in props || {}) { if (k === "class") e.className = props[k]; else if (k.startsWith("on")) e.addEventListener(k.slice(2), props[k]); else if (k === "text") e.textContent = props[k]; else e.setAttribute(k, props[k]); } for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c))); return e; }
async function api(path, body) {
  const opt = { credentials: "same-origin" }; if (body !== undefined) { opt.method = "POST"; opt.headers = { "Content-Type": "application/json" }; opt.body = JSON.stringify(body); }
  const r = await fetch("/api/" + path, opt); let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status)); return j;
}
const view = () => $("view");
const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null));
const tabs = ["overview", "ask", "findings", "timeline", "games", "setup"];
let tab = tabs.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview"; const chatLog = [];

function drawTabs() { const n = $("tabs"); n.replaceChildren(...tabs.map((k) => h("button", { class: k === tab ? "on" : "", onclick: () => { tab = k; history.replaceState(null, "", "#" + k); show(); } }, t("tabs." + k)))); $("quit").textContent = t("quit"); }
function show() { drawTabs(); fill(view(), h("p", { class: "mute" }, t("loading"))); ({ overview, ask, findings, timeline, games, setup })[tab]().catch((e) => fill(view(), h("p", { class: "err" }, String(e.message)))); }
const sev = (s) => h("span", { class: "badge " + (s === "high" ? "bad" : s === "medium" ? "warn" : "mute") }, t("sev." + s));

async function overview() {
  const s = await api("status"); const c = s.collector, w = s.windows, a = s.startup, p = s.processes;
  const card = (title, cls, big, small) => h("div", { class: "card" }, h("h3", {}, title), h("div", { class: "big " + cls }, big), h("div", { class: "mute" }, small));
  const jobs = Object.entries(s.jobs).map(([k, v]) => h("span", { class: "badge " + (v ? "ok" : "mute"), style: "margin-right:6px" }, k + ": " + (v ? t("jobOn") : t("jobOff"))));
  $("dot").style.background = c.recording ? "var(--ok)" : "var(--bad)";
  fill(view(), 
    h("div", { class: "grid" },
      card(t("recording"), c.recording ? "ok" : "bad", c.recording ? t("recOk") : t("recBad"), (c.seconds_since_last_sample == null ? "" : t("lastSample", { s: Math.round(c.seconds_since_last_sample) }) + " · ") + t("hours", { h: c.hours_recorded_24h.toFixed(0) })),
      w.available ? card(t("winDef"), w.open ? (w.high ? "bad" : "warn") : "ok", w.open ? t("open", { n: w.open }) : t("none"), (w.realtime_protection ? t("rtOn") : t("rtOff")) + (w.accepted ? " · " + t("accepted", { n: w.accepted }) : "")) : null,
      a.available ? card(t("autostart"), a.new ? (a.high ? "bad" : "warn") : "ok", a.new ? t("newN", { n: a.new }) : t("noNew"), t("since", { d: a.baseline })) : null,
      p.available ? card(t("procs"), p.flagged ? "bad" : "ok", p.flagged ? t("flagged", { n: p.flagged }) : t("noFlag"), "") : null,
      s.disk ? card(t("disk"), s.disk.free_gb < 30 ? "bad" : "ok", t("free", { g: Math.round(s.disk.free_gb), d: s.disk.name }), "") : null,
      h("div", { class: "card" }, h("h3", {}, t("jobs")), h("div", {}, jobs))),
    h("section", {}, h("h2", {}, t("report")), h("iframe", { src: "/report?hours=24", sandbox: "", title: "report" })));
}

async function ask() {
  const st = (await api("setup")).ollama;
  const log = h("div", { class: "chat" }); const input = h("textarea", { rows: "2", placeholder: t("askPlaceholder") }); const btn = h("button", { class: "primary" }, t("send"));
  const add = (who, text, used) => { const m = h("div", { class: "msg " + who }, text); log.append(used && used.length ? h("div", {}, m, h("div", { class: "tools" }, t("usedTools", { t: used.join(", ") }))) : m); log.scrollIntoView({ block: "end" }); return m; };
  chatLog.forEach((m) => add(m.who, m.text, m.used));
  async function send(text) {
    text = (text || input.value).trim(); if (!text) return; input.value = ""; add("me", text); chatLog.push({ who: "me", text });
    btn.disabled = true; const wait = add("bot", t("thinking"));
    try { const r = await api("ask", { message: text }); wait.remove(); add("bot", r.answer, r.tools); chatLog.push({ who: "bot", text: r.answer, used: r.tools }); }
    catch (e) { wait.remove(); log.append(h("div", { class: "err" }, String(e.message))); }
    btn.disabled = false; input.focus();
  }
  btn.addEventListener("click", () => send()); input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  fill(view(), h("p", { class: "mute" }, t("askHint")), st.running && st.model_ready ? null : h("p", { class: "err" }, st.hint),
    h("div", { class: "row chips" }, t("examples").map((x) => h("button", { onclick: () => send(x) }, x))), log,
    h("div", { class: "row" }, input, btn, h("button", { onclick: async () => { await api("reset", {}); chatLog.length = 0; ask(); } }, t("reset"))));
}

function acceptRow(f, reload) {
  const box = h("div", {}); const reset = () => fill(box, );
  const ask_ = () => { const note = h("input", { type: "text", placeholder: t("why") }); fill(box, h("div", { class: "row" }, note, h("button", { class: "primary", onclick: async () => { await api("ack", { id: f.id, note: note.value }); reload(); } }, t("ok")), h("button", { onclick: reset }, t("cancel")))); note.focus(); };
  if (f.id == null) return h("span", { class: "mute" }, t("infoOnly"));
  if (f.accepted) return h("div", {}, h("div", { class: "mute" }, t("acceptedFor", { d: f.accepted_since || "" }) + (f.accepted_note ? ": " + f.accepted_note : "")), h("button", { onclick: async () => { await api("forget", { id: f.id }); reload(); } }, t("forget")));
  return h("div", {}, h("button", { onclick: ask_ }, t("accept")), box);
}
async function findings() {
  const d = await api("findings"); const reload = () => findings().catch((e) => fill(view(), h("p", { class: "err" }, String(e.message))));
  const groups = {}; d.findings.forEach((f) => (groups[f.source] = groups[f.source] || []).push(f));
  const parts = Object.keys(groups).map((src) => h("section", {}, h("h2", {}, src), groups[src].map((f) => h("div", { class: "item", style: f.accepted ? "opacity:.65" : "" }, sev(f.severity), h("div", { class: "body" }, h("div", { class: "title" }, f.title), h("div", { class: "detail" }, f.detail || "")), acceptRow(f, reload)))));
  fill(view(), ...(parts.length ? parts : [h("p", { class: "mute" }, t("noFindings"))]),
    Object.keys(d.accepted_list).length ? h("section", {}, h("h2", {}, t("acceptedList")), Object.entries(d.accepted_list).map(([k, v]) => h("div", { class: "item" }, h("div", { class: "body" }, h("div", { class: "title" }, k), h("div", { class: "detail" }, (v.since || "") + " " + (v.note || ""))), h("button", { onclick: async () => { await api("forget", { id: k }); reload(); } }, t("forget"))))) : null);
}

async function timeline() {
  const when = h("input", { type: "text", value: "now", placeholder: t("whenHint") }); const mins = h("input", { type: "text", value: "30", style: "max-width:70px;flex:0" });
  const out = h("div", {}); const go = async () => { fill(out, h("p", { class: "mute" }, t("loading")));
    try { const r = await api("timeline?when=" + encodeURIComponent(when.value) + "&minutes=" + encodeURIComponent(mins.value));
      const rows = Object.entries(r.metrics).map(([k, m]) => h("tr", {}, h("td", {}, k), h("td", {}, m.window_avg.toFixed(1)), h("td", {}, m.window_max.toFixed(1)), h("td", {}, m.before_avg_3h == null ? "" : m.before_avg_3h.toFixed(1))));
      fill(out, h("p", { class: "mute" }, r.window.from + " → " + r.window.to + " · " + r.summary),
        rows.length ? h("section", {}, h("h2", {}, t("metricsHdr")), h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "avg"), h("th", {}, "max"), h("th", {}, "3 h before"))), h("tbody", {}, rows))) : null,
        r.heaviest_processes.length ? h("section", {}, h("h2", {}, t("heaviest")), h("div", {}, r.heaviest_processes.map((p) => h("div", {}, p.name + " — " + p.avg_cpu_percent.toFixed(1) + " %")))) : null,
        h("section", {}, h("h2", {}, t("timelineHdr")), r.timeline.length ? r.timeline.map((e) => h("div", { class: "item" }, h("code", {}, e.time), h("span", { class: "badge mute" }, e.type), h("div", { class: "body" }, e.text))) : h("p", { class: "mute" }, t("emptyTl"))));
    } catch (e) { fill(out, h("p", { class: "err" }, String(e.message))); } };
  when.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  fill(view(), h("div", { class: "row" }, h("span", { class: "mute" }, t("whenLabel")), when, h("span", { class: "mute" }, t("window")), mins, h("button", { class: "primary", onclick: go }, t("show"))), h("p", { class: "mute" }, t("whenHint")), out); go();
}

async function games() {
  const d = await api("games"); const out = h("div", {}); const recs = d.recordings.filter((r) => !r.error);
  const show1 = async (name) => { try { const r = await api("game?name=" + encodeURIComponent(name));
    fill(out, h("section", {}, h("h2", {}, name), h("table", {}, h("tbody", {}, [["avg FPS", r.avg_fps], ["1% low", r.low1_fps], ["0.1% low", r.low01_fps], ["p99 ms", r.p99_frame_ms], ["frames > 33 ms", r.frames_over_33ms]].map(([k, v]) => h("tr", {}, h("td", {}, k), h("td", {}, String(v))))))));
  } catch (e) { fill(out, h("p", { class: "err" }, String(e.message))); } };
  const a = h("select", {}, recs.map((r) => h("option", { value: r.name }, r.name))), b = h("select", {}, recs.map((r) => h("option", { value: r.name }, r.name)));
  const cmp = async () => { try { const r = await api("compare?a=" + encodeURIComponent(a.value) + "&b=" + encodeURIComponent(b.value));
    fill(out, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, t("before")), h("th", {}, t("after")), h("th", {}, "%"))), h("tbody", {}, ["avg_fps", "low1_fps", "low01_fps", "p99_frame_ms"].map((k) => h("tr", {}, h("td", {}, k), h("td", {}, String(r[k].before)), h("td", {}, String(r[k].after)), h("td", {}, r[k].change_percent == null ? "" : r[k].change_percent.toFixed(1)))))));
  } catch (e) { fill(out, h("p", { class: "err" }, String(e.message))); } };
  fill(view(), recs.length ? h("div", {}, h("section", {}, h("h2", {}, t("recordings")), recs.map((r) => h("div", { class: "item" }, h("div", { class: "body" }, h("div", { class: "title" }, r.name), h("div", { class: "detail" }, r.recorded + " · " + r.kind)), h("button", { onclick: () => show1(r.name) }, t("report1"))))),
    h("div", { class: "row" }, h("span", { class: "mute" }, t("compare")), a, h("span", { class: "mute" }, "→"), b, h("button", { onclick: cmp }, t("compare")))) : h("p", { class: "mute" }, t("noRec")), out);
}

async function setup() {
  const s = await api("setup"); const msg = h("div", { class: "mute" });
  const act = (fn) => async () => { msg.textContent = t("loading"); try { const r = await fn(); msg.textContent = r.ok === false ? t("failed") + ": " + (r.output || "") : t("done"); } catch (e) { msg.textContent = t("failed") + ": " + e.message; } };
  const jobs = s.jobs.map((j) => h("div", { class: "item" }, h("span", { class: "badge " + (j.state ? "ok" : "mute") }, j.state ? stateText(j.state) : t("jobOff")), h("div", { class: "body" }, h("div", { class: "title" }, "pcassist-" + j.name), h("div", { class: "detail" }, j.what)),
    j.state ? h("button", { onclick: async () => { if (confirm(t("confirmRemove"))) { await act(() => api("job", { task: j.name, action: "remove" }))(); setup(); } } }, t("remove"))
            : h("button", { class: "primary", onclick: async () => { if (confirm(t("confirmInstall"))) { await act(() => api("job", { task: j.name, action: "install" }))(); setup(); } } }, t("install"))));
  fill(view(), h("section", {}, h("h2", {}, t("jobsHdr")), jobs),
    h("section", {}, h("h2", {}, t("ollamaHdr")), h("div", { class: "item" }, h("span", { class: "badge " + (s.ollama.running ? "ok" : "bad") }, s.ollama.running ? t("running") : t("notRunning")), h("div", { class: "body" }, h("div", { class: "title" }, t("model", { m: s.model })), h("div", { class: "detail" }, s.ollama.hint || "")))),
    h("section", {}, h("h2", {}, t("tools")), h("div", { class: "row" }, h("button", { onclick: act(() => api("notify", {})) }, t("testNote")), h("button", { onclick: act(() => api("digest", {})) }, t("digestNow")), msg),
      h("p", { class: "mute" }, t("netstats")), h("p", {}, h("code", {}, s.netstats_command))),
    h("section", {}, h("h2", {}, t("dataFolder")), h("code", {}, s.data_folder)));
}

$("lang").value = lang; $("lang").addEventListener("change", () => { lang = $("lang").value; localStorage.setItem("pca-lang", lang); show(); });
$("quit").addEventListener("click", async () => { try { await api("quit", {}); } catch (e) {} document.body.replaceChildren(h("p", { class: "mute", style: "padding:24px" }, "pcassist has stopped. You can close this tab.")); });
async function beat() { try { const st = await api("status"); $("dot").style.background = st.collector.recording ? "var(--ok)" : "var(--bad)"; } catch (e) {} }
setInterval(() => { api("ping", {}).catch(() => {}); }, 15000); api("ping", {}).catch(() => {}); beat(); setInterval(beat, 60000);
show();
</script></body></html>
"""


def render_page(nonce: str) -> str:
    return PAGE.replace("{{NONCE}}", nonce)
