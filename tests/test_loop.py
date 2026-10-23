from repofixer.environment import LocalEnv
from repofixer.llm import LLMResponse
from repofixer.loop import run_agent
from repofixer.tools import ToolRegistry
from repofixer.trace import load_trace


class FakeLLM:
    """Scripted LLM: pops one response per complete() call. No network."""

    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools=None, temperature=0.2):
        return self.responses.pop(0)


def test_loop_reads_then_done_and_traces(ws):
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    fake = FakeLLM(
        [
            LLMResponse(
                text="",
                tool_calls=[{"id": "c1", "name": "read_file", "arguments": {"path": "f.txt"}}],
                prompt_tokens=10,
                completion_tokens=5,
                finish_reason="tool_calls",
            ),
            LLMResponse(
                text="",
                tool_calls=[{"id": "c2", "name": "done", "arguments": {"summary": "fixed"}}],
                prompt_tokens=20,
                completion_tokens=5,
                finish_reason="tool_calls",
            ),
        ]
    )
    tp = ws / "t.jsonl"
    run = run_agent("task", fake, ToolRegistry(LocalEnv(ws)), tp, max_steps=10)
    assert run.stopped_reason == "done"
    assert run.done_summary == "fixed"
    assert run.total_tokens == 40
    steps = load_trace(tp)
    assert len(steps) == 2
    assert steps[0].tool_calls[0].name == "read_file"


def test_max_steps_stops(ws):
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    responses = [
        LLMResponse(
            text="",
            tool_calls=[{"id": f"c{i}", "name": "read_file", "arguments": {"path": "f.txt"}}],
            finish_reason="tool_calls",
        )
        for i in range(5)
    ]
    fake = FakeLLM(responses)
    run = run_agent("task", fake, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", max_steps=2)
    assert run.stopped_reason == "max_steps"
    assert len(run.steps) == 2


def test_tool_error_is_returned_not_raised(ws):
    fake = FakeLLM(
        [
            LLMResponse(
                text="",
                tool_calls=[{"id": "c1", "name": "read_file", "arguments": {"path": "missing.txt"}}],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                text="",
                tool_calls=[{"id": "c2", "name": "done", "arguments": {"summary": "gave up"}}],
                finish_reason="tool_calls",
            ),
        ]
    )
    run = run_agent("task", fake, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", max_steps=5)
    assert run.steps[0].tool_calls[0].ok is False
    assert run.steps[0].tool_calls[0].error_type == "ToolError"
