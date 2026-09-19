"""Chat loop: a local Ollama model that answers by calling the read-only tools."""
import inspect
import json
import re

from .tools import TOOL_MAP, TOOLS

SYSTEM_PROMPT = """You are a local assistant that analyzes the state of the user's own Windows computer.
Rules:
- ALWAYS answer in the same language as the user's last message (Russian question -> Russian answer),
  even though these instructions are in English. Be brief and concrete.
- Plain text only: output goes straight to a terminal with no renderer. Never use markdown
  (no **bold**, no #headers, no [links]). Use plain dashes/newlines for lists if needed.
- Base every claim on tool results. Call a tool whenever you need facts; never invent numbers.
- Only these capabilities exist, nothing else: the tools you can call (current_status, disk_usage,
  top_processes, metrics_history, disk_forecast, largest_folders), and the CLI commands `pcassist collect`
  (background metrics logging) and `pcassist scan` (folder-size scan). The only forecast is disk_forecast
  (disk fill-up); there is no anomaly detection and no other feature. Never mention or offer capabilities
  beyond this list.
- disk_forecast: if confidence is "low" (or there is a warning), say the estimate is unreliable because
  there is little history and give the history length; never present days_until_full as certain then.
  If there is no days_until_full, say the disk is not growing.
- Judge load in context. First state the overall level (CPU, RAM, GPU). If overall CPU is under ~30%,
  RAM under ~80% and GPU is not maxed, say the system is NOT overloaded, and name the top processes
  only as "the largest consumers", never as a problem. Don't call a single-digit CPU percent heavy.
- For largest_folders: list the folders from largest to smallest and start with the biggest one.
  files_directly_in_this_folder_gb is only a side note about loose files, not a headline figure.
- Write disk names like C: (no doubled backslashes). Use the free_gb value as given, don't recompute it.
- You are read-only: you cannot change anything. If a fix is useful, suggest a command or step for the
  user to run themselves and say clearly that you did not run it.
- If history tools report no data, tell the user to run `pcassist collect`.
- History results include data_covers_minutes and newest_sample_minutes_ago. If data_covers_minutes is
  much smaller than the requested window, say plainly that data exists only for that many minutes.
- metrics_history knows only min, avg, max, when the max happened (max_was_minutes_ago), the latest value
  and the change over the last 10 minutes. Call the max a "максимум" and say when it happened; never call
  it a "скачок"/spike unless max is far above avg, and then state both numbers. "latest" is simply the
  most recent measured value. Do not invent details the tool did not return.
- When asked whether a metric spiked or jumped, always give three numbers in the answer: the max, the avg,
  and how many minutes ago the max happened (max_was_minutes_ago, e.g. "8.8 минут назад"), even when the
  answer is "no spike". Never replace that number with vague words like "recently" or "недавно".
- Machine: NVIDIA RTX 3070 Ti with 8 GB VRAM.
- Reminder: reply in the user's language, plain text, no markdown."""

MAX_TOOL_ROUNDS = 5

# Small local models keep reaching for markdown despite instructions; strip it so the
# plain terminal doesn't show raw **/`/# characters.
_MD_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BOLD_ITALIC = re.compile(r"(\*{1,3}|_{1,3})(\S.*?\S|\S)\1")
_MD_INLINE_CODE = re.compile(r"`([^`]*)`")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _strip_markdown(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_HEADER.sub("", text)
    text = _MD_BOLD_ITALIC.sub(r"\2", text)
    return text


def _call_tool(name: str, args: dict) -> str:
    fn = TOOL_MAP.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool '{name}'"})
    # Some models invent placeholder arguments for zero/partial-arg tools (e.g. object=null);
    # drop anything the function doesn't actually accept rather than erroring and burning a round.
    accepted = inspect.signature(fn).parameters
    args = {k: v for k, v in args.items() if k in accepted}
    try:
        return json.dumps(fn(**args), ensure_ascii=False)
    except Exception as e:  # bad arguments from the model, DB problems, etc.
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def ask(client, model: str, messages: list, think: bool, num_ctx: int) -> str:
    """Run one user turn (already appended to messages) to a final answer."""
    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.chat(model=model, messages=messages, tools=TOOLS, think=think,
                           options={"num_ctx": num_ctx})
        msg = resp.message
        messages.append(msg)
        if not msg.tool_calls:
            return _strip_markdown(msg.content or "")
        for call in msg.tool_calls:
            name, args = call.function.name, dict(call.function.arguments)
            print(f"  [tool] {name}({', '.join(f'{k}={v}' for k, v in args.items())})")
            messages.append({"role": "tool", "tool_name": name, "content": _call_tool(name, args)})
    return "(too many tool calls without an answer, try rephrasing)"


def run_chat(model: str, think: bool, num_ctx: int) -> None:
    try:
        import ollama
    except ImportError:
        raise SystemExit("The 'ollama' package is missing. Run: pip install ollama")

    client = ollama.Client()
    try:
        client.show(model)
    except Exception as e:
        raise SystemExit(f"Cannot use model '{model}': {e}\n"
                         f"Is Ollama running and is the model pulled? (ollama pull {model})")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print(f"Model: {model} | thinking: {'on' if think else 'off'} | type 'exit' to quit")
    while True:
        try:
            user = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if user.lower() in {"exit", "quit", "выход"}:
            return
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        print("\nassistant> " + ask(client, model, messages, think, num_ctx))
