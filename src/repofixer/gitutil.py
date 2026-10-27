"""Git helpers shared by the loop, CLI and tests (moved out of loop.py).

- run(): thin subprocess wrapper with safe defaults
- snapshot(): shadow-git snapshot via `git stash create` (Cline-style: commit
  object without moving HEAD, so eval's `git checkout -- .` reset keeps
  restoring the original buggy baseline)
- diff(): unstaged diff for the verifier
- rollback(): restore a recorded snapshot sha
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def run(workspace: Path, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(workspace)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def snapshot(workspace: Path | None, step_no: int) -> str | None:
    if workspace is None or not (workspace / ".git").exists():
        return None
    try:
        run(workspace, ["add", "-A"])
        p = run(workspace, ["-c", "user.name=repofixer", "-c", "user.email=repofixer@local",
                            "stash", "create", f"repofixer snapshot (step {step_no})"])
        sha = p.stdout.strip()
        run(workspace, ["reset"])  # unstage; keep worktree, keep HEAD
        return sha or None
    except Exception:
        return None


def diff(workspace: Path | None) -> str:
    if workspace is None or not (workspace / ".git").exists():
        return ""
    try:
        return run(workspace, ["diff"]).stdout or ""
    except Exception:
        return ""


def rollback(workspace: Path, sha: str) -> str:
    if not (workspace / ".git").exists():
        return "error: no git repository at the workspace"
    try:
        p = run(workspace, ["reset", "--hard", sha])
        return (p.stdout or "") + (p.stderr or "") or f"reset to {sha[:12]}"
    except Exception as e:
        return f"error: {e}"
