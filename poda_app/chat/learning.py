"""Validate the declarative activities a local model can suggest in learning mode."""
from __future__ import annotations

import json
import re

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


class Graph(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    rate: float = Field(ge=0.15, le=4)


class Checklist(BaseModel):
    items: list[str] = Field(min_length=1, max_length=50)


BLOCK = re.compile(r"^```poda-(quiz|graph|checklist)\s*\n(.*?)^\s*```[ \t]*$", re.M | re.S)
MODELS = {"quiz": Quiz, "graph": Graph, "checklist": Checklist}

INSTRUCTIONS = """
For useful practice, you may include one ```poda-quiz block containing JSON:
{"question":"…","choices":[{"label":"…","feedback":"…"},{"label":"…","feedback":"…"}],"correct":0}
Use 2–5 distinct answers and a zero-based correct index. Explain each choice's reasoning in its feedback. Keep the answer out of the surrounding prose.
For an interactive normalized first-order step response, you may include one ```poda-graph JSON block:
{"title":"First-order step response","rate":1.0}
The graph plots 1-exp(-rate*t), with pole at -rate. Use this component only for that model. Other diagrams can use Mermaid or static plots.
"""

CHECKLIST_INSTRUCTIONS = """
When this is the first reading of an assignment for a linked task, you may suggest its required deliverables
in a fenced poda-checklist JSON block, for example {"items":["1(a) Derive the transfer function","1(b) Plot the response"]}.
Use the exact assignment labels and include required plots and explanations. Leave optional extensions out.
The user reviews and checks off the items. Do not imply they completed any item merely by discussing it.
"""


def extract(content: str) -> tuple[str, list[dict]]:
    activities = []
    for block in BLOCK.finditer(content):
        kind, value = block.groups()
        if len(activities) >= 3 or len(value) > 10000:
            continue
        try:
            parsed = MODELS[kind].model_validate(json.loads(value))
        except (ValueError, ValidationError):
            continue
        activities.append({"kind": kind, **parsed.model_dump()})
    return BLOCK.sub("", content).strip(), activities
