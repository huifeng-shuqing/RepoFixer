"""Edge-case tests for the workhorse tools (error branches were under-covered)."""
from repofixer.environment import LocalEnv
from repofixer.tools import ToolRegistry


def reg(ws):
    return ToolRegistry(LocalEnv(ws))


def test_read_file_offset_beyond_eof(ws):
    (ws / "a.txt").write_text("line1\nline2\n", encoding="utf-8")
    r = reg(ws).call("read_file", {"path": "a.txt", "offset": 5})
    assert r.ok
    assert "line1" not in r.output  # page past the end is empty


def test_read_file_missing(ws):
    r = reg(ws).call("read_file", {"path": "nope.txt"})
    assert not r.ok and "cannot read" in r.output


def test_edit_file_old_text_not_found(ws):
    (ws / "a.txt").write_text("hello", encoding="utf-8")
    r = reg(ws).call("edit_file", {"path": "a.txt", "old_text": "missing", "new_text": "x"})
    assert not r.ok
    assert "old_text not found" in r.output


def test_edit_file_replaces_once(ws):
    (ws / "a.txt").write_text("abab", encoding="utf-8")
    r = reg(ws).call("edit_file", {"path": "a.txt", "old_text": "ab", "new_text": "X"})
    assert r.ok
    assert (ws / "a.txt").read_text(encoding="utf-8") == "Xab"


def test_write_file_creates_dirs(ws):
    r = reg(ws).call("write_file", {"path": "sub/dir/b.txt", "content": "data"})
    assert r.ok
    assert (ws / "sub" / "dir" / "b.txt").read_text(encoding="utf-8") == "data"


def test_tools_block_workspace_escape(ws):
    for tool, args in [
        ("read_file", {"path": "../secret.txt"}),
        ("write_file", {"path": "../s.txt", "content": "x"}),
        ("edit_file", {"path": "../s.txt", "old_text": "a", "new_text": "b"}),
        ("grep", {"pattern": "x", "path": "../"}),
    ]:
        r = reg(ws).call(tool, args)
        assert not r.ok, tool
        assert "escapes workspace" in r.output, tool


def test_grep_no_match(ws):
    (ws / "a.txt").write_text("nothing here", encoding="utf-8")
    r = reg(ws).call("grep", {"pattern": "zzz"})
    assert r.ok and "no files match" in r.output


def test_grep_invalid_regex(ws):
    r = reg(ws).call("grep", {"pattern": "(["})
    assert not r.ok and "invalid regex" in r.output


def test_run_test_failing_command_reports_failures(ws):
    r = reg(ws).call("run_test", {"command": "python -c \"import sys; print('FAILED test_x'); sys.exit(1)\""})
    assert not r.ok
    assert "FAILED test_x" in r.output


def test_plan_read_missing(ws):
    r = reg(ws).call("plan", {})
    assert r.ok and "no plan recorded" in r.output
