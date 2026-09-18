"""Regression check: does the model pick the right tool for each question?

Run: python tests/eval_tools.py [--model qwen3:8b] [--db PATH] [--slow] [--runs N]
Needs a running Ollama. Each question starts a fresh conversation. Exit code 1 on any failure.
"""
import argparse
import sys

import ollama

from pcassist import chat, tools

# (question, tools that must be called, tools that must NOT be called)
CASES = [
    ("какая нагрузка диска", {"current_status"}, {"disk_usage"}),
    ("сколько свободного места на дисках?", {"disk_usage"}, {"current_status"}),
    ("что сейчас больше всего грузит систему?", {"current_status"}, set()),
    ("как менялась температура GPU за последний час?", {"metrics_history"}, set()),
    ("какие процессы грузили процессор за последние 10 минут?", {"top_processes"}, set()),
    ("какая сейчас температура видеокарты?", {"current_status"}, {"metrics_history"}),
]
SLOW_CASES = [
    ("что занимает место на диске C?", {"largest_folders"}, {"disk_usage"}),
]


def run_case(client, model: str, question: str, num_ctx: int) -> tuple[set[str], str]:
    called: list[str] = []
    original = chat._call_tool

    def spy(name: str, args: dict) -> str:
        called.append(name)
        return original(name, args)

    chat._call_tool = spy
    try:
        messages = [{"role": "system", "content": chat.SYSTEM_PROMPT},
                    {"role": "user", "content": question}]
        answer = chat.ask(client, model, messages, think=False, num_ctx=num_ctx)
    finally:
        chat._call_tool = original
    return set(called), answer


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="qwen3:8b")
    p.add_argument("--db", default=str(tools.db.DEFAULT_DB))
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--slow", action="store_true", help="include the folder scan case (up to ~45 s)")
    p.add_argument("--runs", type=int, default=1, help="repeat each question (models are non-deterministic)")
    args = p.parse_args()

    tools.set_db(args.db)
    client = ollama.Client()
    cases = CASES + (SLOW_CASES if args.slow else [])
    failures = 0
    for question, must, must_not in cases:
        for run in range(args.runs):
            called, answer = run_case(client, args.model, question, args.num_ctx)
            missing, forbidden = must - called, must_not & called
            ok = not missing and not forbidden
            failures += not ok
            print(f"[{'PASS' if ok else 'FAIL'}] {question}  -> {sorted(called)}")
            if not ok:
                if missing:
                    print(f"       expected but not called: {sorted(missing)}")
                if forbidden:
                    print(f"       must not be called: {sorted(forbidden)}")
                print(f"       answer: {answer[:200]}")
    total = len(cases) * args.runs
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
