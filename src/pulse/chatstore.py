"""The assistant's conversations, kept in a small JSON file next to the metrics database so an earlier chat can be opened
again (also after the app was restarted). Only what was said is stored: the text of both sides and the names of the tools
that were checked. The model's own context is rebuilt from it when a chat is continued."""
import json
import os
import threading
import time
import uuid
from pathlib import Path

MAX_CHATS = 30
MAX_LOG = 200
TITLE_LEN = 48


class ChatStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._chats: dict[str, dict] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for c in data.get("chats", []):
                if isinstance(c, dict) and isinstance(c.get("id"), str) and isinstance(c.get("log"), list):
                    self._chats[c["id"]] = {"id": c["id"], "title": str(c.get("title") or "New chat")[:TITLE_LEN],
                                            "updated": float(c.get("updated") or 0), "log": c["log"][-MAX_LOG:]}
        except (OSError, ValueError, TypeError, AttributeError):
            self._chats = {}      # missing or damaged file: start with no history, never fail the app

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"chats": list(self._chats.values())}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass                  # the conversation still works from memory

    def create(self) -> str:
        with self._lock:
            cid = uuid.uuid4().hex[:12]
            self._chats[cid] = {"id": cid, "title": "New chat", "updated": time.time(), "log": []}
            for old in sorted(self._chats.values(), key=lambda c: c["updated"])[:max(0, len(self._chats) - MAX_CHATS)]:
                del self._chats[old["id"]]
            self._save()
            return cid

    def exists(self, cid) -> bool:
        return cid in self._chats

    def add(self, cid: str, who: str, text: str, used=None) -> str:
        """Append one message; the first thing the user says names the chat. Returns the chat's title."""
        with self._lock:
            c = self._chats[cid]
            entry = {"who": who, "text": text}
            if used:
                entry["used"] = list(used)
            c["log"] = (c["log"] + [entry])[-MAX_LOG:]
            c["updated"] = time.time()
            if who == "me" and c["title"] == "New chat":
                c["title"] = " ".join(text.split())[:TITLE_LEN] or "New chat"
            self._save()
            return c["title"]

    def get(self, cid) -> dict | None:
        c = self._chats.get(cid)
        return {"id": c["id"], "title": c["title"], "log": list(c["log"])} if c else None

    def listing(self) -> list[dict]:
        return [{"id": c["id"], "title": c["title"], "updated": c["updated"], "messages": len(c["log"])}
                for c in sorted(self._chats.values(), key=lambda c: c["updated"], reverse=True)]

    def delete(self, cid) -> bool:
        with self._lock:
            found = self._chats.pop(cid, None) is not None
            if found:
                self._save()
            return found
