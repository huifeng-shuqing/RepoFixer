"""Smoke test: run the full RepoFixer agent on samples/calc_bug with a real LLM.

Requires: .env with DEEPSEEK_API_KEY (never committed).
Usage:
    uv run python scripts/smoke_local.py            # auto env (docker if available)
    uv run python scripts/smoke_local.py --env docker
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repofixer.environment import create_env  # noqa: E402
from repofixer.llm import LLMClient  # noqa: E402
from repofixer.loop import run_agent  # noqa: E402
from repofixer.tools import ToolRegistry  # noqa: E402


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["auto", "local", "docker"], default="auto")
    ap.add_argument("--max-steps", type=int, default=30)
    args = ap.parse_args()

    ws = ROOT / "samples" / "calc_bug"
    task = (ws / "README.md").read_text(encoding="utf-8")
    env, kind = create_env(ws, args.env)
    print(f"env: {kind}")
    try:
        run = run_agent(
            task,
            LLMClient(),
            ToolRegistry(env),
            ROOT / "traces" / f"smoke_{int(time.time())}.jsonl",
            max_steps=args.max_steps,
        )
        print(f"stopped={run.stopped_reason} done={run.done_summary}")
        print(
            f"steps={len(run.steps)} prompt_tokens={run.prompt_tokens} "
            f"completion_tokens={run.completion_tokens} total={run.total_tokens}"
        )
        check = env.run("python tests/test_ops.py")
        print("final test check:", "PASS" if check.ok else "FAIL")
        return 0 if check.ok else 1
    finally:
        env.close()


if __name__ == "__main__":
    sys.exit(main())
