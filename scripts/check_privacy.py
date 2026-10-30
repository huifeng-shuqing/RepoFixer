"""Pre-push privacy gate: fail if tracked files contain real-looking API keys
or local absolute user paths (both have leaked before).

Usage: uv run python scripts/check_privacy.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_grep(pattern: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-n", "-E", pattern],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return p.stdout or ""


problems: list[str] = []
keys = git_grep(r"sk-[A-Za-z0-9]{20,}")
if keys.strip():
    problems.append("API key patterns:\n" + keys[:2000])
paths = git_grep(r"[A-Za-z]:[\\/]Users|/c/Users/[A-Za-z][A-Za-z0-9_.\-]*/|C:[\\/]{2}Users")
if paths.strip():
    problems.append("local absolute user paths:\n" + paths[:2000])
if problems:
    print("PRIVACY CHECK FAILED:")
    print("\n".join(problems))
    sys.exit(1)
print("privacy check passed")
