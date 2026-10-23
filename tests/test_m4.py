import pathlib
import subprocess
import sys

from repofixer.context import ContextManager, estimate_tokens
from repofixer.environment import LocalEnv
from repofixer.llm import LLMResponse
from repofixer.loop import run_agent
from repofixer.tools import ToolRegistry, summarize_test_output

SAMPLE = pathlib.Path(__file__).resolve().parents[1] / "samples" / "calc_bug"


def _map(repo: pathlib.Path, task: str, *extra) -> str:
    """Repo map via the real subprocess path used by eval/run."""
    p = subprocess.run(
        [sys.executable, "-m", "repofixer", "map", str(repo), task, *extra],
        capture_output=True, text=True, timeout=120,
    )
    assert p.returncode == 0, p.stderr[-400:]
    return p.stdout


# ---------- repo map (subprocess-isolated) ----------

def test_map_subprocess_contains_symbols():
    text = _map(SAMPLE, "fix mul")
    assert "mul" in text and "add" in text


def test_map_subprocess_respects_token_budget():
    text = _map(SAMPLE, "fix mul", "--token-budget", "2")
    assert "more symbols omitted" in text


def test_map_subprocess_task_relevance_ranking():
    text = _map(SAMPLE, "the mul function is broken")
    assert text.find("[function] def mul") < text.find("[function] def add")


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
