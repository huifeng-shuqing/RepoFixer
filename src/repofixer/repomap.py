"""Repository map: tree-sitter symbol extraction + reference-graph ranking.

Idea borrowed from Aider's repo map (Apache-2.0; idea only, implementation
written from scratch and deliberately simplified):
- parse source files with tree-sitter, extract functions/classes/methods
  (name, signature, file, line)
- approximate reference graph: symbol A points to symbol B when B's name
  appears as an identifier in A's file
- rank symbols with a small PageRank-style iteration, seeded by keyword
  relevance to the task description
- render the top symbols within a token budget as a compact map injected
  into the system prompt

Honest limitations (documented for interviews): the reference graph uses
file-level identifier co-occurrence instead of precise AST call edges, and
only python/javascript are parsed; other files are skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

try:
    from tree_sitter import Language, Parser

    import tree_sitter_javascript
    import tree_sitter_python

    _PY_LANG = Language(tree_sitter_python.language())
    _JS_LANG = Language(tree_sitter_javascript.language())
    _LANGS = {
        ".py": _PY_LANG,
        ".pyi": _PY_LANG,
        ".js": _JS_LANG,
        ".jsx": _JS_LANG,
        ".mjs": _JS_LANG,
    }
    _HAS_TREESITTER = True
except Exception:  # pragma: no cover - degraded mode, repo map simply disabled
    _HAS_TREESITTER = False

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", "vendor", ".tmp"}
MAX_FILE_BYTES = 300_000
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
STOPWORDS = {
    "the", "and", "for", "fix", "bug", "function", "with", "that", "this", "from", "your",
    "should", "when", "test", "tests", "pass", "file", "code", "you", "are", "not",
}


@dataclass
class Symbol:
    name: str
    kind: str  # function | class
    file: str  # repo-relative path, for display
    path: Path  # absolute path, for reading
    start_line: int
    signature: str
    doc: str = ""


def _extract_from_source(data: bytes, lang, path: Path, root: Path, symbols: list[Symbol]) -> None:
    parser = Parser(lang)
    tree = parser.parse(data)
    text = data.decode("utf-8", errors="replace")
    rel = path.relative_to(root).as_posix()

    def walk(node) -> None:
        kind = None
        if node.type in ("function_definition", "function_declaration", "method_definition"):
            kind = "function"
        elif node.type in ("class_definition", "class_declaration"):
            kind = "class"
        if kind is not None:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = text[name_node.start_byte : name_node.end_byte]
                body_node = node.child_by_field_name("body")
                end = body_node.start_byte if body_node is not None else node.end_byte
                sig = " ".join(text[node.start_byte : end].split())
                doc = ""
                try:
                    if body_node is not None and body_node.children:
                        first = body_node.children[0]
                        if first.type == "expression_statement" and first.children:
                            s = first.children[0]
                            if s is not None and s.type == "string":
                                doc = text[s.start_byte : s.end_byte].strip()[:120]
                except Exception:
                    pass
                symbols.append(
                    Symbol(name=name, kind=kind, file=rel, path=path,
                           start_line=node.start_point.row + 1, signature=sig, doc=doc)
                )
        for child in node.children:
            walk(child)

    walk(tree.root_node)


def extract_symbols(root: Path) -> list[Symbol]:
    symbols: list[Symbol] = []
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        lang = _LANGS.get(path.suffix.lower())
        if lang is None:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_FILE_BYTES:
            continue
        _extract_from_source(data, lang, path, root, symbols)
    return symbols


def build_graph(symbols: list[Symbol]) -> dict[int, set[int]]:
    """Approximate reference graph: i -> j if j's name appears in i's file."""
    files: dict[str, str] = {}
    for s in symbols:
        if s.file not in files:
            try:
                files[s.file] = s.path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                files[s.file] = ""
    name_to_idx: dict[str, list[int]] = {}
    for i, s in enumerate(symbols):
        name_to_idx.setdefault(s.name, []).append(i)
    graph: dict[int, set[int]] = {i: set() for i in range(len(symbols))}
    for i, s in enumerate(symbols):
        text = files.get(s.file, "")
        for name, idxs in name_to_idx.items():
            if name == s.name:
                continue
            if re.search(rf"\b{re.escape(name)}\b", text):
                graph[i].update(j for j in idxs if j != i)
    return graph


def _keywords(task: str) -> set[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", task.lower())
    return {w for w in words if w not in STOPWORDS}


def rank(symbols: list[Symbol], graph: dict[int, set[int]], task: str, iterations: int = 8, damping: float = 0.85) -> list[float]:
    """PageRank-style iteration seeded by task-keyword relevance."""
    n = len(symbols)
    if n == 0:
        return []
    kw = _keywords(task)
    seeds = []
    for s in symbols:
        relevant = s.name.lower() in kw or any(w in s.doc.lower() or w in s.signature.lower() for w in kw)
        seeds.append(1.0 if relevant else 0.1)
    total = sum(seeds) or 1.0
    scores = [x / total for x in seeds]
    outdeg = {i: len(graph.get(i, set())) for i in range(n)}
    for _ in range(iterations):
        new = [(1.0 - damping) * seeds[i] for i in range(n)]
        for i in range(n):
            for j in graph.get(i, set()):
                new[j] += damping * scores[i] / max(outdeg[i], 1)
        s = sum(new) or 1.0
        scores = [x / s for x in new]
    return scores


def render_map(symbols: list[Symbol], scores: list[float], token_budget: int = 1000, chars_per_token: int = 4) -> str:
    if not symbols:
        return ""
    order = sorted(range(len(symbols)), key=lambda i: -scores[i])
    budget_chars = token_budget * chars_per_token
    lines = [f"# repo map ({len(symbols)} symbols, most relevant first)"]
    used = len(lines[0])
    shown = 0
    for i in order:
        s = symbols[i]
        sig = s.signature if len(s.signature) <= 120 else s.signature[:117] + "..."
        line = f"{s.file}:{s.start_line} [{s.kind}] {sig}"
        if used + len(line) + 1 > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1
        shown += 1
    if shown < len(symbols):
        lines.append(f"# ({len(symbols) - shown} more symbols omitted: token budget {token_budget})")
    return "\n".join(lines)


def build_repo_map(root: Path, task: str, token_budget: int = 1000) -> str:
    if not _HAS_TREESITTER:
        return ""
    symbols = extract_symbols(root)
    if not symbols:
        return ""
    graph = build_graph(symbols)
    scores = rank(symbols, graph, task)
    return render_map(symbols, scores, token_budget)
