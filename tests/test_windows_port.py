"""Windows platform adapters and isolated native verification on Windows runners."""
from pathlib import PureWindowsPath
from types import SimpleNamespace
import sys
import uuid

import pytest

from poda_app.agent import windows_sandbox
from poda_app.connectors.email import outlook_worker, service
from poda_app.connectors.email.models import MailError, encode_id
from poda_app.runtime import config, db
from poda_app.security import keychain


def test_docker_command_keeps_code_and_other_folders_out_of_the_command(monkeypatch):
    monkeypatch.setattr(windows_sandbox, "docker_path", lambda: r"C:\Program Files\Docker\docker.exe")
    command, cleanup, payload = windows_sandbox.prepare(
        ["host-python", "-c", "print('hello')"], PureWindowsPath(r"C:\Projects\User project"))
    assert command[-3:] == ["poda-python:3.12", "python", "-"]
    assert "--network=none" in command and "--pull=never" in command and "--read-only" in command
    assert command.count("--mount") == 1
    assert r"type=bind,source=C:\Projects\User project,target=/workspace" in command
    assert "host-python" not in command and payload == b"print('hello')"
    assert cleanup[-3:-1] == ["rm", "-f"]
    assert cleanup[-1] == command[command.index("--name") + 1]


def test_outlook_message_ids_are_scoped_to_the_selected_profile_account():
    account = {"provider": "outlook_classic", "account_name": "Selected profile"}
    good = encode_id("outlook_classic", "Selected profile", "INBOX", "entry")
    assert service._message_for_account(good, account)["native_id"] == "entry"
    other = encode_id("outlook_classic", "Other profile", "INBOX", "entry")
    with pytest.raises(MailError, match="different mail account"):
        service._message_for_account(other, account)


def test_outlook_worker_checks_message_folder_and_native_type():
    folder = SimpleNamespace(EntryID="folder")
    store = SimpleNamespace(StoreID="store")
    item = SimpleNamespace(Class=43, Parent=folder)
    namespace = SimpleNamespace(GetItemFromID=lambda entry, store_id: item)
    assert outlook_worker._find(namespace, store, folder, "entry") is item
    item.Parent = SimpleNamespace(EntryID="another-folder")
    with pytest.raises(MailError, match="selected Outlook folder"):
        outlook_worker._find(namespace, store, folder, "entry")


def test_outlook_flag_does_not_schedule_a_due_date(monkeypatch):
    class Item:
        EntryID = "message"
        UnRead = False
        FlagStatus = 0

        def MarkAsTask(self, interval):
            assert interval == 4
            self.FlagStatus = 2

        def Save(self):
            pass

    item = Item()
    monkeypatch.setattr(outlook_worker, "_accounts", lambda namespace: [(None, None, "profile", "")])
    monkeypatch.setattr(outlook_worker, "_folders", lambda store: {"INBOX": object()})
    monkeypatch.setattr(outlook_worker, "_find", lambda *args: item)
    result = outlook_worker.dispatch(None, "set_flags", {
        "account": "profile", "mailbox": "INBOX", "native_id": "message", "flagged": True,
    })
    assert result == {"read": True, "flagged": True}


@pytest.mark.skipif(sys.platform != "win32", reason="requires the native Windows credential store")
def test_windows_sqlcipher_key_persists_in_credential_manager(tmp_path, monkeypatch):
    service_name = "PODA.Test." + uuid.uuid4().hex
    monkeypatch.delenv("PODA_DB_KEY_HEX", raising=False)
    for name, value in {
        "DATA_DIR": tmp_path, "DB_PATH": tmp_path / "poda.db",
        "BACKUP_DIR": tmp_path / "backups", "LOG_DIR": tmp_path / "logs",
        "DB_ENCRYPTION": "required", "DB_KEYCHAIN_SERVICE": service_name,
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(db, "_KEY_CACHE", {"loaded": False, "key": None, "source": None, "error": None})
    try:
        keychain.keychain_set(service_name, "roundtrip", "synthetic vault text — windows")
        assert keychain.keychain_get(service_name, "roundtrip") == "synthetic vault text — windows"
        assert db.encryption_enabled(), db.key_status()["error"]
        db.init_database()
        key = keychain.keychain_get(service_name, config.DB_KEYCHAIN_ACCOUNT)
        assert key and len(key) == 64
        assert db.encryption_status()["database_encrypted_on_disk"] is True
        assert db.key_status()["source"] == "Windows Credential Manager"
        db._KEY_CACHE["loaded"] = False
        assert db.db_key() == key
        conn = db.connect()
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    finally:
        keychain.keychain_delete(service_name, "roundtrip")
        keychain.keychain_delete(service_name, config.DB_KEYCHAIN_ACCOUNT)


@pytest.mark.skipif(sys.platform != "win32", reason="requires the native Windows hardware APIs")
def test_windows_hardware_reports_physical_ram():
    from poda_app.runtime.windows_hardware import best_gpu, ram_bytes

    assert ram_bytes() > 1024**3
    name, memory = best_gpu()
    assert memory is None or (name and memory >= 0)
