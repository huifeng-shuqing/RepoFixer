"""Failure attribution: classify unresolved eval tasks from results + traces.

Reads <out-dir>/results.jsonl and the per-task traces, buckets failures by
root cause and writes a markdown report (results/<out-dir>/FAILURES.md).

Categories:
- env_failed        environment could not start (Docker/local setup)
- loop_detected     identical tool calls repeated
- max_steps         budget exhausted before success
- tool_misuse       last steps dominated by ToolUsageError / ToolError
- policy_blocked    dangerous commands auto-denied interfered
- other             model stopped without fixing
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from repofixer.trace import load_trace


def classify(result: dict, trace_path: Path) -> str:
    if result.get("error"):
        return "llm_error" if result.get("stopped_reason") == "llm_error" else "env_failed"
    reason = result.get("stopped_reason", "")
    if reason == "loop_detected":
        return "loop_detected"
    if reason == "no_progress":
        return "no_progress"
    if reason == "done":
        return "premature_done"  # model declared done while tests still failed
    steps = load_trace(trace_path) if trace_path.exists() else []
    errors = Counter()
    denied = 0
    for st in steps:
        for tc in st.tool_calls:
            if tc.error_type in ("ToolUsageError", "ToolError"):
                errors[tc.error_type] += 1
            if tc.error_type == "PolicyDenied":
                denied += 1
    if denied >= 2 and denied >= errors["ToolUsageError"] + errors["ToolError"]:
        return "policy_blocked"
    if errors.total() >= 3:
        return "tool_misuse"
    if reason == "max_steps":
        return "max_steps"
    return "other"


def main() -> int:
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir)
    results_path = out / "results.jsonl"
    if not results_path.exists():
        print(f"no results at {results_path}")
        return 1
    results = [json.loads(l) for l in results_path.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    unresolved = [r for r in results if not r["resolved"]]
    buckets: Counter = Counter()
    lines = [
        "# 失败归因报告",
        "",
        f"- 任务总数: {len(results)}，未解决: {len(unresolved)}",
        "",
    ]
    for r in unresolved:
        tp = Path(r["trace_path"]) if r.get("trace_path") else None
        cat = classify(r, tp) if tp else "env_failed"
        buckets[cat] += 1
        lines.append(f"- **{r['task_id']}** [{cat}] steps={r['steps']} reason={r['stopped_reason']}")
    lines += ["", "## 分类统计", ""]
    for cat, n in buckets.most_common():
        lines.append(f"- {cat}: {n}")
    report = "\n".join(lines) + "\n"
    (out / "FAILURES.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
