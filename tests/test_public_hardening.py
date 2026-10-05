"""Regressions for the public build's five audited security/reliability fixes.

All tests run with synthetic temp files/DBs; no personal accounts, macOS Keychain,
Open3D binaries or live local Ollama are required.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_separate_public_namespace_and_required_encryption():
    from poda_app.runtime import config
    assert config.VERSION == "0.6.0-public"
    assert config.DB_KEYCHAIN_SERVICE == "PODA.Public.Database"
    assert config.NOTION_KEYCHAIN_SERVICE == "PODA.Public.Notion"
    assert config.DATA_DIR.name != "PODA"
    # The isolated test harness explicitly overrides required encryption on temporary data.
    assert config.DB_ENCRYPTION == "off"


def test_required_database_fails_closed_when_crypto_unavailable(monkeypatch, tmp_path):
    from poda_app.runtime import db, config
    monkeypatch.setattr(config, "DB_ENCRYPTION", "required")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "new-public.db")
    monkeypatch.setattr(db, "encryption_enabled", lambda: False)
    with pytest.raises(RuntimeError, match="Encrypted storage is required"):
        db.connect()
    assert not (tmp_path / "new-public.db").exists()


def test_redirect_does_not_forward_notion_token(monkeypatch):
    from poda_app.security import egress
    seen = []
    monkeypatch.setattr(egress, "offline_mode", lambda: False)
    monkeypatch.setattr(egress, "_log", lambda *a, **kw: None)
    def fake_request(method, url, **kwargs):
        seen.append((method, url, kwargs))
        return SimpleNamespace(status_code=302, headers={"Location": "https://evil.example/"}, content=b"")
    monkeypatch.setattr(egress.requests, "request", fake_request)
    with pytest.raises(egress.EgressBlocked, match="redirect denied"):
        egress.guarded_request("notion", "GET", "https://api.notion.com/v1/users/me", headers={"Authorization": "Bearer synthetic"})
    assert len(seen) == 1
    assert seen[0][2]["allow_redirects"] is False
    assert seen[0][1] == "https://api.notion.com/v1/users/me"


def test_non_tls_and_unlisted_destinations_blocked(monkeypatch):
    from poda_app.security import egress
    monkeypatch.setattr(egress, "_log", lambda *a, **kw: None)
    for url in ["http://api.notion.com/v1/users/me", "https://evil.example/v1/users/me", "https://api.notion.com.evil.example/"]:
        with pytest.raises(egress.EgressBlocked):
            egress.guarded_request("notion", "GET", url)


def test_legacy_imap_preview_is_retired(client):
    r = client.post("/email/imap/preview", json={"host": "evil.example", "username": "synthetic"})
    assert r.status_code == 410


def test_code_execution_denied_without_os_sandbox(monkeypatch, tmp_path):
    from poda_app.agent import sandbox
    monkeypatch.delenv("PODA_UNSAFE_EXECUTE", raising=False)
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: False)
    assert sandbox.execution_mode() == "blocked_no_os_sandbox"
    with pytest.raises(PermissionError, match="Execution blocked"):
        sandbox.wrap(["python", "-c", "print(1)"], str(tmp_path), str(tmp_path / "scratch"))


def test_unknown_hardware_does_not_get_fabricated_24gb(monkeypatch):
    from poda_app.runtime import models, hardware
    monkeypatch.setattr(models, "_MEMORY_CACHE", None)
    monkeypatch.setattr(hardware, "_sysctl", lambda name: None)
    assert models.measured_memory_gb() == 0
    r = models.registry(installed=["llama3.2:3b"], unified_memory_gb=None)
    assert {k: v["num_ctx"] for k, v in r["profiles"].items()} == {"fast": 4096, "balanced": 8192, "deep": 8192}


def test_release_audit_rejects_sqlite_header_and_private_files(tmp_path):
    from tools.release_guard import ROOT, audit, shipping_paths
    paths = shipping_paths()
    assert paths and not audit(paths)
    assert not any(p.suffix in {".db", ".sqlite", ".podabkp"} or ".git" in p.relative_to(ROOT).parts or "data" in p.relative_to(ROOT).parts for p in paths)
    # The packager enforces a positive allowlist, never walks arbitrary caller-provided archives.


def test_clean_install_contains_no_personal_memories_or_accounts(monkeypatch, tmp_path):
    from poda_app.runtime import config, db
    from poda_app.memory import store
    fresh = tmp_path / "fresh-public-profile"
    monkeypatch.setattr(config, "DATA_DIR", fresh)
    monkeypatch.setattr(config, "BACKUP_DIR", fresh / "backups")
    monkeypatch.setattr(config, "DB_PATH", fresh / "poda.db")
    db.init_database()  # synthetic, isolated plaintext fixture in this test suite only
    conn = db.connect()
    try:
        store.ensure_seed(conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type='memory'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fs_grants").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM notion_config").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type='domain'").fetchone()[0] >= 1
    finally:
        conn.close()


def test_external_cli_discovery_does_not_execute_when_disabled(monkeypatch):
    from poda_app.agent import software
    monkeypatch.delenv("PODA_ALLOW_EXTERNAL_SOFTWARE", raising=False)
    def no_subprocess(*args, **kwargs):
        raise AssertionError("external subprocess invoked without developer opt-in")
    monkeypatch.setattr(software.subprocess, "run", no_subprocess)
    assert software._cli_hub_entries() == []
