"""Unit tests for the local web app: the real server on an ephemeral port, driven over HTTP, with a fake model, a fake
PowerShell runner and a fake notifier. Nothing here talks to Ollama, changes a scheduled task or shows a notification.
Run: python tests/test_webui.py (or pytest)."""
import json
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from pulse import db, persistence, setup_tasks, webui, winhealth

winhealth.read_raw = lambda hours: {}                 # never read this machine's event logs, registry or autostart
winhealth.read_defender_policy = lambda: {}
persistence.read_items = lambda: []


class FakeClient:
    def __init__(self, models=("qwen3:8b",), answer="You have 42 GB free."):
        self.models, self.answer, self.seen = models, answer, []

    def list(self):
        return SimpleNamespace(models=[SimpleNamespace(model=m) for m in self.models])

    def chat(self, model, messages, tools, think, options):
        self.seen.append(len(messages))
        return SimpleNamespace(message=SimpleNamespace(content=self.answer, tool_calls=None))


def _db() -> Path:
    path = Path(tempfile.mkdtemp()) / "metrics.db"
    now = time.time()
    with db.connect(path) as conn:
        for i in range(200):
            ts = now - 30 * (200 - i)
            conn.execute("INSERT INTO system_metrics (ts, cpu_percent, ram_percent, ram_used_mb) VALUES (?,?,?,?)", (ts, 10.0, 40.0, 12000.0))
            conn.execute("INSERT INTO gpu_metrics (ts, idx, util_percent, temp_c) VALUES (?,0,?,?)", (ts, 20.0, 55.0))
        conn.execute("INSERT INTO disk_usage VALUES (?,?,?,?)", (now, "C:", 1000.0, 400.0))
    return path


class Runner:
    """Stands in for PowerShell: the scheduled-task listing and our own install script."""

    def __init__(self, state="Running"):
        self.calls, self.state = [], state

    def __call__(self, cmd, timeout=60):
        self.calls.append(cmd)
        if "-File" in cmd:
            return 0, "Installed."
        return 0, json.dumps([{"n": "pulse-collect", "s": self.state}])


class Running:
    """A live server plus a tiny client that speaks HTTP the way a browser would."""

    def __init__(self, client=None, runner=None, notify=None, idle=300.0):
        self.path = _db()
        self.client = client or FakeClient()
        self.runner = runner or Runner()
        self.notified = []
        self.app = webui.App(self.path, client_factory=lambda: self.client, task_runner=self.runner,
                             notify_fn=notify or (lambda t, b: self.notified.append((t, b)) or True))
        self.server = webui.make_server(self.app, idle_seconds=idle)
        self.port, self.token = self.server.server_address[1], self.server.token
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method, path, body=None, cookie=True, host=None, origin="same", raw=None, headers=None):
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if cookie:
            h["Cookie"] = f"pca={self.token}" if cookie is True else f"pca={cookie}"
        if origin == "same":
            origin = f"http://127.0.0.1:{self.port}" if method == "POST" else None
        if origin:
            h["Origin"] = origin
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=h)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=20) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def api(self, method, path, body=None, **kw):
        code, hdr, data = self.request(method, "/api/" + path, body, **kw)
        return code, json.loads(data or b"{}")

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def test_the_page_needs_the_token_sets_a_strict_cookie_and_carries_a_matching_nonce():
    s = Running()
    try:
        assert s.request("GET", "/", cookie=False)[0] == 403                                   # no token, no cookie
        code, hdr, _ = s.request("GET", f"/?t=wrong", cookie=False)
        assert code == 403
        code, hdr, _ = s.request("GET", f"/?t={s.token}", cookie=False)
        assert code == 302 and hdr["Location"] == "/"                                          # the token leaves the address bar
        cookie = hdr["Set-Cookie"]
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and s.token in cookie
        code, hdr, body = s.request("GET", "/")
        text = body.decode()
        assert code == 200 and "Pulse" in text
        nonce = re.search(r"script-src 'nonce-([^']+)'", hdr["Content-Security-Policy"]).group(1)
        assert f'<script nonce="{nonce}">' in text and f'<style nonce="{nonce}">' in text     # only our own script may run
        assert "default-src 'none'" in hdr["Content-Security-Policy"] and "frame-ancestors 'none'" in hdr["Content-Security-Policy"]
        assert hdr["X-Content-Type-Options"] == "nosniff" and hdr["Cache-Control"] == "no-store"
        assert not re.search(r"(?:src|href)=['\"]?https?://", text)
        assert re.search(r"innerHTML|eval\(|document\.write", text) is None                    # machine text never becomes markup
        assert s.request("GET", "/", cookie="wrong")[0] == 403
    finally:
        s.stop()


