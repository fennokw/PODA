"""SQLite access layer with SQLCipher encryption at rest and idempotent migrations.

Design:
- The public build defaults to an isolated macOS Application Support directory.
- SQLCipher and a macOS Keychain-backed key are mandatory in normal operation.
- Never silently migrate an old plaintext database or ship plaintext backups.
- `PODA_DB_ENCRYPTION=off` is only for isolated tests and explicit recovery.
- All schema changes are additive and idempotent; `schema_migrations` records them.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from ..security.keychain import keychain_get, keychain_set

try:  # SQLCipher is optional at import time; status is reported truthfully in the ledger.
    from sqlcipher3 import dbapi2 as _sqlcipher  # type: ignore
except Exception:  # pragma: no cover - depends on the installed environment
    _sqlcipher = None

_KEY_LOCK = threading.Lock()
_KEY_CACHE: dict[str, Any] = {"loaded": False, "key": None, "source": None, "error": None}
_SQLITE_HEADER = b"SQLite format 3\x00"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sqlcipher_available() -> bool:
    return _sqlcipher is not None


def _load_key() -> tuple[str | None, str | None, str | None]:
    """Return (hex_key, source, error). Generates and stores a key in Keychain on first use."""
    if config.DB_ENCRYPTION == "off":
        return None, "disabled", None
    env_key = os.getenv("PODA_DB_KEY_HEX")
    if env_key:
        return env_key.strip().lower(), "environment", None
    existing = keychain_get(config.DB_KEYCHAIN_SERVICE, config.DB_KEYCHAIN_ACCOUNT)
    if existing:
        return existing.strip().lower(), "keychain", None
    fresh = os.urandom(32).hex()
    try:
        keychain_set(config.DB_KEYCHAIN_SERVICE, config.DB_KEYCHAIN_ACCOUNT, fresh)
    except Exception as exc:
        return None, None, f"Keychain unavailable: {exc}"
    confirmed = keychain_get(config.DB_KEYCHAIN_SERVICE, config.DB_KEYCHAIN_ACCOUNT)
    if confirmed != fresh:
        return None, None, "Keychain write could not be read back"
    return fresh, "keychain", None


def db_key() -> str | None:
    with _KEY_LOCK:
        if not _KEY_CACHE["loaded"]:
            key, source, error = _load_key()
            _KEY_CACHE.update({"loaded": True, "key": key, "source": source, "error": error})
        return _KEY_CACHE["key"]


def key_status() -> dict[str, Any]:
    db_key()
    return {"source": _KEY_CACHE["source"], "error": _KEY_CACHE["error"], "present": bool(_KEY_CACHE["key"])}


def file_is_plaintext(path: Path) -> bool | None:
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == _SQLITE_HEADER
    except FileNotFoundError:
        return None


def encryption_enabled() -> bool:
    return bool(db_key()) and sqlcipher_available()


def _raw_connect(path: Path, key: str | None):
    if key and _sqlcipher is not None:
        conn = _sqlcipher.connect(str(path), timeout=30, check_same_thread=False)
        conn.execute(f"PRAGMA key=\"x'{key}'\"")
        conn.execute("PRAGMA cipher_memory_security = ON")
        conn.row_factory = _sqlcipher.Row
    else:
        conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect():
    """Open the application database. Callers must close the connection."""
    config.ensure_dirs()
    if config.DB_ENCRYPTION == "required" and not encryption_enabled():
        raise RuntimeError("Encrypted storage is required. Install sqlcipher3 and make macOS Keychain available, "
                           "or explicitly set PODA_DB_ENCRYPTION=off ONLY in an isolated development/test environment.")
    conn = _raw_connect(config.DB_PATH, db_key() if sqlcipher_available() else None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except Exception:
        pass
    _harden_file_permissions()
    return conn


def _harden_file_permissions() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(config.DB_PATH) + suffix)
        if p.exists():
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass


def rows(conn, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def table_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def add_column(conn, table: str, column: str, decl: str) -> bool:
    if column in table_columns(conn, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


# --------------------------------------------------------------------------------------
# Encryption migration
# --------------------------------------------------------------------------------------

def migrate_plaintext_to_encrypted() -> dict[str, Any]:
    """Convert a legacy plaintext database into an encrypted one, keeping a 0600 backup."""
    path = config.DB_PATH
    state = file_is_plaintext(path)
    if state is None:
        return {"migrated": False, "reason": "no database yet"}
    if state is False:
        return {"migrated": False, "reason": "already encrypted"}
    if not encryption_enabled():
        return {"migrated": False, "reason": "encryption unavailable", "key": key_status(), "sqlcipher": sqlcipher_available()}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = config.BACKUP_DIR / f"poda-plaintext-before-encryption-{stamp}.db"
    # Checkpoint and copy using the sqlite backup API so WAL contents are included.
    src = sqlite3.connect(str(path))
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst = sqlite3.connect(str(backup))
        src.backup(dst)
        dst.close()
    finally:
        src.close()
    os.chmod(backup, 0o600)
    encrypted_tmp = path.with_name("poda.encrypting.tmp.db")
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(encrypted_tmp) + suffix)
        if p.exists():
            p.unlink()
    key = db_key()
    plain = _sqlcipher.connect(str(backup))
    try:
        plain.execute(f"ATTACH DATABASE ? AS encrypted KEY \"x'{key}'\"", (str(encrypted_tmp),))
        plain.execute("SELECT sqlcipher_export('encrypted')")
        plain.execute("DETACH DATABASE encrypted")
    finally:
        plain.close()
    # Verify before swapping.
    check = _raw_connect(encrypted_tmp, key)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        enc_tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        check.close()
    plain_conn = sqlite3.connect(str(backup))
    try:
        plain_tables = {r[0] for r in plain_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        plain_conn.close()
    if integrity != "ok" or not plain_tables.issubset(enc_tables):
        encrypted_tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Encrypted copy failed verification (integrity={integrity}); plaintext database left untouched")
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    os.replace(encrypted_tmp, path)
    os.chmod(path, 0o600)
    return {"migrated": True, "backup": str(backup), "integrity": integrity, "tables": sorted(enc_tables)}


def export_plaintext_copy(destination: Path) -> dict[str, Any]:
    """Recovery helper: write a plaintext copy of the encrypted database to `destination`."""
    key = db_key()
    if not key or not sqlcipher_available():
        shutil.copy2(config.DB_PATH, destination)
        os.chmod(destination, 0o600)
        return {"exported": True, "mode": "copy-plaintext"}
    destination.unlink(missing_ok=True)
    conn = _raw_connect(config.DB_PATH, key)
    try:
        conn.execute("ATTACH DATABASE ? AS plain KEY ''", (str(destination),))
        conn.execute("SELECT sqlcipher_export('plain')")
        conn.execute("DETACH DATABASE plain")
    finally:
        conn.close()
    os.chmod(destination, 0o600)
    return {"exported": True, "mode": "sqlcipher_export"}


def encryption_status() -> dict[str, Any]:
    plaintext = file_is_plaintext(config.DB_PATH)
    ks = key_status()
    return {
        "sqlcipher_module": sqlcipher_available(),
        "key_source": ks["source"],
        "key_error": ks["error"],
        "database_exists": plaintext is not None,
        "database_encrypted_on_disk": (plaintext is False) if plaintext is not None else None,
        "policy": config.DB_ENCRYPTION,
        "file_mode": oct(config.DB_PATH.stat().st_mode & 0o777) if config.DB_PATH.exists() else None,
    }


# --------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS memory_nodes (
        id TEXT PRIMARY KEY, parent_id TEXT, title TEXT NOT NULL, node_type TEXT DEFAULT 'memory',
        summary TEXT, content TEXT, reference_triggers TEXT, tags_json TEXT,
        x REAL, y REAL, z REAL, pinned INTEGER DEFAULT 0, importance REAL DEFAULT 0.5,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS memory_links (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT DEFAULT 'related_to',
        strength REAL DEFAULT 0.5, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(source_id, target_id, relation))""",
    """CREATE TABLE IF NOT EXISTS memory_embeddings (
        node_id TEXT PRIMARY KEY, model TEXT NOT NULL, text_hash TEXT NOT NULL, vector_json TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS memory_embedding_jobs (
        node_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, importance INTEGER DEFAULT 5, difficulty INTEGER DEFAULT 5,
        hours_remaining REAL DEFAULT 5, deadline TEXT, progress INTEGER DEFAULT 0, notes TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS integration_state (
        integration TEXT PRIMARY KEY, active INTEGER DEFAULT 0, mode TEXT, detail TEXT, account_identifier TEXT,
        last_verified_at TEXT, last_success_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS capability_ledger (
        capability_key TEXT PRIMARY KEY, state_json TEXT NOT NULL, source TEXT DEFAULT 'runtime_probe', last_verified_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS notion_config (
        id INTEGER PRIMARY KEY CHECK (id = 1), database_id TEXT, database_title TEXT, data_source_id TEXT, data_source_name TEXT,
        schema_json TEXT, notion_version TEXT DEFAULT '2026-03-11', read_verified INTEGER DEFAULT 0, insert_verified INTEGER DEFAULT 0,
        update_verified INTEGER DEFAULT 0, last_verified_at TEXT, last_success_at TEXT)""",
    # ---- v0.4.0 additions ----
    """CREATE TABLE IF NOT EXISTS memory_transactions (
        id TEXT PRIMARY KEY, created_at TEXT NOT NULL, summary TEXT, node_count INTEGER DEFAULT 0, deleted_count INTEGER DEFAULT 0,
        text_changes INTEGER DEFAULT 0, position_changes INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS memory_versions (
        id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL, node_id TEXT NOT NULL, change TEXT NOT NULL,
        before_json TEXT, after_json TEXT, created_at TEXT NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS idx_memory_versions_node ON memory_versions(node_id)""",
    """CREATE TABLE IF NOT EXISTS session_summaries (
        id TEXT PRIMARY KEY, session_id TEXT, summary TEXT NOT NULL, source_message_ids_json TEXT NOT NULL,
        confidence REAL DEFAULT 0.6, model TEXT, node_id TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS fs_grants (
        id TEXT PRIMARY KEY, root_path TEXT NOT NULL, mode TEXT NOT NULL, label TEXT, created_at TEXT NOT NULL,
        expires_at TEXT, revoked_at TEXT, allow_execute INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS action_receipts (
        id TEXT PRIMARY KEY, tool TEXT NOT NULL, target TEXT, args_json TEXT, outcome TEXT NOT NULL,
        verification TEXT, detail TEXT, error TEXT, approved_by_user INTEGER DEFAULT 0, session_id TEXT,
        started_at TEXT NOT NULL, finished_at TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_action_receipts_started ON action_receipts(started_at)""",
    """CREATE TABLE IF NOT EXISTS egress_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, service TEXT NOT NULL, method TEXT NOT NULL,
        host TEXT NOT NULL, path TEXT NOT NULL, status INTEGER, bytes_out INTEGER DEFAULT 0, bytes_in INTEGER DEFAULT 0,
        purpose TEXT, duration_ms INTEGER, blocked INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS model_benchmarks (
        id TEXT PRIMARY KEY, model TEXT NOT NULL, num_ctx INTEGER NOT NULL, load_s REAL, first_token_s REAL, total_s REAL,
        gen_tok_s REAL, prompt_tok_s REAL, eval_tokens INTEGER, memory_bytes INTEGER, hardware TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS notion_mappings (
        id TEXT PRIMARY KEY, version INTEGER NOT NULL, data_source_id TEXT NOT NULL, mapping_json TEXT NOT NULL,
        schema_fingerprint TEXT, created_at TEXT NOT NULL, active INTEGER DEFAULT 1)""",
    """CREATE TABLE IF NOT EXISTS notion_test_pages (
        page_id TEXT PRIMARY KEY, data_source_id TEXT, title TEXT, created_at TEXT NOT NULL, archived INTEGER DEFAULT 0, note TEXT)""",
    """CREATE TABLE IF NOT EXISTS agent_proposals (
        id TEXT PRIMARY KEY, session_id TEXT, created_at TEXT NOT NULL, status TEXT NOT NULL, plan_json TEXT NOT NULL,
        receipt_ids_json TEXT, decided_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS llm_imports (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, source_filename TEXT, fingerprint TEXT NOT NULL UNIQUE,
        conversation_count INTEGER DEFAULT 0, message_count INTEGER DEFAULT 0, memory_count INTEGER DEFAULT 0, cloud_count INTEGER DEFAULT 0,
        profile_json TEXT, parser_meta_json TEXT, created_at TEXT NOT NULL)""",
]

