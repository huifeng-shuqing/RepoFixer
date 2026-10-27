"""Tests for the upgrade batch: plan tool, repo notes, snapshots, review."""
import json
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


def test_extract_and_merge_dedupes_and_caps(ws):
    from repofixer.notes import extract_and_merge

    class ExtLLM:
        def __init__(self, outputs):
            self.outputs = list(outputs)

        def complete(self, messages, tools=None, temperature=0.2, model=None):
            return LLMResponse(text=self.outputs.pop(0), finish_reason="stop")

    append_note(ws, "build: uv run pytest")
    out1 = extract_and_merge(ws, "task failed at test_x", ExtLLM(["- build: uv run pytest\n- pitfall: cache in .pytest_cache"]))
    assert out1
    text = load_notes(ws)
    assert text.count("- build: uv run pytest") == 1  # deduped
    assert "- pitfall: cache in .pytest_cache" in text
    # cap: keep only the newest MAX_FACTS facts
    many = [f"- fact {i}" for i in range(40)]
    extract_and_merge(ws, "session", ExtLLM(["\n".join(many)]))
    text2 = load_notes(ws)
    assert "fact 39" in text2 and "fact 0" not in text2
    assert len([l for l in text2.splitlines() if l.strip().startswith("-")]) <= 30


def test_extract_and_merge_survives_llm_error(ws):
    from repofixer.notes import extract_and_merge

    class BoomLLM:
        def complete(self, messages, tools=None, temperature=0.2, model=None):
            raise ConnectionError("down")

    append_note(ws, "- kept")
    assert extract_and_merge(ws, "session", BoomLLM()) == ""
    assert "- kept" in load_notes(ws)


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


# ---------- safety rules file ----------

def _write_rules(ws, rules):
    d = ws / ".repofixer"
    d.mkdir(parents=True, exist_ok=True)
    (d / "rules.json").write_text(
        json.dumps({"rules": rules}), encoding="utf-8"
    )


def test_rules_allow_overrides_builtin(ws):
    from repofixer.safety import SafetyPolicy

    _write_rules(ws, [{"tool": "bash", "pattern": "pytest", "action": "allow"}])
    p = SafetyPolicy("auto", workspace=ws)
    assert p.check("bash", {"command": "pytest -q"}) is None  # allowed by rule


def test_rules_last_match_wins(ws):
    from repofixer.safety import SafetyPolicy

    _write_rules(ws, [
        {"tool": "bash", "pattern": "git", "action": "allow"},
        {"tool": "bash", "pattern": "git push", "action": "deny"},
    ])
    p = SafetyPolicy("auto", workspace=ws)
    assert p.check("bash", {"command": "git status"}) is None
    assert p.check("bash", {"command": "git push origin main"}) is not None  # deny wins


def test_rules_deny_non_dangerous(ws):
    from repofixer.safety import SafetyPolicy

    _write_rules(ws, [{"tool": "bash", "pattern": "curl", "action": "deny"}])
    p = SafetyPolicy("auto", workspace=ws)
    r = p.check("bash", {"command": "curl http://x"})
    assert r is not None and not r.ok


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


def test_rollback_restores_snapshot(ws):
    from repofixer.gitutil import rollback

    head_before = _git_repo(ws)
    (ws / "f.txt").write_text("CHANGED", encoding="utf-8")
    rollback(ws, head_before)
    assert (ws / "f.txt").read_text(encoding="utf-8") == "hello"


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
