"""Plain-language explanations for the app: what a finding, a timeline event, a metric or a game result means, for someone
who is not an expert. Fixed texts chosen by the kind of item (never generated from the item's own text), so a hostile
process name cannot change what is shown. Each explanation is {"what": ..., "why": ..., "todo": ...}."""

_GENERIC = {"what": "Something the checks noticed on this PC.",
            "why": "It is listed because it looks different from what was seen before, or is a known source of problems.",
            "todo": "If you know why it happened (you installed or changed something), it is fine: press Accept. "
                    "If not, use \"Ask the assistant\" to find out more."}

_FINDINGS = {
    "bugcheck": ("Windows crashed and showed a blue screen, then restarted.",
                 "Usually a faulty driver, unstable memory, overheating or an overclock that is not stable.",
                 "One blue screen is not alarming. If it repeats, update the graphics and chipset drivers, remove any overclock and run the Windows Memory Diagnostic."),
    "unexpected-shutdown": ("The PC turned off without shutting down properly (power loss, hard reset, or a crash).",
                            "Windows records this when it was not closed the normal way.",
                            "If you held the power button or lost power, ignore it. If it happens on its own, check the temperatures and the power supply."),
    "whea": ("The CPU, memory or PCIe bus reported a hardware error to Windows.",
             "Often caused by an unstable overclock or undervolt, an overheating part, or a failing component.",
             "Turn off overclocking / memory profiles (XMP/EXPO) for a test. If it keeps coming back, the hardware needs checking."),
    "disk-errors": ("Windows had trouble reading or writing to a disk.",
                    "A loose cable, a failing drive or a file system that needs repair.",
                    "Back up important files. Then run \"chkdsk\" on the drive and check its health with the maker's tool."),
    "gpu-driver": ("The graphics driver stopped responding and was reset.",
                   "Usually a driver bug, an unstable GPU overclock or overheating. The screen may flash black for a moment.",
                   "Update the graphics driver (a clean install), lower any GPU overclock, and check the GPU temperature."),
    "defender-not-running": ("The Windows antivirus (Defender) is switched off.",
                             "Without it nothing scans your files. Another antivirus can legitimately replace it.",
                             "If you use another antivirus, this is fine: press Accept. If not, turn Defender on in Windows Security."),
    "defender-realtime-off": ("Defender is not scanning files as they are opened or run.",
                              "Someone (you, a program or malware) turned real-time protection off.",
                              "Turn it back on in Windows Security > Virus & threat protection, unless you turned it off on purpose."),
    "defender-signatures-old": ("Defender's list of known threats has not been updated for a while.",
                                "An old list misses new threats.",
                                "Open Windows Security > Virus & threat protection > Check for updates."),
    "defender-policy-off": ("A Windows policy turns Defender protection off.",
                            "This is set by an administrator or by a program, and it is not the normal state of a home PC.",
                            "If you did not set this up, treat it seriously: run a full scan and ask the assistant."),
}
_PREFIX = {
    "crash:": ("A program keeps crashing or freezing.",
               "Repeated crashes point to a broken install, a bad update, a driver problem or a corrupted file.",
               "Update or reinstall the program. If it is a game, verify its files in the launcher."),
    "defender-threat:": ("Defender found something it considers a threat.",
                         "Most detections are handled automatically (blocked or removed). Repeated ones mean it keeps coming back.",
                         "Open Windows Security > Protection history to see what it did. Run a full scan if it repeats."),
}
_AUTORUN = {
    "run_key": "A program that starts by itself every time you sign in to Windows.",
    "startup_folder": "A shortcut in the Startup folder: it starts by itself when you sign in.",
    "scheduled_task": "A task that Windows runs on a schedule or at sign-in, without you opening anything.",
    "service": "A background service that Windows starts on its own.",
    "wmi_consumer": "A hidden Windows rule that runs a command when something happens. Rarely used by normal programs.",
    "browser_extension": "A browser extension (an add-on) that can see or change the pages you open.",
    "browser_setting": "A browser setting that makes it easier to install extensions from outside the store.",
}
_AUTORUN_WHY = ("Programs that start by themselves are how software stays running, but also how unwanted software (and malware) "
                "keeps coming back. It is listed because it is new since the first check, or looks unusual.")
_AUTORUN_TODO = ("If you installed or updated something recently, this is most likely it: press Accept. If you do not recognise "
                 "the name, do not accept yet: ask the assistant about it or search the name first.")


