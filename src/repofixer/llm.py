"""LLM client: OpenAI-compatible endpoint (DeepSeek by default).

One thin wrapper so the harness can switch providers (DeepSeek/GLM/Qwen/GPT)
by changing environment variables only. Usage is captured on every response
for the token/cost accounting used by the eval milestone.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from openai import OpenAI


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict] = field(default_factory=list)  # [{id, name, arguments}]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient:
    """Thin wrapper over the OpenAI-compatible chat completions API."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        self.client = OpenAI(
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            base_url=base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    def complete(self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2) -> LLMResponse:
        kwargs: dict = dict(model=self.model, messages=messages, temperature=temperature)
        if tools:
            kwargs["tools"] = tools
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls: list[dict] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
        return LLMResponse(
            text=msg.content or "",
            tool_calls=calls,
            prompt_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            completion_tokens=resp.usage.completion_tokens if resp.usage else 0,
            finish_reason=resp.choices[0].finish_reason or "",
        )
