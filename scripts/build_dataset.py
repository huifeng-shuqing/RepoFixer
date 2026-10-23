"""Build eval tasks from real bugfix commits (SWE-bench-style, simplified).

Pipeline per repo:
  1. clone
  2. scan recent commits whose message matches fix keywords AND that touch
     both source files and test files
  3. for each candidate: create a worktree at the parent commit, apply the
     commit's TEST changes (git checkout <sha> -- <test files>), run the tests
     -> must fail (this proves the tests actually catch the bug); then check
     out the full fix commit and run again -> must pass
  4. emit a task: repo copy at the parent commit, problem statement from the
     commit message, verify_command = venv python + pytest on the test files,
     gold patch saved OUTSIDE the task repo (never shown to the agent)

Notes:
- verify_command embeds an absolute path to a per-repo venv (pytest installed);
  this dataset is evaluated with --env local. Docker support for these tasks
  would require baking per-repo images (future work, documented).
- task repos keep their .git so the eval runner can reset them between runs.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
DATASET = ROOT / "dataset"
OUT_JSONL = ROOT / "data" / "tasks_real.jsonl"

FIX_RE = re.compile(
    r"\b(fix|bug|correct|typo|regress|properly|handle|guard|crash|wrong|broken|mis|error|exception)\w*",
    re.I,
)

REPOS = {
    "humanize": "https://github.com/python-humanize/humanize",
    "tabulate": "https://github.com/astanin/python-tabulate",
    "shortuuid": "https://github.com/skorokithakis/shortuuid",
    "validators": "https://github.com/kvesteri/validators",
    "inflection": "https://github.com/jpvanhal/inflection",
    "prettytable": "https://github.com/prettytable/prettytable",
    "more-itertools": "https://github.com/more-itertools/more-itertools",
    "boltons": "https://github.com/mahmoud/boltons",
    "wcwidth": "https://github.com/jquast/wcwidth",
    "pathspec": "https://github.com/cpburnz/python-pathspec",
    "tqdm": "https://github.com/tqdm/tqdm",
}


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 600, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=env,
    )


def log(msg: str) -> None:
    print(msg, flush=True)


def clone(url: str, dest: Path) -> None:
    if not dest.exists():
        log(f"cloning {dest.name} ...")
        run(["git", "clone", "--quiet", url, str(dest)], timeout=900)
    run(["git", "-C", str(dest), "checkout", "--quiet", "origin/HEAD"])


def venv_python(name: str) -> Path:
    p = WORK / "venvs" / name / "Scripts" / "python.exe"
    if not p.exists():
        log(f"creating venv for {name} ...")
        run([sys.executable, "-m", "venv", str(WORK / "venvs" / name)])
    check = run([str(p), "-c", "import pytest"], timeout=120)
    if check.returncode != 0:
        log(f"installing pytest into {name} ...")
        r = run(
            [str(p), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
             "--index-url", "https://pypi.org/simple", "pytest<9"],
            timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(f"pip install pytest failed for {name}: {r.stderr[-400:]}")
    return p


def _is_src(f: str) -> bool:
    return f.endswith(".py") and "test" not in Path(f).name.lower()


def _is_test(f: str) -> bool:
    return f.endswith(".py") and ("test" in Path(f).name.lower() or Path(f).parts[0] in ("tests", "test"))


def scan_fix_commits(repo: Path, max_candidates: int) -> list[dict]:
    out = run(["git", "-C", str(repo), "log", "--format=%H%x09%s", "-n", "800"])
    candidates: list[dict] = []
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        if not FIX_RE.search(subject):
            continue
        stat = run(["git", "-C", str(repo), "show", "--stat", "--format=", sha, "--"])
        files = re.findall(r"^\s+(.+?)\s+\|\s+\d+", stat.stdout, re.M)
        src = [f for f in files if _is_src(f)]
        tst = [f for f in files if _is_test(f)]
        if src and tst:
            candidates.append({"sha": sha, "subject": subject.strip(), "test_files": tst, "src_files": src})
            if len(candidates) >= max_candidates:
                break
    return candidates


def patch_scm_version(wt: Path) -> bool:
    """Repos using setuptools_scm generate src/<pkg>/_version.py at build time;
    a bare checkout cannot import the package without it. Write a stub where
    the package's __init__ clearly expects one. Returns True if any stub was
    written."""
    changed = False
    for base in (wt / "src", wt):
        if not base.exists():
            continue
        for pkg in base.glob("*"):
            init = pkg / "__init__.py"
            if not pkg.is_dir() or not init.exists():
                continue
            ver = pkg / "_version.py"
            if ver.exists():
                continue
            try:
                if "_version" in init.read_text(encoding="utf-8", errors="ignore"):
                    ver.write_text(
                        '__version__ = version = "0.0.0"\n__version_tuple__ = version_tuple = (0, 0, 0)\n',
                        encoding="utf-8",
                    )
                    changed = True
            except OSError:
                pass
    return changed


def check_candidate(repo: Path, name: str, cand: dict, py: Path, verbose: bool = False) -> dict | None:
    sha = cand["sha"]
    parent = run(["git", "-C", str(repo), "rev-parse", f"{sha}^"]).stdout.strip()
    wt = WORK / "wt" / f"{name}-{sha[:8]}"
    shutil.rmtree(wt, ignore_errors=True)
    import os

    env = dict(os.environ)
    try:
        run(["git", "-C", str(repo), "worktree", "add", "--quiet", "--detach", str(wt), parent])
        # apply the test changes from the fix commit onto the buggy parent
        for tf in cand["test_files"]:
            run(["git", "-C", str(wt), "checkout", "--quiet", sha, "--", tf])
        if (wt / "src").exists():  # src-layout repos: make the package importable
            env["PYTHONPATH"] = str(wt / "src")
        patch_scm_version(wt)
        fail = run([str(py), "-m", "pytest", "-q", "--tb=no", "--no-header", *cand["test_files"]], cwd=wt, env=env)
        if verbose:
            log(f"    {sha[:8]} parent: rc={fail.returncode} {fail.stdout.strip()[-200:]}")
        if fail.returncode == 0:
            return None  # tests do not catch the bug at parent state
        run(["git", "-C", str(wt), "checkout", "--quiet", "--detach", sha])
        ok = run([str(py), "-m", "pytest", "-q", "--tb=no", "--no-header", *cand["test_files"]], cwd=wt, env=env)
        if verbose:
            log(f"    {sha[:8]} fix:    rc={ok.returncode} {ok.stdout.strip()[-200:]}")
        if ok.returncode != 0:
            return None  # fixed state does not pass (env/version incompatibility)
    finally:
        run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)])
        shutil.rmtree(wt, ignore_errors=True)
    f2p = re.findall(r"(?:FAILED|ERROR)\s+(\S+)", fail.stdout)
    cand = dict(cand)
    cand["parent"] = parent
    cand["f2p"] = f2p or cand["test_files"]
    return cand


def emit_task(repo: Path, name: str, cand: dict, py: Path, out, seen: set[str]) -> None:
    tid = f"{name}-{cand['sha'][:8]}"
    if tid in seen:
        return
    seen.add(tid)
    dest = DATASET / "repos" / tid
    if not dest.exists():
        run(["git", "clone", "--quiet", str(repo), str(dest)], timeout=900)
        run(["git", "-C", str(dest), "checkout", "--quiet", cand["parent"]])
        # SWE-bench-style test patch: bring in the fix commit's TEST changes so
        # the bug is actually observable; the source fix stays out (gold patch).
        for tf in cand["test_files"]:
            run(["git", "-C", str(dest), "checkout", "--quiet", cand["sha"], "--", tf])
        if patch_scm_version(dest):
            pass  # stub written; committed together below
        run(["git", "-C", str(dest), "add", "-A"])
        run(["git", "-C", str(dest), "-c", "user.name=dataset", "-c", "user.email=dataset@local",
             "commit", "--quiet", "-m", "build: apply test patch + stub _version (source fix removed)"])
    gold_dir = DATASET / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)
    gold = gold_dir / f"{tid}.patch"
    patch = run(["git", "-C", str(repo), "diff", f"{cand['sha']}^", cand["sha"], "--"]).stdout
    gold.write_text(patch, encoding="utf-8")
    test_patch = run(["git", "-C", str(repo), "diff", f"{cand['sha']}^", cand["sha"], "--", *cand["test_files"]]).stdout
    (gold_dir / f"{tid}.test.patch").write_text(test_patch, encoding="utf-8")
    src_patch = run(["git", "-C", str(repo), "diff", f"{cand['sha']}^", cand["sha"], "--", *cand["src_files"]]).stdout
    (gold_dir / f"{tid}.src.patch").write_text(src_patch, encoding="utf-8")
    tfiles = " ".join(cand["test_files"])
    prefix = "set PYTHONPATH=src&& " if (dest / "src").exists() else ""
    entry = {
        "id": tid,
        "problem_statement": f"Fix the failing tests.\n\nUpstream commit message: {cand['subject']}",
        "repo": f"../dataset/repos/{tid}",
        "verify_command": f"{prefix}\"{py}\" -m pytest -q --tb=no {tfiles}",
        "timeout": 600,
        "difficulty": "easy" if len(cand["test_files"]) == 1 else "medium",
        "base_commit": cand["parent"],
        "fix_commit": cand["sha"],
        "test_files": cand["test_files"],
        "src_files": cand["src_files"],
    }
    out.write(json.dumps(entry, ensure_ascii=False) + "\n")
    out.flush()
    log(f"  task: {tid}  ({cand['subject'][:60]})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="*", help="subset of repo names; default: all")
    ap.add_argument("--max-candidates", type=int, default=20)
    ap.add_argument("--max-tasks", type=int, default=0, help="stop after N tasks per repo (0 = unlimited)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    names = args.repos or list(REPOS)
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if OUT_JSONL.exists():
        for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen.add(json.loads(line)["id"])
                except json.JSONDecodeError:
                    pass
    with OUT_JSONL.open("a", encoding="utf-8") as out:
        for name in names:
            log(f"== {name} ==")
            repo = WORK / "repos" / name
            try:
                clone(REPOS[name], repo)
                py = venv_python(name)
                cands = scan_fix_commits(repo, args.max_candidates)
                log(f"  {len(cands)} fix candidates")
                done = 0
                for cand in cands:
                    try:
                        valid = check_candidate(repo, name, cand, py, verbose=args.verbose)
                    except subprocess.TimeoutExpired:
                        continue
                    if valid is None:
                        continue
                    emit_task(repo, name, valid, py, out, seen)
                    done += 1
                    if args.max_tasks and done >= args.max_tasks:
                        break
                log(f"  {done} tasks emitted")
            except Exception as e:
                log(f"  repo failed: {e}")
    log(f"done -> {OUT_JSONL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
