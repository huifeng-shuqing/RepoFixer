import time

from repofixer.environment import LocalEnv


def test_resolve_escape_raises(ws):
    env = LocalEnv(ws)
    try:
        env.resolve("../outside")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_run_ok(ws):
    env = LocalEnv(ws)
    r = env.run("echo hello")
    assert r.ok
    assert r.stdout.strip() == "hello"


def test_run_blacklist(ws):
    env = LocalEnv(ws)
    r = env.run("rm -rf /")
    assert not r.ok
    assert "blocked" in r.stderr


def test_run_posix_syntax(ws):
    """Model-written POSIX commands (`;`, pipes, 2>/dev/null) must work."""
    env = LocalEnv(ws)
    r = env.run("echo a; echo b")
    assert r.ok and "a" in r.stdout and "b" in r.stdout
    r2 = env.run("printf 'x\\n' | head -1")
    assert r2.ok and "x" in r2.stdout


def test_run_timeout_kills_process_tree(ws):
    """Grandchild holding the stdout pipe must not hang communicate()."""
    import time

    env = LocalEnv(ws)
    t0 = time.time()
    r = env.run(
        'python -c "import subprocess; subprocess.Popen([\'python\', \'-c\', \'import time; time.sleep(30)\']); import time; time.sleep(30)"',
        timeout=3,
    )
    assert r.timed_out
    assert time.time() - t0 < 20  # tree killed promptly, no hang


def test_run_timeout(ws):
    env = LocalEnv(ws)
    t0 = time.time()
    r = env.run('python -c "import time; time.sleep(30)"', timeout=1)
    assert r.timed_out
    assert time.time() - t0 < 10