def finding(f: dict) -> dict:
    fid = f.get("id") or ""
    if fid in _FINDINGS:
        what, why, todo = _FINDINGS[fid]
    elif fid.startswith("autorun:"):
        kind = fid.split(":", 2)[1]
        what, why, todo = _AUTORUN.get(kind, "A program that starts by itself."), _AUTORUN_WHY, _AUTORUN_TODO
    else:
        hit = next((v for k, v in _PREFIX.items() if fid.startswith(k)), None)
        if hit:
            what, why, todo = hit
        elif f.get("source") == "Processes":
            what = "A program that is running right now and looks unusual."
            why = "For example it runs from a folder where programs normally are not, is not digitally signed, or uses a port typical of remote-control tools."
            todo = "Often it is a game, a launcher or a tool you installed yourself. If you do not recognise the name, ask the assistant."
        else:
            what, why, todo = _GENERIC["what"], _GENERIC["why"], _GENERIC["todo"]
    return {"what": what, "why": why, "todo": todo, "ask": f"Explain this finding in simple words and tell me if I should worry: {f.get('title', '')}. {f.get('detail', '')}"[:400]}


_EVENTS = {
    "metric": ("The load on the PC was different from normal at this time.",
               "Compared with the 3 hours before, this measurement was higher or lower than usual.",
               "A higher value is normal while you play a game or run something heavy. It only matters if you were doing nothing."),
    "process": ("A program showed up for the first time.",
                "It was not seen before in the recorded history.",
                "Usually it is something you just installed or opened. If you do not know it, ask the assistant."),
    "network": ("A program connected to an internet address it had not used before.",
                "Programs talk to servers all the time (updates, sign-in, sync). A first connection is common after a new program or update.",
                "Only worth a look if the program is one you do not recognise."),
    "autostart": ("A new program was set to start by itself.",
                  "Installers often add this. Unwanted software does too.",
                  "If you installed something at that time, it is fine."),
    "gap": ("No data was recorded during this period.",
            "The PC was off or asleep, or the recorder was not running.",
            "Nothing to do. If the PC should have been on, check that the background recorder is installed on the Setup page."),
    "alert": ("The app sent you a notification at this time.", "Something crossed a warning threshold.", "See the Findings page for details."),
    "game": ("A game recording (PresentMon) covers this time.", "You recorded FPS while playing.", "Open the Games page to see how it went."),
    "windows": ("Windows itself logged a problem or a status change.",
                "Examples: a crash, a shutdown that was not clean, a driver reset.",
                "A single one is not alarming. Repeats deserve attention."),
    "defender": ("The Windows antivirus reported something.", "Detections or protection changes are logged here.",
                 "Check Windows Security > Protection history if it says a threat was found."),
}
_METRICS = {
    "cpu_percent": ("Processor load", "How busy the processor was, in percent.", "Above 90% for a long time makes the PC feel slow."),
    "ram_percent": ("Memory use", "How much of the RAM was in use, in percent.", "Above 90% makes Windows swap to the disk and slows everything down."),
    "gpu_temp_c": ("Graphics card temperature", "How hot the graphics card was, in degrees C.", "Up to about 80 is normal under load. Above 90 is too hot."),
    "gpu_util_percent": ("Graphics card load", "How busy the graphics card was, in percent.", "High in games is good: it means the card is used fully."),
}


def event(kind: str, text: str = "") -> dict:
    what, why, todo = _EVENTS.get(kind) or (_GENERIC["what"], _GENERIC["why"], _GENERIC["todo"])
    return {"what": what, "why": why, "todo": todo, "ask": f"Explain in simple words what this means and whether I should worry: {text}"[:400]}


def metric(name: str) -> dict:
    title, what, todo = _METRICS.get(name, (name, "A measurement of this PC.", ""))
    return {"title": title, "what": what, "todo": todo}


_PROCESSES = {
    "chrome.exe": "Google Chrome, the web browser. Many copies are normal: one per tab and extension.",
    "msedge.exe": "Microsoft Edge, the web browser. Many copies are normal.",
    "firefox.exe": "Mozilla Firefox, the web browser.",
    "explorer.exe": "Windows Explorer: the desktop, taskbar and file windows.",
    "svchost.exe": "A Windows host process that runs many system services. Always present.",
    "system": "The Windows kernel. Always present.",
    "python.exe": "Python, a programming language runtime. Running when a script or a developer tool is active.",
    "pythonw.exe": "Python without a window. Used by background scripts (this app's recorder is one).",
    "rustc.exe": "The Rust compiler: running while a Rust program is being built.",
    "code.exe": "Visual Studio Code, a code editor.",
    "steam.exe": "Steam, the game launcher.",
    "discord.exe": "Discord, the chat app.",
    "msmpeng.exe": "Windows Defender's scanning engine. Busy while it scans files.",
    "searchindexer.exe": "Windows Search building its file index. Busy for a while after many files change.",
    "dwm.exe": "Desktop Window Manager: draws the windows on your screen.",
    "docker desktop.exe": "Docker Desktop, runs containers.",
}