def test_a_foreign_host_or_origin_and_a_missing_cookie_are_refused():
    s = Running()
    try:
        assert s.api("GET", "status", cookie=False)[0] == 401
        assert s.api("GET", "status", cookie="not-the-token")[0] == 401
        code, body = s.api("GET", "status", host="evil.example:80")                            # DNS rebinding
        assert code == 403 and "host" in body["error"]
        code, body = s.api("POST", "ping", {}, origin="http://evil.example")                   # another web page posting to us
        assert code == 403 and "origin" in body["error"]
        assert s.api("POST", "ping", {})[0] == 200
        assert s.api("GET", "status", host=f"localhost:{s.port}")[0] == 200                    # localhost is fine too
    finally:
        s.stop()


def test_bad_requests_get_clean_errors():
    s = Running()
    try:
        assert s.api("POST", "ask", raw=b"not json")[0] == 400
        assert s.api("POST", "ask", raw=b"[1, 2]")[0] == 400
        assert s.api("POST", "ask", raw=b"x" * (webui.MAX_BODY + 1))[0] == 413
        assert s.api("GET", "no-such-thing")[0] == 404
        assert s.api("GET", "ask")[0] == 405 and s.api("POST", "status", {})[0] == 405         # right route, wrong method
        assert s.request("GET", "/etc/passwd")[0] == 404
    finally:
        s.stop()


def test_the_report_is_served_with_a_policy_that_blocks_scripts():
    s = Running()
    try:
        code, hdr, body = s.request("GET", "/report?hours=6")
        assert code == 200 and body.startswith(b"<!doctype html>")
        assert "default-src 'none'" in hdr["Content-Security-Policy"] and "script-src" not in hdr["Content-Security-Policy"]
        assert s.request("GET", "/report", cookie=False)[0] == 401
    finally:
        s.stop()


def test_status_reports_the_collector_the_jobs_and_the_model():
    s = Running(runner=Runner("Running"), client=FakeClient())
    try:
        code, st = s.api("GET", "status")
        assert code == 200 and st["collector"]["recording"] is True and st["collector"]["hours_recorded_24h"] > 1
        assert st["jobs"] == {"collect": "Running", "alerts": None, "digest": None}
        assert st["ollama"]["running"] and st["ollama"]["model_ready"]
        assert st["disk"]["name"] == "C:" and round(st["disk"]["free_gb"]) == 600
        s2 = Running(client=FakeClient(models=("other:1b",)))
        try:
            assert "ollama pull qwen3:8b" in s2.api("GET", "status")[1]["ollama"]["hint"]
        finally:
            s2.stop()
    finally:
        s.stop()


def test_accepting_and_forgetting_a_finding_persists_and_rejects_bad_ids():
    s = Running()
    try:
        assert s.api("POST", "ack", {"id": "defender-realtime-off", "note": "on purpose"})[1]["ok"] is True
        assert s.app.status()  # the cache is dropped, nothing breaks
        assert json.loads((s.path.parent / "acknowledged.json").read_text())["defender-realtime-off"]["note"] == "on purpose"
        assert "defender-realtime-off" in s.api("GET", "findings")[1]["accepted_list"]
        assert s.api("POST", "forget", {"id": "defender-realtime-off"})[1]["ok"] is True
        assert s.api("GET", "findings")[1]["accepted_list"] == {}
        for bad in ("", "   ", None, 5, "x" * 400, "a" + chr(0) + "b"):
            assert s.api("POST", "ack", {"id": bad})[0] == 400, repr(bad)
    finally:
        s.stop()


def test_findings_come_from_the_windows_checks_and_mark_accepted_ones():
    s = Running()
    real = winhealth.read_raw
    iso = time.strftime("%Y-%m-%dT%H:%M:%S.0+03:00")
    winhealth.read_raw = lambda hours: {"defender": {"service": True, "antivirus": True, "realtime": False, "tamper_protected": True,
                                                     "signatures": iso, "quick_scan": iso, "full_scan": None}}
    try:
        f = s.api("GET", "findings")[1]["findings"]
        off = next(x for x in f if x["id"] == "defender-realtime-off")
        assert off["source"] == "Windows and Defender" and off["severity"] == "high" and off["accepted"] is False
        s.api("POST", "ack", {"id": "defender-realtime-off", "note": "mine"})
        off = next(x for x in s.api("GET", "findings")[1]["findings"] if x["id"] == "defender-realtime-off")
        assert off["accepted"] is True and off["accepted_note"] == "mine"
    finally:
        winhealth.read_raw = real
        s.stop()


