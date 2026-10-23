"""Context management: observation tiering + selective eviction.

Idea from learn-claude-code s08's layered compaction (MIT; rewritten):
1. tiering at ingestion — long tool outputs are saved to disk and replaced
   by a preview inside the message list
2. importance marking — error / test-failure content is kept longer
3. selective eviction before each LLM call — when the estimated token count
   exceeds the budget, the least valuable old tool results are replaced by
   placeholders (full text stays on disk under obs/)

Every eviction is recorded in <obs_dir>/evictions.jsonl so ablations can
report exactly what was dropped and why.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PREVIEW_CHARS = 2_000
IMPORTANT_RE = re.compile(r"FAIL|Error|Traceback|assert|Exception|FAILED|failed|error", re.I)
EST_CHARS_PER_TOKEN = 4


def estimate_tokens(messages: list[dict]) -> int:
    return len(json.dumps(messages, ensure_ascii=False)) // EST_CHARS_PER_TOKEN


def is_important(text: str) -> bool:
    return bool(IMPORTANT_RE.search(text[:4000]))


class ContextManager:
    def __init__(self, obs_dir: Path | None = None, token_budget: int | None = 60_000, keep_recent: int = 3):
        self.messages: list[dict] = []
        self.obs_dir = obs_dir
        self.token_budget = token_budget
        self.keep_recent = keep_recent
        self.evictions: list[dict] = []
        self._age = 0
        self._tool_index: list[tuple[int, dict]] = []  # (message index, meta)
        if obs_dir is not None:
            obs_dir.mkdir(parents=True, exist_ok=True)

    def add_system(self, content: str) -> None:
        self.messages.append({"role": "system", "content": content})

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, text: str | None, tool_calls: list[dict]) -> None:
        self.messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})

    def add_tool(self, call_id: str, name: str, output: str) -> None:
        self._age += 1
        obs = None
        content = output
        if self.obs_dir is not None and len(output) > PREVIEW_CHARS:
            obs = f"{self._age:04d}_{name}.txt"
            (self.obs_dir / obs).write_text(output, encoding="utf-8")
            content = output[:PREVIEW_CHARS] + f"\n... [full output saved: obs/{obs}, {len(output)} chars]"
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
        meta = {"age": self._age, "name": name, "size": len(output), "important": is_important(output), "obs": obs, "evicted": False}
        self._tool_index.append((len(self.messages) - 1, meta))

    def load(self, messages: list[dict]) -> None:
        """Adopt an existing message list (resume path); rebuild eviction metadata."""
        self.messages = list(messages)
        self._tool_index = []
        self._age = 0
        for idx, m in enumerate(self.messages):
            if m.get("role") != "tool":
                continue
            self._age += 1
            content = str(m.get("content", ""))
            meta = {
                "age": self._age,
                "name": "tool",
                "size": len(content),
                "important": is_important(content),
                "obs": None,
                "evicted": False,
            }
            self._tool_index.append((idx, meta))

    def prepare(self) -> list[dict]:
        if self.token_budget is None:
            return self.messages
        for _ in range(200):
            if estimate_tokens(self.messages) <= self.token_budget:
                break
            if not self._evict_one():
                break
        return self.messages

    def _evict_one(self) -> bool:
        candidates = [
            (idx, meta)
            for idx, meta in self._tool_index
            if not meta["evicted"] and meta["age"] <= self._age - self.keep_recent
        ]
        if not candidates:
            return False

        def value(item: tuple[int, dict]) -> float:
            _, meta = item
            v = (2.0 if meta["important"] else 1.0) * (0.9 ** (self._age - meta["age"]))
            return v - meta["size"] * 0.0005

        idx, meta = min(candidates, key=value)
        meta["evicted"] = True
        obs_ref = f", see obs/{meta['obs']}" if meta["obs"] else ""
        self.messages[idx]["content"] = f"[{meta['name']} output evicted: {meta['size']} chars{obs_ref}]"
        self.evictions.append(
            {"age": meta["age"], "name": meta["name"], "size": meta["size"], "important": meta["important"]}
        )
        return True

    def write_eviction_log(self) -> None:
        if self.obs_dir is not None and self.evictions:
            with (self.obs_dir / "evictions.jsonl").open("a", encoding="utf-8") as f:
                for e in self.evictions:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
