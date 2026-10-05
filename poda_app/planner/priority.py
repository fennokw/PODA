"""Explainable project prioritization. Scores are recommendations with rationale, never objective truth."""
from __future__ import annotations

from datetime import date
from typing import Any

from dateutil import parser as date_parser


def score_project(project: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    imp = float(project.get("importance") or 5)
    diff = float(project.get("difficulty") or 5)
    hrs = max(0.25, float(project.get("hours_remaining") or 5))
    progress = float(project.get("progress") or 0) / 100
    urgency = 3.0
    days_left = None
    deadline = project.get("deadline")
    if deadline:
        try:
            d = date_parser.parse(str(deadline)).date()
            days_left = (d - today).days
            urgency = 10 / (1 + max(0, days_left) / 3)
            if days_left < 0:
                urgency = 11.0
        except Exception:
            urgency = 3.0
    completion_advantage = 10 / (1 + hrs / 4)
    momentum = 1 + progress * 1.7
    score = round(imp * 2.2 + urgency * 2.0 + completion_advantage * 1.4 + momentum - diff * 0.6, 2)
    reasons = []
    if days_left is not None:
        if days_left < 0:
            reasons.append(f"deadline passed {abs(days_left)} day(s) ago")
        elif days_left <= 3:
            reasons.append(f"hard deadline in {days_left} day(s)")
        else:
            reasons.append(f"deadline in {days_left} days")
    else:
        reasons.append("no deadline recorded (urgency assumed moderate)")
    reasons.append(f"importance {int(imp)}/10")
    reasons.append(f"~{hrs:g}h remaining" + (" (quick win)" if hrs <= 2 else ""))
    if progress >= 0.5:
        reasons.append(f"{int(progress*100)}% done — momentum")
    if diff >= 8:
        reasons.append("high difficulty; schedule a focused block")
    confidence = 0.55 + (0.2 if deadline else 0) + (0.1 if project.get("hours_remaining") else 0) + (0.1 if project.get("notes") else 0)
    return {**project, "score": score, "days_left": days_left, "estimated_duration_hours": hrs,
            "rationale": "; ".join(reasons), "confidence": round(min(0.95, confidence), 2),
            "data_source": "local project record (user-entered)", "factors": {"importance": imp, "urgency": round(urgency, 2), "completion_advantage": round(completion_advantage, 2), "momentum": round(momentum, 2), "difficulty_penalty": round(diff * 0.6, 2)}}


def rank(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = [score_project(p) for p in projects]
    scored.sort(key=lambda p: p["score"], reverse=True)
    for i, p in enumerate(scored):
        p["rank"] = i + 1
        p["recommendation"] = ("Do this next" if i == 0 else "Queue after the top item" if i < 3 else "Lower priority for now")
    return scored
