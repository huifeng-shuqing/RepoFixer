"""Repo notes: per-repository long-term memory (the simplest useful form).

Inspired by CLAUDE.md layered instructions and Khoj's extracted memory: one
notes file per repository, injected at session start and appended to at the
end of every run. No extraction pipeline needed at this scale.
"""
from __future__ import annotations

from pathlib import Path

NOTES_NAME = ".repofixer/notes.md"
NOTES_HEADER = "# Repository notes (maintained by RepoFixer across sessions)\n"


def notes_path(workspace: Path) -> Path:
    return workspace / NOTES_NAME


def load_notes(workspace: Path, max_chars: int = 4000) -> str:
    p = notes_path(workspace)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [notes truncated]"
    return text


def append_note(workspace: Path, entry: str) -> None:
    p = notes_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(NOTES_HEADER, encoding="utf-8")
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n- {entry}\n")
