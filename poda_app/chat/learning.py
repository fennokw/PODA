"""Validate the declarative activities a local model can suggest in learning mode."""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from pydantic import BaseModel, Field, ValidationError, model_validator


class Choice(BaseModel):
    label: str = Field(min_length=1, max_length=180)
    feedback: str = Field(min_length=1, max_length=650)


class Quiz(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    choices: list[Choice] = Field(min_length=2, max_length=5)
    correct: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_answer(self):
        labels = {choice.label.strip().casefold() for choice in self.choices}
        if self.correct >= len(self.choices) or len(labels) != len(self.choices):
            raise ValueError("Each answer must be unique and the correct index must exist.")
        return self


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=650)


class Plot(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    x_label: str = Field(min_length=1, max_length=80)
    y_label: str = Field(min_length=1, max_length=80)
    points: list[tuple[float, float]] = Field(min_length=2, max_length=48)

    @model_validator(mode="after")
    def valid_axes(self):
        import math
        if any(not math.isfinite(n) or abs(n) > 1e12 for pair in self.points for n in pair):
            raise ValueError("Use finite plot samples.")
        if any(a[0] >= b[0] for a, b in zip(self.points, self.points[1:])):
            raise ValueError("Use strictly increasing horizontal coordinates.")
        return self


class Graph(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    rate: float = Field(ge=0.15, le=4)


class Checklist(BaseModel):
    items: list[str] = Field(min_length=1, max_length=50)


BLOCK = re.compile(r"^```poda-(quiz|question|graph|plot|checklist)[ \t]*\n(.*?)^\s*```[ \t]*$", re.M | re.S)
MODELS = {"quiz": Quiz, "question": Question, "graph": Graph, "plot": Plot, "checklist": Checklist}
QUESTION_LABEL = re.compile(r"\b(?:question|your turn|quick check|try this)[*: ]*:", re.I)
OPTION = re.compile(r"(?<![^\s*])(?:\*{1,2})?([A-E])[.)](?:\*{1,2})?\s+")
INTRO = re.compile(
    r"^(?:(?:hello[!,. ]*|hi[!,. ]*)?(?:i am|i'm) poda\b[^.!?\n]*[.!?]?[ \t]*|"
    r"(?:hello|perfect|great|excellent|absolutely|sure|good question|excellent choice)[!.][ \t]*)", re.I)


INSTRUCTIONS = r"""
Ask at most one question that expects a learner response. Put the whole question and choices in ONE card, not in the surrounding prose.
For open-ended questions, use this exact fenced block:
```poda-question
{"question":"What happens to the stress at the free end?"}
```
For multiple choice, use this exact fenced block:
```poda-quiz
{"question":"Which condition applies at the free end?","choices":[{"label":"Zero stress","feedback":"There is no applied traction."},{"label":"Zero strain by assumption","feedback":"Start from the traction boundary condition."}],"correct":0}
```
Use distinct choices, a zero-based correct index, no spoilers in the surrounding text, and no instructions to type an answer. The card handles answering.
Use dollar delimiters for math, with properly JSON-escaped backslashes inside a card.
Include a visual when it clarifies the current step. Use Mermaid for a simple relationship or a fenced poda-plot
with JSON {"title":"Stress along the bar","x_label":"Position","y_label":"Stress","points":[[0,1],[0.5,0.5],[1,0]]} for a quantitative plot, when you can justify the samples. The user can inspect points.
For the normalized first-order step response only, use an interactive fenced poda-graph
{"title":"First-order step response","rate":1.0} for 1-exp(-rate*t).
"""

CHECKLIST_INSTRUCTIONS = """
When this is the first reading of an assignment for a linked task, you may suggest its required deliverables
in a fenced poda-checklist JSON block, for example {"items":["1(a) Derive the transfer function","1(b) Plot the response"]}.
Use the exact assignment labels and include required plots and explanations. Leave optional extensions out.
The user reviews and checks off the items. Do not imply they completed any item merely by discussing it.
"""


def _plain(text):
    return " ".join(re.findall(r"[\w.-]+", text.casefold()))


def _fallback(text: str):
    """Find a trailing prose question and move it into the same interactive card."""
    match = next(reversed(list(QUESTION_LABEL.finditer(text))), None)
    if match:
        start = text.rfind("\n", 0, match.start()) + 1
        prefix_on_line = text[start:match.start()].strip().strip("*")
        if prefix_on_line:
            start = match.start()
        tail = text[match.end():].strip().lstrip("*").strip()
    else:
        paragraphs = list(re.finditer(r"(?m)^[^\n].*?\?\s*(?=\n\s*\n|\Z)", text))
        if not paragraphs:
            return text, None
        candidate = paragraphs[-1]
        if text[candidate.end():].strip():
            return text, None
        start, tail = candidate.start(), candidate[0].strip()
    if "?" not in tail:
        return text, None
    question, remainder = tail.split("?", 1)
    question = question.strip(" *") + "?"
    if len(question) < 5 or len(question) > 650:
        return text, None
    before = text[:start].rstrip()
    before = re.sub(r"(?:\n|^)\s*(?:#{1,6}\s*)?\*{0,2}(?:next quiz|quiz time|quick check|practice question)(?:[^\n]*)$", "", before, flags=re.I).rstrip()
    options = list(OPTION.finditer(remainder))
    if 2 <= len(options) <= 5 and [m[1] for m in options] == list("ABCDE"[:len(options)]):
        labels = []
        for option, end in zip(options, [m.start() for m in options[1:]] + [len(remainder)]):
            label = re.split(r"\n\s*\n|\n\s*\((?:please|select|type|choose)", remainder[option.end():end], maxsplit=1, flags=re.I)[0].strip(" \n*")
            labels.append(label[:180])
        if all(labels):
            return before, {"kind": "quiz", "question": question,
                            "choices": [{"label": label} for label in labels], "correct": None}
    return before, {"kind": "question", "question": question}


def extract(content: str) -> tuple[str, list[dict]]:
    activities = []
    for block in BLOCK.finditer(content):
        kind, value = block.groups()
        if len(activities) >= 4 or len(value) > 10000:
            continue
        try:
            parsed = MODELS[kind].model_validate(json.loads(value))
        except (ValueError, ValidationError):
            continue
        if kind in {"quiz", "question"} and any(w["kind"] in {"quiz", "question"} for w in activities):
            continue
        activities.append({"kind": kind, **parsed.model_dump()})
    text = BLOCK.sub("", content).strip()
    text = INTRO.sub("", text).strip()
    text = re.sub(r"^Let's (?:dive into|break down|walk through)[^.!?\n]*[.!?]\s*", "", text, count=1, flags=re.I)
    text = re.sub(r"^(?:#{1,6} [^\n]+|\*\*(?:problem|part|step|solution) [^\n]+\*\*)\n+",
                  "", text, count=1, flags=re.I).strip()
    shorter, detected = _fallback(text)
    current = next((w for w in activities if w["kind"] in {"quiz", "question"}), None)
    if detected and not current:
        text = shorter
        activities.append(detected)
    elif detected and current and (
        detected["kind"] == "quiz" or
        SequenceMatcher(None, _plain(detected["question"]), _plain(current["question"])).ratio() >= .55
    ):
        text = shorter
    return text, activities
