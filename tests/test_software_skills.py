"""Software skills (CLI-Anything) — discovery over a fixture repo, grants, classification, receipted runs, endpoints. No network, no real installs."""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from poda_app.agent import software


@pytest.fixture()
def fake_repo(client, tmp_path, monkeypatch):  # `client` boots the app so the schema exists
    monkeypatch.setenv("PODA_ALLOW_EXTERNAL_SOFTWARE", "I_TRUST_MY_INSTALLED_HARNESSES")
    repo = tmp_path / "CLI-Anything"
    (repo / "skills" / "cli-anything-demo").mkdir(parents=True)
    (repo / "demo" / "agent-harness" / "cli_anything" / "demo").mkdir(parents=True)
    (repo / "skills" / "cli-anything-demo" / "SKILL.md").write_text("---\nname: cli-anything-demo\n---\n# demo\n\nUse `cli-anything-demo info` to inspect.\n")
    (repo / "demo" / "agent-harness" / "setup.py").write_text("from setuptools import setup\nsetup(name='cli-anything-demo')\n")
    (repo / "registry.json").write_text(json.dumps({"meta": {}, "clis": [
        {"name": "demo", "display_name": "Demo App", "description": "Demo harness", "requires": "nothing", "category": "testing",
         "install_cmd": "pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=demo/agent-harness", "entry_point": "cli-anything-demo", "skill_md": "skills/cli-anything-demo/SKILL.md"},
        {"name": "remote-only", "display_name": "Remote", "description": "No local checkout", "requires": "x", "category": "testing",
         "install_cmd": "pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=remote-only/agent-harness", "entry_point": "cli-anything-remote-only", "skill_md": "skills/none/SKILL.md"}]}))
    (repo / "public_registry.json").write_text(json.dumps({"meta": {}, "clis": [{"name": "lark", "display_name": "Lark", "description": "npm cli", "package_manager": "npm", "install_cmd": "npm i -g lark", "entry_point": "lark-cli", "category": "communication"}]}))
    software.set_repo_paths([str(repo)])
    # Fake installed executable inside the software venv dir.
    venv_bin = software.SOFTWARE_VENV / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    exe = venv_bin / "cli-anything-demo"
    exe.write_text("#!/bin/sh\nif [ \"$1\" = \"--json\" ]; then shift; fi\nif [ \"$1\" = \"--help\" ]; then echo 'Usage: demo [--json] info|export'; exit 0; fi\n"
                   "if [ \"$1\" = \"info\" ]; then echo '{\"ok\": true, \"name\": \"demo\"}'; exit 0; fi\n"
                   "if [ \"$1\" = \"export\" ]; then echo exported; exit 0; fi\n"
                   "if [ \"$1\" = \"fail\" ]; then echo 'This process is not trusted (accessibility)' 1>&2; exit 2; fi\n"
                   "echo unknown; exit 3\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    (venv_bin / "python").write_text("#!/bin/sh\nexit 0\n"); (venv_bin / "python").chmod(0o755)
    software._CACHE["catalog"] = None
    yield repo
    software.revoke_grant("demo")
    software._CACHE["catalog"] = None


def test_discovery_reads_registries_and_skills(fake_repo):
    items = {e["name"]: e for e in software.catalog(force=True)}
    assert {"demo", "remote-only", "lark"} <= set(items)
    d = items["demo"]
    assert d["installed"] and d["local_harness"].endswith("demo/agent-harness") and "Use `cli-anything-demo info`" in d["skill_excerpt"]
    assert items["remote-only"]["local_harness"] is None and items["lark"]["package_manager"] == "npm"


def test_classification():
    assert software.classify_args(["info"]) == "read"
    assert software.classify_args(["--help"]) == "read"
    assert software.classify_args(["project", "export", "--out", "x"]) == "mutating"
    assert software.classify_args(["macro", "run", "x"]) == "mutating"
    assert software.classify_args(["macro", "list"]) == "read"
    assert software.classify_args(["project", "info"]) == "read"
    assert software.classify_args(["frobnicate"]) == "unknown"


def test_run_requires_enable_then_allows_read_only(fake_repo):
    out = software.run("demo", ["info"])
    assert not out["ok"] and out["code"] == "NOT_ENABLED" and out["receipt"]["outcome"] == "blocked"
    software.set_grant("demo", enabled=True, allow_mutating=False)
    software._CACHE["catalog"] = None
    out = software.run("demo", ["info"])
    assert out["ok"] and out["result"]["json"] == {"ok": True, "name": "demo"} and out["receipt"]["outcome"] == "succeeded"
    assert "--json" in out["result"]["command"]


def test_mutating_blocked_without_flag_and_approval(fake_repo):
    software.set_grant("demo", enabled=True, allow_mutating=False); software._CACHE["catalog"] = None
    out = software.run("demo", ["export", "x"], approved=True)
    assert out["code"] == "MUTATING_NOT_ALLOWED"
    software.set_grant("demo", enabled=True, allow_mutating=True); software._CACHE["catalog"] = None
    out = software.run("demo", ["export", "x"], approved=False)
    assert out["code"] == "APPROVAL_REQUIRED"
    out = software.run("demo", ["export", "x"], approved=True)
    assert out["ok"] and out["receipt"]["outcome"] == "succeeded"


def test_permission_error_is_coded(fake_repo):
    software.set_grant("demo", enabled=True, allow_mutating=True); software._CACHE["catalog"] = None
    out = software.run("demo", ["fail"], approved=True)
    assert not out["ok"] and out["result"]["code"] == "MACOS_ACCESSIBILITY_REQUIRED" and "Accessibility" in out["result"]["remedy"]
    assert out["receipt"]["outcome"] == "failed"


def test_tool_registration_and_availability(fake_repo):
    from poda_app.agent import tools
    software.refresh_tools()
    assert {"software_list", "software_skill", "software_query", "software_cli"} <= set(tools.TOOLS)
    assert tools.availability()["software_query"]["available"] is False
    software.set_grant("demo", enabled=True); software._CACHE["catalog"] = None; software.refresh_tools()
    assert tools.availability()["software_query"]["available"] is True
    assert "demo" in tools.TOOLS["software_query"]["description"]
    res = tools.execute("software_query", {"name": "demo", "args": ["info"]})
    assert res["receipt"]["outcome"] == "succeeded"
    res = tools.execute("software_query", {"name": "demo", "args": ["export", "x"]})
    assert res["receipt"]["outcome"] == "blocked"
    software.revoke_grant("demo"); software._CACHE["catalog"] = None; software.refresh_tools()
    assert tools.availability()["software_query"]["available"] is False


def test_install_requires_confirm_and_network_flag(fake_repo):
    with pytest.raises(PermissionError):
        software.install("demo", confirm=False)
    with pytest.raises(PermissionError):
        software.install("remote-only", confirm=True, allow_network=False)
    with pytest.raises(RuntimeError):
        software.install("lark", confirm=True)


def test_endpoints(client, fake_repo):
    r = client.get("/software").json()
    assert r["count"] >= 3 and "demo" in r["installed"] and r["capability"]["can_drive_software"] is False
    assert client.get("/software/demo").json()["skill_md"].startswith("---")
    assert client.post("/software/demo/run", json={"args": ["info"]}).status_code == 403
    g = client.post("/software/demo/grant", json={"enabled": True, "allow_mutating": False}).json()
    assert g["grant"]["enabled"] == 1
    out = client.post("/software/demo/run", json={"args": ["info"]}).json()
    assert out["ok"] and out["receipt"]["outcome"] == "succeeded"
    assert client.post("/software/demo/run", json={"args": ["export", "x"], "approve": True}).status_code == 403
    assert client.post("/software/demo/install", json={"confirm": False}).status_code == 400
    assert client.post("/software/demo/install", json={"confirm": False}).json()["detail"]["code"] == "CONFIRM_REQUIRED"
    assert client.get("/software/receipts").json()["receipts"][0]["tool"].startswith("software_")
    assert client.delete("/software/demo/grant").json()["revoked"] is True
    assert client.get("/software").json()["capability"]["can_drive_software"] is False
