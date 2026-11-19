"""Parse only the explicit command prefix of a user-authored chat message."""
from __future__ import annotations

import re
from dataclasses import dataclass

MODES = {
    "teach me": "teach",
    "learn": "learn",
    "build": "build",
    "research": "research",
    "act": "act",
}
PREFIX = re.compile(r"^/(work|teach[ \t]+me|learn|build|research|act)(?=\s|$)", re.I)
RESERVED = {"work", "teach-me", *MODES.keys()}


@dataclass(frozen=True)
class ChatCommands:
    message: str
    work: bool = False
    mode: str = ""


def parse(message: str) -> ChatCommands:
    rest = message.lstrip()
    work = False
    mode = ""
    while match := PREFIX.match(rest):
        name = " ".join(match[1].lower().split())
        if name == "work":
            work = True
        else:
            mode = MODES[name]
        rest = rest[match.end():].lstrip()
    return ChatCommands(rest if work or mode else message, work, mode)


MODE_INSTRUCTIONS = {
    "learn": "Help the user understand and practice the concept. Ask for a prediction or give an interactive question when helpful. Give concise feedback on the specific reasoning and adapt the next exercise.",
    "teach": "Guide the user through their actual material or attempt one meaningful step at a time. Find the first consequential error and invite a retry. Give a full solution if explicitly requested.",
    "build": "Use the approved workspace tools when the request calls for implementation. Identify entry points, inspect exact source, work from requirements and test real behavior. Provide a reviewable diff and accurately report checks.",
    "research": "Use available search and page-reading tools for requested research. Cite primary evidence for the claims and distinguish uncertainty from verified information.",
    "act": "Use the appropriate approved connector or tool. Track each requested outcome, verify writes where possible, and report the applicable action receipts.",
}
