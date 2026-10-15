"""Route persistent memory only when it is likely to improve the current task.

Fresh account data, capability questions, and ordinary factual requests should not
automatically pull unrelated second-brain nodes into the answer.
"""
from __future__ import annotations

import re

_HISTORY = re.compile(
    r"\b(?:remember|recall|previous(?:ly)?|earlier|last time|we discussed|we talked|"
    r"you told me|i told you|what did i say|what did we say|my preferences?|"
    r"my goals?|my background|my history|based on (?:what you know|our conversations)|"
    r"our past conversations?|past chats?|from our conversations?|"
    r"continue (?:where we left off|our work)|pick up where we left off)\b", re.I
)
_PERSONAL = re.compile(
    r"\b(?:what do you know about me|what am i working on|what projects am i working on|"
    r"how do i usually|what have i been working on|my long.term plans?)\b", re.I
)
_EXTERNAL = re.compile(
    r"\b(?:emails?|e-mails?|inbox|mailbox|calendar|notion|mail messages?|"
    r"appointments?|meetings? (?:today|tomorrow|this week)|"
    r"files? (?:on|in|at) (?:my|the)|latest news|weather|stock prices?)\b", re.I
)


def should_recall(message: str) -> bool:
    """Conservatively recall only for a history-based or personal-context request.

    An explicit history reference wins even if it also references mail/calendar:
    e.g. "find the email from the professor we discussed yesterday."
    Ordinary references to "last week" describe a date filter, not a memory need.
    """
    text = " ".join((message or "").split())
    if not text:
        return False
    if _HISTORY.search(text) or _PERSONAL.search(text):
        return True
    if _EXTERNAL.search(text):
        return False
    return False


_MAIL = re.compile(r"\b(?:emails?|e-mails?|inbox|mailbox|mail messages?)\b", re.I)
_READ_TASK = re.compile(
    r"\b(?:summari[sz]e|summary|overview|brief|digest|recap|find|search|"
    r"show|list|review|check|look through|look for|what|which|anything|"
    r"important|urgent|recent|past|last|today|yesterday|this week|read|tell me)\b", re.I
)
_MUTATION = re.compile(
    r"\b(?:send|reply|respond|forward|draft|write (?:an? )?email|"
    r"archive|delete|trash|mark (?:as )?(?:read|unread)|move|flag|unflag)\b", re.I
)
_ABILITY = re.compile(
    r"\b(?:can you|are you able|do you have access|what can you|"
    r"capabilit(?:y|ies)|connect to|connected to)\b", re.I
)


def is_mail_read_request(message: str) -> bool:
    """True for an actual read/search request, not setup/ability or mail mutation."""
    text = " ".join((message or "").split())
    return bool(_MAIL.search(text) and _READ_TASK.search(text)
                and not _MUTATION.search(text) and not _ABILITY.search(text))
