"""Regression coverage for work-time calibration, file intake, and draft scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

from poda_app.workload import service
from poda_app.workload.router import _extract_text


def test_local_pace_feedback_and_override(client):
    reference = client.post("/workload", json={
        "title": "Sample coding exercise", "category": "coding", "difficulty": 6,
        "baseline_minutes": 60, "user_minutes": 140, "importance": 5
    })
    assert reference.status_code == 200, reference.text
    example_id = reference.json()["id"]
    other_id = None
    try:
        out = client.get("/workload/estimate?title=Next%20exercise&category=coding&difficulty=6").json()["estimate"]
        # 140/60 calibration feedback should slow down future coding predictions.
        assert out["calibration"]["examples"] >= 1
        assert out["calibration"]["multiplier"] > 1
        other = client.post("/workload", json={
            "title": "Additional coding", "category": "coding", "difficulty": 6,
            "baseline_minutes": 60, "importance": 7
        })
        assert other.status_code == 200
        other_id = other.json()["id"]
        assert other.json()["estimate"]["minutes"] > 60
        updated = client.put("/workload/" + other_id, json={
            "title": "Additional coding", "category": "coding", "difficulty": 6,
            "baseline_minutes": 60, "importance": 7, "user_minutes": 95,
            "actual_minutes": 120, "progress": 30
        })
        assert updated.status_code == 200
        assert updated.json()["estimate"]["minutes"] == 95
        assert updated.json()["estimate"]["remaining_minutes"] < 95
        assert client.get("/workload").json()["total"] >= 2
    finally:
        assert client.delete("/workload/" + example_id).json()["deleted"]
        if other_id:
            assert client.delete("/workload/" + other_id).json()["deleted"]


def test_deadline_bounded_plan_without_notion(client):
    # Verify draft plans work even when Notion is unavailable, without claiming conflicts checked.
    deadline = (datetime.now().astimezone() + timedelta(days=3)).date().isoformat()
    response = client.post("/workload", json={
        "title": "Tomorrow's study workload", "category": "study", "difficulty": 4,
        "importance": 10, "deadline": deadline, "user_minutes": 130
    })
    assert response.status_code == 200
    item_id = response.json()["id"]
    try:
        plan = client.get("/workload/schedule?days=5&include_calendar=false")
        assert plan.status_code == 200
        result = plan.json()
        assert any(s["item_id"] == item_id for s in result["sessions"])
        assert all(s["deadline"] == deadline for s in result["sessions"] if s["item_id"] == item_id)
        assert "NOT checked" in result["calendar"]
        assert "NOT added to Notion" in result["note"]
        phases = client.get("/workload/" + item_id + "/breakdown").json()["phases"]
        assert len(phases) == 3
        assert sum(s["minutes"] for s in phases) == 130
    finally:
        client.delete("/workload/" + item_id)


def test_document_intake_is_preview_only(client):
    before = client.get("/workload").json()["total"]
    res = client.post("/workload/intake", files={
        "file": ("homework.txt", b"- Question 1: Solve the equation\n- Question 2: Draw the graph\n", "text/plain")
    })
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["suggestions"]
    assert data["suggestions"][0]["category"] == "homework"
    assert client.get("/workload").json()["total"] == before
    assert client.post("/workload/intake", files={
        "file": ("unsafe.exe", b"rm -rf /", "application/octet-stream")
    }).status_code == 422


def test_docx_extract_has_no_external_requests():
    out = BytesIO()
    with ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Homework problem 4</w:t></w:r></w:p></w:body></w:document>')
    assert "Homework problem 4" in _extract_text("assignment.docx", out.getvalue())


def test_workload_rename_persists(client):
    result = client.post("/workload", json={"title": "History essay", "category": "writing"})
    assert result.status_code == 200
    tid = result.json()["id"]
    try:
        upd = client.put("/workload/" + tid, json={"title": "Final essay", "category": "writing"})
        assert upd.status_code == 200
        assert upd.json()["title"] == "Final essay"
    finally:
        client.delete("/workload/" + tid)
