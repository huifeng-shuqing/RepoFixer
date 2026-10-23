"""The agent loop.

Structure adapted from learn-claude-code s01 (MIT, rewritten) and
mini-swe-agent (MIT, vendor copy kept for reference): messages accumulate
linearly, every tool result is written back to the model, and the loop ends
when the model stops, calls the `done` tool, or hits the step budget. Every
step is flushed to a JSONL trace immediately (crash-safe).

Context engineering (M4): an optional repo map is injected into the system
prompt, and a ContextManager tiers long observations and selectively evicts
low-value tool results when a token budget is exceeded.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .context import ContextManager
from .llm import LLMClient
from .tools import ToolRegistry
from .trace import StepTrace, ToolCallTrace, TraceWriter

SYSTEM_PROMPT = """You are RepoFixer, a software-repair agent working inside a git repository.
You are given a task. Explore, locate the bug, edit files, run tests, and iterate until the task is done.

Rules:
- Read code before changing it; prefer targeted edits (edit_file) over rewriting whole files.
- When tests fail, read the failure output and fix the cause, not the symptom.
- Call `done` with a summary ONLY when the task is finished. Never call done while tests still fail.
- Be terse: report what you changed and what you verified.
"""


@dataclass
class AgentRun:
    steps: list[StepTrace] = field(default_factory=list)
    done_summary: str | None = None
    stopped_reason: str = "max_steps"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    evictions: list[dict] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def run_agent(
    task: str,
    llm: LLMClient,
    registry: ToolRegistry,
    trace_path: Path,
    *,
    max_steps: int = 40,
    repo_map: str | None = None,
    context_budget: int | None = None,
    obs_dir: Path | None = None,
) -> AgentRun:
    cm = ContextManager(obs_dir, context_budget)
    system = SYSTEM_PROMPT + (f"\n\n{repo_map}" if repo_map else "")
    cm.add_system(system)
    cm.add_user(task)
    tw = TraceWriter(trace_path)
    run = AgentRun()
    try:
        for step_no in range(1, max_steps + 1):
            t0 = time.perf_counter()
            resp = llm.complete(cm.prepare(), registry.schemas())
            run.prompt_tokens += resp.prompt_tokens
            run.completion_tokens += resp.completion_tokens
            st = StepTrace(
                step=step_no,
                ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                finish_reason=resp.finish_reason,
                assistant_text=resp.text[:500],
            )

            if not resp.tool_calls:
                run.stopped_reason = "model_stopped"
                tw.write(st)
                run.steps.append(st)
                break

            cm.add_assistant(
                resp.text or None,
                [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
                    }
                    for c in resp.tool_calls
                ],
            )
            for call in resp.tool_calls:
                t1 = time.perf_counter()
                result = registry.call(call["name"], call["arguments"])
                st.tool_calls.append(
                    ToolCallTrace(
                        name=call["name"],
                        args=call["arguments"],
                        ok=result.ok,
                        error_type=result.error_type,
                        output_chars=len(result.output),
                        truncated=result.truncated,
                        latency_ms=int((time.perf_counter() - t1) * 1000),
                    )
                )
                cm.add_tool(call["id"], call["name"], result.output)
                if call["name"] == "done" and result.ok:
                    run.done_summary = result.output
                    run.stopped_reason = "done"

            st.wall_ms = int((time.perf_counter() - t0) * 1000)
            tw.write(st)
            run.steps.append(st)
            if run.done_summary is not None:
                break
        run.evictions = list(cm.evictions)
        cm.write_eviction_log()
    finally:
        tw.close()
    return run
