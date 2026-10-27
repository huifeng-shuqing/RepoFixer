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

from .context import ContextManager, estimate_tokens
from .gitutil import diff as _get_diff
from .gitutil import snapshot as _snapshot
from .llm import LLMClient
from .review import build_review_messages, final_summary, parse_verdict, review_feedback
from .safety import SafetyPolicy
from .tools import ToolRegistry, ToolResult
from .trace import StepTrace, ToolCallTrace, TraceWriter

SYSTEM_PROMPT = """You are RepoFixer, a software-repair agent working inside a git repository.
You are given a task. Explore, locate the bug, edit files, run tests, and iterate until the task is done.

Rules:
- Read code before changing it; prefer targeted edits (edit_file) over rewriting whole files.
- Prefer the dedicated tools (read_file/edit_file/grep/run_test) over shell commands; use bash mainly for running commands that have no dedicated tool.
- Before editing, call `plan` to record your goal, how you will verify it, and your steps; update the plan as you progress.
- When tests fail, read the failure output and fix the cause, not the symptom.
- As soon as the tests pass, stop exploring and call `done` immediately.
- Call `done` with a summary ONLY when the task is finished. Never call done while tests still fail.
- When you need several independent pieces of information (several files or searches), request them in a single response with multiple tool calls.
- Do not try to read the whole codebase: make a small targeted edit as soon as you have a plausible cause, then use test output to steer. Read-only exploration for more than ~10 steps counts as being stuck.
- Be terse: report what you changed and what you verified.
"""

PLAN_MODE_PROMPT = """You are in PLAN mode: you CANNOT modify files yet (edit_file/write_file are disabled).
Explore the repository (read_file/grep/bash/run_test), form a root-cause hypothesis,
then record your repair plan with the `plan` tool (goal, how you will verify, concrete steps)
and call `done` to switch to ACT mode where you will execute it.
Spend at most a few steps here; the plan does not need to be perfect."""

# Plan-phase tool allowlist: exploration and the plan tool, no mutations.
PLAN_TOOLS = {"read_file", "grep", "bash", "run_test", "plan", "done"}


