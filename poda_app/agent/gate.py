"""Pre-model action gate and intent classification.

Separates informational questions ("What can you do with files?") from side-effect requests
("Create test.py on my Desktop"). Side effects are either routed to real tools (when a grant/
connector permits) or answered with a truthful blocker — never role-played by the model.
"""
from __future__ import annotations

import re
from typing import Any

CAPABILITY_PHRASES = [
    "what can you access", "what do you have access", "do you have access", "are you connected", "what are you connected",
    "calendar access", "file access", "your capabilities", "capability ledger", "status report", "what can you do right now",
    "what integrations", "which integrations", "are you able to access", "can you create a file", "can you make a file",
    "can you write a file", "can you edit files", "can you change files", "can you read files", "can you run code",
    "can you execute code", "can you code into a file", "can you save to my desktop", "can you write to my desktop",
    "can you read my email", "can you read my inbox", "can you access my calendar", "what can you do with files",
    "could you do with files", "what could you do", "what can you do", "which files can you", "what files can you",
]

# A message that names a concrete task object is a request, never a capability question — even if it starts with "Can you".
_TASK_CUES = re.compile(
    r"\b(give me|show me|tell me|summar|overview|list|find|look up|look at|check|read my latest|latest|recent|past (day|week|hour)|last (day|week|night|hour)|"
    r"today|tonight|tomorrow|yesterday|this (week|morning|afternoon|evening)|next (week|month)|on (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"at what time|what time|when is|due|deadline|assignment|homework|class(es)?|lecture|meeting|schedule for|events? (on|for|i have)|"
    r"from [a-z0-9.]+@|@[a-z0-9.-]+\.[a-z]{2,}|gradescope|canvas|notion calendar|my notion|draft|reply to|send|archive|flag|unflag|mark)\b", re.I)
_STRONG_TASK = re.compile(r"\b(give me|show me|tell me|summar\w*|overview|list( me| my| the)?|find|look up|look at|check my|read my|read the|latest|most recent|draft|reply|send|archive|flag|unflag|mark|at what time|what time|when is|when are|what('s| is) on|what do i have)\b", re.I)
_ABILITY_SHAPE = re.compile(
    r"^\s*(what|which|do|does|are|is|can|could|would|how)\b.{0,40}\byou\b.{0,30}\b(access|connected|capab|able to|allowed|permission|can do|able)\b|"
    r"^\s*(can|could|are you able to|do you know how to|would you be able to)\s+you?\s*(read|see|access|open|edit|create|write|run|execute|send|modify|reach)\b[^?]{0,40}\?\s*$", re.I)

_INFORMATIONAL_OPENERS = re.compile(r"^\s*(what|which|could|can|would|are|is|do|does|how)\b", re.I)
_IMPERATIVE_FILE = re.compile(r"\b(create|make|write|save|put|export|generate|add)\b.{0,80}\b(file|folder|\.py|\.txt|\.md|\.json|\.js|\.html|\.csv|desktop|documents)\b", re.I | re.S)
_IMPERATIVE_EDIT = re.compile(r"\b(edit|change|modify|update|replace|rename|move|refactor|fix)\b.{0,80}\b(file|folder|\.py|\.txt|\.md|\.json|\.js|\.html|\.csv|desktop|documents|/users/|~/)", re.I | re.S)
_IMPERATIVE_READ = re.compile(r"\b(read|open|inspect|look at|scan|list|show me|search)\b.{0,80}\b(file|folder|directory|\.py|\.txt|\.md|\.json|desktop|documents|/users/|~/)", re.I | re.S)
_IMPERATIVE_DELETE = re.compile(r"\b(delete|remove|trash|erase)\b.{0,60}\b(file|folder|\.py|\.txt|\.md|\.json|desktop|documents)\b", re.I | re.S)


def is_capability_query(text: str) -> bool:
    """True only for questions ABOUT PODA's abilities. Any concrete task object (a date, an account, 'give me an overview',
    'latest email', 'events tomorrow') makes it a request that must be served, not answered with the ledger."""
    lower = " ".join((text or "").lower().split())
    if not lower:
        return False
    strong_task = _STRONG_TASK.search(lower)
    if any(p in lower for p in CAPABILITY_PHRASES) and not strong_task:
        return True
    if _TASK_CUES.search(lower):
        return False
    return bool(_ABILITY_SHAPE.search(lower)) and len(lower.split()) <= 14