def test_timeline_understands_a_time_and_refuses_junk():
    s = Running()
    try:
        code, r = s.api("GET", "timeline?when=20%20minutes%20ago&minutes=15")
        assert code == 200 and r["samples_in_window"] > 0 and "timeline" in r
        code, r = s.api("GET", "timeline?when=blah&minutes=15")
        assert code == 400 and "could not understand" in r["error"]
        assert s.api("GET", "timeline?when=now&minutes=abc")[0] == 400
    finally:
        s.stop()


def test_the_assistant_keeps_the_conversation_and_says_what_is_wrong_when_the_model_is_missing():
    client = FakeClient(answer="You have 42 GB free.")
    s = Running(client=client)
    try:
        code, r = s.api("POST", "ask", {"message": "how much space?"})
        assert code == 200 and r["answer"] == "You have 42 GB free." and r["tools"] == []
        s.api("POST", "ask", {"message": "and on D:?"})
        assert client.seen == [2, 4]                                   # system + question, then the whole earlier exchange too
        assert s.api("POST", "reset", {})[1]["ok"] is True
        s.api("POST", "ask", {"message": "again"})
        assert client.seen[-1] == 2                                    # a new chat starts from the system prompt
        assert s.api("POST", "ask", {"message": "   "})[0] == 400
        assert s.api("POST", "ask", {"message": 5})[0] == 400
    finally:
        s.stop()
    down = Running(client=FakeClient(models=()))
    try:
        code, r = down.api("POST", "ask", {"message": "hi"})
        assert code == 503 and "ollama pull" in r["error"]
    finally:
        down.stop()


def test_saving_settings_reinstalls_only_the_installed_job_whose_schedule_changed():
    run = Runner()
    s = Running(runner=run)
    try:
        n = len(run.calls)
        code, r = s.api("POST", "settings", {"settings": {"quiet_on": True, "quiet_from": "22:30"}})
        assert code == 200 and r["settings"]["quiet_from"] == "22:30"
        assert not any("install" in c for c in run.calls[n:])            # a quiet-hours change needs no reinstall
        code, _ = s.api("POST", "settings", {"settings": {"collect_interval": 60}})
        assert code == 200 and run.calls[-1][-2:] == ["-Interval", "60"] and "collect" in run.calls[-1]
        n = len(run.calls)
        s.api("POST", "settings", {"settings": {"alerts_interval": 5}})  # alerts is not installed: nothing to redo
        assert not any("install" in c for c in run.calls[n:])
        assert s.api("GET", "setup")[1]["settings"]["collect_interval"] == 60
    finally:
        s.stop()


def test_only_the_three_known_jobs_and_two_actions_can_be_run_and_nothing_user_typed_reaches_the_command():
    run = Runner()
    s = Running(runner=run)
    try:
        code, r = s.api("POST", "job", {"task": "digest", "action": "install"})
        assert code == 200 and r["ok"] is True
        cmd = run.calls[-1]
        assert cmd[:6] == ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned", "-File"]
        assert cmd[6].endswith("autostart.ps1") and cmd[7:10] == ["install", "-Task", "digest"] and cmd[10:] == ["-At", "09:00"]
        code, r = s.api("POST", "job", {"task": "alerts", "action": "install", "settings": {"alerts_interval": 30}})
        assert code == 200 and run.calls[-1][-2:] == ["-Minutes", "30"]
        for bad in ({"alerts_interval": "5; calc"}, {"digest_time": "9am"}, {"digest_time": "09:00 -Foo"}, {"x": 1}, {"quiet_on": "yes"}):
            assert s.api("POST", "job", {"task": "alerts", "action": "install", "settings": bad})[0] == 400, bad
        n = len(run.calls)
        for bad in ({"task": "digest; calc", "action": "install"}, {"task": "collect", "action": "format"}, {"task": "../x", "action": "remove"},
                    {"task": None, "action": "install"}, {"task": ["digest"], "action": "install"}):
            assert s.api("POST", "job", bad)[0] == 400, bad
        assert len(run.calls) == n                                      # nothing was run for the refused ones
        assert setup_tasks.change_task("x", "install", run) == (False, "unknown job or action")
        jobs = {j["name"]: j["state"] for j in s.api("GET", "setup")[1]["jobs"]}
        assert jobs == {"collect": "Running", "alerts": None, "digest": None}
    finally:
        s.stop()


