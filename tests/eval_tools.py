"""Regression check: right tool per question, and answers that agree with the tool results.

Run: python tests/eval_tools.py [--model qwen3:8b] [--db PATH] [--slow] [--runs N]
Needs a running Ollama. Each question starts a fresh conversation. Exit code 1 on any failure.
"""
import argparse
import json
import os
import re
import sys
import tempfile

import ollama

from pcassist import chat, tools


def num_in(answer: str, value) -> bool:
    """True if the number appears in the answer (accepts 53.6 / 53,6 and 48 for 48.0)."""
    text = answer.replace(",", ".")
    text = re.sub(r"(?<=\d)[   ](?=\d{3}(?!\d))", "", text)   # thousands separator: 1 843 -> 1843
    forms = {f"{value:g}"}
    if isinstance(value, float):
        forms.add(f"{value:.1f}")
        forms.add(str(round(value)))
    # Whole-number match only: "4" must not match inside "48" or "3.5".
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d]|\.\d)", text) for f in forms)


def first(results: dict, name: str):
    calls = results.get(name) or [{}]
    return calls[0]


# Answer checks: (answer, results) -> error message or None.
def no_markdown(answer, results):
    bad = [t for t in ("**", "##", "`") if t in answer]
    return f"markdown in answer: {bad}" if bad else None


def no_double_backslash(answer, results):
    return "doubled backslash in answer" if "\\\\" in answer else None


def disk_load_answer(answer, results):
    r = first(results, "current_status").get("system", {})
    for key in ("disk_read_mbps", "disk_write_mbps"):
        if key in r and not num_in(answer, r[key]):
            return f"{key}={r[key]} not in answer"
    return None


def free_space_answer(answer, results):
    disks = results.get("disk_usage", [[]])[0]
    if not disks:
        return "no disk_usage result"
    for d in disks:
        if not num_in(answer, d["free_gb"]):
            return f"free_gb {d['free_gb']} of {d['disk']} not in answer"
    return None


def history_answer(answer, results):
    r = first(results, "metrics_history")
    if "error" in r:
        return None if "pcassist collect" in answer else "no-data case: answer must mention `pcassist collect`"
    for key in ("min", "max"):
        if not num_in(answer, r[key]):
            return f"{key}={r[key]} not in answer"
    if not (num_in(answer, r["latest"]) or num_in(answer, r["avg"])):
        return f"neither latest={r['latest']} nor avg={r['avg']} in answer"
    if "warning" in r and not num_in(answer, r["data_covers_minutes"]):
        return f"short history ({r['data_covers_minutes']} min) not stated in answer"
    return None


def spike_answer(answer, results):
    """'Was there a spike?' must be answered with max AND avg/when, not a bare max called a jump."""
    r = first(results, "metrics_history") or first(results, "anomalies").get("range", {})   # either tool carries the numbers
    if not r:
        return "no metrics_history or anomalies range in the results"
    if "error" in r:
        return history_answer(answer, results)
    for key in ("max", "avg", "max_was_minutes_ago"):
        if not num_in(answer, r[key]):
            return f"{key}={r[key]} not in answer"
    return None


HEDGE = re.compile(r"недостаточно|мало данных|ненадёжн|ненадежн|неточн|приблизительн|предварительн|"
                   r"только за|всего за|пока что|больше данных|нельзя (?:точно|надёжно|надежно)|низк\w+ (?:точност|достоверност)",
                   re.IGNORECASE)


def forecast_answer(answer, results):
    rows = first_list(results, "disk_forecast")
    if any("error" in r for r in rows):
        return None if "pcassist collect" in answer else "no-data case: answer must mention `pcassist collect`"
    if any(r["confidence"] == "low" for r in rows):
        hours = rows[0]["history_hours"]
        if not (HEDGE.search(answer) or num_in(answer, hours)):
            return f"low-confidence forecast ({hours} h of history) presented without a caveat"
    for r in rows:
        if "days_until_full" not in r and r["disk"] == "C:" and re.search(r"через\s+\d+\s+(дн|мес|лет)", answer):
            return "invented a fill-up date for a disk that is not growing"
    return None