COLUMN_MIGRATIONS = [
    ("memory_nodes", "z", "REAL"),
    ("memory_nodes", "semantic_x", "REAL"),
    ("memory_nodes", "semantic_y", "REAL"),
    ("memory_nodes", "semantic_z", "REAL"),
    ("memory_nodes", "position_source", "TEXT DEFAULT 'semantic'"),
    ("memory_nodes", "reference_triggers", "TEXT DEFAULT ''"),
    ("memory_nodes", "memory_kind", "TEXT DEFAULT 'durable'"),
    ("memory_nodes", "source_message_id", "TEXT"),
    ("memory_nodes", "speaker", "TEXT"),
    ("memory_nodes", "confidence", "REAL DEFAULT 0.7"),
    ("memory_nodes", "superseded_by", "TEXT"),
    ("memory_nodes", "valid_until", "TEXT"),
    ("memory_nodes", "source_ref", "TEXT"),
    # v0.5.0 pair nodes + surface/in-depth tiers
    ("memory_nodes", "response_message_id", "TEXT"),
    ("memory_nodes", "user_text", "TEXT"),
    ("memory_nodes", "assistant_text", "TEXT"),
    ("memory_nodes", "surface_id", "TEXT"),
    ("memory_nodes", "member_count", "INTEGER DEFAULT 0"),
    ("memory_nodes", "stale", "INTEGER DEFAULT 0"),
    ("memory_nodes", "user_edited", "INTEGER DEFAULT 0"),
    ("messages", "session_id", "TEXT"),
    ("messages", "provenance", "TEXT DEFAULT 'chat'"),
    ("messages", "metadata_json", "TEXT"),
    ("notion_config", "read_verified", "INTEGER DEFAULT 0"),
    ("notion_config", "insert_verified", "INTEGER DEFAULT 0"),
    ("notion_config", "update_verified", "INTEGER DEFAULT 0"),
    ("notion_config", "workspace_id", "TEXT"),
    ("notion_config", "workspace_name", "TEXT"),
    ("notion_config", "bot_id", "TEXT"),
    ("notion_config", "schema_fingerprint", "TEXT"),
    ("notion_config", "status", "TEXT DEFAULT 'DISCONNECTED'"),
    ("notion_config", "status_detail", "TEXT"),
    ("notion_config", "related_json", "TEXT"),
    ("integration_state", "account_identifier", "TEXT"),
    ("integration_state", "last_success_at", "TEXT"),
]