def requested_action_intents(text: str) -> set[str]:
    lower = " ".join((text or "").lower().split())
    intents: set[str] = set()
    informational = bool(_INFORMATIONAL_OPENERS.match(lower)) and "?" in lower and not re.search(r"\b(please|now|go ahead)\b", lower)
    if informational or is_capability_query(lower):
        return intents  # questions about ability are not requests to act
    if _IMPERATIVE_FILE.search(lower):
        intents.add("file_create")
    if _IMPERATIVE_EDIT.search(lower):
        intents.add("file_edit")
    if _IMPERATIVE_READ.search(lower):
        intents.add("file_read")
    if _IMPERATIVE_DELETE.search(lower):
        intents.add("file_delete")
    if any(t in lower for t in ["run this code", "execute this code", "run the script", "execute the script", "run python", "execute python", "run this file", "run the tests", "run pytest"]):
        intents.add("code_execute")
    if any(t in lower for t in ["send an email", "send email", "email them", "reply to", "forward this email"]):
        intents.add("email_send")
    if any(t in lower for t in ["archive the email", "delete the email", "label the email", "mark as read", "move the email"]):
        intents.add("email_modify")
    if any(t in lower for t in ["read my email", "read my inbox", "check my inbox", "search my email", "search my inbox"]):
        intents.add("email_read")
    if (re.search(r"\b(add|put|schedule|create|book)\b.{0,40}\b(calendar|notion|to[- ]?do list|event)\b", lower)
            or any(t in lower for t in ["create a calendar event", "create a notion page", "add a task to notion", "mark it done in notion", "update the notion page"])):
        intents.add("calendar_write")
    if any(t in lower for t in ["read my calendar", "check my calendar", "what's on my calendar", "what is on my calendar", "what's in my notion", "what is in my notion"]):
        intents.add("calendar_read")
    return intents


def extract_path_hint(text: str) -> str | None:
    m = re.search(r"((?:~|/Users)/[^\s'\"`]+)", text)
    if m:
        return m.group(1)
    if re.search(r"\bdesktop\b", text, re.I):
        return "~/Desktop"
    if re.search(r"\bdocuments\b", text, re.I):
        return "~/Documents"
    return None


def extract_filename_hint(text: str) -> str | None:
    quoted = re.search(r"[`'\"“”‘’]([^`'\"“”‘’]{1,120}\.[A-Za-z0-9]{1,8})[`'\"“”‘’]", text)
    if quoted:
        return quoted.group(1).strip()
    m = re.search(r"\b([\w\-. ]{1,80}\.(?:py|txt|md|json|js|ts|html|css|csv|sh|yaml|yml|toml))\b", text)
    return m.group(1).strip() if m else None


def unsupported_action_response(message: str, cap: dict[str, Any]) -> str | None:
    """Return a truthful blocker when a requested side effect has no real tool behind it. None means proceed."""
    intents = requested_action_intents(message)
    if not intents:
        return None
    fs = cap.get("filesystem", {})
    blockers: list[str] = []
    for intent in sorted(intents):
        if intent == "file_create" and not fs.get("general_user_file_create"):
            blockers.append("create or save files (no folder has been granted edit access)")
        elif intent == "file_edit" and not fs.get("general_user_file_edit"):
            blockers.append("edit or move user files (no folder has been granted edit access)")
        elif intent == "file_read" and not fs.get("general_user_file_read"):
            blockers.append("read user files (no folder has been granted)")
        elif intent == "file_delete":
            blockers.append("delete user files (deletion is not an available tool)")
        elif intent == "code_execute" and not (cap.get("code", {}).get("can_execute_user_code") or cap.get("filesystem", {}).get("code_execution")):
            blockers.append("execute code (no granted folder allows execution)")
        elif intent == "email_send" and not cap.get("email", {}).get("can_send"):
            blockers.append("send email (sending is disabled until you enable it per account in the Mail screen)")
        elif intent == "email_modify" and not cap.get("email", {}).get("can_modify_mailbox"):
            blockers.append("modify the mailbox (no mail account with edit permission is connected)")
        elif intent == "email_read" and not (cap.get("email", {}).get("can_read") or cap.get("email", {}).get("can_read_without_credentials_in_request")):
            blockers.append("read your inbox (no mail account is connected in the Mail screen)")
        elif intent == "calendar_write" and not cap.get("calendar", {}).get("chat_can_execute_writes"):
            if cap.get("calendar", {}).get("can_write_calendar"):
                blockers.append("write to Notion from chat yet — write permission is verified but no schema mapping has been saved in the Notion Mapper")
            else:
                blockers.append("create or modify Notion/calendar entries (Notion is not connected with verified write access)")
        elif intent == "calendar_read" and not cap.get("calendar", {}).get("can_read_calendar"):
            blockers.append("read your Notion calendar database (not connected)")
    if not blockers:
        return None
    unique = list(dict.fromkeys(blockers))
    extra = ""
    if any(i.startswith("file_") or i == "code_execute" for i in intents):
        extra = " You can grant a specific folder in Files & Agent; after that I can act there and show you a receipt. I can still draft the contents here."
    return ("I can't do that in the current verified state. The runtime ledger says I cannot " + "; ".join(unique) + "." + extra +
            " Nothing was created, changed, sent, or executed.")


