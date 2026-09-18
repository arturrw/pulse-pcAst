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
    for key in ("min", "max", "latest"):
        if not num_in(answer, r[key]):
            return f"{key}={r[key]} not in answer"
    if "warning" in r and not num_in(answer, r["data_covers_minutes"]):
        return f"short history ({r['data_covers_minutes']} min) not stated in answer"
    return None


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


COMMON = [no_markdown, no_double_backslash]

# (question, must call, must NOT call, extra answer checks, db override)
CASES = [
    ("какая нагрузка диска", {"current_status"}, {"disk_usage"}, [disk_load_answer], None),
    ("сколько свободного места на дисках?", {"disk_usage"}, {"current_status"}, [free_space_answer], None),
    ("что сейчас больше всего грузит систему?", {"current_status"}, set(), [], None),
    ("как менялась температура GPU за последний час?", {"metrics_history"}, set(), [history_answer], None),
    ("какие процессы грузили процессор за последние 10 минут?", {"top_processes"}, set(), [], None),
    ("какая сейчас температура видеокарты?", {"current_status"}, {"metrics_history"}, [], None),
    ("как менялась температура GPU за последний час?", {"metrics_history"}, set(), [history_answer], "EMPTY"),
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
            problems += [m for c in COMMON + checks if (m := c(answer, results))]
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