def first_list(results: dict, name: str) -> list:
    return (results.get(name) or [[]])[0]


def folders_answer(answer, results):
    r = first(results, "largest_folders")
    folders = r.get("folders")
    if not folders:
        return "no folders in result"
    biggest = max(folders, key=lambda f: f["size_gb"])
    name = biggest["folder"].rstrip("\\").split("\\")[-1]
    if name.lower() not in answer.lower():
        return f"largest folder {name} not in answer"
    if any(w in answer.lower() for w in ("наименьш", "smallest")):
        return "answer calls something the smallest"
    return None


def game_report_answer(answer, results):
    r = first(results, "game_session_report")
    if "error" in r:
        return None
    for key in ("avg_fps", "low1_fps"):
        if not num_in(answer, r[key]):
            return f"{key}={r[key]} not in answer"
    return None


def game_compare_answer(answer, results):
    r = first(results, "game_sessions_compare")
    if "error" in r:
        return f"compare failed: {r['error']}"
    for key in ("avg_fps", "low1_fps"):
        for side in ("before", "after"):
            if not num_in(answer, r[key][side]):
                return f"{key} {side}={r[key][side]} not in answer"
    return None


def game_none_answer(answer, results):
    """No recordings: the answer must not contain invented FPS numbers."""
    return "FPS numbers in an answer without recordings" if re.search(r"\d+\s*(fps|кадр)", answer, re.I) else None


def cyrillic_share(text: str) -> float:
    letters = re.findall(r"[A-Za-zЀ-ӿ]", text)
    return sum("Ѐ" <= ch <= "ӿ" for ch in letters) / max(1, len(letters))


def answer_in_english(answer, results):
    share = cyrillic_share(answer)
    return f"English question answered in Russian ({share:.0%} Cyrillic)" if share > 0.15 else None


def answer_in_russian(answer, results):
    share = cyrillic_share(answer)
    return f"Russian question answered in another language ({share:.0%} Cyrillic)" if share < 0.5 else None


