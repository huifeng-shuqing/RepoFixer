"""The agent loop.

Structure adapted from learn-claude-code s01 (MIT, rewritten) and
mini-swe-agent (MIT, vendor copy kept for reference): messages accumulate
linearly, every tool result is written back to the model, and the loop ends
when the model stops, calls the `done` tool, or hits the step budget. Every
step is flushed to a JSONL trace immediately (crash-safe).

Context engineering (M4): an optional repo map is injected into the system
prompt, and a ContextManager tiers long observations and selectively evicts
low-value tool results when a token budget is exceeded.

Reliability (M5):
- loop guard: 4 consecutive identical tool calls (name+args) stop the run
- resume: optional state file keeps {next_step, messages} so a crashed run
  continues instead of restarting
- safety policy: dangerous bash commands can be auto-denied or asked for
  approval before execution
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .context import ContextManager
from .llm import LLMClient
from .safety import SafetyPolicy
from .tools import ToolRegistry
from .trace import StepTrace, ToolCallTrace, TraceWriter

SYSTEM_PROMPT = """You are RepoFixer, a software-repair agent working inside a git repository.
You are given a task. Explore, locate the bug, edit files, run tests, and iterate until the task is done.

Rules:
- Read code before changing it; prefer targeted edits (edit_file) over rewriting whole files.
- When tests fail, read the failure output and fix the cause, not the symptom.
- As soon as the tests pass, stop exploring and call `done` immediately.
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
    policy_denials: list[dict] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def save_state(path: Path, step_next: int, messages: list[dict], max_steps: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"step_next": step_next, "messages": messages, "max_steps": max_steps}, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


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
    state_path: Path | None = None,
    policy: SafetyPolicy | None = None,
    loop_guard: int = 4,
    solved_check: Callable[[], bool] | None = None,
) -> AgentRun:
    cm = ContextManager(obs_dir, context_budget)
    step_start = 1
    resumed = False
    if state_path is not None:
        st = load_state(state_path)
        if st and st.get("messages"):
            cm.load(st["messages"])
            step_start = int(st.get("step_next", 1))
            max_steps = max(max_steps, int(st.get("max_steps", 0)))  # new call's cap wins if larger
            resumed = True
    if not resumed:
        system = SYSTEM_PROMPT + (f"\n\n{repo_map}" if repo_map else "")
        cm.add_system(system)
        cm.add_user(task)

    tw = TraceWriter(trace_path)
    run = AgentRun()
    recent_calls: deque[str] = deque(maxlen=loop_guard)
    try:
        for step_no in range(step_start, max_steps + 1):
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
                if state_path is not None:
                    state_path.unlink(missing_ok=True)
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
                blocked = policy.check(call["name"], call["arguments"]) if policy else None
                if blocked is not None:
                    result = blocked
                else:
                    result = registry.call(call["name"], call["arguments"])
                if blocked is not None:
                    run.policy_denials.extend(policy.denied)
                    policy.denied.clear()
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
                recent_calls.append(f"{call['name']}:{json.dumps(call['arguments'], sort_keys=True)}")
                if call["name"] == "done" and result.ok:
                    run.done_summary = result.output
                    run.stopped_reason = "done"

            if (
                len(recent_calls) == recent_calls.maxlen
                and len(set(recent_calls)) == 1
                and run.done_summary is None
            ):
                run.stopped_reason = "loop_detected"

            st.wall_ms = int((time.perf_counter() - t0) * 1000)
            tw.write(st)
            run.steps.append(st)
            if solved_check is not None:
                try:
                    if solved_check():
                        run.stopped_reason = "solved_early"
                except Exception:
                    pass
            if run.done_summary is not None or run.stopped_reason in ("loop_detected", "solved_early"):
                if state_path is not None and run.done_summary is not None:
                    state_path.unlink(missing_ok=True)
                break
            if state_path is not None:
                save_state(state_path, step_no + 1, cm.messages, max_steps)
        run.evictions = list(cm.evictions)
        cm.write_eviction_log()
    finally:
        tw.close()
    return run
