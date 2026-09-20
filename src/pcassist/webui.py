"""The local web app: `pcassist ui` opens a dashboard in the browser with everything the command line can do.

It is a small server on 127.0.0.1 only. Because a web page can be attacked from other web pages, every request is
checked: the Host must be the local address (DNS-rebinding), a POST's Origin must be this app, and every call needs a
secret token that is random per run (given once in the address, kept in an HttpOnly SameSite=Strict cookie). Pages are
served with a content policy that blocks every script but the app's own, and the app builds its screen with textContent
only, so a hostile process name cannot inject anything. Only a fixed list of actions exists: read the tools, accept or
forget a finding, ask the assistant, install or remove our own three background jobs. It is read-only otherwise.
The server stops when the browser tab has been closed for a few minutes."""
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import ack, alerts, chat, db, digest, report, setup_tasks, tools
from .webui_page import render_page

MAX_BODY = 16 * 1024
MAX_MESSAGE = 2000
KEEP_MESSAGES = 24           # the conversation the model sees, besides the system prompt
IDLE_SECONDS = 300           # no heartbeat from the page for this long: the tab is closed, stop the server
STATUS_TTL = 45              # the Windows checks take seconds: reuse them for a moment


class ApiError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def _clean_id(value, what: str = "id") -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 300 or any(ord(c) < 32 for c in value):
        raise ApiError(f"bad {what}")
    return value.strip()


