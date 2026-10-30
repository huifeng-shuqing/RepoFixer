"""LLMClient unit tests with a faked OpenAI SDK (no network)."""
import json

import pytest

import repofixer.llm as llm_mod
from repofixer.llm import LLMClient, LLMResponse


class FakeUsage:
    prompt_tokens = 100
    completion_tokens = 50


class FakeMsg:
    content = "done"
    tool_calls = []


class FakeChoice:
    message = FakeMsg()
    finish_reason = "stop"


class FakeResp:
    choices = [FakeChoice()]
    usage = FakeUsage()


class FakeCompletions:
    def __init__(self, resp=None, raise_err=None):
        self.resp = resp or FakeResp()
        self.raise_err = raise_err
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.raise_err:
            raise self.raise_err
        return self.resp


class FakeClient:
    def __init__(self, resp=None, raise_err=None):
        self.completions = FakeCompletions(resp, raise_err)
        self.chat = type("Chat", (), {"completions": self.completions})()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(llm_mod, "OpenAI", lambda **kw: FakeClient())
    return LLMClient(api_key="k", base_url="https://x", model="m")


def test_complete_basic(client):
    r = client.complete([{"role": "user", "content": "hi"}])
    assert r.text == "done"
    assert r.prompt_tokens == 100 and r.completion_tokens == 50
    assert r.finish_reason == "stop"


def test_complete_no_usage_and_no_tools(client):
    class R:
        class C:
            message = type("M", (), {"content": "", "tool_calls": None})()
            finish_reason = None
        choices = [C()]
        usage = None
    client.client.completions.resp = R()
    r = client.complete([{"role": "user", "content": "hi"}], tools=None)
    assert r.prompt_tokens == 0 and r.finish_reason == ""
    assert client.client.completions.kwargs.get("tools") is None


def test_complete_passes_tools_and_model(client):
    tools = [{"type": "function", "function": {"name": "bash"}}]
    client.complete([{"role": "user", "content": "hi"}], tools=tools, model="other")
    assert client.client.completions.kwargs["tools"] == tools
    assert client.client.completions.kwargs["model"] == "other"


def test_complete_tool_call_json_parse(client):
    tc = type("TC", (), {"id": "c1", "function": type("F", (), {"name": "bash", "arguments": '{"command": "ls"}'})()})()
    client.client.completions.resp = type("R", (), {
        "choices": [type("C", (), {"message": type("M", (), {"content": "", "tool_calls": [tc]})(), "finish_reason": "tool_calls"})()],
        "usage": FakeUsage(),
    })()
    r = client.complete([{"role": "user", "content": "hi"}], tools=[{}])
    assert r.tool_calls == [{"id": "c1", "name": "bash", "arguments": {"command": "ls"}}]


def test_complete_tool_call_malformed_json(client):
    tc = type("TC", (), {"id": "c1", "function": type("F", (), {"name": "bash", "arguments": "not json"})()})()
    client.client.completions.resp = type("R", (), {
        "choices": [type("C", (), {"message": type("M", (), {"content": "", "tool_calls": [tc]})(), "finish_reason": "tool_calls"})()],
        "usage": FakeUsage(),
    })()
    r = client.complete([{"role": "user", "content": "hi"}], tools=[{}])
    assert r.tool_calls[0]["arguments"] == {"_raw": "not json"}


def test_complete_propagates_api_error(client):
    import openai

    client.client.completions.raise_err = openai.APIConnectionError(request=None)
    with pytest.raises(openai.APIConnectionError):
        client.complete([{"role": "user", "content": "hi"}])


def test_summarize_returns_content(client):
    class R:
        class C:
            message = type("M", (), {"content": "summary text"})()
        choices = [C()]
    client.client.completions.resp = R()
    assert client.summarize([{"role": "assistant", "content": "log"}]) == "summary text"
    # goes through the small model
    assert client.client.completions.kwargs["model"] == "m"


def test_summarize_swallows_errors(client):
    client.client.completions.raise_err = RuntimeError("boom")
    assert client.summarize([{"role": "assistant", "content": "log"}]) == ""


def test_response_total_tokens():
    r = LLMResponse(text="", prompt_tokens=3, completion_tokens=4)
    assert r.total_tokens == 7
