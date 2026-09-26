# Chat tool API

The local model (`pulse chat` / `pulse ui`) answers only by calling these thirteen read-only
functions from `src/pulse/tools.py`. Every number in an answer traces back to one of them — see
[Scope and safety](README.md#scope-and-safety-of-the-assistant) for what that guarantees. Docstrings
here are the same text the model reads to decide when and how to call each tool.

## Live state

### `current_status()`
CPU, RAM, disk I/O and network rates, GPU load/VRAM/temperature/power, and the top processes by CPU
and memory, right now. `system` also carries static hardware facts (`cpu_name`, `cpu_cores_logical`,
`cpu_cores_physical`, `ram_total_mb`, `gpu_driver_version`, `hostname`) and `battery`
(percent/plugged-in, `null` on a desktop). A `null` field means that fact isn't available — never
derived or guessed.

### `disk_usage()`
Free/used/total space per disk or partition, in GB. Capacity, not I/O load.

## History

### `top_processes(sort_by="cpu", minutes=10, limit=10)`
Heaviest processes over a recent window, from collected history. `sort_by` is `"cpu"` (average
percent) or `"memory"` (peak RAM in MB).

### `metrics_history(metric, minutes=60)`
Min/avg/max (+ when the max happened), latest value, and the change over the last 10 minutes for one
metric: `cpu_percent`, `ram_percent`, `ram_used_mb`, `swap_percent`, `disk_read_mbps`,
`disk_write_mbps`, `net_sent_kbps`, `net_recv_kbps`, `gpu_util_percent`, `gpu_mem_used_mb`,
`gpu_temp_c`, `gpu_power_w`. For `gpu_temp_c`, `ram_percent`, `ram_used_mb` and `swap_percent` it also
returns `unusual_periods` (see [Anomaly detection](README.md#anomaly-detection)), each flagged
`during_game` when a recording overlaps. Load metrics have no such check — they swing with whatever's
running.

### `disk_forecast(days=30)`
Growth in GB/day and days until full per disk, fit on `days` of history. `confidence` is `"low"` under
24 h of history.

## Disk contents

### `largest_folders(path="C:\\", limit=10)`
Largest subfolders of a directory (scan, up to ~45 s). Local absolute paths only — network shares and
device paths are refused. Call again on a large subfolder to drill down.

## Games

### `game_sessions(limit=10)`
Recorded sessions and benchmark runs, newest first, with the `name` to pass to the two tools below.

### `game_session_report(name)`
Average FPS, 1% / 0.1% lows, frame times, GPU-or-CPU limiter, slowest 10 s stretches and worst hitches
of one recording.

### `game_sessions_compare(before, after)`
Differences in avg FPS, lows and p99 frame time between two recordings. Only meaningful for the same
scene/route; a single run varies.

## Security-adjacent (never a verdict — see below)

### `process_watch(minutes=1440)`
Processes that behave unusually against their **own** history: a name never seen before, far more CPU
than usual, or memory that keeps growing. Names/CPU/memory of the heaviest processes only — no file
path, signature or network data. `confidence` is `"low"` under 24 h of history.

### `system_health(hours=168)`
Blue screens, unexpected shutdowns, hardware/disk errors, driver resets, apps that keep crashing, and
Defender's state (real-time protection, Group Policy overrides, signature age, detections), from
Windows' own event logs. The Security log (failed logins) needs admin rights and isn't read.

### `startup_changes(hours=168)`
What starts by itself (Run keys, startup folders, scheduled tasks, services, WMI subscriptions,
browser extensions) and what's new or looks wrong since the first snapshot.

### `what_happened(when="now", minutes=30)`
Every metric change, new process/connection, new autostart entry, alert, game recording, gap and
Windows event around one moment, on a single timeline. `when` accepts `"14:03"`,
`"yesterday 21:30"`, `"2026-09-20 14:03"`, `"45 minutes ago"`. Says what happened together in time,
never what caused what.

---

**None of `process_watch`, `system_health` or `startup_changes` may ever say a process, file or entry
is safe or malicious, or that nothing/no virus/no threat was found** — see
[Scope and safety](README.md#scope-and-safety-of-the-assistant). They report findings; a human
decides. This is enforced in `chat.py`'s system prompt and checked in `tests/eval_tools.py`
(`watch_answer`, `defender_answer`) with repeated sampling, because a single run can hide a
regression here.
