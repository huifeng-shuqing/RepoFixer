"""Simulate a fresh clone: run the test suite against HEAD's TRACKED files only.

Catches the class of bug where local artifacts (a nested .git, an edited but
gitignored file, uncommitted state) make tests green locally while CI fails.

Usage: uv run python scripts/check_fresh.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp(prefix="repofixer_fresh_"))
try:
    subprocess.run(
        ["git", "-C", str(ROOT), "clone", "--local", "--quiet", str(ROOT), str(tmp)],
        check=True,
    )
    env = {**os.environ, "PYTHONPATH": str(tmp / "src")}
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(tmp / "tests")], env=env)
    print(f"\nfresh-clone pytest exit: {p.returncode}")
    sys.exit(p.returncode)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
