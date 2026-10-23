from repofixer.environment import LocalEnv
from repofixer.tools import ToolRegistry


def _setup(ws):
    (ws / "a.txt").write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
    (ws / "b.txt").write_text("nothing to see\n", encoding="utf-8")
    reg = ToolRegistry(LocalEnv(ws))
    return reg


def test_read_paging(ws):
    reg = _setup(ws)
    r = reg.call("read_file", {"path": "a.txt", "limit": 2})
    assert r.ok
    assert "line1" in r.output and "line3" not in r.output
    r2 = reg.call("read_file", {"path": "a.txt", "offset": 3, "limit": 2})
    assert r2.ok and "line3" in r2.output


def test_edit_exact_only(ws):
    reg = _setup(ws)
    r = reg.call("edit_file", {"path": "a.txt", "old_text": "line1", "new_text": "LINE1"})
    assert r.ok
    assert (ws / "a.txt").read_text(encoding="utf-8").startswith("LINE1")
    r2 = reg.call("edit_file", {"path": "a.txt", "old_text": "not there", "new_text": "x"})
    assert not r2.ok and "not found" in r2.output


def test_grep_names_only(ws):
    reg = _setup(ws)
    r = reg.call("grep", {"pattern": "line1"})
    assert r.ok
    assert "a.txt" in r.output
    assert "line1" not in r.output  # ACI: file names only, not match contents


def test_bash_empty_output_explicit(ws):
    reg = _setup(ws)
    r = reg.call("bash", {"command": "cd ."})
    assert r.ok
    assert "(ok, no output)" in r.output


def test_unknown_tool(ws):
    reg = _setup(ws)
    r = reg.call("no_such_tool", {})
    assert not r.ok and r.error_type == "ToolUsageError"
