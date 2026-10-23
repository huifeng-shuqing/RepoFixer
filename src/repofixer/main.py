"""CLI entry point."""
from __future__ import annotations

import argparse
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
    args = parser.parse_args(argv)

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