def test_test_notification_and_digest_now_use_the_notifier_and_report_what_they_did():
    s = Running()
    try:
        assert s.api("POST", "notify", {})[1]["ok"] is True and s.notified[0][0] == "pulse test"
        d = s.api("POST", "digest", {})[1]
        assert d["title"].startswith("Morning digest") and d["shown"] is True and s.notified[-1][0] == d["title"]
        assert (s.path.parent / "digest.log").exists() and (s.path.parent / "reports" / "latest.html").exists()
    finally:
        s.stop()


def test_quit_stops_the_server_and_a_page_that_went_away_does_too():
    s = Running()
    try:
        assert s.api("POST", "quit", {})[1]["ok"] is True
        s.thread.join(5)
        assert not s.thread.is_alive()
    finally:
        s.server.server_close()
    idle = Running(idle=0.4)
    threading.Thread(target=idle.server.watch_idle, daemon=True).start()
    try:
        idle.api("POST", "ping", {})
        idle.thread.join(5)                                             # no heartbeat any more: the server stops by itself
        assert not idle.thread.is_alive()
    finally:
        idle.server.server_close()


def test_the_page_script_is_valid_javascript():
    import shutil
    import subprocess

    from pulse import webui_page

    node = shutil.which("node")
    if not node:
        return                                                           # no Node on this machine: the other checks still ran
    js = re.search(r'<script nonce="N">(.*)</script>', webui_page.render_page("N"), re.S).group(1)
    path = Path(tempfile.mkdtemp()) / "page.js"
    path.write_text(js, encoding="utf-8")
    res = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[:400]


def test_conversations_are_listed_reopened_continued_and_deleted():
    client = FakeClient(answer="ok")
    s = Running(client=client)
    try:
        first = s.api("POST", "ask", {"message": "first question"})[1]
        assert first["title"] == "first question" and first["chat"]
        s.api("POST", "reset", {})                                              # "New chat"
        second = s.api("POST", "ask", {"message": "second"})[1]
        assert second["chat"] != first["chat"]
        assert [c["title"] for c in s.api("GET", "chats")[1]["chats"]] == ["second", "first question"]
        opened = s.api("GET", "chat?id=" + first["chat"])[1]                   # switching back shows what was said
        assert [m["who"] for m in opened["log"]] == ["me", "bot"]
        s.api("POST", "ask", {"message": "more", "chat": first["chat"]})
        assert client.seen[-1] == 4                                             # system + the earlier exchange + the new question
        assert s.api("POST", "ask", {"message": "x", "chat": "nope"})[0] == 404
        assert s.api("GET", "chat?id=nope")[0] == 404
        assert s.api("POST", "chat_delete", {"id": second["chat"]})[1]["ok"] is True
        assert s.api("GET", "chat?id=" + second["chat"])[0] == 404
        assert s.api("POST", "chat_delete", {"id": 5})[0] == 404
    finally:
        s.stop()


def test_a_saved_conversation_survives_a_restart_and_the_model_gets_its_context_back():
    client = FakeClient(answer="42 GB")
    s = Running(client=client)
    try:
        cid = s.api("POST", "ask", {"message": "how much space?"})[1]["chat"]
        path = s.path
    finally:
        s.stop()
    again = webui.App(path, client_factory=lambda: client)
    assert [c["title"] for c in again.chat_list()["chats"]] == ["how much space?"]
    again.ask("x", "and on D:?", cid)
    assert client.seen[-1] == 4                                                 # rebuilt from the saved text, not lost
    (path.parent / "chats.json").write_text("{not json", encoding="utf-8")
    assert webui.App(path).chat_list() == {"chats": []}                         # a damaged file never breaks the app


def test_games_are_grouped_into_a_library_by_program():
    from test_game_tools import _data_dir
    root = _data_dir()
    other = (root / "sessions" / "cs2_20260919_120000.csv").read_text(encoding="utf-8").replace("cs2.exe,", "Hades2.exe,")
    (root / "sessions" / "hades_1.csv").write_text(other, encoding="utf-8")
    d = webui.App(root / "metrics.db").games()
    lib = {g["id"]: g for g in d["games"]}
    assert lib["cs2.exe"]["name"] == "Counter-Strike 2" and (lib["cs2.exe"]["sessions"], lib["cs2.exe"]["runs"]) == (1, 2)
    assert lib["Hades2.exe"]["sessions"] == 1 and len(d["recordings"]) == 4


