"""Safety policy for dangerous commands (basis of human-in-the-loop).

Modes:
- "off": no checks (development)
- "auto": dangerous commands are DENIED and reported to the model
- "ask": dangerous commands require interactive approval (CLI)

Optional ordered rules file (opencode-style, last match wins):
`.repofixer/rules.json` -> {"rules": [{"tool": "bash", "pattern": "pytest", "action": "allow"},
                                       {"tool": "bash", "pattern": ".*", "action": "deny"}]}
Actions: allow | ask | deny. Rules are evaluated in order; the LAST matching
rule decides. Without a file, the built-in defaults apply unchanged.
"""
from __future__ import annotations

import json
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

RULES_NAME = ".repofixer/rules.json"


def is_dangerous(command: str) -> bool:
    return any(re.search(p, command, re.I) for p in DANGEROUS_PATTERNS)


def escapes_workspace(command: str) -> bool:
    return bool(ESCAPE_RE.search(command))


def load_rules(workspace: Path | None) -> list[dict]:
    """Ordered rule list from .repofixer/rules.json (last match wins)."""
    if workspace is None:
        return []
    p = workspace / RULES_NAME
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        rules = data.get("rules", [])
        return [r for r in rules if r.get("action") in ("allow", "ask", "deny")]
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return []


@dataclass
class SafetyPolicy:
    mode: str = "auto"  # off | auto | ask
    asker: Callable[[str], bool] | None = None  # returns True to approve
    workspace: Path | None = None  # if set, bash commands may not cd outside it
    denied: list[dict] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.rules:
            self.rules = load_rules(self.workspace)

    def _rule_action(self, tool_name: str, command: str) -> str | None:
        action = None
        for rule in self.rules:
            if rule.get("tool", tool_name) != tool_name:
                continue
            pattern = rule.get("pattern", ".*")
            try:
                if re.search(pattern, command, re.I):
                    action = rule["action"]  # last match wins
            except re.error:
                continue
        return action

    def check(self, tool_name: str, args: dict) -> ToolResult | None:
        """Return a ToolResult to use instead of executing, or None to proceed."""
        if self.mode == "off" or tool_name != "bash":
            return None
        command = str(args.get("command", ""))
        rule_action = self._rule_action(tool_name, command)
        if self.workspace is not None and escapes_workspace(command) and rule_action != "allow":
            self.denied.append({"tool": tool_name, "command": command[:200], "reason": "workspace_escape"})
            return ToolResult.err(
                "denied by safety policy: stay inside the workspace directory "
                "(use relative paths; the workspace root is the repository)",
                "PolicyDenied",
            )
        if rule_action == "allow":
            return None
        if rule_action == "deny":
            self.denied.append({"tool": tool_name, "command": command[:200], "reason": "rule"})
            return ToolResult.err(
                "denied by safety policy: matched a deny rule", "PolicyDenied"
            )
        if not is_dangerous(command):
            return None
        if self.mode == "ask" or rule_action == "ask":
            if self.asker is not None and self.asker(command):
                return None
        self.denied.append({"tool": tool_name, "command": command[:200]})
        return ToolResult.err(
            "denied by safety policy: dangerous command requires explicit approval", "PolicyDenied"
        )
