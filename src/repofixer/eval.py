"""Evaluation: dataset schema, runner and metrics.

Task structure borrows the SWE-bench idea (MIT; structure referenced, not
code copied): each task = repo + problem statement + an objective verifier.
Here the verifier is a `verify_command` whose exit code decides success, which
keeps the harness dependency-free; a fail_to_pass-style test list can be added
later for SWE-bench instances.

Runner properties:
- per-task trace saved under <out_dir>/traces/<task_id>.jsonl
- results appended to <out_dir>/results.jsonl one line per task, keyed by
  task_id => resume = skip already-done tasks (no double spending)
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .environment import create_env
from .llm import LLMClient
from .loop import run_agent
from .notes import append_note, load_notes
from .tools import ToolRegistry

# price per 1M tokens (input, output), in yuan — rough estimate, configurable later
DEFAULT_PRICES = {"deepseek-chat": (2.0, 8.0)}


@dataclass
class Task:
    id: str
    problem_statement: str
    repo: str = "."
    verify_command: str = ""
    timeout: int = 300
    difficulty: str = "easy"
    base_commit: str = ""
    fix_commit: str = ""
    test_files: list = field(default_factory=list)
    src_files: list = field(default_factory=list)
    src_layout: bool = False


@dataclass
class EvalResult:
    task_id: str
    resolved: bool
    stopped_reason: str
    steps: int
    prompt_tokens: int
    completion_tokens: int
    wall_ms: int
    trace_path: str = ""
    error: str = ""


def load_dataset(path: Path) -> list[Task]:
    tasks: list[Task] = []
    with path.open(encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tasks.append(Task(**json.loads(line)))
    return tasks


def run_task(
    task: Task,
    llm: LLMClient,
    env_kind: str,
    out_dir: Path,
    *,
    base_dir: Path | None = None,
    max_steps: int = 40,
    tool_filter: set[str] | None = None,
    use_repo_map: bool = True,
    context_budget: int | None = 60_000,
) -> EvalResult:
    t0 = time.time()
    repo = Path(task.repo)
    if not repo.is_absolute() and base_dir is not None:
        repo = (base_dir / repo).resolve()
    try:
        env, _kind = create_env(repo, env_kind)
    except Exception as e:
        return EvalResult(task_id=task.id, resolved=False, stopped_reason="env_failed",
                          steps=0, prompt_tokens=0, completion_tokens=0, wall_ms=0, error=str(e))
    trace_path = out_dir / "traces" / f"{task.id}.jsonl"
    obs_dir = out_dir / "obs" / task.id
    # reset the task repo to its buggy baseline (task repos keep .git for this)
    if (repo / ".git").exists():
        subprocess.run(["git", "-C", str(repo), "checkout", "--", "."], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "clean", "-fdq"], capture_output=True)
    repo_map = None
    if use_repo_map:
        # tree-sitter runs in a subprocess: its native binding has a flaky
        # shutdown crash on some interpreter versions; isolation keeps the
        # eval process pure-python and lets a crashed child degrade to no map.
        try:
            p = subprocess.run(
                [sys.executable, "-m", "repofixer", "map", str(repo), task.problem_statement],
                capture_output=True, text=True, timeout=90,
            )
            if p.returncode == 0 and p.stdout.strip():
                repo_map = p.stdout
        except Exception:
            repo_map = None
    try:
        from .safety import SafetyPolicy

        registry = ToolRegistry(env)
        if tool_filter:
            registry.filter(tool_filter)
        verify_env = None
        if task.src_layout:
            pypath = "/workspace/src" if env_kind == "docker" else str(repo / "src")
            verify_env = {"PYTHONPATH": pypath}
        notes_text = load_notes(repo)
        run = run_agent(
            task.problem_statement,
            llm,
            registry,
            trace_path,
            max_steps=max_steps,
            repo_map=repo_map,
            context_budget=context_budget,
            obs_dir=obs_dir,
            policy=SafetyPolicy("auto", workspace=repo),
            solved_check=lambda: env.run(task.verify_command, timeout=min(task.timeout, 120), env=verify_env).ok,
            notes=notes_text or None,
            snapshot=True,
            review=True,
        )
        verify = env.run(task.verify_command, timeout=min(task.timeout, 180), env=verify_env)
        try:
            append_note(
                repo,
                f"{time.strftime('%Y-%m-%d')} {task.id} resolved={verify.ok} "
                f"steps={len(run.steps)} reason={run.stopped_reason}: {run.done_summary or ''}".rstrip(),
            )
        except OSError:
            pass
        return EvalResult(
            task_id=task.id,
            resolved=verify.ok,
            stopped_reason=run.stopped_reason,
            steps=len(run.steps),
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            wall_ms=int((time.time() - t0) * 1000),
            trace_path=str(trace_path),
        )
    finally:
        env.close()


def run_eval(
    tasks: list[Task],
    llm: LLMClient,
    env_kind: str,
    out_dir: Path,
    *,
    base_dir: Path | None = None,
    max_steps: int = 40,
    resume: bool = True,
    tool_filter: set[str] | None = None,
    use_repo_map: bool = True,
    context_budget: int | None = 60_000,
) -> list[EvalResult]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    done: dict[str, EvalResult] = {}
    if resume and results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done[d["task_id"]] = EvalResult(**d)
    results: list[EvalResult] = []
    for i, task in enumerate(tasks, 1):
        if task.id in done:
            results.append(done[task.id])
            print(f"[{i}/{len(tasks)}] {task.id} (cached) resolved={done[task.id].resolved}", flush=True)
            continue
        print(f"[{i}/{len(tasks)}] {task.id} running...", flush=True)
        res = run_task(
            task, llm, env_kind, out_dir, base_dir=base_dir, max_steps=max_steps,
            tool_filter=tool_filter, use_repo_map=use_repo_map, context_budget=context_budget,
        )
        print(f"[{i}/{len(tasks)}] {task.id} -> resolved={res.resolved} steps={res.steps} ({res.stopped_reason})", flush=True)
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
        results.append(res)
    return results


def summarize(results: list[EvalResult], model: str, prices: dict[str, tuple[float, float]] | None = None) -> dict:
    n = len(results)
    resolved = sum(1 for r in results if r.resolved)
    steps = [r.steps for r in results]
    pt = sum(r.prompt_tokens for r in results)
    ct = sum(r.completion_tokens for r in results)
    p_in, p_out = (prices or DEFAULT_PRICES).get(model, (2.0, 8.0))
    cost = pt / 1_000_000 * p_in + ct / 1_000_000 * p_out
    walls = sorted(r.wall_ms for r in results)
    return {
        "n": n,
        "resolved": resolved,
        "success_rate": round(resolved / n, 3) if n else 0,
        "avg_steps": round(sum(steps) / n, 1) if n else 0,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "est_cost_yuan": round(cost, 4),
        "p50_ms": walls[n // 2] if n else 0,
        "p95_ms": walls[int(n * 0.95)] if n else 0,
    }
