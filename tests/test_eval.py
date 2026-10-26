import pathlib
import shutil

from repofixer.eval import Task, load_dataset, run_eval, summarize
from repofixer.environment import LocalEnv
from repofixer.llm import LLMResponse
from repofixer.tools import ToolRegistry

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "tasks_calc_bug.jsonl"
SAMPLE = ROOT / "samples" / "calc_bug"
FIX = "return a + b  # BUG: should be a * b"
FIXED = "return a * b"


def _tasks(ws):
    """Hermetic copy of the sample repo + task pointing at the copy."""
    dst = ws / "calc_bug"
    shutil.copytree(SAMPLE, dst)
    t = load_dataset(DATA)[0]
    return [Task(id=t.id, problem_statement=t.problem_statement, repo=str(dst),
                 verify_command=t.verify_command, timeout=t.timeout)]


class FixLLM:
    """Scripted LLM that applies the correct fix, then calls done. No network."""

    def complete(self, messages, tools=None, temperature=0.2):
        if not hasattr(self, "used"):
            self.used = True
            return LLMResponse(
                text="",
                tool_calls=[{"id": "c1", "name": "edit_file",
                             "arguments": {"path": "calc/ops.py", "old_text": FIX, "new_text": FIXED}}],
                prompt_tokens=100,
                completion_tokens=50,
                finish_reason="tool_calls",
            )
        return LLMResponse(
            text="",
            tool_calls=[{"id": "c2", "name": "done", "arguments": {"summary": "fixed mul"}}],
            prompt_tokens=200,
            completion_tokens=50,
            finish_reason="tool_calls",
        )


class ObserveLLM:
    """Never edits; declares done immediately."""

    def complete(self, messages, tools=None, temperature=0.2):
        return LLMResponse(
            text="",
            tool_calls=[{"id": "c1", "name": "done", "arguments": {"summary": "cannot fix without edit"}}],
            finish_reason="tool_calls",
        )


def test_eval_pipeline_resolves(ws):
    tasks = _tasks(ws)
    results = run_eval(tasks, FixLLM(), "local", ws, base_dir=ws)
    assert results[0].resolved is True
    # the fix lands on step 1; the verify-based early stop fires before `done`
    assert results[0].steps == 1
    assert results[0].stopped_reason == "solved_early"
    assert (ws / "results.jsonl").exists()
    assert (ws / "traces" / "calc_bug-1.jsonl").exists()
    s = summarize(results, "deepseek-chat")
    assert s["success_rate"] == 1.0
    assert s["est_cost_yuan"] > 0


def test_eval_resume_skips_done(ws):
    tasks = _tasks(ws)
    run_eval(tasks, FixLLM(), "local", ws, base_dir=ws)
    results2 = run_eval(tasks, FixLLM(), "local", ws, base_dir=ws)
    assert len(results2) == 1
    with (ws / "results.jsonl").open(encoding="utf-8") as f:
        assert len(f.read().splitlines()) == 1


def test_noedit_baseline_fails(ws):
    tasks = _tasks(ws)
    results = run_eval(
        tasks, ObserveLLM(), "local", ws, base_dir=ws,
        tool_filter={"read_file", "bash", "grep", "done"},
    )
    assert results[0].resolved is False


def test_registry_filter(ws):
    reg = ToolRegistry(LocalEnv(ws))
    reg.filter({"read_file", "done"})
    assert sorted(reg._tools) == ["done", "read_file"]


class FlakyLLM:
    """Raises once (simulating a transient provider error), then behaves like FixLLM."""

    def __init__(self):
        self.raised = False

    def complete(self, messages, tools=None, temperature=0.2, model=None):
        if not self.raised:
            self.raised = True
            raise ConnectionError("transient")
        return FixLLM().complete(messages, tools, temperature)


class AlwaysFailLLM:
    def complete(self, messages, tools=None, temperature=0.2, model=None):
        raise ConnectionError("persistent")


def test_eval_retries_transient_llm_error(ws):
    tasks = _tasks(ws)
    results = run_eval(tasks, FlakyLLM(), "local", ws, base_dir=ws)
    assert results[0].resolved is True  # one retry recovered the task


def test_eval_records_persistent_llm_error(ws):
    tasks = _tasks(ws)
    results = run_eval(tasks, AlwaysFailLLM(), "local", ws, base_dir=ws)
    assert results[0].resolved is False
    assert results[0].stopped_reason == "llm_error"
    assert results[0].error
