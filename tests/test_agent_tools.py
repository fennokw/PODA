import hashlib
import os
import stat
import sys
from pathlib import Path

import pytest

from poda_app.agent import grants, tools, fs_tools, exec_tools, receipts
from poda_app.runtime.db import init_database


@pytest.fixture(autouse=True)
def _schema():
    init_database()


@pytest.fixture()
def granted(tmp_folder):
    g = grants.add_grant(str(tmp_folder), "edit", "test", allow_execute=True)
    yield tmp_folder, g
    grants.revoke_grant(g["id"])


@pytest.fixture()
def read_only(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    g = grants.add_grant(str(folder), "read", "ro")
    yield folder, g
    grants.revoke_grant(g["id"])


def test_no_grant_blocks_with_receipt(tmp_path):
    out = tools.execute("fs_read", {"path": str(tmp_path / "x.txt")})
    assert out["receipt"]["outcome"] == "blocked" and out["result"]["ok"] is False
    assert "grant" in out["result"]["error"].lower()
    with pytest.raises(PermissionError):
        grants.resolve(str(tmp_path / "x.txt"), "read")


def test_path_traversal_and_symlink_escape_refused(granted, tmp_path):
    folder, _ = granted
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    assert fs_tools.fs_read(str(folder / ".." / "outside.txt"))["ok"] is False
    link = folder / "link.txt"
    link.symlink_to(outside)
    res = fs_tools.fs_read(str(link))
    assert res["ok"] is False and "No active grant" in res["error"]
    assert fs_tools.fs_write(str(folder / ".." / "escape.txt"), "x")["ok"] is False
    assert not (tmp_path / "escape.txt").exists()


def test_write_readback_sha_and_overwrite_rules(granted):
    folder, _ = granted
    target = folder / "hello.txt"
    out = tools.execute("fs_write", {"path": str(target), "content": "hello world\n"}, approved=True)
    assert out["receipt"]["outcome"] == "succeeded"
    assert out["result"]["result"]["sha256"] == hashlib.sha256(b"hello world\n").hexdigest()
    assert target.read_text() == "hello world\n"
    again = tools.execute("fs_write", {"path": str(target), "content": "new"}, approved=True)
    assert again["receipt"]["outcome"] == "failed" and "overwrite=false" in again["result"]["error"]
    assert target.read_text() == "hello world\n"
    ow = tools.execute("fs_write", {"path": str(target), "content": "new\n", "overwrite": True}, approved=True)
    assert ow["receipt"]["outcome"] == "succeeded" and ow["result"]["result"]["backup"] and Path(ow["result"]["result"]["backup"]).read_text() == "hello world\n"


def test_mutating_requires_approval(granted):
    folder, _ = granted
    out = tools.execute("fs_write", {"path": str(folder / "a.txt"), "content": "x"}, approved=False)
    assert out["receipt"]["outcome"] == "blocked" and not (folder / "a.txt").exists()


def test_edit_hash_validation(granted):
    folder, _ = granted
    f = folder / "code.py"
    f.write_text("def f():\n    return 1\n")
    sha = fs_tools.fs_read(str(f))["result"]["sha256"]
    stale = fs_tools.fs_edit(str(f), "deadbeef", "return 1", "return 2")
    assert stale["ok"] is False and "Stale hash" in stale["error"] and f.read_text() == "def f():\n    return 1\n"
    good = fs_tools.fs_edit(str(f), sha, "return 1", "return 2")
    assert good["ok"] and f.read_text() == "def f():\n    return 2\n"
    ambiguous = fs_tools.fs_edit(str(f), good["result"]["sha256"], "\n", "\n\n")
    assert ambiguous["ok"] is False and "matches" in ambiguous["error"]


def test_run_python_real_exit_code_and_bounded_output(granted, monkeypatch):
    monkeypatch.setenv("PODA_UNSAFE_EXECUTE", "I_UNDERSTAND_THIS_RUNS_AS_ME")
    folder, _ = granted
    script = folder / "exit3.py"
    script.write_text("import sys\nprint('x' * 100000)\nsys.exit(3)\n")
    out = tools.execute("run_python", {"cwd": str(folder), "path": str(script)}, approved=True)
    res = out["result"]["result"]
    assert res["exit_code"] == 3 and out["receipt"]["outcome"] == "failed"
    assert len(res["stdout"]) <= exec_tools.MAX_OUTPUT and res["stdout_truncated"] is True
    ok = exec_tools.run_python(str(folder), code="print('hi')")
    assert ok["ok"] and ok["result"]["exit_code"] == 0 and "hi" in ok["result"]["stdout"]
    assert "PODA_DATA_DIR" not in exec_tools._scrubbed_env(folder)


def test_execute_requires_execute_grant(read_only):
    folder, _ = read_only
    out = exec_tools.run_python(str(folder), code="print(1)")
    assert out["ok"] is False and "No active grant" in out["error"]
    assert tools.availability()["run_python"]["available"] is False


def test_revocation_blocks_immediately(tmp_folder):
    g = grants.add_grant(str(tmp_folder), "edit", "temp")
    assert fs_tools.fs_write(str(tmp_folder / "one.txt"), "1")["ok"]
    grants.revoke_grant(g["id"])
    out = fs_tools.fs_write(str(tmp_folder / "two.txt"), "2")
    assert out["ok"] is False and not (tmp_folder / "two.txt").exists()
    assert grants.filesystem_capability()["general_user_file_read"] is False


def test_validate_args():
    with pytest.raises(tools.ToolValidationError):
        tools.validate_args("fs_read", {"path": "/x", "bogus": 1})
    with pytest.raises(tools.ToolValidationError):
        tools.validate_args("fs_read", {"path": 123})
    with pytest.raises(tools.ToolValidationError):
        tools.validate_args("fs_write", {"path": "/x"})
    with pytest.raises(tools.ToolValidationError):
        tools.validate_args("nope", {})
    assert tools.validate_args("fs_list", '{"path": "/tmp", "depth": "2"}') == {"path": "/tmp", "depth": 2}


def test_hallucinated_tool_in_plan_is_dropped(monkeypatch, granted):
    from poda_app.agent import orchestrator
    folder, _ = granted
    (folder / "readme.md").write_text("hello")
    responses = iter([
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "delete_everything", "arguments": {}}},
            {"function": {"name": "fs_read", "arguments": {"path": str(folder / "readme.md")}}},
            {"function": {"name": "fs_write", "arguments": {"path": str(folder / "out.txt"), "content": "x"}}}]}},
        {"message": {"role": "assistant", "content": "done planning"}},
    ])
    monkeypatch.setattr(orchestrator.ollama, "chat", lambda *a, **k: next(responses))
    monkeypatch.setattr(orchestrator.ollama, "status", lambda: {"reachable": True, "models": ["llama3.2:3b"]})
    snapshot = {"filesystem": grants.filesystem_capability(), "notion": {}}
    plan = orchestrator.plan("read readme then write out.txt", snapshot, "", "s1", "llama3.2:3b")
    assert plan["needs_tools"] and plan["flagged"][0]["name"] == "delete_everything"
    assert plan["executed"][0]["step"]["tool"] == "fs_read" and plan["executed"][0]["receipt"]["outcome"] == "succeeded"
    assert plan["steps"][0]["tool"] == "fs_write" and plan["proposal_id"]
    assert not (folder / "out.txt").exists()
    result = orchestrator.approve(plan["proposal_id"])
    assert result["status"] == "executed" and (folder / "out.txt").read_text() == "x"
    assert orchestrator.get_proposal(plan["proposal_id"])["status"] == "executed"


