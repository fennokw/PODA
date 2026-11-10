"""Resolve evidence from mail without letting the model choose calendar timestamps."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.parser import parse

_WEEKDAYS = {name.lower(): index for index, name in enumerate(calendar.day_name)}
_MONTH = re.compile(r"\b(?:" + "|".join(calendar.month_name[1:] + calendar.month_abbr[1:]) + r")\.?\b", re.I)
_DATE_CUE = re.compile(
    r"\b(?:the day after tomorrow|day after tomorrow|tomorrow|today|tonight|yesterday|"
    r"\d{4}-\d{2}-\d{2}|(?:(?:this|next) )?(?:" + "|".join(_WEEKDAYS) + r")|"
    + _MONTH.pattern + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)\b", re.I
)
_RELATIVE = {"today": 0, "tonight": 0, "tomorrow": 1, "the day after tomorrow": 2, "day after tomorrow": 2, "yesterday": -1}
_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?", re.I)
_DELAY = re.compile(r"in\s+(\d{1,3}|an?|one|two|three)\s+(minute|hour)s?", re.I)
_ZONES = {"ET": "America/New_York", "CT": "America/Chicago", "MT": "America/Denver", "PT": "America/Los_Angeles"}
_FIXED = {"UTC": 0, "GMT": 0, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5,
          "MST": -7, "MDT": -6, "PST": -8, "PDT": -7}


def _zone(value: str) -> tzinfo:
    key = value.strip()
    if key.upper() in _FIXED:
        return timezone(timedelta(hours=_FIXED[key.upper()]))
    if offset := re.fullmatch(r"(?:UTC|GMT)?([+-])(\d\d):?(\d\d)", key, re.I):
        minutes = int(offset[2]) * 60 + int(offset[3])
        if minutes < 24 * 60 and int(offset[3]) < 60:
            return timezone(timedelta(minutes=minutes if offset[1] == "+" else -minutes))
    try:
        return ZoneInfo(_ZONES.get(key.upper(), key))
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("The event timezone is ambiguous or unsupported.") from exc


def _day(expression: str, reference: date) -> date:
    value = " ".join(expression.lower().strip(" ,.!").split())
    if value in _RELATIVE:
        return reference + timedelta(days=_RELATIVE[value])
    if weekday := re.fullmatch(r"(?:(this|next) )?(" + "|".join(_WEEKDAYS) + ")", value):
        delta = (_WEEKDAYS[weekday[2]] - reference.weekday()) % 7
        return reference + timedelta(days=delta or (7 if weekday[1] == "next" else 0))
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    if _MONTH.search(value) and re.search(r"\b\d{1,2}(?:st|nd|rd|th)?\b", value):
        return parse(value, default=datetime.combine(reference, time())).date()
    raise ValueError("The email does not specify a resolvable event date.")


def stated_dates(text: str) -> list[str]:
    return list(dict.fromkeys(match[0].casefold() for match in _DATE_CUE.finditer(" ".join(text.split()))))


def _clock(expression: str) -> time:
    value = expression.strip().lower()
    if value in {"noon", "midnight"}:
        return time(12 if value == "noon" else 0)
    parsed = _CLOCK.fullmatch(value)
    if not parsed or not (parsed[2] or parsed[3]):
        raise ValueError("Use an explicit AM/PM or 24-hour event time.")
    hour, minute = int(parsed[1]), int(parsed[2] or 0)
    if parsed[3]:
        if not 1 <= hour <= 12:
            raise ValueError("Invalid 12-hour clock time.")
        hour = hour % 12 + (12 if parsed[3].startswith("p") else 0)
    elif hour < 10 and len(parsed[1]) == 1:
        raise ValueError("The event time does not say AM or PM.")
    return time(hour, minute)


def _at(day: date, clock: time, zone: tzinfo) -> datetime:
    local = datetime.combine(day, clock, tzinfo=zone)
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError("The event falls in an ambiguous or nonexistent daylight-saving time.")
    return local


def resolve_event_time(sent_at: str, date_text: str, time_text: str, timezone_name: str,
                       end_text: str = "", event_timezone: str = "", *, sender_timezone_known: bool = False) -> dict[str, str | None]:
    """Resolve quoted date/time phrases relative to the original send timestamp."""
    sent = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    if sent.tzinfo is None:
        raise ValueError("An original send timestamp with timezone is required.")
    default = _zone(timezone_name)
    scheduled_local = sent.astimezone(default)
    explicit_zone = _zone(event_timezone) if event_timezone else None
    # Native providers may expose only a UTC-normalized sent timestamp.
    # A sender offset that matches the automation's zone can use its DST rules.
    sender_zone = default
    if (sender_timezone_known or sent.utcoffset()) and sent.utcoffset() != scheduled_local.utcoffset():
        sender_zone = sent.tzinfo
    zone = explicit_zone or sender_zone
    anchor = sent.astimezone(zone)
    if delay := _DELAY.fullmatch(time_text.strip()):
        count = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}.get(delay[1].lower())
        if count is None:
            count = int(delay[1])
        if date_text.strip() or end_text.strip() or count < 1:
            raise ValueError("A countdown reminder must specify one relative start time.")
        minutes = count * (60 if delay[2].lower() == "hour" else 1)
        start = (sent.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(zone)
        return {"start": start.isoformat(), "end": None}
    if not date_text.strip():
        raise ValueError("The email does not specify an event date.")
    day = _day(date_text, anchor.date())
    # A range such as "8:00–8:30 PM" supplies its meridiem at the end.
    first = _CLOCK.fullmatch(time_text.strip())
    last = _CLOCK.fullmatch(end_text.strip())
    if first and last and not first[3] and last[3] and 1 <= int(first[1]) <= 12:
        time_text += " " + last[3]
    start = _at(day, _clock(time_text), zone)
    end = _at(day, _clock(end_text), zone) if end_text else None
    if end and end.astimezone(timezone.utc) <= start.astimezone(timezone.utc):
        raise ValueError("The event end does not follow its start on the stated date.")
    return {"start": start.isoformat(), "end": end.isoformat() if end else None}
