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
            timeout=120,      # hard per-request timeout: a hung call must not freeze the harness
            max_retries=1,    # one retry only: 2*120s worst case instead of SDK defaults
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

    def summarize(self, messages: list[dict], max_tokens: int = 800) -> str:
        """Compress a working log into a continuity summary (used when the
        context budget is exceeded and eviction alone is not enough)."""
        try:
            text = json.dumps(messages, ensure_ascii=False)[:60_000]
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize this agent working log for continuity: what was done, "
                            "current state, key findings. Be concise but preserve file paths, "
                            "error messages and test names verbatim."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=max_tokens,
                temperature=0.0,
            )
            return resp.choices[0].message.content or ""
        except Exception:
            return ""
