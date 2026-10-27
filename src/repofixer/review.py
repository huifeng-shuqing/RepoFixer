"""Verifier: independent-context review of the produced diff.

Pattern borrowed from Claude Code's reviewer subagent and OpenCode's reviewer
agent (reference/opencode): after the main loop claims done, the diff is sent
to a FRESH conversation that only sees (task, diff) and returns a verdict.
Critical issues are fed back into the main loop for one correction round.
"""
from __future__ import annotations

import re

REVIEW_SYSTEM = """You are a strict code reviewer. You receive a task description and a git diff
produced by another agent. Judge ONLY whether the diff correctly and completely
solves the task, and whether it introduces obvious regressions.

Reply in this exact format:
VERDICT: OK
or
VERDICT: ISSUES
- <concrete, actionable issue 1>
- <issue 2>
"""


def build_review_messages(task: str, diff: str, max_diff_chars: int = 12_000) -> list[dict]:
    if len(diff) > max_diff_chars:
        diff = diff[:max_diff_chars] + "\n... [diff truncated]"
    return [
        {"role": "system", "content": REVIEW_SYSTEM},
        {"role": "user", "content": f"Task:\n{task}\n\nDiff:\n{diff}"},
    ]


def parse_verdict(text: str) -> tuple[bool, str]:
    """Returns (ok, issues_text). ok=True only on an explicit OK verdict."""
    if re.search(r"VERDICT\s*:\s*OK\b", text, re.I):
        return True, ""
    issues = text.replace("VERDICT: ISSUES", "").strip()
    return False, issues


def review_feedback(issues: str) -> str:
    return (
        "A reviewer examined your changes and found issues. Address them before finishing:\n"
        f"{issues}"
    )


def final_summary(task: str, llm, cm, reason: str, max_steps: int) -> str | None:
    """OpenCode-style forced final summary: one last (cheap) call asking the
    model what blocked completion, so failed runs self-report their root cause
    instead of ending with a bare stop reason."""
    messages = [
        {"role": "system", "content": (
            "You are a repair agent whose run was terminated by the harness. "
            "In at most 80 words, state concisely what blocked you from "
            "finishing and what remains to be done."
        )},
        {"role": "user", "content": f"Task: {task}\n\nRun ended with reason: {reason} (max {max_steps} steps)."},
    ]
    last = [m for m in cm.messages if m["role"] == "assistant"]
    if last:
        messages.append({"role": "user", "content": f"Your last output: {str(last[-1].get('content'))[:800]}"})
    try:
        return llm.complete(messages, None, temperature=0.0, model=getattr(llm, "small_model", None)).text.strip()
    except Exception:
        return None
