// Opens the running app in headless Chrome and checks every page in light and dark, wide and narrow:
// it renders, nothing sticks out sideways, no script error or blocked resource, and the interactive parts work
// (chart hover across all charts, game library and batch table, chat list). Screenshots go to <outDir>.
//   node tests/ui_smoke.mjs <app url with ?t=token> <outDir>
import { spawn } from "node:child_process";
import fs from "node:fs";

const [url, outDir] = process.argv.slice(2);
const CHROME = process.env.CHROME || (process.platform === "win32" ? "C:/Program Files/Google/Chrome/Application/chrome.exe" : "google-chrome");
fs.mkdirSync(outDir, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const problems = [];
const TABS = ["overview", "ask", "findings", "timeline", "games", "setup"];
let port = 9500;

async function open(theme, width, height) {
  const p = port++;
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${p}`, `--user-data-dir=${outDir}/profile-${p}`, "about:blank"], { stdio: "ignore" });
  let list;
  for (let i = 0; i < 60; i++) { try { list = await (await fetch(`http://127.0.0.1:${p}/json`)).json(); if (list.length) break; } catch (e) {} await sleep(250); }
  const ws = new WebSocket(list.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const waiting = new Map(); const errors = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); return; }
    if (d.method === "Runtime.exceptionThrown") errors.push("exception: " + (d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text));
    if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") errors.push("console.error: " + d.params.args.map((a) => a.value ?? a.description).join(" "));
    if (d.method === "Log.entryAdded" && d.params.entry.level === "error" && !/favicon/.test(d.params.entry.url || "") && !/\/api\/ask$/.test(d.params.entry.url || "")) errors.push("log: " + d.params.entry.text + " " + (d.params.entry.url || ""));
  };
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; waiting.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.text + " in " + expr.slice(0, 80)); return r.result.result.value; };
  const waitFor = async (expr, what, ms = 120000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (await evalJs(expr)) return true; } catch (e) {} await sleep(300); } throw new Error("timed out waiting for " + what); };
  await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
  await send("Page.navigate", { url });
  await waitFor("typeof go === 'function' && document.querySelectorAll('#tabs button').length === 6", "the page");
  const shot = async (name) => { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(`${outDir}/${name}.png`, Buffer.from(r.result.data, "base64")); };
  return { send, evalJs, waitFor, shot, errors, close: () => { ws.close(); chrome.kill(); } };
}