def covers_stated(answer: str, minutes: float) -> bool:
    """The coverage in minutes ("345") or as hours and minutes ("5 часов 45 минут")."""
    return num_in(answer, minutes) or (num_in(answer, minutes // 60) and num_in(answer, round(minutes % 60)))


def anomalies_answer(answer, results):
    r = first(results, "anomalies")
    if "error" in r:
        return None
    if r["events_found"] == 0 and not re.search(r"не (?:было|найден|обнаружен|выявл|зафиксир|наблюда)|нет |ничего|не замет|отсутств|no unusual|none|nothing", answer, re.I):
        return "no events found but the answer does not say that"
    if "warning" in r and not covers_stated(answer, r["data_covers_minutes"]):
        return f"short coverage ({r['data_covers_minutes']} min) not stated in answer"
    for e in r["events"][:3]:
        if not num_in(answer, e["most_unusual_value"]):
            return f"event value {e['most_unusual_value']} not in answer"
    return None


COMMON = [no_markdown, no_double_backslash]

# (question, must call, must NOT call, extra answer checks, db override)
CASES = [
    ("какая нагрузка диска", {"current_status"}, {"disk_usage"}, [disk_load_answer], None),
    ("сколько свободного места на дисках?", {"disk_usage"}, {"current_status", "disk_forecast"},
     [free_space_answer, answer_in_russian], None),
    ("когда закончится место на диске?", {"disk_forecast"}, set(), [forecast_answer], None),
    ("когда закончится место на диске?", {"disk_forecast"}, set(), [forecast_answer], "EMPTY"),
    ("что сейчас больше всего грузит систему?", {"current_status"}, set(), [], None),
    ("как менялась температура GPU за последний час?", {"metrics_history"}, set(), [history_answer], None),
    ("был ли за последний час скачок температуры GPU?", set(), set(), [spike_answer], None),
    ("какие процессы грузили процессор за последние 10 минут?", {"top_processes"}, set(), [], None),
    ("какая сейчас температура видеокарты?", {"current_status"}, {"metrics_history"}, [], None),
    ("как менялась температура GPU за последний час?", {"metrics_history"}, set(), [history_answer], "EMPTY"),
    ("какие у меня есть записи игровых сессий?", {"game_sessions"}, {"game_session_report"}, [], None),
    ("покажи FPS в последней игровой записи", {"game_sessions", "game_session_report"}, set(),
     [game_report_answer], None),
    ("сравни записи combo_base_1 и combo_fsr3_1", {"game_sessions_compare"}, set(), [game_compare_answer], None),
    ("покажи FPS в последней игре", {"game_sessions"}, {"game_session_report"}, [game_none_answer], "EMPTY"),
    ("было ли за последние сутки что-то странное с температурой видеокарты?", {"anomalies"}, set(),
     [anomalies_answer], None),
    ("были ли аномалии в использовании оперативной памяти за последние сутки?", {"anomalies"}, set(),
     [anomalies_answer], None),
    ("how much free space do I have on my disks?", {"disk_usage"}, set(), [free_space_answer, answer_in_english], None),
    ("was there a spike in GPU temperature in the last hour?", set(), set(),
     [spike_answer, answer_in_english], None),
    ("compare recordings combo_base_1 and combo_fsr3_1", {"game_sessions_compare"}, set(),
     [game_compare_answer, answer_in_english], None),
    ("was there anything unusual with the GPU temperature in the last day?", {"anomalies"}, set(),
     [anomalies_answer, answer_in_english], None),
    ("были ли аномалии в загрузке видеокарты за сутки?", set(), set(), [], None),   # load metric: any sane answer, no crash
]
SLOW_CASES = [
    ("что занимает место на диске C?", {"largest_folders"}, {"disk_usage"}, [folders_answer], None),
]


def run_case(client, model: str, question: str, num_ctx: int):
    called: list[str] = []
    results: dict[str, list] = {}
    original = chat._call_tool

    def spy(name: str, args: dict) -> str:
        out = original(name, args)
        called.append(name)
        results.setdefault(name, []).append(json.loads(out))
        return out

    chat._call_tool = spy
    try:
        messages = [{"role": "system", "content": chat.SYSTEM_PROMPT},
                    {"role": "user", "content": question}]
        answer = chat.ask(client, model, messages, think=False, num_ctx=num_ctx)
    finally:
        chat._call_tool = original
    return set(called), results, answer


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # answers may hold characters cp1251 cannot print
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="qwen3:8b")
    p.add_argument("--db", default=str(tools.db.DEFAULT_DB))
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--slow", action="store_true", help="include the folder scan case (up to ~45 s)")
    p.add_argument("--runs", type=int, default=1, help="repeat each question (models are non-deterministic)")
    args = p.parse_args()

    client = ollama.Client()
    empty_db = os.path.join(tempfile.mkdtemp(), "empty.db")
    cases = CASES + (SLOW_CASES if args.slow else [])
    failures = 0
    for question, must, must_not, checks, db_override in cases:
        tools.set_db(empty_db if db_override == "EMPTY" else args.db)
        label = question + ("  [empty db]" if db_override == "EMPTY" else "")
        for _ in range(args.runs):
            called, results, answer = run_case(client, args.model, question, args.num_ctx)
            problems = []
            if must - called:
                problems.append(f"expected but not called: {sorted(must - called)}")
            if must_not & called:
                problems.append(f"must not be called: {sorted(must_not & called)}")
            for c in COMMON + checks:
                try:
                    if m := c(answer, results):
                        problems.append(m)
                except (KeyError, IndexError, TypeError) as e:   # e.g. the expected tool was not called at all
                    problems.append(f"{c.__name__} could not check the answer ({type(e).__name__}: {e})")
            failures += bool(problems)
            print(f"[{'FAIL' if problems else 'PASS'}] {label}  -> {sorted(called)}")
            for m in problems:
                print(f"       {m}")
            if problems:
                print(f"       answer: {answer[:300]}")
    total = len(cases) * args.runs
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
