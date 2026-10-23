"""Structured per-step tracing (JSONL).

Every loop step is appended as one JSON line: tokens, latency, finish reason
and each tool call (name/args/ok/error/output size). This is the substrate for
replay, resume, cost accounting and the failure-attribution report in later
milestones.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ToolCallTrace:
    name: str
    args: dict
    ok: bool
    error_type: str | None = None
    output_chars: int = 0
    truncated: bool = False
    latency_ms: int = 0


@dataclass
class StepTrace:
    step: int
    ts: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = ""
    assistant_text: str = ""
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    wall_ms: int = 0


class TraceWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("w", encoding="utf-8")

    def write(self, step: StepTrace) -> None:
        self._f.write(json.dumps(asdict(step), ensure_ascii=False) + "\n")
        self._f.flush()  # survive a crash: every step is on disk immediately

    def close(self) -> None:
        self._f.close()


def load_trace(path: Path) -> list[StepTrace]:
    """Replay loader (used by the resume milestone)."""
    steps = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            d["tool_calls"] = [ToolCallTrace(**t) for t in d.get("tool_calls", [])]
            steps.append(StepTrace(**d))
    return steps