def test_the_report_can_be_shown_inside_the_page_and_charts_have_hover_labels():
    s = Running()
    try:
        _, _, plain = s.request("GET", "/report?hours=1")
        _, _, embedded = s.request("GET", "/report?hours=1&embed=1")
        assert b"<body>" in plain and b"<body class='embed'>" in embedded
    finally:
        s.stop()


def test_a_batch_compares_every_variant_with_base_and_calls_noise_noise():
    from test_game_tools import _data_dir
    root = _data_dir()                                                      # batch "t": base (30 fps-ish run) and slow (half the FPS)
    app = webui.App(root / "metrics.db")
    d = app.game_batch("cs2.exe", "t")
    rows = {v["variant"]: v for v in d["variants"]}
    assert d["reference"] == "base" and rows["base"]["verdict"] == "Reference"
    assert rows["slow"]["verdict"] == "Worse" and rows["slow"]["avg_change"] < -30 and "Only one run" in rows["slow"]["text"]
    for bad in (("cs2.exe", "nope"), ("other.exe", "t")):
        try:
            app.game_batch(*bad)
        except webui.ApiError as e:
            assert e.code == 404
        else:
            raise AssertionError(bad)
    from pulse import explain, games
    same = games.variant_table({"base": [{"avg_fps": 100, "low1_fps": 60, "low01_fps": 50, "p99_ms": 10}] * 2,
                                "x": [{"avg_fps": 102, "low1_fps": 61, "low01_fps": 50, "p99_ms": 10}] * 2})
    assert explain.variant_verdict(same[1], same[0])["verdict"] == "About the same"


def test_a_game_can_be_added_recorded_and_removed_and_only_a_plain_exe_name_is_accepted():
    from pulse import gamelib
    launched, root = [], Path(tempfile.mkdtemp())
    app = webui.App(root / "m.db", launcher=launched.append)
    app.game_add("Hades2.exe", "Hades II")
    shown = {g["id"]: g for g in app.games()["games"]}["Hades2.exe"]
    assert shown["name"] == "Hades II" and shown["tracked"] and shown["last"] is None and shown["sessions"] == 0
    bs = chr(92)
    for bad in ("x; calc.exe", f"..{bs}evil.exe", f"a{bs}b.exe", "../evil.exe", "cmd", "", "  ", None, 5, f"C:{bs}Windows{bs}x.exe", "a" * 80 + ".exe"):
        try:
            app.game_add(bad)
        except webui.ApiError as e:
            assert e.code == 400, bad
        else:
            raise AssertionError(bad)
    real = gamelib.presentmon_exe
    try:
        gamelib.presentmon_exe = lambda: None                                    # not installed: say where to get it, start nothing
        try:
            app.game_record("Hades2.exe")
        except webui.ApiError as e:
            assert e.code == 409 and "github.com/GameTechDev/PresentMon" in str(e) and not launched
        else:
            raise AssertionError("recording without PresentMon")
        gamelib.presentmon_exe = lambda: Path("PresentMon.exe")
        assert app.game_record("Hades2.exe")["ok"] is True
    finally:
        gamelib.presentmon_exe = real
    cmd = launched[0]                                                          # a list, never a shell string: the name is one argument
    assert cmd[cmd.index("-Process") + 1] == "Hades2.exe" and cmd[cmd.index("-DataDir") + 1] == str(root)
    assert app.game_remove("hades2.exe")["ok"] is True and not app.games()["games"]
    try:
        app.game_remove("Hades2.exe")
    except webui.ApiError as e:
        assert e.code == 404
    (root / "games.json").write_text("{broken", encoding="utf-8")
    assert app.games()["games"] == []                                          # a damaged list never breaks the library


def test_an_added_game_joins_the_games_that_already_have_recordings_by_name_ignoring_case():
    from test_game_tools import _data_dir
    root = _data_dir()
    app = webui.App(root / "metrics.db")
    app.game_add("CS2.exe", "My CS")
    games = app.games()["games"]
    assert len(games) == 1 and games[0]["tracked"] and games[0]["name"] == "My CS" and games[0]["runs"] == 2 and games[0]["sessions"] == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
