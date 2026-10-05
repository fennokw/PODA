"""Encrypted backup/restore of the PODA database using AES-256-GCM with a Keychain-protected backup key.

Backups are independent of the live SQLCipher key so that a backup can be restored onto a fresh install
whose database key differs (the backup key is exported alongside instructions, never inside the archive).
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..runtime import config
from ..runtime.db import connect, db_key, export_plaintext_copy, sqlcipher_available, _raw_connect, file_is_plaintext
from .keychain import keychain_get, keychain_set

BACKUP_KEYCHAIN_SERVICE = "PODA.Public.BackupKey"
BACKUP_KEYCHAIN_ACCOUNT = "primary"
MAGIC = b"PODABKP1"


def backup_key() -> bytes:
    env = os.getenv("PODA_BACKUP_KEY_HEX")
    if env:
        return bytes.fromhex(env)
    existing = keychain_get(BACKUP_KEYCHAIN_SERVICE, BACKUP_KEYCHAIN_ACCOUNT)
    if existing:
        return bytes.fromhex(existing)
    fresh = os.urandom(32)
    keychain_set(BACKUP_KEYCHAIN_SERVICE, BACKUP_KEYCHAIN_ACCOUNT, fresh.hex())
    return fresh


def create_encrypted_backup() -> dict[str, Any]:
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "poda-plain.db"
        export_plaintext_copy(plain)
        payload = plain.read_bytes()
    key = backup_key()
    nonce = os.urandom(12)
    header = json.dumps({"app": "PODA", "version": config.VERSION, "build": config.BUILD_ID, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "bytes": len(payload)}).encode()
    ciphertext = AESGCM(key).encrypt(nonce, payload, header)
    out = config.BACKUP_DIR / f"poda-backup-{stamp}.podabkp"
    with open(out, "wb") as fh:
        fh.write(MAGIC)
        fh.write(len(header).to_bytes(4, "big"))
        fh.write(header)
        fh.write(nonce)
        fh.write(ciphertext)
    os.chmod(out, 0o600)
    return {"created": True, "path": str(out), "bytes": out.stat().st_size, "plaintext_bytes": len(payload), "key_storage": "macOS Keychain (PODA.Public.BackupKey)"}


def list_backups() -> list[dict[str, Any]]:
    if not config.BACKUP_DIR.exists():
        return []
    out = []
    for p in sorted(config.BACKUP_DIR.glob("*.podabkp"), reverse=True):
        try:
            with open(p, "rb") as fh:
                if fh.read(8) != MAGIC:
                    continue
                hlen = int.from_bytes(fh.read(4), "big")
                header = json.loads(fh.read(hlen))
            out.append({"filename": p.name, "bytes": p.stat().st_size, **header})
        except Exception:
            continue
    return out


def decrypt_backup(path: Path) -> bytes:
    with open(path, "rb") as fh:
        if fh.read(8) != MAGIC:
            raise ValueError("Not a PODA backup file")
        hlen = int.from_bytes(fh.read(4), "big")
        header = fh.read(hlen)
        nonce = fh.read(12)
        ciphertext = fh.read()
    return AESGCM(backup_key()).decrypt(nonce, ciphertext, header)


def restore_encrypted_backup(filename: str) -> dict[str, Any]:
    src = config.BACKUP_DIR / Path(filename).name
    if not src.exists():
        raise FileNotFoundError(f"Backup {filename} not found in {config.BACKUP_DIR}")
    payload = decrypt_backup(src)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "restore-plain.db"
        plain.write_bytes(payload)
        check = sqlite3.connect(str(plain))
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            tables = check.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        finally:
            check.close()
        if integrity != "ok" or tables == 0:
            raise RuntimeError(f"Backup failed verification (integrity={integrity}, tables={tables}); live database untouched")
        # Keep the current live database as a safety copy before replacing it.
        safety = config.BACKUP_DIR / f"poda-before-restore-{stamp}.db"
        if config.DB_PATH.exists():
            os.replace(config.DB_PATH, safety)
            os.chmod(safety, 0o600)
        for suffix in ("-wal", "-shm"):
            Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
        key = db_key()
        if key and sqlcipher_available():
            from sqlcipher3 import dbapi2 as sc
            plain_conn = sc.connect(str(plain))
            try:
                plain_conn.execute("ATTACH DATABASE ? AS encrypted KEY \"x'%s'\"" % key, (str(config.DB_PATH),))
                plain_conn.execute("SELECT sqlcipher_export('encrypted')")
                plain_conn.execute("DETACH DATABASE encrypted")
            finally:
                plain_conn.close()
        else:
            config.DB_PATH.write_bytes(payload)
        os.chmod(config.DB_PATH, 0o600)
    conn = connect()
    try:
        from ..runtime.db import init_schema
        init_schema(conn)
        conn.commit()
        nodes = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
    finally:
        conn.close()
    return {"restored": True, "from": src.name, "previous_database_saved_as": str(safety), "memory_nodes": nodes, "encrypted": not file_is_plaintext(config.DB_PATH)}
