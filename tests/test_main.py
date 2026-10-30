"""CLI dispatch tests (main.py was 0% covered; every subcommand branch)."""
import repofixer.main as m


def test_main_requires_subcommand():
    import pytest

    with pytest.raises(SystemExit) as e:
        m.main([])
    assert e.value.code == 2


def test_eval_dispatch(monkeypatch, tmp_path):
    called = {}

    def fake_run_eval(tasks, llm, env_kind, out_dir, **opts):
        called["tasks"] = tasks
        called["out_dir"] = out_dir
        called["opts"] = opts
        return []

    monkeypatch.setattr(m, "LLMClient", lambda: type("L", (), {"model": "m"})())
    monkeypatch.setattr("repofixer.eval.run_eval", fake_run_eval)
    monkeypatch.setattr("repofixer.eval.load_dataset", lambda p: [{"id": "t1", "repo": "../samples/calc_bug", "verify_command": "python tests/test_ops.py", "timeout": 300, "problem_statement": "x", "src_layout": False}])

    ds = tmp_path / "ds.jsonl"
    ds.write_text("{}", encoding="utf-8")
    rc = m.main(["eval", "--dataset", str(ds), "--out-dir", str(tmp_path / "out"), "--baseline", "nomap"])
    assert rc == 0
    assert called["out_dir"].name == "out"
    assert called["opts"]["use_repo_map"] is False


def test_run_dispatch_defaults(monkeypatch, tmp_path):
    called = {}

    class FakeEnv:
        workspace = tmp_path

        def close(self):
            pass

    def fake_create_env(ws, kind):
        return FakeEnv(), "local"

    def fake_run_agent(task, llm, registry, trace_path, **kw):
        called.update(kw)
        from repofixer.loop import AgentRun
        return AgentRun()

    monkeypatch.setattr(m, "create_env", fake_create_env)
    monkeypatch.setattr(m, "LLMClient", lambda: type("L", (), {"model": "m"})())
    monkeypatch.setattr(m, "run_agent", fake_run_agent)   # top-level import in main.py
    monkeypatch.setattr(m, "ToolRegistry", lambda env: "registry")
    monkeypatch.setattr("repofixer.notes.load_notes", lambda ws: "")
    monkeypatch.setattr("repofixer.notes.append_note", lambda ws, entry: None)

    rc = m.main(["run", "fix the bug", "-w", str(tmp_path), "--no-repo-map", "--trace-dir", str(tmp_path)])
    assert rc == 0
    assert called["plan_first"] is True   # plan-act on by default
    assert called["snapshot"] is False
    assert called["review"] is False
    assert called["repo_map"] is None     # --no-repo-map


def test_run_dispatch_flags(monkeypatch, tmp_path):
    called = {}

    class FakeEnv:
        workspace = tmp_path

        def close(self):
            pass

    monkeypatch.setattr(m, "create_env", lambda ws, kind: (FakeEnv(), "local"))
    monkeypatch.setattr(m, "LLMClient", lambda: type("L", (), {"model": "m"})())

    def fake_run_agent(task, llm, registry, trace_path, **kw):
        called.update(kw)
        from repofixer.loop import AgentRun
        return AgentRun()

    monkeypatch.setattr(m, "run_agent", fake_run_agent)
    monkeypatch.setattr(m, "ToolRegistry", lambda env: "registry")
    monkeypatch.setattr("repofixer.notes.load_notes", lambda ws: "")
    monkeypatch.setattr("repofixer.notes.append_note", lambda ws, entry: None)

    m.main(["run", "task", "-w", str(tmp_path), "--no-plan", "--snapshot", "--review", "--trace-dir", str(tmp_path)])
    assert called["plan_first"] is False
    assert called["snapshot"] is True
    assert called["review"] is True


def test_rollback_dispatch(monkeypatch, tmp_path):
    monkeypatch.setattr("repofixer.gitutil.rollback", lambda ws, sha: f"reset {sha}")
    rc = m.main(["rollback", "abc123", "-w", str(tmp_path)])
    assert rc == 0


def test_map_dispatch(capsys):
    rc = m.main(["map", "samples/calc_bug", "the mul function is broken", "--token-budget", "300"])
    assert rc == 0
    assert "mul" in capsys.readouterr().out
