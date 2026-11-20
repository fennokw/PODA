"""Resolve explicitly mentioned connections and installed software harnesses."""
from __future__ import annotations

import re
from dataclasses import dataclass

BUILTINS = {
    "github": ("GitHub", ("github_",)),
    "web": ("Web search and reading", ("web_",)),
    "mail": ("Mail", ("mail_",)),
    "calendar": ("Calendar", ("notion_agenda", "notion_query_items", "notion_calendar_")),
    "messages": ("Messages", ("messages_",)),
}
MENTION = re.compile(r"(?<![\w@/])@(?:\[([\w .-]{1,80})\]|([A-Za-z][\w-]{0,79}))(?![\w@.-]|\.\w)",
                     re.UNICODE)


@dataclass(frozen=True)
class Plugin:
    name: str
    label: str
    tools: frozenset[str]
    software: bool = False

    def guard(self, name, args):
        if self.software and name.startswith("software_") and args.get("name") != self.name:
            return False, "Use the add-on explicitly named in this message."
        return True, ""


def catalog() -> dict[str, Plugin]:
    from ..agent import software, tools

    entries = {key: Plugin(key, label, frozenset(name for name in tools.TOOLS if name.startswith(prefixes)))
               for key, (label, prefixes) in BUILTINS.items()}
    for name in software.enabled_names():
        entries.setdefault(name.casefold(), Plugin(name, name, frozenset({"software_skill", "software_query", "software_cli"}), True))
    return entries


def resolve(message: str) -> tuple[str, Plugin | None]:
    if "@" not in message:
        return message, None
    spans = [(m.start(), m.end()) for m in re.finditer(r"```[\s\S]*?```|`[^`]*`", message)]
    registry = None
    for match in MENTION.finditer(message):
        if any(begin <= match.start() < end for begin, end in spans):
            continue
        if registry is None:
            registry = catalog()
        key = (match[1] or match[2]).casefold().replace(" ", "-")
        if key in registry:
            return (message[:match.start()].rstrip() + " " + message[match.end():].lstrip()).strip(), registry[key]
    return message, None
