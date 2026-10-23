"""Safety policy for dangerous commands (basis of human-in-the-loop).

Modes:
- "off": no checks (development)
- "auto": dangerous commands are DENIED and reported to the model
- "ask": dangerous commands require interactive approval (CLI)

Denials are recorded so runs can report exactly what was blocked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .tools import ToolResult

DANGEROUS_PATTERNS = (
    r"\brm\b",
    r"\bdel\b",
    r"pip install",
    r"npm install",
    r"git push",
    r"git reset --hard",
    r"format ",
    r"\bshutdown\b",
    r"\bsudo\b",
    r"chmod 777",
    r"> /dev/",
)

# `cd` to anything outside the current directory: absolute POSIX paths (/),
# drive letters (C:/), home (~) or parent (..). Relative `cd tests` is fine.
ESCAPE_RE = re.compile(r"\bcd\s+(?:/|~|[A-Za-z]:[/\\]|\.\.)")


def is_dangerous(command: str) -> bool:
    return any(re.search(p, command, re.I) for p in DANGEROUS_PATTERNS)


def escapes_workspace(command: str) -> bool:
    return bool(ESCAPE_RE.search(command))


@dataclass
class SafetyPolicy:
    mode: str = "auto"  # off | auto | ask
    asker: Callable[[str], bool] | None = None  # returns True to approve
    workspace: Path | None = None  # if set, bash commands may not cd outside it
    denied: list[dict] = field(default_factory=list)

    def check(self, tool_name: str, args: dict) -> ToolResult | None:
        """Return a ToolResult to use instead of executing, or None to proceed."""
        if self.mode == "off" or tool_name != "bash":
            return None
        command = str(args.get("command", ""))
        if self.workspace is not None and escapes_workspace(command):
            self.denied.append({"tool": tool_name, "command": command[:200], "reason": "workspace_escape"})
            return ToolResult.err(
                "denied by safety policy: stay inside the workspace directory "
                "(use relative paths; the workspace root is the repository)",
                "PolicyDenied",
            )
        if not is_dangerous(command):
            return None
        if self.mode == "ask" and self.asker is not None:
            if self.asker(command):
                return None
        self.denied.append({"tool": tool_name, "command": command[:200]})
        return ToolResult.err(
            "denied by safety policy: dangerous command requires explicit approval", "PolicyDenied"
        )
