"""Tool layer: one registry, structured results, no exceptions out of handlers.

Design follows:
- dispatch-map pattern from learn-claude-code s02 (MIT): adding a tool =
  adding one handler + one schema, the loop never changes;
- ACI principles from the SWE-agent paper (arXiv:2405.15793, ideas only):
  1. file viewer returns line-numbered pages, never raw cat of huge files
  2. search lists matching file names only, not match contents
  3. edits are exact-block replace; unknown text is a failure, never fuzzy
  4. empty command output is reported explicitly ("ok, no output")
- handler isolation from mini-swe-agent (MIT): every action runs as an
  independent subprocess via the Environment, no persistent shell session.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .environment import Environment

MAX_OUTPUT_CHARS = 20_000
SKIP_DIRS = (".git", ".venv", "__pycache__", "node_modules", ".pytest_cache")


@dataclass
class ToolResult:
    ok: bool
    output: str
    error_type: str | None = None  # ToolError | ToolTimeout | ToolUsageError
    truncated: bool = False

    @classmethod
    def err(cls, output: str, error_type: str = "ToolError") -> "ToolResult":
        return cls(ok=False, output=output, error_type=error_type)


def _cap(text: str) -> tuple[str, bool]:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text) - MAX_OUTPUT_CHARS} more chars]", True
    return text, False


def _fmt_page(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + i:>6} | {line}" for i, line in enumerate(lines))


IMPORTANT_RE = re.compile(r"FAIL|Error|Traceback|assert|Exception|FAILED|failed", re.I)


def summarize_test_output(text: str, max_lines: int = 60) -> str:
    """Keep failure-relevant lines plus a little context; drop the noise."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if IMPORTANT_RE.search(line):
            for j in range(max(0, i - 2), min(len(lines), i + 3)):
                keep.add(j)
    if not keep:
        return "\n".join(lines[:30]) + f"\n... [{len(lines) - 30} lines omitted]"
    parts: list[str] = []
    prev = -2
    for i in sorted(keep):
        if i > prev + 1:
            parts.append("...")
        parts.append(lines[i])
        prev = i
    return "\n".join(parts)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., ToolResult]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def build_tools(env: Environment) -> list[Tool]:
    def read_file(path: str, offset: int = 1, limit: int = 100) -> ToolResult:
        try:
            p = env.resolve(path)
        except ValueError as e:
            return ToolResult.err(str(e), "ToolUsageError")
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return ToolResult.err(f"cannot read {path}: {e}")
        offset = max(offset, 1)
        page = lines[offset - 1 : offset - 1 + limit]
        out = _fmt_page(page, offset)
        if offset + limit <= len(lines):
            out += f"\n... [{len(lines) - offset - limit + 1} more lines; pass offset={offset + limit} for next page]"
        out, trunc = _cap(out)
        return ToolResult(ok=True, output=out, truncated=trunc)

    def write_file(path: str, content: str) -> ToolResult:
        try:
            p = env.resolve(path)
        except ValueError as e:
            return ToolResult.err(str(e), "ToolUsageError")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as e:
            return ToolResult.err(f"cannot write {path}: {e}")
        return ToolResult(ok=True, output=f"wrote {len(content)} chars to {path}")

    def edit_file(path: str, old_text: str, new_text: str) -> ToolResult:
        try:
            p = env.resolve(path)
        except ValueError as e:
            return ToolResult.err(str(e), "ToolUsageError")
        try:
            content = p.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult.err(f"cannot read {path}: {e}")
        if old_text not in content:
            return ToolResult.err(
                f"old_text not found in {path} (exact match required; re-read the file to get exact text)",
                "ToolUsageError",
            )
        p.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return ToolResult(ok=True, output=f"replaced 1 occurrence in {path}")

    def bash(command: str) -> ToolResult:
        res = env.run(command)
        out = res.stdout
        if res.stderr:
            out = (out + ("\n" if out else "") + "[stderr]\n" + res.stderr)
        out = out.rstrip()
        if res.timed_out:
            return ToolResult.err(out or "command timed out", "ToolTimeout")
        if res.exit_code != 0:
            out, trunc = _cap(out)
            return ToolResult(
                ok=False, output=out or f"(exit code {res.exit_code}, no output)", error_type="ToolError", truncated=trunc
            )
        out, trunc = _cap(out)
        return ToolResult(ok=True, output=out or "(ok, no output)", truncated=trunc)

    def grep(pattern: str, path: str = ".", max_files: int = 50) -> ToolResult:
        try:
            root = env.resolve(path)
        except ValueError as e:
            return ToolResult.err(str(e), "ToolUsageError")
        if not root.exists():
            return ToolResult.err(f"path does not exist: {path}", "ToolUsageError")
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return ToolResult.err(f"invalid regex: {e}", "ToolUsageError")
        matched: list[str] = []
        for p in root.rglob("*"):
            if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
                continue
            try:
                if rx.search(p.read_text(encoding="utf-8", errors="ignore")):
                    matched.append(str(p.relative_to(root)))
            except OSError:
                continue
            if len(matched) >= max_files:
                break
        if not matched:
            return ToolResult(ok=True, output=f"(no files match /{pattern}/ under {path})")
        out = f"{len(matched)} file(s) match:\n" + "\n".join(f"- {m}" for m in matched)
        return ToolResult(ok=True, output=out)

    def done(summary: str) -> ToolResult:
        return ToolResult(ok=True, output=summary)

    def run_test(command: str) -> ToolResult:
        res = env.run(command)
        out = (res.stdout + ("\n[stderr]\n" + res.stderr if res.stderr else "")).rstrip()
        out = summarize_test_output(out)
        if res.timed_out:
            return ToolResult.err(out or "command timed out", "ToolTimeout")
        if res.exit_code != 0:
            return ToolResult(
                ok=False, output=out or f"(exit code {res.exit_code}, no output)", error_type="ToolError"
            )
        return ToolResult(ok=True, output=out or "(ok, no output)")

    return [
        Tool(
            name="read_file",
            description="Read a file page with line numbers (about 100 lines). Pass a larger offset for the next page.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
                "required": ["path"],
            },
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description="Create or overwrite a file with the given content.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            handler=write_file,
        ),
        Tool(
            name="edit_file",
            description="Replace one exact occurrence of old_text with new_text in a file. old_text must match the file byte-for-byte.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["path", "old_text", "new_text"],
            },
            handler=edit_file,
        ),
        Tool(
            name="bash",
            description="Run a shell command in the workspace. Each call is a fresh shell, so use full paths/&& in one command when needed.",
            parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            handler=bash,
        ),
        Tool(
            name="grep",
            description="List files whose content matches a regex. Returns file names only (not match contents).",
            parameters={
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["pattern"],
            },
            handler=grep,
        ),
        Tool(
            name="run_test",
            description="Run a test/verification command and return a summary focused on failures.",
            parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            handler=run_test,
        ),
        Tool(
            name="done",
            description="Finish the task. Call ONLY when the task is complete (tests pass or the task is fully addressed).",
            parameters={"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
            handler=done,
        ),
    ]


class ToolRegistry:
    def __init__(self, env: Environment):
        self.env = env
        self._tools: dict[str, Tool] = {t.name: t for t in build_tools(env)}

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def filter(self, names: set[str]) -> None:
        """Keep only the given tools (used by eval baselines, e.g. noedit)."""
        self._tools = {k: v for k, v in self._tools.items() if k in names}

    def call(self, name: str, args: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.err(f"unknown tool '{name}'; available: {sorted(self._tools)}", "ToolUsageError")
        try:
            return tool.handler(**args)
        except TypeError as e:
            return ToolResult.err(f"bad arguments for '{name}': {e}", "ToolUsageError")
        except ValueError as e:
            return ToolResult.err(str(e), "ToolUsageError")
        except Exception as e:  # handlers must never raise out of the loop
            return ToolResult.err(f"{type(e).__name__}: {e}", "ToolError")
