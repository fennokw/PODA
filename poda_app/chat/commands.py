"""Explicit, single-word mode settings at the beginning of a chat message."""
from __future__ import annotations

import re
from dataclasses import dataclass

MODES = ("teach", "build", "research")
PREFIX = re.compile(r"^/(teach|build|research)(off)?(?=\s|$)", re.I)
RETIRED = re.compile(r"^/(work|learn|act)(?=\s|$)", re.I)


@dataclass(frozen=True)
class ChatCommands:
    message: str
    mode: str = ""
    off: bool = False


def parse(message: str) -> ChatCommands:
    text = message.lstrip()
    if match := PREFIX.match(text):
        return ChatCommands(text[match.end():].lstrip(), match[1].lower(), bool(match[2]))
    return ChatCommands(message)


def active_mode(previous: str, command: ChatCommands) -> str:
    previous = "teach" if previous == "learn" else previous
    if previous not in MODES:
        previous = ""
    if not command.mode:
        return previous
    if command.off:
        return "" if previous == command.mode else previous
    return command.mode


MODE_INSTRUCTIONS = {
    "teach": (
        "Start directly with the current concept or feedback. Skip greetings, recaps, plans, repeated titles, "
        "and generic praise. Work through one meaningful step at a time. After a learner answers, give brief, "
        "specific feedback and either advance one step or ask for a retry. If they're stuck, give one useful hint. "
        "Show a full solution when explicitly requested. Use an interactive card for every question that needs a reply."
    ),
    "build": "Use the approved workspace tools when the request calls for implementation. Identify entry points, inspect exact source, work from requirements and test real behavior. Provide a reviewable diff and accurately report checks.",
    "research": "Use available search and page-reading tools for requested research. Cite primary evidence for the claims and distinguish uncertainty from verified information.",
}


def title_from_message(message: str) -> str:
    # Older discussion titles were pulled from the raw command-bearing prompt.
    text = re.sub(r"^\s*(?:/(?:work|teach\s+me|learn|act)\b\s*)+", "", message, flags=re.I)
    return parse(text).message.strip() if text else ""
