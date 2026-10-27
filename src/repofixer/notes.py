"""Repo notes: per-repository long-term memory.

Inspired by CLAUDE.md layered instructions and Khoj's extracted memory: one
notes file per repository, injected at session start and maintained at the end
of every run. Two writers:
- append_note: one-line session record (always)
- extract_and_merge: LLM-extracted durable facts (build/test commands,
  pitfalls) merged with dedupe + cap (Khoj's extract-then-update idea)
"""
from __future__ import annotations

from pathlib import Path

NOTES_NAME = ".repofixer/notes.md"
NOTES_HEADER = "# Repository notes (maintained by RepoFixer across sessions)\n"
MAX_FACTS = 30

EXTRACT_SYSTEM = (
    "You maintain a repository's long-term memory. From the session record, "
    "extract up to 5 durable facts that would help a FUTURE repair session "
    "on this repository (build/test commands, tricky behaviors, known pitfalls). "
    "Output one line per fact, each starting with '- '. No other text."
)


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


def _facts(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip().startswith("-")]


def extract_and_merge(workspace: Path, session_note: str, llm) -> str:
    """Khoj-style extract-then-update: pull durable facts from the session
    record with a cheap call and merge them into the notes file (deduped,
    capped). Returns the extracted facts joined, or '' on failure."""
    try:
        resp = llm.complete(
            [
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": f"Session record:\n{session_note[:2000]}"},
            ],
            None,
            temperature=0.0,
            model=getattr(llm, "small_model", None),
        )
        facts = _facts(resp.text)
    except Exception:
        return ""
    if not facts:
        return ""
    p = notes_path(workspace)
    existing = _facts(load_notes(workspace))
    seen: set[str] = set()
    merged: list[str] = []
    for f in existing + facts:
        key = f.lower()
        if key not in seen:
            seen.add(key)
            merged.append(f)
    merged = merged[-MAX_FACTS:]  # keep the newest facts when over the cap
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(NOTES_HEADER + "".join(f"{f}\n" for f in merged), encoding="utf-8")
    return "\n".join(facts)
