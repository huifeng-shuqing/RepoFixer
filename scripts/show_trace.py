"""Human-readable trace viewer (demo & debugging).

Usage: uv run python scripts/show_trace.py <trace.jsonl>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from repofixer.trace import load_trace  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print("usage: uv run python scripts/show_trace.py <trace.jsonl>")
        return 2
    steps = load_trace(Path(sys.argv[1]))
    for st in steps:
        print(f"\n=== step {st.step} ({st.wall_ms}ms, p{st.prompt_tokens}/c{st.completion_tokens} tokens) ===")
        if st.assistant_text:
            print(f"assistant: {st.assistant_text}")
        for tc in st.tool_calls:
            args = str(tc.args)[:120]
            status = "ok" if tc.ok else f"FAIL({tc.error_type})"
            print(f"  [{status}] {tc.name}({args}) -> {tc.output_chars} chars, {tc.latency_ms}ms")
    print(f"\n{len(steps)} steps total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