def process(name: str) -> dict:
    known = _PROCESSES.get((name or "").lower())
    return {"what": known or "A program running on this PC.",
            "why": "It used a lot of processor time in this period." if known else "It is listed because it used the most processor time in this period.",
            "todo": "Nothing to do if you were using it." if known else "If you do not recognise the name and it was busy while you were away, ask the assistant.",
            "ask": f"What is the program {name} and is it normal for it to use the processor?"[:300]}


# ---- games
def game_summary(r: dict) -> dict:
    """A verdict in words for one recording. r is the game_session_report result."""
    avg, low1, hitch = r["avg_fps"], r["low1_fps"], r["frames_over_33ms"]
    if avg < 30 or low1 < 20:
        verdict, tone = "Choppy", "bad"
        text = "The game ran poorly here. Expect visible lag."
    elif avg < 60 or low1 < 45:
        verdict, tone = "Playable, with some stutter", "warn"
        text = "The game was playable, but the slowest moments dipped enough to notice."
    elif hitch > max(5, r["window_seconds"] / 20):
        verdict, tone = "Fast, with occasional hitches", "warn"
        text = "The frame rate is high, but there were brief freezes."
    else:
        verdict, tone = "Smooth", "ok"
        text = "The game ran smoothly."
    limiter = ""
    lim = r.get("limiter_percent_of_frames")
    if lim:
        top = max(lim, key=lim.get)
        limiter = {"gpu": "The graphics card was the limit most of the time: lowering graphics settings would raise the FPS.",
                   "cpu": "The processor was the limit most of the time: lowering graphics settings will not help much.",
                   "neither": "Neither the graphics card nor the processor was clearly the limit (the game or a frame cap may be)."}[top]
    if limiter and tone == "ok":
        limiter += " That only matters if you want even more FPS."
    return {"verdict": verdict, "tone": tone, "text": text, "limiter": limiter,
            "numbers": [{"label": "Average FPS", "value": round(r["avg_fps"]), "hint": "How many pictures per second on average. Higher is better; 60 is smooth."},
                        {"label": "Worst 1% FPS", "value": round(r["low1_fps"]), "hint": "The FPS during the slowest 1% of the time. If this is much lower than the average, the game stutters."},
                        {"label": "Freezes", "value": r["frames_over_33ms"], "hint": "Moments when one picture took over 33 ms (a visible hitch). Fewer is better."}],
            "minutes": round(r["window_seconds"] / 60, 1)}


def variant_verdict(row: dict, ref: dict) -> dict:
    """Plain words for one variant against the reference. A difference smaller than the run-to-run spread (or 5%) is
    called noise, so a lucky run is not presented as a result."""
    if row.get("reference"):
        return {"verdict": "Reference", "tone": "mute", "text": "The other variants are compared with this one."}
    avg, low = row["avg_change"], row["low1_change"]
    if avg is None or low is None:
        return {"verdict": "No comparison", "tone": "mute", "text": "Not enough data."}
    noise = max(5.0, 100 * max(row["spread"], ref["spread"]) / ref["avg_fps"]) if ref["avg_fps"] else 5.0
    text = f"Average FPS {avg:+.0f}%, worst 1% {low:+.0f}%."
    if row["runs"] < 2 or ref["runs"] < 2:
        text += " Only one run: it could be just chance."
    if abs(avg) < noise and abs(low) < noise:
        return {"verdict": "About the same", "tone": "mute", "text": text + f" Within the run-to-run noise (about {noise:.0f}%)."}
    better = avg + low > 0
    return {"verdict": "Better" if better else "Worse", "tone": "ok" if better else "bad", "text": text}


def game_compare_text(c: dict) -> dict:
    avg = c["avg_fps"]["change_percent"]
    low = c["low1_fps"]["change_percent"]
    parts = []
    if avg is not None:
        parts.append(f"Average FPS is {abs(avg):.0f}% {'higher' if avg > 0 else 'lower'}")
    if low is not None:
        parts.append(f"the worst-1% FPS is {abs(low):.0f}% {'higher' if low > 0 else 'lower'}")
    text = ", and ".join(parts) + "." if parts else "Not enough data to compare."
    small = all(v is None or abs(v) < 5 for v in (avg, low))
    verdict = "About the same" if small else ("Second is better" if (low or 0) + (avg or 0) > 0 else "Second is worse")
    return {"verdict": verdict, "text": text,
            "note": "One run varies from the next one. Differences under about 5% are usually just noise; repeat each setting a couple of times before trusting it."}
