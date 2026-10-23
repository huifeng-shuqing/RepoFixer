import pathlib

from repofixer.context import ContextManager, estimate_tokens
from repofixer.environment import LocalEnv
from repofixer.llm import LLMResponse
from repofixer.loop import run_agent
from repofixer.repomap import build_graph, build_repo_map, extract_symbols, rank, render_map
from repofixer.tools import ToolRegistry, summarize_test_output

SAMPLE = pathlib.Path(__file__).resolve().parents[1] / "samples" / "calc_bug"


# ---------- repo map ----------

def test_extract_symbols_finds_functions():
    syms = extract_symbols(SAMPLE)
    names = {s.name for s in syms}
    assert {"mul", "add", "sub", "div"} <= names
    mul = next(s for s in syms if s.name == "mul")
    assert mul.file.endswith("calc/ops.py")
    assert mul.kind == "function"


def test_render_map_respects_budget():
    syms = extract_symbols(SAMPLE)
    graph = build_graph(syms)
    scores = rank(syms, graph, "fix the mul function")
    text = render_map(syms, scores, token_budget=1000)
    assert "mul" in text
    small = render_map(syms, scores, token_budget=2)
    assert "more symbols omitted" in small


def test_rank_task_relevance():
    syms = extract_symbols(SAMPLE)
    graph = build_graph(syms)
    scores = rank(syms, graph, "the mul function is broken")
    idx = {s.name: i for i, s in enumerate(syms)}
    assert scores[idx["mul"]] >= scores[idx["add"]]


def test_build_repo_map_end_to_end():
    text = build_repo_map(SAMPLE, "fix mul")
    assert "mul" in text


# ---------- test output summarization ----------

def test_summarize_test_output_keeps_failures():
    out = "\n".join(f"line{i}" for i in range(100)) + "\nFAILED test_mul\nTraceback: boom\n"
    s = summarize_test_output(out)
    assert "FAILED test_mul" in s
    assert len(s.splitlines()) < 100


# ---------- context management ----------

def test_context_tiering_previews_long_output(ws):
    cm = ContextManager(obs_dir=ws, token_budget=None)
    cm.add_tool("c1", "bash", "x" * 3000)
    content = cm.messages[0]["content"]
    assert len(content) < 3000
    assert "full output" in content
    obs = list(ws.glob("*.txt"))
    assert obs and obs[0].read_text(encoding="utf-8") == "x" * 3000


def test_context_eviction_replaces_old_outputs(ws):
    cm = ContextManager(obs_dir=ws, token_budget=300, keep_recent=1)
    for i in range(5):
        cm.add_tool(f"c{i}", "bash", "y" * 200)
    assert estimate_tokens(cm.messages) > 300
    cm.prepare()
    assert estimate_tokens(cm.messages) <= 360
    assert len(cm.evictions) >= 1
    assert "evicted" not in cm.messages[-1]["content"]  # newest kept


def test_context_keeps_important_longer(ws):
    cm = ContextManager(obs_dir=ws, token_budget=500, keep_recent=0)
    cm.add_tool("c0", "run_test", "FAILED test_x")
    for i in range(1, 6):
        cm.add_tool(f"c{i}", "bash", "z" * 400)
    cm.prepare()
    assert cm.evictions
    assert "run_test" not in [e["name"] for e in cm.evictions]


# ---------- loop integration ----------

class CaptureLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools=None, temperature=0.2):
        self.seen.append(list(messages))
        return self.responses.pop(0)


def test_repo_map_injected_into_system(ws):
    fake = CaptureLLM(
        [LLMResponse(text="", tool_calls=[{"id": "c", "name": "done", "arguments": {"summary": "x"}}], finish_reason="tool_calls")]
    )
    run_agent("task", fake, ToolRegistry(LocalEnv(ws)), ws / "t.jsonl", repo_map="# repo map: MAPX")
    assert "MAPX" in fake.seen[0][0]["content"]
