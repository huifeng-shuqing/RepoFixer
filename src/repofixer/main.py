"""CLI entry point."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from .environment import create_env
from .llm import LLMClient
from .loop import run_agent
from .tools import ToolRegistry


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="repofixer", description="RepoFixer - code-repository repair agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="run the agent on a task in a workspace")
    run_p.add_argument("task")
    run_p.add_argument("--workspace", "-w", default=".", help="target repository directory")
    run_p.add_argument("--env", choices=["auto", "local", "docker"], default="auto", help="execution environment")
    run_p.add_argument("--max-steps", type=int, default=40)
    run_p.add_argument("--trace-dir", default="traces")

    eval_p = sub.add_parser("eval", help="run the agent over a dataset and print metrics")
    eval_p.add_argument("--dataset", required=True, help="JSONL dataset file")
    eval_p.add_argument("--env", choices=["auto", "local", "docker"], default="auto")
    eval_p.add_argument("--out-dir", default="results")
    eval_p.add_argument("--max-steps", type=int, default=40)
    eval_p.add_argument("--baseline", choices=["none", "step3", "noedit"], default="none")
    args = parser.parse_args(argv)

    if args.cmd == "eval":
        from .eval import load_dataset, run_eval, summarize

        dataset_path = Path(args.dataset).resolve()
        tasks = load_dataset(dataset_path)
        max_steps = 3 if args.baseline == "step3" else args.max_steps
        tool_filter = {"read_file", "bash", "grep", "done"} if args.baseline == "noedit" else None
        llm = LLMClient()
        results = run_eval(
            tasks,
            llm,
            args.env,
            Path(args.out_dir),
            base_dir=dataset_path.parent,
            max_steps=max_steps,
            tool_filter=tool_filter,
        )
        print(json.dumps(summarize(results, llm.model), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "run":
        env, kind = create_env(Path(args.workspace).resolve(), args.env)
        print(f"env: {kind}")
        try:
            llm = LLMClient()
            registry = ToolRegistry(env)
            trace_path = Path(args.trace_dir) / f"run_{int(time.time())}.jsonl"
            run = run_agent(args.task, llm, registry, trace_path, max_steps=args.max_steps)
            print(f"stopped: {run.stopped_reason}")
            print(f"done summary: {run.done_summary}")
            print(
                f"steps={len(run.steps)} prompt_tokens={run.prompt_tokens} "
                f"completion_tokens={run.completion_tokens} total={run.total_tokens}"
            )
            print(f"trace: {trace_path}")
            return 0
        finally:
            env.close()
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