FTS_STATEMENTS = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        node_id UNINDEXED, title, summary, content, reference_triggers, tags, tokenize='porter unicode61')""",
    """CREATE TRIGGER IF NOT EXISTS memory_fts_ai AFTER INSERT ON memory_nodes BEGIN
        INSERT INTO memory_fts(node_id, title, summary, content, reference_triggers, tags)
        VALUES (new.id, new.title, COALESCE(new.summary,''), COALESCE(new.content,''), COALESCE(new.reference_triggers,''), COALESCE(new.tags_json,''));
    END""",
    """CREATE TRIGGER IF NOT EXISTS memory_fts_ad AFTER DELETE ON memory_nodes BEGIN
        DELETE FROM memory_fts WHERE node_id = old.id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS memory_fts_au AFTER UPDATE OF title, summary, content, reference_triggers, tags_json ON memory_nodes BEGIN
        DELETE FROM memory_fts WHERE node_id = old.id;
        INSERT INTO memory_fts(node_id, title, summary, content, reference_triggers, tags)
        VALUES (new.id, new.title, COALESCE(new.summary,''), COALESCE(new.content,''), COALESCE(new.reference_triggers,''), COALESCE(new.tags_json,''));
    END""",
]

DEFAULT_SETTINGS = {
    "personality": "PODA is a precise, skeptical, private, single-device personal organization assistant. It is not a yes-man. It should optimize for truth, calibration, and useful problem solving rather than affirmation. If an assumption is uncertain, say so plainly. If a plan has a flaw, point it out directly.",
    "memory_policy": "Every chat item that PODA may later reference must have a visible memory node. Durable user facts are organized into semantic topic clouds; ordinary user/assistant discussion is mirrored into Conversation Memory as episodic context. Runtime capability state remains separate because it is live system state, not personal memory. Keep personal data local and distinguish user facts from prior assistant text.",
    "system_rules": "PODA runs on one Mac only. The live Capability Ledger is authoritative. Never claim a tool, file, account, email inbox, calendar, internet source, execution environment, or completed action unless the current verified ledger explicitly confirms it. A capability is not the same as an action result. Never claim to create, save, edit, execute, send, schedule, read, or modify anything unless a real PODA tool completed that action and returned success. If a requested action is unsupported, state the blocker directly instead of role-playing completion. Use fast model for simple prompts and advanced/deep models for complex reasoning.",
    "offline_mode": "0",
    "embedding_space_version": config.EMBED_SPACE_VERSION,
    "retrieval_text_weight": "0.72",
    "retrieval_geometry_weight": "0.28",
    "notion_calendar_profile": "",
    # Surface/in-depth tiers. Default join threshold is a starting heuristic; users can calibrate
    # it against their own embedding corpus and inspect resulting surface groups.
    "surface_join_threshold": "0.84",
    "retrieval_unified_self_weight": "0.6",
    "retrieval_unified_surface_weight": "0.4",
    "surface_show_default": "1",
}


def init_schema(conn) -> list[str]:
    applied: list[str] = []
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    for table, column, decl in COLUMN_MIGRATIONS:
        if add_column(conn, table, column, decl):
            applied.append(f"{table}.{column}")
    conn.execute("UPDATE memory_nodes SET position_source='semantic' WHERE position_source IS NULL OR position_source=''")
    conn.execute("UPDATE memory_nodes SET reference_triggers='' WHERE reference_triggers IS NULL")
    conn.execute("UPDATE memory_nodes SET memory_kind='durable' WHERE memory_kind IS NULL OR memory_kind=''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_nodes_source_message ON memory_nodes(source_message_id) WHERE source_message_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_nodes_parent ON memory_nodes(parent_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_source ON memory_links(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)")
    for stmt in FTS_STATEMENTS:
        conn.execute(stmt)
    fts_count = conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
    node_count = conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
    if fts_count != node_count:
        conn.execute("DELETE FROM memory_fts")
        conn.execute(
            """INSERT INTO memory_fts(node_id, title, summary, content, reference_triggers, tags)
               SELECT id, title, COALESCE(summary,''), COALESCE(content,''), COALESCE(reference_triggers,''), COALESCE(tags_json,'') FROM memory_nodes"""
        )
        applied.append("memory_fts.rebuild")
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, now_iso()))
    for integration, mode, detail in [
        ("email", "connectors_only", "No persistent email account is connected. Configure Apple Mail or Gmail in Mail."),
        ("calendar", "extract_only", "No calendar account is connected. PODA can extract event candidates from text but cannot read or write a calendar yet."),
        ("notion", "not_connected", "No Notion database is connected."),
    ]:
        conn.execute("INSERT OR IGNORE INTO integration_state (integration, active, mode, detail, last_verified_at) VALUES (?, 0, ?, ?, ?)", (integration, mode, detail, now_iso()))
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)", (config.VERSION, now_iso()))
    return applied


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str, conn=None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value, now_iso()))
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def init_database() -> dict[str, Any]:
    """Startup entrypoint: migrate encryption if possible, then apply schema migrations."""
    config.ensure_dirs()
    report: dict[str, Any] = {"encryption": None, "schema": []}
    if config.DB_PATH.exists() and file_is_plaintext(config.DB_PATH) and config.DB_ENCRYPTION == "required":
        raise RuntimeError("Refusing to open a plaintext legacy database. The public release defaults to an "
                           "independent encrypted profile; export and import private data only through an explicit "
                           "encrypted-backup migration, never by copying an old data/ folder.")
    if config.DB_PATH.exists() and file_is_plaintext(config.DB_PATH) and encryption_enabled() and config.DB_ENCRYPTION != "required":
        report["encryption"] = migrate_plaintext_to_encrypted()
    conn = connect()
    try:
        report["schema"] = init_schema(conn)
        conn.commit()
    finally:
        conn.close()
    report["encryption_status"] = encryption_status()
    return report


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
