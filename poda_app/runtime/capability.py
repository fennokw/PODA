"""Capability Ledger: live, verified runtime state. Every prompt is built from this, never from assumptions."""
from __future__ import annotations

import json
from typing import Any

from . import config, ollama
from .db import connect, now_iso, rows, one, encryption_status
from ..agent.grants import filesystem_capability
from ..connectors.notion import state as notion_state
from ..connectors.email import state as email_state


def manifest() -> dict[str, Any]:
    ollama_state = ollama.status()
    conn = connect()
    try:
        integrations = {r["integration"]: r for r in rows(conn, "SELECT * FROM integration_state")}
        memory_count = conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE node_type='memory'").fetchone()[0]
        project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        receipts = conn.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0]
        offline = (one(conn, "SELECT value FROM settings WHERE key='offline_mode'") or {}).get("value") in {"1", "true", "on"}
    finally:
        conn.close()
    notion = notion_state.capability()
    fs = filesystem_capability()
    enc = encryption_status()
    email = integrations.get("email", {})
    return {
        "verified_at": now_iso(),
        "authority": "live_runtime_probe",
        "build_id": config.BUILD_ID,
        "version": config.VERSION,
        "local_only": True,
        "offline_mode": offline,
        "network_workers": False,
        "internet_browsing": False,
        "cloud_models": False,
        "ollama": ollama_state,
        "code": {
            "can_generate_code_text": True,
            "can_create_user_code_files": fs["general_user_file_create"],
            "can_execute_user_code": fs["code_execution"],
            "can_run_shell_commands": False,
            "detail": ("Scoped filesystem/code tools are active for granted folders only." if fs["general_user_file_read"] else
                       "PODA can generate code in chat. File and code tools activate only for folders you grant in Files & Agent."),
        },
        "email": email_state.capability(email),
        "notion": notion,
        "calendar": {
            "persistent_connection": notion["connected"],
            "provider": "Notion database/data source" if notion["connected"] else None,
            "connected_account": notion.get("database_title"),
            "can_read_calendar": notion["can_read_content"],
            "can_write_calendar": bool(notion["can_insert_content"] and notion["can_update_content"]),
            "chat_can_execute_writes": bool(notion["can_insert_content"] and notion["can_update_content"] and notion["mapping_active"]),
            "can_extract_event_candidates_from_text": True,
            "detail": (
                (f"Notion status {notion['status']}: read={'verified' if notion['can_read_content'] else 'unverified'}, "
                 f"insert={'verified' if notion['can_insert_content'] else 'unverified'}, update={'verified' if notion['can_update_content'] else 'unverified'}, "
                 f"schema mapping={'active' if notion['mapping_active'] else 'not saved'}. Writes from chat require a saved mapping and explicit confirmation.")
                if notion["connected"] else integrations.get("calendar", {}).get("detail", "No calendar connection.")
            ),
        },
        "filesystem": {**fs, "managed_internal_paths": [str(config.DATA_DIR), str(config.STATIC_DIR)]},
        "memory": {
            "enabled": True,
            "storage": "local SQLite (SQLCipher encrypted)" if enc.get("database_encrypted_on_disk") else "local SQLite (NOT encrypted at rest)",
            "encrypted_at_rest": bool(enc.get("database_encrypted_on_disk")),
            "memory_nodes": memory_count,
            "hierarchical_clouds": True,
            "visualization": "Open3D geometry + local WebGL2 renderer",
            "retrieval": "hybrid FTS5/BM25 + cosine + committed geometry",
            "can_create_memory_nodes": True,
            "can_edit_memory_nodes": True,
        },
        "projects": {"priority_engine": True, "projects_tracked": project_count, "can_create_project_records": True, "can_rank_projects": True},
        "receipts": {"recorded": receipts},
    }


