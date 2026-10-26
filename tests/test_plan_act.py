"""Plan-Act separation tests (loop level)."""
from repofixer.environment import LocalEnv
from repofixer.llm import LLMResponse
from repofixer.loop import run_agent
from repofixer.tools import ToolRegistry


def _call(cid, name, args):
    return {"id": cid, "name": name, "arguments": args}


class ScriptLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools=None, temperature=0.2, model=None):
        return self.responses.pop(0)


class RecScriptLLM:
    """Scripts responses AND records the messages sent (for injection checks)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools=None, temperature=0.2, model=None):
        self.seen.append(list(messages))
        return self.responses.pop(0)


def test_plan_phase_blocks_edit_tools(ws):
    (ws / "a.py").write_text("x", encoding="utf-8")
    llm = ScriptLLM(
        [
            # step1 (plan phase): attempt edit -> blocked with error
            LLMResponse(text="", tool_calls=[_call("c1", "edit_file", {"path": "a.py", "old_text": "x", "new_text": "y"})], finish_reason="tool_calls"),
            # step2: record plan + done -> transition
            LLMResponse(text="", tool_calls=[
                _call("c2", "plan", {"goal": "g", "verify": "v", "steps": ["s"]}),
                _call("c3", "done", {"summary": "plan ready"}),
            ], finish_reason="tool_calls"),
            # step3 (act phase): edit now allowed, then done
            LLMResponse(text="", tool_calls=[_call("c4", "edit_file", {"path": "a.py", "old_text": "x", "new_text": "y"})], finish_reason="tool_calls"),
            LLMResponse(text="", tool_calls=[_call("c5", "done", {"summary": "fixed"})], finish_reason="tool_calls"),
        ]
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", plan_first=True, max_steps=10)
    assert run.steps[0].tool_calls[0].error_type is not None  # blocked in plan phase
    assert run.steps[1].phase == "plan"
    assert run.steps[2].phase == "act"
    assert run.steps[2].tool_calls[0].ok is True  # edit allowed in act phase
    assert run.done_summary == "fixed"
    assert run.plan == {"goal": "g", "verify": "v", "steps": ["s"]}


def test_plan_content_injected_at_transition(ws):
    llm = RecScriptLLM(
        [
            LLMResponse(text="", tool_calls=[
                _call("c1", "plan", {"goal": "g1", "verify": "v1", "steps": ["s1"]}),
                _call("c2", "done", {"summary": "plan ready"}),
            ], finish_reason="tool_calls"),
            LLMResponse(text="", tool_calls=[_call("c3", "done", {"summary": "fixed"})], finish_reason="tool_calls"),
        ]
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", plan_first=True, max_steps=10)
    assert run.done_summary == "fixed"
    second_messages = llm.seen[1]
    injected = [str(m.get("content")) for m in second_messages if m.get("role") == "user" and "ACT mode" in str(m.get("content"))]
    assert injected and '"steps"' in injected[0]  # plan JSON is injected for the act phase


def test_plan_phase_forces_transition_on_budget(ws):
    llm = RecScriptLLM(
        [
            LLMResponse(text="", tool_calls=[_call(f"c{i}", "bash", {"command": f"echo {i}"})], finish_reason="tool_calls")
            for i in range(3)
        ] + [LLMResponse(text="", tool_calls=[_call("c9", "done", {"summary": "done anyway"})], finish_reason="tool_calls")],
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", plan_first=True, plan_max_steps=2, max_steps=10)
    assert run.steps[0].phase == "plan"
    assert run.steps[2].phase == "act"  # forced transition after budget
    assert run.done_summary == "done anyway"


def test_plan_first_off_by_default(ws):
    (ws / "a.py").write_text("x", encoding="utf-8")
    llm = ScriptLLM(
        [
            LLMResponse(text="", tool_calls=[_call("c1", "edit_file", {"path": "a.py", "old_text": "x", "new_text": "y"})], finish_reason="tool_calls"),
            LLMResponse(text="", tool_calls=[_call("c2", "done", {"summary": "fixed"})], finish_reason="tool_calls"),
        ]
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", max_steps=10)
    assert run.steps[0].tool_calls[0].error_type is None  # edit allowed immediately
    assert run.done_summary == "fixed"