@dataclass
class AgentRun:
    steps: list[StepTrace] = field(default_factory=list)
    done_summary: str | None = None
    stopped_reason: str = "max_steps"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    evictions: list[dict] = field(default_factory=list)
    policy_denials: list[dict] = field(default_factory=list)
    review_rounds: int = 0
    review_feedback: str | None = None
    review_triggered: bool = False
    plan: dict | None = None
    final_summary: str | None = None
    snapshots: list[str] = field(default_factory=list)

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
    loop_guard: int = 6,
    loop_warn_at: int = 3,
    solved_check: Callable[[], bool] | None = None,
    notes: str | None = None,
    snapshot: bool = False,
    review: bool = False,
    max_review_rounds: int = 1,
    review_min_steps: int = 15,
    read_nudge_at: int = 10,
    read_force_at: int = 15,
    plan_first: bool = False,
    plan_max_steps: int = 15,
) -> AgentRun:
    cm = ContextManager(obs_dir, context_budget)
    step_start = 1
    resumed = False
    phase = "plan" if plan_first else "act"
    if state_path is not None:
        st = load_state(state_path)
        if st and st.get("messages"):
            cm.load(st["messages"])
            step_start = int(st.get("step_next", 1))
            max_steps = max(max_steps, int(st.get("max_steps", 0)))  # new call's cap wins if larger
            resumed = True
    if not resumed:
        system = SYSTEM_PROMPT
        if plan_first:
            system += f"\n\n{PLAN_MODE_PROMPT}"
        if notes:
            system += f"\n\nRepository notes from previous sessions:\n{notes}"
        system += f"\n\n{repo_map}" if repo_map else ""
        cm.add_system(system)
        cm.add_user(task)

    def _active_schemas() -> list[dict]:
        if phase == "plan":
            return [s for s in registry.schemas() if s["function"]["name"] in PLAN_TOOLS]
        return registry.schemas()

    def _enter_act(force: bool = False) -> None:
        nonlocal phase
        plan_path = registry.env.resolve(Path(".repofixer/plan.json"))
        plan_text = ""
        try:
            if plan_path.exists():
                plan_text = plan_path.read_text(encoding="utf-8", errors="replace")
                run.plan = json.loads(plan_text)
        except (OSError, json.JSONDecodeError):
            run.plan = None
        phase = "act"
        if plan_text:
            cm.add_user(
                "Plan mode finished. You are now in ACT mode with full edit tools. "
                f"Execute this plan, update it as needed:\n{plan_text}"
            )
        else:
            cm.add_user(
                "Plan mode finished" + (" (step budget exceeded)" if force else "") +
                ". You are now in ACT mode with full edit tools. Proceed with your best judgment."
            )

    tw = TraceWriter(trace_path)
    run = AgentRun()
    # two-level loop guard: warn after N consecutive identical tool calls,
    # kill after M (per-call granularity, interleaved edits reset the counter)
    last_call_sig: str | None = None
    consecutive_identical = 0
    loop_warned = False
    # read-budget guard: steps since the last file-modifying tool call; a long
    # read-only stretch means the model is stuck in exploration (the dominant
    # failure mode on hard tasks: 40 steps of sed/grep and zero edits)
    steps_since_edit = 0
    read_nudged = False
    read_forced = False
    # no_progress: true probe loops show tiny bash outputs repeatedly; real
    # investigation (sed/pytest) produces large outputs. Track per-step bash
    # output size; -1 marks a step that used any non-bash tool.
    recent_bash_sizes: deque[int] = deque(maxlen=8)
    steps_in_phase = 0
    try:
        for step_no in range(step_start, max_steps + 1):
            t0 = time.perf_counter()
            messages = cm.prepare()
            # second-chance compaction: if eviction alone is not enough, ask
            # the model to summarize the oldest history into one block
            if cm.token_budget and estimate_tokens(messages) > cm.token_budget:
                if cm.compact_with_llm(lambda old: llm.summarize(old)):
                    messages = cm.prepare()
            resp = llm.complete(messages, _active_schemas())
            run.prompt_tokens += resp.prompt_tokens
            run.completion_tokens += resp.completion_tokens
            st = StepTrace(
                step=step_no,
                ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                finish_reason=resp.finish_reason,
                assistant_text=resp.text[:500],
                phase=phase,
            )

            if not resp.tool_calls:
                if phase == "plan":
                    # model stopped without recording a plan: force the transition
                    _enter_act(force=True)
                    steps_in_phase = 0
                    tw.write(st)
                    run.steps.append(st)
                    continue
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
                blocked = None
                if phase == "plan" and call["name"] not in PLAN_TOOLS:
                    result = ToolResult.err("this tool is disabled in plan mode; explore and call `plan`/`done` first")
                else:
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
                sig = f"{call['name']}:{json.dumps(call['arguments'], sort_keys=True)}"
                consecutive_identical = consecutive_identical + 1 if sig == last_call_sig else 1
                last_call_sig = sig
                if call["name"] == "done" and result.ok:
                    if phase == "plan":
                        _enter_act()
                        steps_in_phase = 0
                    else:
                        run.done_summary = result.output
                        run.stopped_reason = "done"

            steps_in_phase += 1
            if phase == "plan" and steps_in_phase >= plan_max_steps:
                _enter_act(force=True)
                steps_in_phase = 0

            step_bash_chars = sum(tc.output_chars for tc in st.tool_calls if tc.name == "bash")
            if st.tool_calls and all(tc.name == "bash" for tc in st.tool_calls):
                recent_bash_sizes.append(step_bash_chars)
            else:
                recent_bash_sizes.append(-1)

            if any(tc.name in ("edit_file", "write_file") for tc in st.tool_calls):
                steps_since_edit = 0
            elif phase == "act":
                steps_since_edit += 1
            if (
                phase == "act"
                and steps_since_edit >= read_force_at
                and not read_forced
                and run.done_summary is None
            ):
                read_forced = True
                cm.add_user(
                    "You have spent many steps reading without any modification. "
                    "Stop exploring now: call edit_file or write_file with your best "
                    "minimal fix immediately, then run the tests and adjust from there."
                )
            elif (
                phase == "act"
                and steps_since_edit >= read_nudge_at
                and not read_nudged
                and run.done_summary is None
            ):
                read_nudged = True
                cm.add_user(
                    "You have been exploring for several steps without an edit. "
                    "Make a small targeted edit with your current best hypothesis and "
                    "use the test output to steer further."
                )

            if (
                consecutive_identical >= loop_warn_at
                and not loop_warned
                and run.done_summary is None
            ):
                loop_warned = True
                cm.add_user(
                    "Warning: you are repeating the same action. If you are stuck, "
                    "change your approach or state what is blocking you."
                )
            if consecutive_identical >= loop_guard and run.done_summary is None:
                run.stopped_reason = "loop_detected"
            elif (
                step_no >= 12
                and len(recent_bash_sizes) == recent_bash_sizes.maxlen
                and all(0 <= x < 400 for x in recent_bash_sizes)
                and run.done_summary is None
            ):
                run.stopped_reason = "no_progress"

            st.wall_ms = int((time.perf_counter() - t0) * 1000)
            tw.write(st)
            run.steps.append(st)
            if snapshot:
                sha = _snapshot(getattr(registry.env, "workspace", None), step_no)
                if sha:
                    run.snapshots.append(sha)
            if phase == "act" and solved_check is not None and any(
                tc.name in ("edit_file", "write_file") for tc in st.tool_calls
            ):
                # verify only after steps that actually changed files: pure
                # read/probe steps cannot have fixed anything, and skipping
                # them halves verify frequency (and its flaky-test exposure)
                try:
                    if solved_check():
                        run.stopped_reason = "solved_early"
                except Exception:
                    pass
            # Independent-context review: when the model declares done, a fresh
            # conversation judges the diff; critical issues reopen the loop for
            # at most max_review_rounds correction rounds. Gated to long or
            # error-prone runs only (the review costs a full extra call).
            had_tool_errors = any(
                tc.error_type for s in run.steps for tc in s.tool_calls
            )
            if (
                run.done_summary is not None
                and review
                and run.review_rounds < max_review_rounds
                and (len(run.steps) >= review_min_steps or had_tool_errors)
            ):
                run.review_triggered = True
                run.review_rounds += 1
                diff = _get_diff(getattr(registry.env, "workspace", None))
                if diff:
                    try:
                        rtext = llm.complete(
                            build_review_messages(task, diff), None,
                            temperature=0.0, model=getattr(llm, "small_model", None),
                        ).text
                        ok, issues = parse_verdict(rtext)
                        if not ok and issues:
                            run.review_feedback = issues
                            cm.add_user(review_feedback(issues))
                            run.done_summary = None
                            run.stopped_reason = "review_issues"
                    except Exception:
                        pass
            if run.done_summary is not None or run.stopped_reason in ("loop_detected", "solved_early", "no_progress"):
                if state_path is not None and run.done_summary is not None:
                    state_path.unlink(missing_ok=True)
                break
            if state_path is not None:
                save_state(state_path, step_no + 1, cm.messages, max_steps)
        if run.done_summary is None and run.steps:
            run.final_summary = final_summary(task, llm, cm, run.stopped_reason, max_steps)
        run.evictions = list(cm.evictions)
        cm.write_eviction_log()
    finally:
        tw.close()
    return run
