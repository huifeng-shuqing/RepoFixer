"""Tests for the upgrade batch: plan tool, repo notes, snapshots, review."""
import subprocess

from repofixer.environment import LocalEnv
from repofixer.llm import LLMResponse
from repofixer.loop import run_agent
from repofixer.notes import append_note, load_notes
from repofixer.tools import ToolRegistry, build_tools


def _git(ws, *args):
    return subprocess.run(
        ["git", "-C", str(ws), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )


def _git_repo(ws):
    _git(ws, "init", "-q")
    _git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    _git(ws, "add", "f.txt")
    _git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "f")
    return _git(ws, "rev-parse", "HEAD").stdout.strip()


class ScriptLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools=None, temperature=0.2):
        return self.responses.pop(0)


class ReviewLLM:
    """tools=None responses are the reviewer's fresh-context replies."""

    def __init__(self, main_responses, review_responses):
        self.main = list(main_responses)
        self.reviews = list(review_responses)

    def complete(self, messages, tools=None, temperature=0.2, model=None):
        if tools is None:
            return self.reviews.pop(0)
        return self.main.pop(0)


def _edit_call(path="f.txt", old="hello", new="HELLO"):
    return {"id": "c1", "name": "edit_file", "arguments": {"path": path, "old_text": old, "new_text": new}}


def _done_call(summary="fixed"):
    return {"id": "c2", "name": "done", "arguments": {"summary": summary}}


# ---------- plan tool ----------

def test_plan_tool_write_and_read(ws):
    reg = ToolRegistry(LocalEnv(ws))
    r = reg.call("plan", {"goal": "g", "verify": "v", "steps": ["a", "b"]})
    assert r.ok and "2 steps" in r.output
    assert (ws / ".repofixer" / "plan.json").exists()
    r2 = reg.call("plan", {})
    assert r2.ok and "steps" in r2.output


# ---------- repo notes ----------

def test_notes_roundtrip(ws):
    assert load_notes(ws) == ""
    append_note(ws, "first entry")
    text = load_notes(ws)
    assert "first entry" in text
    append_note(ws, "second entry")
    assert "second entry" in load_notes(ws)


def test_notes_injected_into_system(ws):
    class CaptureLLM:
        def __init__(self):
            self.seen = []

        def complete(self, messages, tools=None, temperature=0.2):
            self.seen.append(list(messages))
            return LLMResponse(text="", tool_calls=[_done_call()], finish_reason="tool_calls")

    llm = CaptureLLM()
    run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", notes="NOTE123")
    assert "NOTE123" in llm.seen[0][0]["content"]


# ---------- snapshots ----------

def test_snapshot_preserves_baseline(ws):
    head_before = _git_repo(ws)
    llm = ScriptLLM(
        [
            LLMResponse(text="", tool_calls=[_edit_call()], finish_reason="tool_calls"),
            LLMResponse(text="", tool_calls=[_done_call()], finish_reason="tool_calls"),
        ]
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", snapshot=True, max_steps=5)
    assert run.snapshots, "a step that changed a tracked file must produce a snapshot sha"
    assert _git(ws, "rev-parse", "HEAD").stdout.strip() == head_before  # HEAD untouched
    # eval-style reset still restores the baseline
    _git(ws, "checkout", "--", ".")
    _git(ws, "clean", "-fdq")
    assert (ws / "f.txt").read_text(encoding="utf-8") == "hello"


# ---------- final summary ----------

def test_final_summary_on_failed_run(ws):
    """A failed run triggers one last (tool-free) call that self-reports the blocker."""
    from repofixer.tools import ToolRegistry

    class ScriptedFinalLLM:
        def __init__(self):
            self.n = 0

        def complete(self, messages, tools=None, temperature=0.2, model=None):
            self.n += 1
            if tools is None:
                return LLMResponse(text="blocked: could not reproduce the failing test", finish_reason="stop")
            return LLMResponse(text="", tool_calls=[{"id": f"c{self.n}", "name": "bash", "arguments": {"command": f"echo probe{self.n}"}}], finish_reason="tool_calls")

    llm = ScriptedFinalLLM()
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", max_steps=2)
    assert run.done_summary is None
    assert run.stopped_reason == "max_steps"
    assert run.final_summary == "blocked: could not reproduce the failing test"


# ---------- MCP conversion ----------

def test_mcp_tool_conversion(ws):
    from repofixer.mcp_server import to_mcp_tools

    specs = to_mcp_tools(build_tools(LocalEnv(ws)))
    names = {s["name"] for s in specs}
    assert {"read_file", "edit_file", "bash", "plan", "done"} <= names
    for s in specs:
        assert s["inputSchema"]["type"] == "object"


# ---------- verifier ----------

def test_review_issues_reopen_loop(ws):
    _git_repo(ws)
    llm = ReviewLLM(
        main_responses=[
            LLMResponse(text="", tool_calls=[_edit_call()], finish_reason="tool_calls"),
            LLMResponse(text="", tool_calls=[_done_call("fixed v1")], finish_reason="tool_calls"),
            LLMResponse(text="", tool_calls=[_done_call("fixed v2")], finish_reason="tool_calls"),
        ],
        review_responses=[
            LLMResponse(text="VERDICT: ISSUES\n- missing test run", finish_reason="stop"),
            LLMResponse(text="VERDICT: OK", finish_reason="stop"),
        ],
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", review=True, max_steps=10, review_min_steps=1)
    assert run.review_rounds == 1  # max_review_rounds=1: one review, at most one correction round
    assert "missing test run" in (run.review_feedback or "")
    assert run.done_summary == "fixed v2"
    assert run.stopped_reason == "done"


def test_review_gated_off_for_short_clean_runs(ws):
    """Default gate: review only for long runs or runs with tool errors."""
    _git_repo(ws)
    llm = ReviewLLM(
        main_responses=[
            LLMResponse(text="", tool_calls=[_edit_call()], finish_reason="tool_calls"),
            LLMResponse(text="", tool_calls=[_done_call("fixed")], finish_reason="tool_calls"),
        ],
        review_responses=[LLMResponse(text="VERDICT: OK", finish_reason="stop")],
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", review=True, max_steps=10)
    assert run.review_triggered is False
    assert run.review_rounds == 0
    assert run.done_summary == "fixed"


def test_review_ok_passes_through(ws):
    _git_repo(ws)
    llm = ReviewLLM(
        main_responses=[
            LLMResponse(text="", tool_calls=[_edit_call()], finish_reason="tool_calls"),
            LLMResponse(text="", tool_calls=[_done_call("fixed")], finish_reason="tool_calls"),
        ],
        review_responses=[LLMResponse(text="VERDICT: OK", finish_reason="stop")],
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", review=True, max_steps=10, review_min_steps=1)
    assert run.review_rounds == 1
    assert run.review_feedback is None
    assert run.done_summary == "fixed"