def test_acceptance_create_poda_test_on_desktop_standin(tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    target = desktop / "PODA Test 1.py"
    blocked = tools.execute("fs_write", {"path": str(target), "content": "print('hello from PODA')\n"}, approved=True)
    assert blocked["receipt"]["outcome"] == "blocked" and not target.exists()
    g = grants.add_grant(str(desktop), "edit", "desktop")
    try:
        done = tools.execute("fs_write", {"path": str(target), "content": "print('hello from PODA')\n"}, approved=True)
        assert done["receipt"]["outcome"] == "succeeded" and done["receipt"]["verification"].startswith("wrote")
        assert target.read_text() == "print('hello from PODA')\n"
        back = tools.execute("fs_read", {"path": str(target)})
        assert back["result"]["result"]["sha256"] == done["result"]["result"]["sha256"]
        # root bypasses chmod; this assertion tests normal-user DAC only.
        if os.geteuid() == 0:
            pytest.skip("root bypasses directory permission bits")
        # forced failure: read-only directory → failed receipt, no success claim
        ro = desktop / "locked"
        ro.mkdir()
        os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
        try:
            fail = tools.execute("fs_write", {"path": str(ro / "x.py"), "content": "x"}, approved=True)
            assert fail["receipt"]["outcome"] == "failed" and fail["result"]["ok"] is False
            assert fail["receipt"]["error"]
        finally:
            os.chmod(ro, stat.S_IRWXU)
    finally:
        grants.revoke_grant(g["id"])
    assert receipts.get_receipt(done["receipt"]["id"])["outcome"] == "succeeded"