def sync_ledger(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    state = snapshot or manifest()
    verified_at = state.get("verified_at") or now_iso()
    conn = connect()
    try:
        for key, value in state.items():
            if key in {"verified_at", "authority"}:
                continue
            conn.execute("""INSERT INTO capability_ledger (capability_key, state_json, source, last_verified_at) VALUES (?, ?, 'runtime_probe', ?)
                            ON CONFLICT(capability_key) DO UPDATE SET state_json=excluded.state_json, source=excluded.source, last_verified_at=excluded.last_verified_at""",
                         (key, json.dumps(value, default=str), verified_at))
        conn.commit()
    finally:
        conn.close()
    return state


def ledger_context(cap: dict[str, Any]) -> str:
    """Concise system-state memory injected into every normal model request."""
    models = cap.get("ollama", {}).get("models") or []
    fs = cap.get("filesystem", {})
    notion = cap.get("notion", {})
    roots = fs.get("granted_roots") or []
    fs_line = ("Filesystem: granted folders → " + "; ".join(f"{r['path']} [{r['mode']}{', exec' if r.get('allow_execute') else ''}]" for r in roots) +
               ". Anything outside these folders is NOT accessible.") if roots else "Filesystem: NO folder grants. CANNOT read/create/edit any user files or Desktop files."
    code_line = ("Code: CAN generate code; CAN run Python inside granted folders that allow execution." if cap.get("code", {}).get("can_execute_user_code")
                 else "Code: CAN generate code text in chat; CANNOT execute code or run shell commands.")
    notion_line = (f"Notion: {notion.get('status')} to '{notion.get('database_title') or notion.get('database_id')}' (workspace {notion.get('workspace_name') or 'unknown'}); "
                   f"read={'verified' if notion.get('can_read_content') else 'unverified'}, insert={'verified' if notion.get('can_insert_content') else 'unverified'}, "
                   f"update={'verified' if notion.get('can_update_content') else 'unverified'}, mapping={'active' if notion.get('mapping_active') else 'none'}."
                   if notion.get("connected") else "Notion calendar database: NOT CONNECTED (status DISCONNECTED). Calendar text extraction only.")
    em = cap.get("email", {})
    if em.get("can_read"):
        accts = "; ".join(f"{a.get('account_name')} {a.get('address_masked')} [{'read+modify' if a.get('allow_modify') else 'read-only'}{', send' if a.get('allow_send') else ''}]" for a in em.get("accounts", []))
        email_line = (f"Mail: CONNECTED via {', '.join(em.get('providers') or [])} → {accts}. Tools: mail_list, mail_search, mail_read"
                      + (", mail_mark, mail_move, mail_draft" if em.get("can_modify_mailbox") else "") + (", mail_send (confirm)" if em.get("can_send") else "") + ". Sending is " + ("ENABLED (per-message confirm)." if em.get("can_send") else "OFF."))
    else:
        email_line = "Mail: NOT CONNECTED. Connect Apple Mail or Gmail in the Mail screen. Legacy arbitrary-host IMAP preview is retired."
    lines = [
        f"Verified at: {cap.get('verified_at')} (build {cap.get('build_id')})",
        f"Ollama reachable: {bool(cap.get('ollama', {}).get('reachable'))}; installed models: {', '.join(models[:8]) or 'none'}",
        code_line, fs_line,
        email_line,
        notion_line,
        f"Memory: local second brain, {cap.get('memory', {}).get('memory_nodes', 0)} memory nodes; encrypted at rest: {cap.get('memory', {}).get('encrypted_at_rest')}.",
        f"Projects: priority engine active; {cap.get('projects', {}).get('projects_tracked', 0)} tracked.",
        f"Network: offline_mode={cap.get('offline_mode')}; only api.notion.com is allowlisted for outbound requests; no cloud models, no telemetry.",
        "A completed side effect may ONLY be claimed when an action receipt exists for it.",
    ]
    return "\n".join(f"- {line}" for line in lines)


def persisted_ledger() -> dict[str, Any]:
    conn = connect()
    try:
        result = {}
        for row in rows(conn, "SELECT * FROM capability_ledger ORDER BY capability_key"):
            try:
                value = json.loads(row["state_json"])
            except Exception:
                value = row["state_json"]
            result[row["capability_key"]] = {"state": value, "source": row["source"], "last_verified_at": row["last_verified_at"]}
        return result
    finally:
        conn.close()
