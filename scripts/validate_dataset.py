"""Dataset quality gate (SWE-bench `--gold` equivalent).

For every task: fresh scratch checkout at base_commit -> apply the TEST patch
(checkout the fix commit's test files) -> verify must FAIL (the bug must be
observable) -> apply the SOURCE patch -> verify must PASS -> cleanup.

Exits 1 if any task fails validation.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_dataset import patch_scm_version  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
DATASET_JSONL = ROOT / "data" / "tasks_real.jsonl"


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def verify(command: str, cwd: Path) -> int:
    p = subprocess.run(
        command, cwd=str(cwd), shell=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    return p.returncode


def main() -> int:
    lines = [l for l in DATASET_JSONL.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    tasks = [json.loads(l) for l in lines]
    ok = bad = 0
    for t in tasks:
        name = t["id"].rsplit("-", 1)[0]
        src_repo = WORK / "repos" / name
        scratch = WORK / "val" / t["id"]
        shutil.rmtree(scratch, ignore_errors=True)
        if not src_repo.exists():
            print(f"BAD {t['id']}: source repo {name} not cloned")
            bad += 1
            continue
        try:
            run(["git", "clone", "--quiet", str(src_repo), str(scratch)])
            run(["git", "-C", str(scratch), "checkout", "--quiet", t["base_commit"]])
            for tf in t["test_files"]:
                run(["git", "-C", str(scratch), "checkout", "--quiet", t["fix_commit"], "--", tf])
            patch_scm_version(scratch)  # same stub the task repos carry (setuptools_scm)
            rc_before = verify(t["verify_command"], scratch)
            if rc_before == 0:
                print(f"BAD {t['id']}: verify passes BEFORE the fix (bug not observable)")
                bad += 1
                continue
            for sf in t["src_files"]:
                run(["git", "-C", str(scratch), "checkout", "--quiet", t["fix_commit"], "--", sf])
            rc_after = verify(t["verify_command"], scratch)
            if rc_after != 0:
                print(f"BAD {t['id']}: verify still fails AFTER applying the source fix (rc={rc_after})")
                bad += 1
                continue
            print(f"OK   {t['id']}")
            ok += 1
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    print(f"validated: {ok} ok, {bad} bad, {len(tasks)} total")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
