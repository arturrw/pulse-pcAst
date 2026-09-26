"""The local web app: `pulse ui` opens a dashboard in the browser with everything the command line can do.

It is a small server on 127.0.0.1 only. Because a web page can be attacked from other web pages, every request is
checked: the Host must be the local address (DNS-rebinding), a POST's Origin must be this app, and every call needs a
secret token that is random per run (given once in the address, kept in an HttpOnly SameSite=Strict cookie). Pages are
served with a content policy that blocks every script but the app's own, and the app builds its screen with textContent
only, so a hostile process name cannot inject anything. Only a fixed list of actions exists: read the tools, accept or
forget a finding, ask the assistant, install or remove our own three background jobs, add the user to the Performance
Log Users group so games can be recorded (Windows asks for confirmation), and - the one setting this app ever writes on
the user's behalf - apply or revert one CS2 video setting, always from an explicit button click, never from the
assistant's own tool call. Everything else is read-only.
The server stops when the browser tab has been closed for a few minutes."""
import json
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import ack, alerts, autorec, chat, chatstore, cs2settings, db, digest, explain, gamelib, games, paths, report, settings, setup_tasks, tools
from .collectors import LiveSampler
from .webui_page import render_page

MAX_BODY = 16 * 1024
MAX_MESSAGE = 2000
ANOMALY_TEXT = {"ram_percent": ("Memory use", "%"), "ram_used_mb": ("Memory in use", " MB"), "swap_percent": ("Swap file use", "%"),
                "gpu_temp_c": ("Graphics card temperature", " °C")}
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

    def __init__(self, db_path, model: str = "qwen3:8b", client_factory=None, task_runner=None, notify_fn=None, launcher=None):
        self.db_path = Path(db_path)
        self.model = model
        self._client_factory = client_factory
        self._task_runner = task_runner
        self._notify = notify_fn or alerts.notify
        self._launch = launcher or (lambda cmd: subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        self.gamelist = gamelib.GameList(self.db_path.parent)
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self._refreshing: set[str] = set()
        self._sessions: dict[str, list] = {}       # what the model sees, per chat
        self.chats = chatstore.ChatStore(self.db_path.parent / "chats.json")
        self._current: str | None = None            # the chat a request without an id continues
        self._live = LiveSampler()                   # started now, so the first reading already spans a real interval
        tools.set_db(self.db_path)

    # ---- small cache: the Windows checks take a couple of seconds each
    def _cached(self, key: str, fn, ttl: float = STATUS_TTL):
        """Stale-while-revalidate: an old value is returned at once and refreshed in the background, so only the very
        first call (or the first after the cache was cleared) waits for the slow Windows checks."""
        with self._lock:
            hit = self._cache.get(key)
            if hit:
                if time.time() - hit[0] >= ttl and key not in self._refreshing:
                    self._refreshing.add(key)
                    threading.Thread(target=self._refresh, args=(key, fn), daemon=True).start()
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (time.time(), value)
        return value

    def _refresh(self, key: str, fn) -> None:
        try:
            value = fn()
            with self._lock:
                self._cache[key] = (time.time(), value)
        except Exception:   # noqa: BLE001 - keep the old value, try again next time
            pass
        finally:
            with self._lock:
                self._refreshing.discard(key)

    def warm(self) -> None:
        """Fill the caches before the first page asks (run in the background at start)."""
        for fn in (self.status, self.findings, self.setup):
            try:
                fn()
            except Exception:   # noqa: BLE001
                pass

    def _mark_accepted(self, fid: str, accepted: bool, note: str = "") -> None:
        """Reflect an accept / forget in the cached checks right away and refresh them in the background, so the page never
        waits for the slow Windows checks and never shows the old state."""
        with self._lock:
            for key in ("health", "health168", "startup", "startup168"):
                hit = self._cache.get(key)
                if not hit:
                    continue
                value = hit[1]
                rows = list(value.get("findings", [])) + list(value.get("new_or_changed", [])) + list(value.get("already_present_but_suspicious", []))
                for r in rows:
                    if r.get("id") == fid:
                        r["accepted"], r["accepted_note"] = accepted, note
                self._cache[key] = (0.0, value)         # stale: the next read refreshes it in the background

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

    def live(self) -> dict:
        """Load right now, measured by this server itself (not from the database), for the Overview's live row."""
        with self._lock:
            return self._live.sample()

    def _runner_args(self) -> tuple:
        return (self._task_runner,) if self._task_runner else ()

    def _last_line(self, name: str) -> str:
        try:
            lines = (self.db_path.parent / name).read_text(encoding="utf-8").strip().splitlines()
            return lines[-1] if lines else ""
        except OSError:
            return ""

    def anomalies(self, hours=24) -> dict:
        """The unusual stretches of the state metrics, one entry per moment, with a ready question for the assistant."""
        try:
            hours = max(1.0, min(float(hours), 168.0))
        except (TypeError, ValueError):
            hours = 24.0
        return self._cached(f"anomalies:{hours}", lambda: self._anomalies(hours), 60)

    def _anomalies(self, hours: float) -> dict:
        now = time.time()
        events = []
        for metric, r in report.state_anomalies(hours).items():
            for e in ([] if "error" in r else r["events"]):
                label, unit = ANOMALY_TEXT[metric]
                events.append({"metric": metric, "label": label, "start": now - e["minutes_ago"] * 60, "started": e["started"],
                               "minutes": e["duration_minutes"], "game": e["during_game"],
                               "text": f"{label}: usually about {e['typical_value']:.0f}{unit}, up to {e['most_unusual_value']:.0f}{unit}"})
        events.sort(key=lambda e: e["start"])
        moments: list[list[dict]] = []
        for e in events:                                   # memory and swap often move together: one moment, not three rows
            if moments and e["start"] - moments[-1][0]["start"] <= 600:
                moments[-1].append(e)
            else:
                moments.append([e])
        periods = []
        for m in moments:
            first, minutes = m[0], max(e["minutes"] for e in m)
            game = next((e["game"] for e in m if e["game"]), None)
            what = " and ".join(dict.fromkeys(e["label"] for e in m if e["metric"] != "ram_used_mb" or len(m) == 1))
            details = "; ".join(e["text"] for e in m)
            question = (f"At {first['started']} the PC showed something unusual for about {minutes:.0f} minutes: {details}. "
                        + (f"A game session ({game}) was recorded around then, so it may just be the game. " if game else
                           "I was not doing anything unusual on the PC then. ")
                        + f"Check what was going on at that time (use what_happened for {first['started']}), tell me what could explain it "
                          "and whether I should worry. Say plainly what you could not find out.")
            periods.append({"title": what or first["label"], "started": first["started"], "minutes": round(minutes), "start_ts": first["start"],
                            "end_ts": first["start"] + minutes * 60, "details": [e["text"] for e in m], "during_game": game, "question": question})
        return {"hours": hours, "periods": periods[::-1]}

    def report_html(self, hours: float = 24) -> str:
        hours = max(1.0, min(float(hours), 168.0))
        return self._cached(f"report:{hours}", lambda: report.build_report(hours), 60)

    # ---- findings and accepted risks
    def findings(self) -> dict:
        out = []
        health = self._cached("health168", lambda: tools.system_health(168))
        if health.get("available"):
            out += [{**f, "source": "Windows and Defender"} for f in health["findings"]]
        startup = self._cached("startup168", lambda: tools.startup_changes(168))
        if startup.get("available"):
            for x in startup["new_or_changed"] + startup["already_present_but_suspicious"]:
                out.append({"id": x["id"], "source": "Autostart", "severity": x["severity"], "title": x["name"],
                            "detail": f"{x['kind'].replace('_', ' ')}: " + "; ".join(x["reasons"] or ["new entry"]) + f" ({x['command'][:120]})",
                            "accepted": x["accepted"], "accepted_note": x.get("accepted_note", "")})
        watch = self._cached("watch", lambda: tools.process_watch(1440))
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
        allf = [{**f, "explain": explain.finding(f)} for f in out + info]
        return {"findings": allf, "summary": {"windows": health.get("summary"), "startup": startup.get("summary")},
                "accepted_list": ack.load(self.db_path)}

    def acknowledge(self, finding_id, note) -> dict:
        fid = _clean_id(finding_id)
        note = (note or "")[:300] if isinstance(note, str) else ""
        entry = ack.acknowledge(self.db_path, fid, note)
        self._mark_accepted(fid, True, note)
        return {"ok": True, "id": fid, **entry}

    def forget(self, finding_id) -> dict:
        fid = _clean_id(finding_id)
        ok = ack.forget(self.db_path, fid)
        self._mark_accepted(fid, False)
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
        result["timeline"] = [{**e, "explain": explain.event(e["type"], e["text"])} for e in result["timeline"]]
        result["heaviest_processes"] = [{**p, "explain": explain.process(p["name"])} for p in result["heaviest_processes"]]
        result["metric_info"] = {k: explain.metric(k) for k in result["metrics"]}
        return result

    def games(self) -> dict:
        """The library: every recording, and the games they belong to (newest played first)."""
        recs = tools.game_sessions(1000)
        good = [r for r in recs if "error" not in r]
        library: dict[str, dict] = {}
        for r in good:                                   # newest first, so the first one seen is the last played
            g = library.setdefault(r["game"].lower(), {"id": r["game"], "name": tools.game_title(r["game"]), "sessions": 0,
                                                       "runs": 0, "last": r["recorded"], "tracked": False})
            g["runs" if r["kind"] == "benchmark run" else "sessions"] += 1
        for t in self.gamelist.load():                   # games the user added: shown even before the first recording
            g = library.get(t["process"].lower())
            if g is None:
                library[t["process"].lower()] = {"id": t["process"], "name": t["title"] or tools.game_title(t["process"]),
                                                 "sessions": 0, "runs": 0, "last": None, "tracked": True}
            else:
                g["tracked"] = True
                if t["title"]:
                    g["name"] = t["title"]
        return {"recordings": recs, "games": list(library.values()), "presentmon": gamelib.presentmon_exe() is not None}

    def programs(self) -> dict:
        """Programs running now, to pick a game from."""
        return {"programs": gamelib.running_programs(), "presentmon": gamelib.presentmon_exe() is not None}

    def game_add(self, process, title=None) -> dict:
        try:
            entry = self.gamelist.add(process, (title or "").strip() if isinstance(title, str) else "")
        except ValueError as e:
            raise ApiError(str(e)) from None
        return {"ok": True, "game": entry}

    def game_remove(self, process) -> dict:
        try:
            removed = self.gamelist.remove(process)
        except ValueError as e:
            raise ApiError(str(e)) from None
        if not removed:
            raise ApiError("this game is not in your list (games with recordings stay in the library)", 404)
        return {"ok": True}

    def game_record(self, process) -> dict:
        """Start PresentMon for a game (Windows asks for administrator rights); it stops by itself when the game closes."""
        try:
            proc = gamelib.valid_process(process)
        except ValueError as e:
            raise ApiError(str(e)) from None
        if gamelib.presentmon_exe() is None:
            raise ApiError("PresentMon is not installed. Download PresentMon-...-x64.exe from "
                           "https://github.com/GameTechDev/PresentMon/releases and put it in " + str(gamelib.presentmon_folder()), 409)
        script = paths.resource("scripts", "record_presentmon.ps1")
        if not script.exists():
            raise ApiError("the recording script was not found", 500)
        self.gamelist.add(proc)
        try:
            self._launch(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                          "-Process", proc, "-DataDir", str(self.db_path.parent)])
        except OSError as e:
            raise ApiError(f"could not start the recorder: {e}", 500) from None
        return {"ok": True, "message": "Allow the administrator prompt, then start the game. The recording stops when you close it "
                                       "and shows up here."}

    def game_batch(self, game, batch) -> dict:
        """One batch of benchmark runs: every variant with its repeats averaged, and what it did against the reference."""
        game, batch = _clean_id(game, "game"), _clean_id(batch, "batch")
        runs = [r for r in tools.game_sessions(1000) if "error" not in r and r["game"].lower() == game.lower() and r["kind"] == "benchmark run"
                and r.get("batch") == batch]
        if not runs:
            raise ApiError("no such batch", 404)
        groups: dict[str, list[dict]] = {}
        for r in sorted(runs, key=lambda r: r["name"]):
            try:
                groups.setdefault(r["variant"], []).append(tools.run_stats(r["name"]))
            except (OSError, ValueError):
                continue                                  # an unreadable run is left out, the others still count
        rows = games.variant_table(groups)
        if not rows:
            raise ApiError("these runs could not be read", 422)
        ref = rows[0]
        for r in rows:
            r.update(explain.variant_verdict(r, ref))
        return {"batch": batch, "reference": ref["variant"], "variants": tools._round_deep(rows),
                "note": "Each variant is the average of its repeats. Differences smaller than the spread between repeats are noise."}

    def game_report(self, name) -> dict:
        r = tools.game_session_report(_clean_id(name, "name"))
        if "error" in r:
            raise ApiError(r["error"])
        return {"name": r["name"], **explain.game_summary(r)}

    def game_compare(self, before, after) -> dict:
        r = tools.game_sessions_compare(_clean_id(before, "name"), _clean_id(after, "name"))
        if "error" in r:
            raise ApiError(r["error"])
        return {"before": r["before"], "after": r["after"], **explain.game_compare_text(r)}

    # ---- CS2 video settings: the only setting this app ever writes, and only from a button click
    def cs2_settings(self) -> dict:
        return cs2settings.read_settings()

    def cs2_apply(self, key, value) -> dict:
        try:
            return cs2settings.apply_setting(_clean_id(key, "key"), _clean_id(str(value), "value"),
                                             self.db_path.parent / "cs2_backups")
        except cs2settings.Cs2Error as e:
            raise ApiError(str(e)) from None

    def cs2_revert(self, backup) -> dict:
        try:
            return cs2settings.revert(self.db_path.parent / "cs2_backups", _clean_id(backup, "backup"))
        except cs2settings.Cs2Error as e:
            raise ApiError(str(e)) from None

    # ---- the assistant
    def _client(self):
        if self._client_factory:
            return self._client_factory()
        import ollama

        return ollama.Client()

    def _history(self, cid: str) -> list:
        """The model's context of a chat: kept in memory, rebuilt from the saved text after a restart."""
        if cid not in self._sessions:
            past = [{"role": "user" if m["who"] == "me" else "assistant", "content": m["text"]}
                    for m in (self.chats.get(cid) or {"log": []})["log"]]
            self._sessions[cid] = [{"role": "system", "content": chat.SYSTEM_PROMPT}] + past[-KEEP_MESSAGES:]
        return self._sessions[cid]

    def ask(self, session: str, message, chat_id=None) -> dict:
        if not isinstance(message, str) or not message.strip():
            raise ApiError("empty message")
        message = message.strip()[:MAX_MESSAGE]
        if chat_id is not None and not (isinstance(chat_id, str) and self.chats.exists(chat_id)):
            raise ApiError("no such chat", 404)
        status = self._cached("ollama", lambda: setup_tasks.ollama_status(self.model, self._client_factory), 20)
        if not status["running"] or not status["model_ready"]:
            raise ApiError(status["hint"] or "the model is not available", 503)
        with self._lock:
            cid = chat_id or (self._current if self._current and self.chats.exists(self._current) else None)
        if cid is None:
            cid = self.chats.create()
        with self._lock:
            self._current = cid
            history = self._history(cid)
            history.append({"role": "user", "content": message})
        title = self.chats.add(cid, "me", message)
        used: list[str] = []
        turn_start = len(history)
        try:
            answer = chat.ask(self._client(), self.model, history, False, 8192, on_tool=lambda n, a: used.append(n))
        except Exception as e:   # noqa: BLE001 - shown to the user instead of a broken page
            raise ApiError(f"the model failed: {type(e).__name__}: {e}"[:300], 502) from None
        proposed = None   # the last propose_cs2_setting call this turn that did not itself error
        for m in history[turn_start:]:
            if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_name") == "propose_cs2_setting":
                try:
                    payload = json.loads(m["content"])
                except ValueError:
                    continue
                if "error" not in payload:
                    proposed = payload
        with self._lock:
            del history[1:-KEEP_MESSAGES]            # the system prompt stays, the conversation is trimmed
        self.chats.add(cid, "bot", answer, used)
        result = {"answer": answer, "tools": used, "chat": cid, "title": title}
        if proposed is not None:
            result["proposed_action"] = {"kind": "cs2_setting", **proposed}
        return result

    def reset_chat(self, session: str) -> dict:
        """A request without a chat id starts a new conversation next time; the old one stays in the list."""
        with self._lock:
            self._current = None
        return {"ok": True}

    def chat_list(self) -> dict:
        return {"chats": self.chats.listing()}

    def chat_open(self, chat_id) -> dict:
        c = self.chats.get(chat_id) if isinstance(chat_id, str) else None
        if c is None:
            raise ApiError("no such chat", 404)
        with self._lock:
            self._current = c["id"]
        return c

    def chat_delete(self, chat_id) -> dict:
        if not (isinstance(chat_id, str) and self.chats.delete(chat_id)):
            raise ApiError("no such chat", 404)
        with self._lock:
            self._sessions.pop(chat_id, None)
            if self._current == chat_id:
                self._current = None
        return {"ok": True}

    # ---- setup
    def setup(self) -> dict:
        jobs = self._cached("jobs", lambda: setup_tasks.tasks_status(*self._runner_args()), 20)
        return {"jobs": [{"name": t, "state": s, "what": setup_tasks.DESCRIPTIONS[t]} for t, s in jobs.items()],
                "ollama": self._cached("ollama", lambda: setup_tasks.ollama_status(self.model, self._client_factory), 20),
                "settings": settings.load(self.db_path.parent), "choices": {k: list(v) for k, v in settings.CHOICES.items()},
                "data_folder": str(self.db_path.parent), "model": self.model,
                "netstats_command": "pulse netstats --seconds 60",
                "autorec": {"presentmon": gamelib.presentmon_exe() is not None,
                            "in_group": self._cached("perf_group", autorec.in_perf_log_users, 20),
                            "games": [g["process"] for g in self.gamelist.load()],
                            "state": autorec.read_state(self.db_path.parent)}}

    def autorec_join(self) -> dict:
        """Add this user to Performance Log Users (Windows asks for administrator confirmation)."""
        sid = autorec.user_sid()
        if sid is None:
            raise ApiError("could not read your Windows user id", 500)
        try:
            self._launch(autorec.join_command(sid))
        except OSError as e:
            raise ApiError(f"could not start PowerShell: {e}", 500) from None
        self._forget_cache("perf_group")
        return {"ok": True, "message": "Confirm the Windows prompt, then sign out of Windows and back in: the new group "
                                       "only applies to a fresh sign-in."}

    def job(self, task, action, values=None) -> dict:
        if task not in setup_tasks.TASKS or action not in setup_tasks.ACTIONS:
            raise ApiError("unknown job or action")
        if values:
            self.save_settings(values, reinstall=False)
        ok, text = setup_tasks.change_task(task, action, *self._runner_args(), cfg=settings.load(self.db_path.parent))
        self._forget_cache()
        return {"ok": ok, "output": text}

    SCHEDULE_KEYS = {"collect": "collect_interval", "alerts": "alerts_interval", "digest": "digest_time"}

    def save_settings(self, values, reinstall: bool = True) -> dict:
        """Store the user's choices; a job that is installed and whose schedule changed is installed again with the new one."""
        if not isinstance(values, dict):
            raise ApiError("bad settings")
        try:
            clean = settings.clean(values)
        except ValueError as e:
            raise ApiError(str(e)) from None
        before = settings.load(self.db_path.parent)
        after = settings.save(self.db_path.parent, clean)
        problems = []
        if reinstall:
            state = setup_tasks.tasks_status(*self._runner_args())
            for task, key in self.SCHEDULE_KEYS.items():
                if state.get(task) and before[key] != after[key]:
                    ok, text = setup_tasks.change_task(task, "install", *self._runner_args(), cfg=after)
                    if not ok:
                        problems.append(f"{task}: {text}")
            self._forget_cache("jobs")
        if problems:
            raise ApiError("saved, but could not update the schedule: " + "; ".join(problems)[:300], 500)
        return {"ok": True, "settings": after}

    def test_notification(self) -> dict:
        return {"ok": bool(alerts.send(self._notify, "pulse test", "If you can read this, notifications reach you.", "setup"))}

    def run_digest(self) -> dict:
        d = digest.run(self.db_path, notify_fn=self._notify, refresh_report=True)
        self._forget_cache()
        return {"title": d["title"], "lines": d["lines"], "todo": d["todo"], "shown": d.get("shown")}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "pulse"
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
        self._json(401, {"error": "not authorized: open the address that `pulse ui` printed"})
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
                self._send(403, b"Open the address that `pulse ui` printed in the terminal.", "text/plain; charset=utf-8")
                return
            nonce = secrets.token_urlsafe(12)
            csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self' ipc: http://ipc.localhost; "
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
            if (query.get("embed") or [""])[0] == "1":
                html = html.replace("<body>", "<body class='embed'>", 1)     # shown inside the app page
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
            ("GET", "live"): lambda: app.live(),
            ("GET", "findings"): lambda: app.findings(),
            ("GET", "games"): lambda: app.games(),
            ("GET", "game"): lambda: app.game_report(q("name")),
            ("GET", "compare"): lambda: app.game_compare(q("a"), q("b")),
            ("GET", "batch"): lambda: app.game_batch(q("game"), q("batch")),
            ("GET", "programs"): lambda: app.programs(),
            ("GET", "cs2_settings"): lambda: app.cs2_settings(),
            ("POST", "cs2_apply"): lambda: app.cs2_apply(body.get("key"), body.get("value")),
            ("POST", "cs2_revert"): lambda: app.cs2_revert(body.get("backup")),
            ("GET", "anomalies"): lambda: app.anomalies(q("hours", "24")),
            ("POST", "game_add"): lambda: app.game_add(body.get("process"), body.get("title")),
            ("POST", "game_remove"): lambda: app.game_remove(body.get("process")),
            ("POST", "game_record"): lambda: app.game_record(body.get("process")),
            ("GET", "timeline"): lambda: app.timeline(q("when", "now"), q("minutes", "30")),
            ("GET", "setup"): lambda: app.setup(),
            ("POST", "ping"): lambda: self.server.touch() or {"ok": True},
            ("GET", "chats"): lambda: app.chat_list(),
            ("GET", "chat"): lambda: app.chat_open(q("id")),
            ("POST", "ask"): lambda: app.ask(sid, body.get("message"), body.get("chat")),
            ("POST", "chat_delete"): lambda: app.chat_delete(body.get("id")),
            ("POST", "reset"): lambda: app.reset_chat(sid),
            ("POST", "ack"): lambda: app.acknowledge(body.get("id"), body.get("note")),
            ("POST", "forget"): lambda: app.forget(body.get("id")),
            ("POST", "job"): lambda: app.job(body.get("task"), body.get("action"), body.get("settings")),
            ("POST", "settings"): lambda: app.save_settings(body.get("settings")),
            ("POST", "notify"): lambda: app.test_notification(),
            ("POST", "autorec_join"): lambda: app.autorec_join(),
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
    allow_reuse_address = False      # on Windows this would let a second copy bind the same port and share it with the first

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
        print(f"pulse is running at {url}\nClose the browser tab and it stops by itself (or press Ctrl+C).", flush=True)
    threading.Thread(target=app.warm, daemon=True).start()
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