def capability_answer(cap: dict[str, Any]) -> str:
    models = cap["ollama"].get("models") or []
    fs = cap.get("filesystem", {})
    notion = cap.get("notion", {})
    lines = ["Current verified PODA state (refreshed from the runtime, not inferred):", ""]
    lines.append(f"- **Ollama:** {'reachable' if cap['ollama'].get('reachable') else 'not reachable'}. Installed: {', '.join(models[:8]) or 'none detected'}.")
    if fs.get("granted_roots"):
        lines.append("- **Files:** granted folders — " + "; ".join(f"`{r['path']}` ({r['mode']}{', can run Python' if r.get('allow_execute') else ''})" for r in fs["granted_roots"]) + ". Nothing outside these is accessible. Every write produces a receipt.")
    else:
        lines.append("- **Files:** no folder grants are active, so I cannot read, create, or edit any user files or Desktop files. Grant a folder in Files & Agent to enable scoped tools.")
    lines.append("- **Code:** I can write code in chat." + (" I can run Python inside granted folders that allow execution." if cap.get("code", {}).get("can_execute_user_code") else " I cannot execute code or shell commands right now."))
    em = cap.get("email", {})
    if em.get("can_read"):
        accts = ", ".join(f"{a.get('account_name')} ({a.get('address_masked')}; {'read+edit' if a.get('allow_modify') else 'read only'}{', send enabled' if a.get('allow_send') else ''})" for a in em.get("accounts", []))
        lines.append(f"- **Mail:** connected via {', '.join(em.get('providers') or ['Apple Mail'])} — {accts}. I can list, search, and read messages" + ("; mark, flag, archive, move, and draft where edits are allowed" if em.get("can_modify_mailbox") else "") + ("; sending is enabled with per-message confirmation." if em.get("can_send") else "; sending is off."))
    else:
        last_email = em.get("last_successful_preview_account")
        lines.append("- **Mail:** no account is connected yet (connect Apple Mail or Gmail in the Mail screen). The legacy arbitrary-host IMAP preview is retired." + (f" Last request-scoped preview: {last_email}." if last_email else ""))
    if notion.get("connected"):
        lines.append(f"- **Notion:** {notion.get('status')} to '{notion.get('database_title') or notion.get('database_id')}' in workspace {notion.get('workspace_name') or 'unknown'}. "
                     f"Read {'verified' if notion.get('can_read_content') else 'unverified'}; insert {'verified' if notion.get('can_insert_content') else 'unverified'}; update {'verified' if notion.get('can_update_content') else 'unverified'}; "
                     f"schema mapping {'saved' if notion.get('mapping_active') else 'not saved'}. Token lives in macOS Keychain.")
    else:
        lines.append("- **Notion / calendar:** not connected. I can extract event candidates from text but cannot read or write your Notion database.")
    lines.append(f"- **Memory:** local second brain with {cap['memory']['memory_nodes']} memory nodes; encrypted at rest: {'yes' if cap['memory'].get('encrypted_at_rest') else 'no'}. Every chat turn I can recall is a visible node in the Memory Web.")
    lines.append(f"- **Projects:** priority engine active with {cap['projects']['projects_tracked']} tracked projects.")
    lines.append(f"- **Network:** local-only. Offline mode {'on' if cap.get('offline_mode') else 'off'}; the only allowlisted external host is api.notion.com; no cloud models or telemetry.")
    return "\n".join(lines)
