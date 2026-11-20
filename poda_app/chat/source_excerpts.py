"""Label exact excerpts supplied to the model and resolve citations to those excerpts."""
from __future__ import annotations

import re

PAGE = re.compile(r"^\[Page (\d+)([^\]]*)\][ \t]*$", re.M)
REFERENCE = re.compile(r"\[\s*(?:source:\s*)?(D\d{1,3})\s*\]", re.I)
PROBLEM = re.compile(r"\b(?:[A-Z]{1,5}-\d+\.\d+|(?:problem|question|exercise)\s+\d+(?:\.\d+)*(?:\([a-z]\))?)\b", re.I)


def label_text(content: str, filename: str, excerpts: list[dict], *, pdf=False) -> str:
    pages = list(PAGE.finditer(content)) if pdf else []
    sections = [
        (int(marker[1]), "ocr" in marker[2].casefold(), marker[0], content[marker.end():end])
        for marker, end in zip(pages, [m.start() for m in pages[1:]] + [len(content)])
    ] if pages else [(None, False, "", content)]
    labeled = []
    for page, ocr, title, text in sections:
        if title:
            labeled.append(title)
        chunk = ""
        for line in text.splitlines(keepends=True):
            if chunk and (len(chunk) + len(line) > 520 or PROBLEM.match(line.strip())):
                labeled.append(_label(chunk, filename, page, ocr, excerpts))
                chunk = ""
            while len(line) > 520:
                labeled.append(_label(line[:520], filename, page, ocr, excerpts))
                line = line[520:]
            chunk += line
        if chunk.strip():
            labeled.append(_label(chunk, filename, page, ocr, excerpts))
    return "\n".join(labeled)


def _label(text, filename, page, ocr, excerpts):
    text = text.strip()
    if not text or len(excerpts) >= 140:
        return text
    marker = f"D{len(excerpts) + 1}"
    excerpts.append({"id": marker, "file": filename, "page": page, "ocr": ocr,
                     "text": text})
    return f"[{marker}]\n{text}"


def label_image(filename, thumbnail, excerpts):
    marker = f"D{len(excerpts) + 1}"
    excerpts.append({"id": marker, "file": filename, "page": None, "image": thumbnail})
    return f"[{marker}] Attached image: {filename}"


def resolve(answer: str, known: list[dict], *, anchors: str = "") -> tuple[str, list[dict]]:
    if not known:
        return answer, []
    available = {item["id"]: item for item in known}
    selected = []
    for match in REFERENCE.finditer(answer):
        key = match[1].upper()
        if key in available and key not in selected:
            selected.append(key)
    if not selected:
        # A weak local model may use the problem label while omitting its source ID.
        # Match only precise identifiers or actual quotations in the supplied text.
        text = answer + "\n" + anchors
        labels = {match[0].casefold() for match in PROBLEM.finditer(text)}
        normalized = " ".join(text.casefold().split())
        for excerpt in known:
            raw = excerpt.get("text", "")
            source_labels = {match[0].casefold() for match in PROBLEM.finditer(raw)}
            quotable = [" ".join(line.casefold().split()) for line in raw.splitlines()]
            if labels.intersection(source_labels) or any(len(line) >= 42 and line in normalized for line in quotable):
                selected.append(excerpt["id"])
    return REFERENCE.sub("", answer).strip(), [available[key] for key in selected[:4]]
