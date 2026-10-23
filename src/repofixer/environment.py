"""Execution environment abstraction.

Borrowed idea (rewritten, see REPORT.md) from mini-swe-agent (MIT): keep every
action behind one interface so that "sandboxing = swap LocalEnv for DockerEnv".
Each action runs as an independent process — no persistent shell session —
which sidesteps "when does a command end", "a bad command kills the shell",
and "interrupted commands pollute state".

LocalEnv: development mode, confined to the workspace directory (NOT a
security boundary). DockerEnv: one throwaway container per task, no network,
capped resources, destroyed on close — the real isolation layer.
"""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BASH_CANDIDATES = (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe")


def find_bash() -> str | None:
    # prefer Git Bash explicitly: `which bash` can resolve to the WSL stub in
    # System32, whose failure mode is an opaque UTF-16 ACCESSDENIED
    for c in BASH_CANDIDATES:
        if Path(c).exists():
            return c
    found = shutil.which("bash")
    if found and Path(found).parent.resolve() != Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32":
        return found
    return None


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process and its whole tree (cross-platform)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except Exception:
                pass


def _run_with_timeout(
    cmd: list[str] | str,
    *,
    cwd: str,
    timeout: int,
    env: dict | None,
    shell: bool = False,
) -> ExecResult:
    """Run a command with a REAL timeout: on expiry the whole process tree is
    killed (subprocess.run's timeout only kills the direct child, and
    grandchildren holding the stdout pipe make communicate() hang forever)."""
    kwargs: dict = dict(
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, shell=shell, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
        return ExecResult(
            ok=proc.returncode == 0, stdout=out or "", stderr=err or "", exit_code=proc.returncode or 0
        )
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
            try:
                proc.kill()
            except Exception:
                pass
        return ExecResult(
            ok=False,
            stdout=out or "",
            stderr=(err or "") + f"\n[timeout after {timeout}s: process tree killed]",
            exit_code=124,
            timed_out=True,
        )


@dataclass
class ExecResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class Environment:
    def run(self, command: str, cwd: str = ".", timeout: int | None = None) -> ExecResult:  # pragma: no cover
        raise NotImplementedError

    def resolve(self, path: str) -> Path:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        return None


class LocalEnv(Environment):
    """Runs commands on the host, confined to the workspace directory.

    Uses Git Bash when available so that POSIX-style commands the model
    naturally writes (`grep -rn x . | head`, `a; b`, `2>/dev/null`) behave
    like they would inside the Docker sandbox; falls back to cmd.exe.
    NOTE: this is the development environment, not a security boundary.
    """

    def __init__(
        self,
        workspace: Path,
        command_blacklist: tuple[str, ...] = ("rm -rf /", "sudo", "shutdown", "reboot", "format c:"),
    ):
        self.workspace = workspace.resolve()
        self.command_blacklist = command_blacklist
        self.bash = find_bash()

    def resolve(self, path: str) -> Path:
        p = (self.workspace / path).resolve()
        if not p.is_relative_to(self.workspace):
            raise ValueError(f"path escapes workspace: {path}")
        return p

    def run(self, command: str, cwd: str = ".", timeout: int = 120, env: dict | None = None) -> ExecResult:
        low = command.lower()
        for bad in self.command_blacklist:
            if bad in low:
                return ExecResult(
                    ok=False,
                    stdout="",
                    stderr=f"blocked: command contains blacklisted pattern '{bad}'",
                    exit_code=1,
                )
        full_env = {**os.environ, **(env or {})}
        if self.bash:
            full_env.setdefault("MSYS_NO_PATHCONV", "1")  # keep C:\... args verbatim for native exes
            return _run_with_timeout(
                [self.bash, "-lc", command],
                cwd=str(self.resolve(cwd)),
                timeout=timeout,
                env=full_env,
            )
        return _run_with_timeout(
            command,
            cwd=str(self.resolve(cwd)),
            timeout=timeout,
            env=full_env,
            shell=True,
        )


def docker_available() -> bool:
    """True if the docker CLI can reach a running daemon."""
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:
        return False


class DockerEnv(Environment):
    """One throwaway container per task.

    - workspace mounted read-write at /workspace
    - `--network none`: the agent's commands cannot reach the internet
      (python:3.12-slim provides python + shell + core tools)
    - capped memory / cpus / pids; per-command `timeout` inside the container
    - every command runs in a fresh `sh -c` (no persistent shell session)
    - container is force-removed on close()
    """

    def __init__(
        self,
        workspace: Path,
        *,
        image: str = "python:3.12-slim",
        name: str | None = None,
        network: str = "none",
        memory: str = "2g",
        cpus: str = "2.0",
        timeout: int = 120,
    ):
        self.workspace = workspace.resolve()
        self.image = image
        self.name = name or f"repofixer-{os.getpid()}-{int(time.time())}"
        self.network = network
        self.memory = memory
        self.cpus = cpus
        self.timeout = timeout
        self._container_id: str | None = None
        self._ensure_container()

    def _docker(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(["docker"] + args, capture_output=True, text=True, timeout=timeout)

    def _ensure_container(self) -> None:
        proc = self._docker(
            [
                "run", "-d", "--name", self.name,
                "--network", self.network,
                "--memory", self.memory, "--cpus", self.cpus, "--pids-limit", "256",
                "-v", f"{self.workspace.as_posix()}:/workspace",
                "-w", "/workspace",
                self.image, "sh", "-c", "sleep infinity",
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(f"docker run failed: {proc.stderr.strip()[:500]}")
        self._container_id = proc.stdout.strip()

    def resolve(self, path: str) -> Path:
        p = (self.workspace / path).resolve()
        if not p.is_relative_to(self.workspace):
            raise ValueError(f"path escapes workspace: {path}")
        return p

    def run(self, command: str, cwd: str = ".", timeout: int | None = None, env: dict | None = None) -> ExecResult:
        timeout = timeout or self.timeout
        target = (self.workspace / cwd).resolve()
        if not target.is_relative_to(self.workspace):
            return ExecResult(ok=False, stdout="", stderr=f"cwd escapes workspace: {cwd}", exit_code=1)
        rel = "" if target == self.workspace else f"/{target.relative_to(self.workspace).as_posix()}"
        wrapped = f"cd /workspace{rel} && timeout {timeout} sh -c {shlex.quote(command)}"
        args = ["exec"]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        proc = self._docker(args + [self.name, "sh", "-c", wrapped], timeout=timeout + 15)
        return ExecResult(
            ok=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            timed_out=proc.returncode == 124,
        )

    def close(self) -> None:
        if self._container_id:
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, text=True, timeout=30)
            self._container_id = None

    def __enter__(self) -> "DockerEnv":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def create_env(workspace: Path, kind: str = "auto") -> tuple[Environment, str]:
    """kind: 'auto' | 'local' | 'docker'. Returns (env, actual_kind)."""
    if kind == "auto":
        kind = "docker" if docker_available() else "local"
    if kind == "docker":
        return DockerEnv(workspace), "docker"
    return LocalEnv(workspace), "local"