class App:
    """Everything the pages can ask for, as plain functions returning JSON-ready dicts (no HTTP in here)."""

    def __init__(self, db_path, model: str = "qwen3:8b", client_factory=None, task_runner=None, notify_fn=None):
        self.db_path = Path(db_path)
        self.model = model
        self._client_factory = client_factory
        self._task_runner = task_runner
        self._notify = notify_fn or alerts.notify
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self._sessions: dict[str, list] = {}
        tools.set_db(self.db_path)

    # ---- small cache: the Windows checks take a couple of seconds each
    def _cached(self, key: str, fn, ttl: float = STATUS_TTL):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (time.time(), value)
        return value

    def _forget_cache(self, *keys: str) -> None:
        with self._lock:
            for k in keys or list(self._cache):
                self._cache.pop(k, None)

    # ---- overview
    def status(self) -> dict:
        now = time.time()
        with db.connect(self.db_path) as conn:
            last = conn.execute("SELECT MAX(ts) FROM system_metrics").fetchone()[0]
            stamps = [t for (t,) in conn.execute("SELECT ts FROM system_metrics WHERE ts >= ? ORDER BY ts", (now - 86400,))]
        from . import anomaly

        collector = {"recording": last is not None and now - last < 180, "seconds_since_last_sample": None if last is None else now - last,
                     "hours_recorded_24h": anomaly.recorded_seconds(stamps) / 3600}
        health = self._cached("health", lambda: tools.system_health(24))
        startup = self._cached("startup", lambda: tools.startup_changes(24))
        watch = self._cached("watch", lambda: tools.process_watch(1440))
        disks = self._cached("disks", lambda: tools.disk_forecast(30))
        h = {"available": bool(health.get("available"))}
        if h["available"]:
            open_ = [f for f in health["findings"] if not f["accepted"]]
            h.update(open=len(open_), high=sum(f["severity"] == "high" for f in open_), accepted=health["accepted_count"],
                     realtime_protection=health["defender"].get("realtime_protection"))
        s = {"available": bool(startup.get("available"))}
        if s["available"]:
            s.update(new=startup["new_or_changed_count"], high=startup["high"], baseline=startup["baseline_at"])
        p = {"available": "error" not in watch}
        if p["available"]:
            files = [f for f in watch["suspect_files"] if f["severity"] == "high"]
            net = watch.get("network", {})
            p.update(flagged=len(files) + len(net.get("from_suspicious_files", [])) + len(net.get("suspicious_ports", [])),
                     confidence=watch["confidence"])
        low = min((d for d in disks if "error" not in d), key=lambda d: d["free_gb"], default=None)
        return {"collector": collector, "windows": h, "startup": s, "processes": p,
                "disk": None if low is None else {"name": low["disk"], "free_gb": low["free_gb"]},
                "jobs": self._cached("jobs", lambda: setup_tasks.tasks_status(*self._runner_args()), 20),
                "ollama": self._cached("ollama", lambda: setup_tasks.ollama_status(self.model, self._client_factory), 20),
                "recent_alerts": alerts.recent_log(self.db_path, 5),
                "last_digest": self._last_line("digest.log")}

    def _runner_args(self) -> tuple:
        return (self._task_runner,) if self._task_runner else ()

    def _last_line(self, name: str) -> str:
        try:
            lines = (self.db_path.parent / name).read_text(encoding="utf-8").strip().splitlines()
            return lines[-1] if lines else ""
        except OSError:
            return ""

    def report_html(self, hours: float = 24) -> str:
        hours = max(1.0, min(float(hours), 168.0))
        return self._cached(f"report:{hours}", lambda: report.build_report(hours), 60)

    # ---- findings and accepted risks
    def findings(self) -> dict:
        out = []
        health = tools.system_health(168)
        if health.get("available"):
            out += [{**f, "source": "Windows and Defender"} for f in health["findings"]]
        startup = tools.startup_changes(168)
        if startup.get("available"):
            for x in startup["new_or_changed"] + startup["already_present_but_suspicious"]:
                out.append({"id": x["id"], "source": "Autostart", "severity": x["severity"], "title": x["name"],
                            "detail": f"{x['kind'].replace('_', ' ')}: " + "; ".join(x["reasons"] or ["new entry"]) + f" ({x['command'][:120]})",
                            "accepted": x["accepted"], "accepted_note": x.get("accepted_note", "")})
        watch = tools.process_watch(1440)
        info = []
        if "error" not in watch:
            for f in watch["suspect_files"]:
                info.append({"id": None, "source": "Processes", "severity": f["severity"], "title": f["name"],
                             "detail": "; ".join(f["reasons"]) + f" ({f['exe']})", "accepted": False})
            net = watch.get("network", {})
            for n in net.get("suspicious_ports", []):
                info.append({"id": None, "source": "Processes", "severity": "medium", "title": f"{n['name']} on port {n['port']}",
                             "detail": f"typical of {n['typical_of']}", "accepted": False})
        rank = {"high": 0, "medium": 1, "low": 2}
        out.sort(key=lambda f: (f["accepted"], rank.get(f["severity"], 3), f["title"]))
        return {"findings": out + info, "summary": {"windows": health.get("summary"), "startup": startup.get("summary")},
                "accepted_list": ack.load(self.db_path)}

    def acknowledge(self, finding_id, note) -> dict:
        fid = _clean_id(finding_id)
        note = (note or "")[:300] if isinstance(note, str) else ""
        entry = ack.acknowledge(self.db_path, fid, note)
        self._forget_cache()
        return {"ok": True, "id": fid, **entry}

    def forget(self, finding_id) -> dict:
        ok = ack.forget(self.db_path, _clean_id(finding_id))
        self._forget_cache()
        return {"ok": ok}

    # ---- timeline and games
    def timeline(self, when, minutes) -> dict:
        if not isinstance(when, str) or len(when) > 60:
            raise ApiError("bad time")
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ApiError("bad window") from None
        result = tools.what_happened(when, minutes)
        if "error" in result:
            raise ApiError(result["error"])
        return result

    def games(self) -> dict:
        return {"recordings": tools.game_sessions(30)}

    def game_report(self, name) -> dict:
        r = tools.game_session_report(_clean_id(name, "name"))
        if "error" in r:
            raise ApiError(r["error"])
        return r

    def game_compare(self, before, after) -> dict:
        r = tools.game_sessions_compare(_clean_id(before, "name"), _clean_id(after, "name"))
        if "error" in r:
            raise ApiError(r["error"])
        return r

    # ---- the assistant
    def _client(self):
        if self._client_factory:
            return self._client_factory()
        import ollama

        return ollama.Client()

    def ask(self, session: str, message) -> dict:
        if not isinstance(message, str) or not message.strip():
            raise ApiError("empty message")
        message = message.strip()[:MAX_MESSAGE]
        status = self._cached("ollama", lambda: setup_tasks.ollama_status(self.model, self._client_factory), 20)
        if not status["running"] or not status["model_ready"]:
            raise ApiError(status["hint"] or "the model is not available", 503)
        with self._lock:
            history = self._sessions.setdefault(session, [{"role": "system", "content": chat.SYSTEM_PROMPT}])
            history.append({"role": "user", "content": message})
        used: list[str] = []
        try:
            answer = chat.ask(self._client(), self.model, history, False, 8192, on_tool=lambda n, a: used.append(n))
        except Exception as e:   # noqa: BLE001 - shown to the user instead of a broken page
            raise ApiError(f"the model failed: {type(e).__name__}: {e}"[:300], 502) from None
        with self._lock:
            del history[1:-KEEP_MESSAGES]            # the system prompt stays, the conversation is trimmed
        return {"answer": answer, "tools": used}

    def reset_chat(self, session: str) -> dict:
        with self._lock:
            self._sessions.pop(session, None)
        return {"ok": True}

    # ---- setup
    def setup(self) -> dict:
        self._forget_cache("jobs", "ollama")
        return {"jobs": [{"name": t, "state": s, "what": setup_tasks.DESCRIPTIONS[t]}
                         for t, s in setup_tasks.tasks_status(*self._runner_args()).items()],
                "ollama": setup_tasks.ollama_status(self.model, self._client_factory),
                "data_folder": str(self.db_path.parent), "model": self.model,
                "netstats_command": "pcassist netstats --seconds 60"}

    def job(self, task, action) -> dict:
        if task not in setup_tasks.TASKS or action not in setup_tasks.ACTIONS:
            raise ApiError("unknown job or action")
        ok, text = setup_tasks.change_task(task, action, *self._runner_args())
        self._forget_cache()
        return {"ok": ok, "output": text}

    def test_notification(self) -> dict:
        return {"ok": bool(self._notify("pcassist test", "If you can read this, notifications reach you."))}

    def run_digest(self) -> dict:
        d = digest.run(self.db_path, notify_fn=self._notify, refresh_report=True)
        self._forget_cache()
        return {"title": d["title"], "lines": d["lines"], "todo": d["todo"], "shown": d.get("shown")}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "pcassist"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:   # quiet: the terminal is not a log
        pass

    # -- helpers
    def _hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None, csp: str = "") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict, extra: dict | None = None) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)

    def _cookie_ok(self) -> bool:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "pca" and secrets.compare_digest(v, self.server.token):
                return True
        return False

    def _guard(self, post: bool) -> bool:
        """Host, Origin and token checks. Sends the refusal itself and returns False."""
        if self.headers.get("Host") not in self._hosts():
            self._json(403, {"error": "wrong host"})
            return False
        if post:
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {f"http://{h}" for h in self._hosts()}:
                self._json(403, {"error": "wrong origin"})
                return False
        return True

    def _authorized(self) -> bool:
        if self._cookie_ok():
            return True
        self._json(401, {"error": "not authorized: open the address that `pcassist ui` printed"})
        return False

    # -- routes
    def do_GET(self) -> None:
        if not self._guard(False):
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/":
            token = (query.get("t") or [""])[0]
            if token and secrets.compare_digest(token, self.server.token):    # the first visit: set the cookie, drop the token from the address
                self._send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": f"pca={self.server.token}; HttpOnly; SameSite=Strict; Path=/"})
                return
            if not self._cookie_ok():
                self._send(403, b"Open the address that `pcassist ui` printed in the terminal.", "text/plain; charset=utf-8")
                return
            nonce = secrets.token_urlsafe(12)
            csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; "
                   "frame-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self._send(200, render_page(nonce).encode("utf-8"), "text/html; charset=utf-8", csp=csp)
            return
        if not self._authorized():
            return
        if url.path == "/report":
            try:
                hours = float((query.get("hours") or ["24"])[0])
            except ValueError:
                hours = 24.0
            html = self.server.app.report_html(hours)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                       csp="default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'")
            return
        if url.path.startswith("/api/"):
            self._api("GET", url.path[5:], query, {})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._guard(True) or not self._authorized():
            return
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._json(413, {"error": "request too large"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self._json(400, {"error": "the request body must be a JSON object"})
            return
        self._api("POST", url.path[5:], {}, body)

    def _api(self, method: str, name: str, query: dict, body: dict) -> None:
        app: App = self.server.app
        q = lambda key, default="": (query.get(key) or [default])[0]
        sid = self.server.token[:12]          # one conversation per run of the app
        routes = {
            ("GET", "status"): lambda: app.status(),
            ("GET", "findings"): lambda: app.findings(),
            ("GET", "games"): lambda: app.games(),
            ("GET", "game"): lambda: app.game_report(q("name")),
            ("GET", "compare"): lambda: app.game_compare(q("a"), q("b")),
            ("GET", "timeline"): lambda: app.timeline(q("when", "now"), q("minutes", "30")),
            ("GET", "setup"): lambda: app.setup(),
            ("POST", "ping"): lambda: self.server.touch() or {"ok": True},
            ("POST", "ask"): lambda: app.ask(sid, body.get("message")),
            ("POST", "reset"): lambda: app.reset_chat(sid),
            ("POST", "ack"): lambda: app.acknowledge(body.get("id"), body.get("note")),
            ("POST", "forget"): lambda: app.forget(body.get("id")),
            ("POST", "job"): lambda: app.job(body.get("task"), body.get("action")),
            ("POST", "notify"): lambda: app.test_notification(),
            ("POST", "digest"): lambda: app.run_digest(),
            ("POST", "quit"): lambda: self.server.request_stop() or {"ok": True},
        }
        fn = routes.get((method, name))
        if fn is None:
            self._json(404 if not any(k[1] == name for k in routes) else 405, {"error": "unknown route or method"})
            return
        try:
            self._json(200, fn())
        except ApiError as e:
            self._json(e.code, {"error": str(e)})
        except Exception as e:   # noqa: BLE001 - a failing check must not kill the page
            self._json(500, {"error": f"{type(e).__name__}: {e}"[:300]})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, app: App, token: str, idle_seconds: float = IDLE_SECONDS):
        super().__init__(addr, Handler)
        self.app, self.token, self.idle_seconds = app, token, idle_seconds
        self._last_ping = time.time()
        self._pinged = False
        self._stopping = False

    def touch(self) -> None:
        self._last_ping = time.time()
        self._pinged = True

    def request_stop(self) -> None:
        if not self._stopping:
            self._stopping = True
            threading.Thread(target=self.shutdown, daemon=True).start()

    def watch_idle(self) -> None:
        """Stop when the page has been gone for a while (a closed tab sends no more heartbeats)."""
        while not self._stopping:
            time.sleep(min(5.0, max(0.05, self.idle_seconds / 4)))
            if time.time() - self._last_ping > self.idle_seconds:
                self.request_stop()


def make_server(app: App, token: str | None = None, port: int = 0, idle_seconds: float = IDLE_SECONDS) -> Server:
    return Server(("127.0.0.1", port), app, token or secrets.token_urlsafe(24), idle_seconds)


def serve(db_path, port: int = 8765, model: str = "qwen3:8b", open_browser: bool = True, idle_seconds: float = IDLE_SECONDS,
          ready_json: bool = False) -> None:
    """Run the app. idle_seconds=0 keeps it running until it is told to quit (a desktop shell manages its life);
    ready_json prints one JSON line {url, port, token} for such a shell instead of the human message."""
    app = App(db_path, model)
    server = None
    for p in range(port, port + 20):            # the wanted port may be taken (a second copy is running)
        try:
            server = make_server(app, port=p, idle_seconds=idle_seconds)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"no free port between {port} and {port + 19}")
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={server.token}"
    if ready_json:
        print(json.dumps({"url": url, "port": server.server_address[1], "token": server.token}), flush=True)
    else:
        print(f"pcassist is running at {url}\nClose the browser tab and it stops by itself (or press Ctrl+C).", flush=True)
    if idle_seconds > 0:
        threading.Thread(target=server.watch_idle, daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
