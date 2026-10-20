"""Receipt-grounded mail task fulfillment.

Mail queries are executed as read-only tools *before* answering. This avoids a
model responding with 'I'll search' and ending its turn without delivering
results. Provider failures or incomplete scans are surfaced, never hidden.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from ..runtime import config, ollama

from ..agent import tools
from ..runtime.db import connect, rows

MAIL_TOPIC = re.compile(r"\b(email|emails|e-mail|inbox|mailbox|messages|meeting requests?|schedule(?:d)? tasks?|follow[- ]?ups?|what should i (?:check|reply))\b", re.I)
MAIL_ACTION = re.compile(r"\b(send|draft|reply to|forward|delete|archive|move|mark (?:as )?read|flag)\b", re.I)
MAIL_SEARCH = re.compile(r"\b(search|find|look through|check|review|summari[sz]e|overview|anything|which|what|did you find|show|list)\b", re.I)
FOLLOWUP = re.compile(r"\b(did you find|what did you find|any emails|anything i should|what about those|any results|did it find)\b", re.I)

TOPIC_CUES = {
    "meeting": ("meeting", "calendar invitation", "invite", "availability", "scheduling", "zoom"),
    "deadline": ("deadline", "due date", "assignment", "submit", "action required", "task"),
    "important": ("urgent", "important", "action needed", "respond", "follow up", "attention"),
    "financial": ("invoice", "payment", "receipt", "billing", "reimbursement", "expense"),
    "academic": ("course", "homework", "exam", "professor", "office hours", "assignment"),
    "project": ("project", "milestone", "deliverable", "update", "approval", "feedback"),
}
_ACTION_WORDS = ("action required", "urgent", "deadline", "due ", "please reply", "please respond",
                 "rsvp", "confirmation needed", "submit", "approval requested", "follow up",
                 "needs your attention", "time sensitive")
_MEETING_WORDS = ("meeting", "invite", "zoom", "call", "availability", "schedule", "reschedule",
                  "calendly", "calendar", "appointment", "rsvp", "interview")
_TASK_WORDS = ("assignment", "homework", "deadline", "submit", "due ", "request", "review",
               "todo", "to-do", "action item", "form")



_IGNORE = {"find", "search", "show", "list", "summarize", "summarise", "review", "email",
           "emails", "message", "messages", "from", "with", "about", "what", "which",
           "recent", "past", "last", "week", "month", "please", "could", "would",
           "that", "have", "been", "some", "anything", "everything", "should", "look",
           "through", "give", "overview", "this", "those", "them", "related", "regarding",
           "my", "your", "their", "for", "are", "was", "and", "the", "to", "any"}

def _keywords(message: str) -> list[str]:
    """Convert user request to individual topic tokens, not the whole instruction."""
    found = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", message.lower())
    return list(dict.fromkeys(word for word in found if word not in _IGNORE))[:5]


def _local_semantic_terms(message: str) -> list[str]:
    """Optional local-only expansion with strict output bounds."""
    try:
        prompt = ("Suggest 5 related concepts/synonyms that could appear in emails about "
                  "this search. Return ONLY a JSON array of short strings (1-3 words each), "
                  "without instructions, URLs or commentary:\n" + message[:700])
        answer = ollama.chat([{"role":"system","content":"Return a JSON array only."},
                              {"role":"user","content":prompt}],
                             config.FAST_MODEL,
                             options={"temperature":0.0,"num_predict":130,"num_ctx":2048})
        raw = (answer.get("message") or {}).get("content") or ""
        match = re.search(r"\[[\s\S]*?\]", raw)
        terms = json.loads(match.group(0)) if match else []
        if not isinstance(terms, list):
            return []
        return [term.strip() for term in terms[:7]
                if isinstance(term, str) and 2 <= len(term.strip()) <= 40
                and not re.search(r"[/<>]", term)]
    except Exception:
        return []  # Ollama downtime must not break mail searching.


def _previous_search(session_id: str | None) -> dict[str, Any]:
    if not session_id:
        return {}
    db = connect()
    try:
        rec = rows(db, """SELECT args_json FROM action_receipts
                          WHERE session_id=? AND tool='mail_search' AND outcome='succeeded'
                          ORDER BY started_at DESC LIMIT 1""", (session_id,))
    finally:
        db.close()
    if not rec:
        return {}
    try:
        return json.loads(rec[0].get("args_json") or "{}")
    except (ValueError, TypeError):
        return {}


def is_mail_request(message: str, session_id: str | None = None) -> bool:
    if MAIL_ACTION.search(message):
        return False  # existing proposal/approval workflow owns write requests
    if MAIL_TOPIC.search(message) and MAIL_SEARCH.search(message):
        return True
    return bool(FOLLOWUP.search(message) and _previous_search(session_id))


def _date_span(message: str, now: datetime) -> tuple[str | None, str | None]:
    q = message.lower()
    # Explicit ISO dates take precedence. Inclusive endpoints are provider agnostic.
    explicit = re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", q)
    if len(explicit) >= 2:
        return explicit[0], explicit[1]
    today = now.date()
    if "last week" in q and "past week" not in q:
        monday = today - timedelta(days=today.weekday() + 7)
        return monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    if any(k in q for k in ("past week", "this week", "last 7 days", "past 7 days")):
        return (today - timedelta(days=7)).isoformat(), today.isoformat()
    if any(k in q for k in ("past month", "last month", "last 30 days")):
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    if any(k in q for k in ("yesterday",)):
        v = today - timedelta(days=1)
        return v.isoformat(), v.isoformat()
    if "today" in q:
        return today.isoformat(), today.isoformat()
    return None, None


def parameters(message: str, session_id: str | None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    begin, end = _date_span(message, now)
    previous = _previous_search(session_id) if FOLLOWUP.search(message) else {}
    if previous and not begin and not end:
        begin, end = previous.get("start_date"), previous.get("end_date")
    broad = bool(re.search(r"\b(all|anything|everything|past week|last week|this week|overview|brief|recent|important|should (?:check|look))\b", message, re.I))
    topics = []
    for cue, terms in TOPIC_CUES.items():
        if cue in message.lower() or (cue == "meeting" and "scheduled" in message.lower()) or (cue == "deadline" and "tasks" in message.lower()):
            topics.extend(terms)
    # Broad reviews must NOT be reduced to a handful of literal terms.
    tokens = _keywords(message)
    query = "" if broad else ((previous.get("query") or "") if previous and FOLLOWUP.search(message) else (tokens[0] if tokens else ""))
    related = list(dict.fromkeys([*topics, *tokens]))[:7]
    if previous and not related:
        related = (previous.get("related_terms") or [])[:7]
    return {"query": query, "start_date": begin, "end_date": end,
            "related_terms": related, "limit": 80, "include_body": True}


def _evaluate(message: dict[str, Any]) -> tuple[int, list[str]]:
    content = " ".join(str(message.get(k) or "") for k in ("subject", "snippet")).lower()
    score = 0
    labels = []
    if any(w in content for w in _ACTION_WORDS):
        score += 5
        labels.append("Action requested")
    if any(w in content for w in _MEETING_WORDS):
        score += 4
        labels.append("Meeting / scheduling")
    if any(w in content for w in _TASK_WORDS):
        score += 4
        labels.append("Task / deadline")
    if message.get("flagged"):
        score += 3
        labels.append("Flagged")
    if not message.get("read", True):
        score += 1
    if "unsubscribe" in content or "newsletter" in content or "promotion" in content:
        score -= 3
    return score, labels


def report(params: dict[str, Any], outcome: dict[str, Any]) -> str:
    receipt = outcome.get("receipt") or {}
    result = outcome.get("result") or {}
    if receipt.get("outcome") != "succeeded" or not result.get("ok"):
        error = result.get("error") or receipt.get("error") or "Mail provider did not complete the search."
        return f"The email search did not complete: {error}. I cannot report findings until the search succeeds."
    data = result.get("result") or {}
    messages = data.get("messages") or []
    scored = [(score, labels, item) for item in messages for score, labels in [_evaluate(item)]]
    scored.sort(key=lambda v: (v[0], v[2].get("date") or ""), reverse=True)
    important = [(score, labels, row) for score, labels, row in scored if score >= 3]
    period = (f"{params['start_date']} through {params['end_date']}" if params.get("start_date") and params.get("end_date") else "the searched mailboxes")
    lines = [f"**Email review: {period}**"]
    lines.append(f"Search completed. I examined {data.get('scanned_headers', 0)} message headers and "
                 f"{data.get('read_bodies', 0)} message bodies; {len(messages)} candidate messages were returned.")
    if important:
        lines.append(f"**{len(important)} messages warrant a closer look:**")
        for _, labels, row in important[:12]:
            subject = (row.get("subject") or "(no subject)").replace("\n", " ")[:120]
            sender = (row.get("from_name") or row.get("from_address_masked") or "Unknown sender")[:90]
            dated = (row.get("date") or "")[:10]
            brief = (row.get("snippet") or "").replace("\n", " ").strip()[:200]
            lines.append(f"- **{subject}** — {sender} ({dated}). {', '.join(labels)}." +
                         (f" {brief}" if brief else ""))
    elif messages:
        lines.append("I did not find clear meeting, deadline, or action-required signals among the returned candidates. "
                     "Some messages may still matter personally; check the Mail screen for the complete list.")
    else:
        lines.append("**No messages were returned within these search constraints.** "
                     "That does not establish that the inbox is empty.")
    errors = data.get("errors") or []
    if data.get("truncated") or errors:
        lines.append("**Coverage limitation:** " +
                     ("The search reached a scanning/result limit. " if data.get("truncated") else "") +
                     (f"{len(errors)} mailbox/provider errors occurred. " if errors else "") +
                     "These results are not a complete mailbox audit.")
    if not messages:
        lines.append("Try widening the dates or checking account/mailbox synchronization; "
                     "I won't claim there are no important emails without a complete search.")
    return "\n\n".join(lines)


def fulfill(message: str, session_id: str | None) -> tuple[dict[str, Any], dict[str, Any], str]:
    params = parameters(message, session_id)
    if params.get("query") and not FOLLOWUP.search(message):
        # Only local Ollama sees the user query; no third-party inference.
        params["related_terms"] = list(dict.fromkeys([
            *(params.get("related_terms") or []), *_local_semantic_terms(message)]))[:7]
    outcome = tools.execute("mail_search", params, approved=False, session_id=session_id)
    return params, outcome, report(params, outcome)