async function check(theme, width) {
  const ctx = `${theme}-${width}`;
  const s = await open(theme, width, width < 700 ? 800 : 1000);
  const fail = (where, msg) => { problems.push(`[${ctx}] ${where}: ${msg}`); console.log("FAIL", `[${ctx}]`, where, msg); };
  const guard = async (where, fn) => { try { await fn(); } catch (e) { fail(where, e.message); } };
  const settle = async (tab, extra) => {
    await s.evalJs(`go('${tab}')`);
    await s.waitFor("!document.querySelector('#view').innerText.includes('Loading')", tab + " to load");
    if (extra) await extra();
    const sides = await s.evalJs(`(() => { const W = innerWidth; const bad = [...document.querySelectorAll('#view *')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > W + 1; }).slice(0, 3).map((e) => e.tagName + '.' + e.className); return { sw: document.documentElement.scrollWidth, W, bad }; })()`);
    if (sides.sw > sides.W + 1 || sides.bad.length) fail(tab, `sticks out sideways (page ${sides.sw}px in a ${sides.W}px window): ${sides.bad.join(", ")}`);
    if ((await s.evalJs("document.querySelector('#view').innerText.trim().length")) < 20) fail(tab, "the page is empty");
    if (tab !== "ask" && (await s.evalJs("document.querySelectorAll('#view .err').length"))) fail(tab, "shows an error: " + (await s.evalJs("document.querySelector('#view .err').textContent")));
    const motion = await s.evalJs("(() => { const v = document.querySelector('#view > *'); const b = document.querySelector('button'); return [getComputedStyle(v).animationName, parseFloat(getComputedStyle(b).transitionDuration) > 0, getComputedStyle(document.querySelector('#tabs .ink')).transitionDuration]; })()");
    if (motion[0] !== "rise" || !motion[1] || parseFloat(motion[2]) <= 0) fail(tab, "the page or the buttons do not animate: " + JSON.stringify(motion));
    await s.shot(`${ctx}-${tab}`);
  };

  await guard("overview", () => settle("overview", async () => {
    await s.waitFor("(() => { const f = document.querySelector('iframe'); return f && f.contentDocument && f.contentDocument.body && f.contentDocument.querySelectorAll('svg').length >= 4 && parseInt(f.style.height) > 200; })()", "the report");
    const inner = await s.evalJs("(() => { const d = document.querySelector('iframe').contentDocument; return d.documentElement.scrollWidth - d.documentElement.clientWidth; })()");
    if (inner > 1) fail("overview", `the report sticks out sideways by ${inner}px`);
    // one moment hovered in the first chart must show a value in all four
    const pos = await s.evalJs(`(() => { const f = document.querySelector('iframe'); const hits = f.contentDocument.querySelectorAll('svg')[0].querySelectorAll('.hit'); const hit = hits[Math.floor(hits.length / 2)]; hit.scrollIntoView({ block: 'center' }); const r = hit.getBoundingClientRect(), fr = f.getBoundingClientRect(); return [Math.round(fr.left + r.left + r.width / 2), Math.round(fr.top + r.top + r.height / 2)]; })()`);
    await sleep(300);
    const pos2 = await s.evalJs(`(() => { const f = document.querySelector('iframe'); const hits = f.contentDocument.querySelectorAll('svg')[0].querySelectorAll('.hit'); const hit = hits[Math.floor(hits.length / 2)]; const r = hit.getBoundingClientRect(), fr = f.getBoundingClientRect(); return [Math.round(fr.left + r.left + r.width / 2), Math.round(fr.top + r.top + r.height / 2)]; })()`);
    await s.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos2[0], y: pos2[1] }); await sleep(400);
    const shown = await s.evalJs("(() => { const d = document.querySelector('iframe').contentDocument; return [...d.querySelectorAll('svg')].map((svg) => [...svg.querySelectorAll('.tip')].filter((t) => getComputedStyle(t).opacity === '1').length); })()");
    if (shown.slice(0, 4).some((n) => n !== 1)) fail("overview", "hovering one moment did not show a value in every chart: " + JSON.stringify(shown));
    // a click on a table heading sorts that table, another click reverses it
    const sorted = await s.evalJs(`(() => { const d = document.querySelector('iframe').contentDocument; const th = d.querySelector('th.sortable'); if (!th) return 'no sortable heading'; th.click(); const a = th.getAttribute('aria-sort'); th.click(); return a + '/' + th.getAttribute('aria-sort'); })()`);
    if (!/^(ascending\/descending|descending\/ascending)$/.test(sorted)) fail("overview", "clicking a table heading did not sort: " + sorted);
    // the underline under the tabs sits under the chosen one
    const ink = await s.evalJs("(() => { const n = document.querySelector('#tabs'); const on = n.querySelector('button.on'), i = n.querySelector('.ink'); return [Math.abs(parseFloat(i.style.left) - on.offsetLeft), parseFloat(i.style.width) - on.offsetWidth]; })()");
    if (ink[0] > 1 || Math.abs(ink[1]) > 1) fail("overview", "the tab underline is not under the chosen tab: " + JSON.stringify(ink));
    await s.evalJs("document.querySelector('main').scrollTop = 0");
  }));

  await guard("ask", () => settle("ask", async () => {
    await s.waitFor("document.querySelectorAll('.chatrow').length >= 2", "the chat list");
    await s.evalJs("document.querySelectorAll('.chatrow')[1].click()");
    await s.waitFor("document.querySelector('.msg.me')", "an earlier chat to open");
  }));

  await guard("findings", () => settle("findings"));
  await guard("timeline", () => settle("timeline"));

  await guard("games", () => settle("games", async () => {
    await s.waitFor("document.querySelector('.gamecard')", "the game library");
    await s.evalJs("[...document.querySelectorAll('button')].find((b) => /Add a game/.test(b.textContent)).click()");
    await s.waitFor("document.querySelectorAll('.progs button').length > 0", "the list of running programs");
    await s.evalJs("document.querySelector('.gamecard').click()");
    await s.waitFor("document.querySelector('.verdict')", "the latest recording");
    await s.evalJs("[...document.querySelectorAll('.item.col .head')].find((h) => /variants? ·/.test(h.textContent)).click()");
    await s.waitFor("document.querySelectorAll('.vrow:not(.head)').length >= 2", "the batch table");
    const headHidden = await s.evalJs("getComputedStyle(document.querySelector('.vrow.head')).display === 'none'");
    await s.evalJs("document.querySelector('.vrow:not(.head)').scrollIntoView({ block: 'center' })");   // the screenshot shows the table
    if ((width <= 640) !== headHidden) fail("games", `the column headings should be ${width <= 640 ? "hidden" : "shown"} at ${width}px`);
  }));

  await guard("setup", () => settle("setup"));
  // an unusual moment offers a question for the assistant; the button opens a new chat with it (only when the data has one)
  await guard("unusual", async () => {
    await s.evalJs("go('overview')");
    await s.waitFor("!document.querySelector('#view').innerText.includes('Loading')", "the overview");
    await sleep(1500);
    const has = await s.evalJs("[...document.querySelectorAll('#view button')].some((b) => /What could it be/.test(b.textContent))");
    if (!has) return;
    await s.evalJs("[...document.querySelectorAll('#view button')].find((b) => /What could it be/.test(b.textContent)).click()");
    await s.waitFor("document.querySelector('.msg.me') && /unusual/.test(document.querySelector('.msg.me').textContent)", "the question to appear in a new chat");
  });
  const filtered = s.errors.filter((e, i) => s.errors.indexOf(e) === i);
  if (filtered.length) fail("console", filtered.slice(0, 5).join(" | "));
  s.close();
}

for (const theme of ["dark", "light"]) for (const width of [1200, 400]) await check(theme, width);
if (problems.length) { console.log(`\n${problems.length} problem(s):\n` + problems.join("\n")); process.exit(1); }
console.log("interface check passed: " + TABS.length + " pages x 4 settings");
process.exit(0);
