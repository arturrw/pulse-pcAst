# Contributing

Windows only (the project reads PowerShell, Task Scheduler, Windows event logs, NVML). See
[ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

[Ollama](https://ollama.com) running locally (`ollama pull qwen3:8b`) is needed for the chat-related
tests and for trying `pulse chat` / `pulse ui` yourself; everything else works without it.

## Tests

One process per file — several tests swap module attributes and must not leak into each other:

```powershell
Get-ChildItem tests\test_*.py | ForEach-Object { python $_.FullName }
```

Most need no Ollama and no GPU (they use synthetic/recorded data). The two that do:

```powershell
python tests\eval_tools.py --runs 2   # model picks the right tool + answers correctly; needs Ollama
python tests\eval_nab.py --sweep      # anomaly detector scored against a public labeled benchmark
```

`eval_tools.py` is the main regression gate for the chat model: it runs real questions through a real
Ollama model and checks the tool called, the numbers in the answer, and the answer's language. A
single run is noisy (small local models aren't fully deterministic) — `--runs 2` or `--runs 3` catches
issues a single pass hides. If you change `SYSTEM_PROMPT` in `chat.py`, run this before and after.

CI (`.github/workflows/tests.yml`) runs the unit tests on Python 3.10 and 3.13, plus a headless-Chrome
smoke test of every page in `pulse ui` (light/dark, wide/narrow). It does not run `eval_tools.py`
(no Ollama in CI). To catch prompt regressions anyway, run it nightly on your own PC:
```powershell
powershell -File scripts\autostart.ps1 install -Task eval   # daily at 03:00 (-At hh:mm to change), --runs 3
```
Each run adds a line to `data\eval.log` and writes the full output to `data\eval_last.txt`. A notification
comes up only when the score fell more than 3 points against the last run of the same model, or when
Ollama was not running.

## Adding a chat tool

1. Write the function in `tools.py`: read-only, returns a plain dict, the docstring is what the model
   reads to decide when to call it.
2. Add it to `TOOLS`/`TOOL_MAP` in the same file.
3. Add a paragraph to `SYSTEM_PROMPT` in `chat.py` saying when to call it and how to phrase the answer
   — small models follow explicit, repeated instructions much better than a one-line hint.
4. Add a few cases to `tests/eval_tools.py` (question, expected tool(s), forbidden tool(s), answer
   checks) and run it with `--runs 3` before trusting the prompt wording.

## Guarding against hallucination

This codebase has a specific, recurring failure mode worth knowing about: a small local model will
state a fact with confidence even when no tool gave it one (an invented CPU model, a guessed driver
status, a reassuring "nothing found" from a check that can't know that). The fix is never just prompt
wording — verify with repeated sampling (`--runs 3`+), because these bugs hide in a single run. See
the `driver_freshness_answer` / `watch_answer` checks in `eval_tools.py` for the pattern: assert the
answer cites a real tool value, and separately assert it never states a fact with no tool result
behind it.

## Style

- No comments explaining *what* code does — names should do that. A comment is for a non-obvious
  *why* (a Windows quirk, a threshold's origin, a workaround).
- Docstrings on tool functions are user-facing (the model reads them) and on modules are a one-line
  summary of the file's job — both are treated as documentation, not boilerplate.
- Prefer extending an existing tool's fields over adding a near-duplicate tool.
- Commit messages explain *why*, not *what* (the diff already shows what).

## Releasing a version

1. Add a `## X.Y.Z` section to [CHANGELOG.md](CHANGELOG.md), written for the people who use Pulse: it becomes the
   GitHub release notes and the "What's new" list in the app's update banner.
2. Set the version in `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml` (and the `pulse-desktop`
   entry of `Cargo.lock`), `desktop/package.json`, `desktop/package-lock.json` and `pyproject.toml`; commit.
3. `git tag vX.Y.Z && git push origin main vX.Y.Z`. The `installer` workflow builds the signed installer and
   publishes the release with `latest.json`; installed apps then offer "Update now". It refuses a tag that does not
   match `tauri.conf.json` or has no CHANGELOG section (`tests/test_release_notes.py` catches the latter earlier).

## Reporting a security issue

See [SECURITY.md](SECURITY.md).
