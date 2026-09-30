"""Auto-tune for CS2: lower each graphics setting one step from what the player has now, benchmark every step
against their own settings, and rank the steps by what they gain.

Only the plan and the ranking live here; the runs themselves are scripts/bench_autotune.ps1 -> bench_batch.ps1, and
every step is a named variant in scripts/bench_variants.json, so the app shows the batch like any other."""
from pathlib import Path

from . import cs2settings

# key -> [(value, variant)] from the best picture to the cheapest. The variant is the bench_variants.json entry that
# sets exactly {key: value}; the top value is never a step down, so it needs none.
LADDERS = {
    "setting.videocfg_fsr_detail": [("0", None), ("1", "fsr1"), ("2", "fsr2"), ("3", "fsr3"), ("4", "fsr4")],
    "setting.msaa_samples": [("8", None), ("4", "msaa4"), ("2", "msaa2"), ("0", "msaa0")],
    "setting.videocfg_shadow_quality": [("2", None), ("1", "shadowM"), ("0", "shadowL")],
    "setting.videocfg_dynamic_shadows": [("1", None), ("0", "dynOff")],
    "setting.videocfg_ao_detail": [("2", None), ("1", "aoLow"), ("0", "aoOff")],
    "setting.shaderquality": [("1", None), ("0", "shaderL")],
    "setting.videocfg_texture_detail": [("2", None), ("1", "texM"), ("0", "texL")],
}


def current_values(text: str) -> dict[str, str]:
    """The value of every laddered key found in the text of cs2_video.txt."""
    out = {}
    for key in LADDERS:
        m = cs2settings._key_pattern(key).search(text)
        if m:
            out[key] = m.group(2)
    return out


def plan(current: dict[str, str]) -> list[str]:
    """The variants to benchmark: each setting one step cheaper than its current value. A setting already at its
    cheapest, missing, or at a value the ladder does not know is left out."""
    steps = []
    for key, ladder in LADDERS.items():
        values = [v for v, _ in ladder]
        if current.get(key) in values:
            i = values.index(current[key])
            if i + 1 < len(ladder):
                steps.append(ladder[i + 1][1])
    return steps


def plan_from_game() -> list[str]:
    path = cs2settings.video_settings_path()
    if path is None:
        raise cs2settings.Cs2Error("CS2's settings file was not found")
    return plan(current_values(path.read_text(encoding="utf-8", errors="ignore")))


def report(paths, process: str | None = None, title: str = "Auto-tune") -> str:
    """The batch ranked by average FPS gain, then which steps are worth taking and which cost nothing to skip."""
    from . import explain, games

    rows = games.batch_rows(paths, process)
    if len(rows) < 2:
        return "no usable runs (need your settings and at least one step)"
    ref, steps = rows[0], sorted(rows[1:], key=lambda r: r["avg_change"] or 0, reverse=True)
    pct = lambda x: "" if x is None else f"{x:+.0f}%"
    out = [f"{title}: each setting one step lower than yours ({ref['runs']} runs of your settings)", "",
           f"{'':<38}{'avg FPS':>9}{'1% low':>9}  verdict",
           f"{ref['label']:<38}{ref['avg_fps']:>9.0f}{ref['low1_fps']:>9.0f}  reference"]
    out += [f"{r['label']:<38}{pct(r['avg_change']):>9}{pct(r['low1_change']):>9}  {r['verdict']}" for r in steps]
    better = [r for r in steps if r["verdict"] == "Better"]
    same = [r for r in steps if r["verdict"] == "About the same"]
    out.append("")
    if better:
        out.append("Worth lowering: " + ", ".join(f"{r['label']} ({pct(r['avg_change'])})" for r in better) + ".")
    else:
        out.append("No single step lowers the picture for a clear gain: your settings are already a good balance.")
    if same:
        out.append("Keep as they are (lowering gains nothing beyond the noise): " + ", ".join(r["label"] for r in same) + ".")
    out.append("Steps are measured one at a time; two worthwhile steps together usually gain a bit less than their sum.")
    groups: dict[str, list[list[int]]] = {}
    for p in paths:
        try:
            groups.setdefault(games.run_variant(p), []).append(games.analyze(p, process=process)["slow_seconds"])
        except (OSError, ValueError):
            continue                                      # an unreadable run is already left out of the table
    spots = explain.slow_spots_text(games.slow_spots(groups))
    if spots:
        out += ["", spots]
    return "\n".join(out)
