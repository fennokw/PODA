"""Regression tests distilled from the 2026-10-02 interaction where PODA misread intent, denied access, and misread the calendar."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from poda_app.agent import gate
from poda_app.runtime import models
from poda_app.connectors.notion import mapping as nm

INTERACTION = [
    "Can you give me an overview of what events I have on the calendar for tomorrow and what emails I received in my student@example.test email in the past day?",
    "Give me an overview of what events I have on the calendar for tomorrow and what emails I received in my student@example.test email in the past day.",
    "READ MY LATEST GRADESCOPE EMAIL TO ME",
    "Now tell me what events I have on the schedule for tomorrow.",
    "I mean look at my Notion calendar and find what events/classes I have going on tomorrow and tell me at what time.",
]


@pytest.mark.parametrize("text", INTERACTION)
def test_real_requests_are_not_capability_questions(text):
    assert not gate.is_capability_query(text)


@pytest.mark.parametrize("text", ["What can you do with files?", "Can you read my email?", "Are you connected to Notion?", "Do you have access to my Notion calendar?", "What are your capabilities?"])
def test_ability_questions_still_detected(text):
    assert gate.is_capability_query(text)


@pytest.mark.parametrize("text", INTERACTION)
def test_data_access_questions_route_to_grounded_model(text):
    assert models.choose_profile(text, "auto", "balanced") == "balanced"


REAL_SCHEMA = {
    "Source URL": {"type": "url"}, "Source Date": {"type": "date"}, "Source Status": {"type": "select"}, "Date": {"type": "date"},
    "Calendar Source": {"type": "relation"}, "Source Fingerprint": {"type": "rich_text"}, "Import Source": {"type": "select"},
    "Type": {"type": "select"}, "Commitment": {"type": "select"}, "Area": {"type": "multi_select"}, "Notes": {"type": "rich_text"},
    "Source Last Seen": {"type": "date"}, "Source Name": {"type": "rich_text"}, "Sync Managed": {"type": "checkbox"},
    "Canvas Course ID": {"type": "rich_text"}, "External Event ID": {"type": "rich_text"}, "Canvas Assignment ID": {"type": "rich_text"}, "Name": {"type": "title"},
}


def test_infer_mapping_picks_date_not_source_date():
    m = nm.infer_mapping(REAL_SCHEMA)
    assert m["inferred"] is True and m["title"] == "Name" and m["date"] == "Date" and m["type"] == "Type"
    assert m["external_ids"]["canvas_assignment_id"] == "Canvas Assignment ID"
    assert "Commitment" in m["context_props"] and "Notes" in m["context_props"]


def test_interpret_item_converts_canvas_utc_due_to_local_evening():
    m = nm.infer_mapping(REAL_SCHEMA)
    page = {"id": "p1", "title": "Lab 2", "properties": {"Name": "Lab 2", "Date": {"start": "2026-10-03T03:59:00.000+00:00", "end": None, "time_zone": None},
                                                         "Type": "Homework", "Import Source": "Canvas", "Notes": "16.002 Unified Engineering", "Commitment": "Maybe"}}
    it = nm.interpret_item(page, m, "America/New_York")
    assert it["local_date"] == "2026-10-02" and it["start_time"] == "11:59 PM" and it["is_deadline"] is True
    assert it["summary"].startswith("[DUE] 2026-10-02 11:59 PM — Lab 2")


def test_interpret_item_keeps_ics_class_times_and_attendance():
    m = nm.infer_mapping(REAL_SCHEMA)
    page = {"id": "p2", "title": "16.002 rec", "properties": {"Name": "16.002 rec", "Date": {"start": "2026-10-02T09:00:00.000-04:00", "end": "2026-10-02T10:00:00.000-04:00", "time_zone": None},
                                                              "Type": "Event", "Import Source": "ICS", "Commitment": "Committed", "Source Name": "16.002 rec"}}
    it = nm.interpret_item(page, m)
    assert it["is_deadline"] is False and it["start_time"] == "9:00 AM" and it["end_time"] == "10:00 AM"
    assert "attendance: Committed" in it["summary"]


def test_resolve_day_words():
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    assert nm.resolve_day_word("today") == (today, today)
    s, e = nm.resolve_day_word("this week"); assert s <= today <= e
    assert nm.resolve_day_word("2026-10-03") == ("2026-10-03", "2026-10-03")
