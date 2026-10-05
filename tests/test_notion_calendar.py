"""Calendar view: field resolution without a saved mapping, interpretation of real-shaped pages, range validation."""
from __future__ import annotations

from poda_app.connectors.notion import calendar

SCHEMA = {
    "Name": {"type": "title"}, "Date": {"type": "date"}, "Source Date": {"type": "date"}, "Type": {"type": "select"},
    "Source Status": {"type": "select"}, "Commitment": {"type": "select"}, "Area": {"type": "multi_select"}, "Import Source": {"type": "select"},
    "Notes": {"type": "rich_text"}, "Source URL": {"type": "url"},
}


def test_resolve_fields_prefers_date_and_title_without_mapping():
    fields = calendar.resolve_fields({"schema": SCHEMA}, None)
    assert fields["date"] == "Date" and fields["title"] == "Name"
    assert fields["type"] == "Type" and fields["status"] == "Source Status" and fields["commitment"] == "Commitment"
    assert fields["area"] == "Area" and fields["source"] == "Import Source" and fields["source_url"] == "Source URL"
    assert fields["from_mapping"] is False


def test_resolve_fields_honours_saved_mapping():
    active = {"mapping": {"title": "Name", "date": "Source Date", "status": "Source Status", "status_done_values": ["Done"]}}
    fields = calendar.resolve_fields({"schema": SCHEMA}, active)
    assert fields["date"] == "Source Date" and fields["done_values"] == ["Done"] and fields["from_mapping"] is True


def test_interpret_timed_and_all_day_pages():
    fields = calendar.resolve_fields({"schema": SCHEMA}, None)
    timed = {"id": "p1", "url": "https://www.notion.so/p1", "title": "Serenades", "last_edited_time": "2026-10-01T10:00:00Z",
             "properties": {"Name": "Serenades", "Date": {"start": "2026-10-02T19:30:00.000-04:00", "end": "2026-10-02T22:30:00.000-04:00", "time_zone": None},
                            "Type": "Event", "Source Status": "Active", "Commitment": "Yes", "Area": ["Music"], "Import Source": "Calendar", "Notes": "Bring charts"}}
    ev = calendar.interpret(timed, fields)
    assert ev["title"] == "Serenades" and ev["all_day"] is False and ev["end"].startswith("2026-10-02T22:30") and ev["area"] == ["Music"] and ev["notes"] == "Bring charts"
    all_day = {"id": "p2", "properties": {"Name": "Lab 3", "Date": {"start": "2026-10-10", "end": None, "time_zone": None}, "Type": ["Homework", "Lab"]}}
    ev2 = calendar.interpret(all_day, fields)
    assert ev2["all_day"] is True and ev2["type"] == "Homework"
    assert calendar.interpret({"id": "p3", "properties": {"Name": "No date"}}, fields) is None


def test_overlap_keeps_multi_day_pages_that_started_earlier():
    ev = {"start": "2026-09-28", "end": "2026-10-02"}
    assert calendar._overlaps(ev, "2026-10-01", "2026-10-31") is True
    assert calendar._overlaps({"start": "2026-09-20", "end": None}, "2026-10-01", "2026-10-31") is False


def test_calendar_endpoint_reports_not_connected(client):
    r = client.get("/notion/calendar?start=2026-10-01&end=2026-10-31")
    assert r.status_code in (400, 401, 503)
    assert r.json()["detail"]["code"] in ("not_connected", "no_token", "validation")
