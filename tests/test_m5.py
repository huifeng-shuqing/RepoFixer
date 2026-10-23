from repofixer.context import ContextManager
from repofixer.environment import LocalEnv
from repofixer.llm import LLMResponse
from repofixer.loop import load_state, run_agent, save_state
from repofixer.safety import SafetyPolicy, is_dangerous
from repofixer.tools import ToolRegistry


class ScriptedLLM:
    """Pops one scripted response per complete() call."""

    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools=None, temperature=0.2):
        return self.responses.pop(0)


def _bash_call(rid: str, command: str) -> dict:
    return {"id": rid, "name": "bash", "arguments": {"command": command}}


def test_loop_guard_stops_repetition(ws):
    llm = ScriptedLLM(
        [LLMResponse(text="", tool_calls=[_bash_call(f"c{i}", "echo hi")], finish_reason="tool_calls") for i in range(10)]
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", max_steps=20)
    assert run.stopped_reason == "loop_detected"
    assert len(run.steps) == 4


def test_resume_continues_from_state(ws):
    responses = [
        LLMResponse(text="", tool_calls=[_bash_call("c1", "echo a")], finish_reason="tool_calls"),
        LLMResponse(text="", tool_calls=[_bash_call("c2", "echo b")], finish_reason="tool_calls"),
        LLMResponse(text="", tool_calls=[{"id": "c3", "name": "done", "arguments": {"summary": "ok"}}], finish_reason="tool_calls"),
    ]
    state = ws / "run.state.json"
    r1 = run_agent("task", ScriptedLLM(responses[:1]), ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", max_steps=1, state_path=state)
    assert r1.stopped_reason == "max_steps"
    assert state.exists()
    st = load_state(state)
    assert st["step_next"] == 2
    assert len(st["messages"]) == 4  # system, user, assistant(+tool_calls), tool result
    r2 = run_agent("task", ScriptedLLM(responses[1:]), ToolRegistry(LocalEnv(ws)), ws / "t2.jsonl", max_steps=10, state_path=state)
    assert r2.stopped_reason == "done"
    assert r2.done_summary == "ok"
    assert not state.exists()  # completed runs clean up their state


def test_context_llm_compaction(ws):
    cm = ContextManager(obs_dir=ws, token_budget=None)
    for i in range(6):
        cm.add_tool(f"c{i}", "bash", f"output number {i} " + "x" * 50)
    assert cm.compact_with_llm(lambda old: "SUMMARY")
    contents = [m["content"] for m in cm.messages if m["role"] == "tool"]
    assert contents[0] == "SUMMARY"
    assert contents[1] == ""
    assert contents[-1] != ""  # last keep_recent entries untouched
    assert cm.compactions and cm.compactions[0]["compacted_tools"] == 3


def test_solved_check_early_stop(ws):
    llm = ScriptedLLM(
        [LLMResponse(text="", tool_calls=[_bash_call(f"c{i}", "echo hi")], finish_reason="tool_calls") for i in range(10)]
    )
    run = run_agent(
        "task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", max_steps=20,
        solved_check=lambda: True,
    )
    assert run.stopped_reason == "solved_early"
    assert len(run.steps) == 1


def test_no_progress_guard_stops_probe_loops(ws):
    """Model keeps probing with varying commands but never edits/tests."""
    llm = ScriptedLLM(
        [
            LLMResponse(
                text="",
                tool_calls=[_bash_call(f"c{i}", f"python -c \"print({i})\"")],
                finish_reason="tool_calls",
            )
            for i in range(10)
        ]
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", max_steps=20)
    assert run.stopped_reason == "no_progress"
    assert len(run.steps) == 6


def test_safety_policy_denies_dangerous(ws):
    policy = SafetyPolicy("auto")
    blocked = policy.check("bash", {"command": "rm -rf data"})
    assert blocked is not None and blocked.error_type == "PolicyDenied"
    assert policy.denied
    assert policy.check("bash", {"command": "python tests/test_x.py"}) is None


def test_policy_integrated_in_loop(ws):
    llm = ScriptedLLM(
        [
            LLMResponse(text="", tool_calls=[_bash_call("c1", "rm -rf .")], finish_reason="tool_calls"),
            LLMResponse(text="", tool_calls=[{"id": "c2", "name": "done", "arguments": {"summary": "x"}}], finish_reason="tool_calls"),
        ]
    )
    run = run_agent("task", llm, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", policy=SafetyPolicy("auto"), max_steps=5)
    assert run.steps[0].tool_calls[0].error_type == "PolicyDenied"
    assert run.policy_denials


def test_context_load_rebuilds_metadata(ws):
    cm = ContextManager(obs_dir=ws, token_budget=None)
    cm.add_tool("c1", "bash", "FAILED test")
    cm.add_tool("c2", "bash", "x" * 100)
    cm2 = ContextManager(obs_dir=ws, token_budget=100, keep_recent=1)
    cm2.load(cm.messages)
    cm2.add_tool("c3", "bash", "y" * 300)
    cm2.prepare()
    assert cm2.evictions
    assert is_dangerous("pip install x") and not is_dangerous("python -m pytest -q")
