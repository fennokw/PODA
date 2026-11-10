from __future__ import annotations

import base64
import io
import json
import stat
from zipfile import ZipFile

from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from poda_app.agent import orchestrator, sandbox_files
from poda_app.chat import inputs, router as chat


def _events(response):
    assert response.status_code == 200, response.text
    return [(event[7:], json.loads(data[6:])) for block in response.text.strip().split("\n\n")
            for lines in [block.splitlines()]
            for event in lines if event.startswith("event: ")
            for data in lines if data.startswith("data: ")]


def _prompt(client, message, files):
    started = client.post("/chat/runs/with-files", data={"prompt": json.dumps({"message": message, "use_memory": False})},
                          files=[("files", (name, data, "application/octet-stream")) for name, data in files])
    assert started.status_code == 200, started.text
    run = started.json()
    return run, _events(client.get(f"/chat/runs/{run['run_id']}/stream"))


def _model(monkeypatch, response):
    monkeypatch.setattr(chat.models, "resolve", lambda *a: {
        "model": "qwen3:14b", "profile": "balanced", "num_ctx": 8192, "think": True,
        "options": {"num_ctx": 8192, "num_predict": 1600},
    })
    monkeypatch.setattr(chat.inference, "chat_stream", response)

    def fail(*args, **kwargs):
        raise AssertionError("This request should go directly to the reply writer.")

    monkeypatch.setattr(orchestrator, "plan", fail)


def test_long_zip_skips_planner_streams_progress_and_adds_launchers(client, monkeypatch):
    async def writer(messages, model, options, think, format):
        assert model == "qwen3:14b"
        assert options["num_predict"] >= 4000
        assert think is False and format["type"] == "array"
        body = json.dumps([{"filename": "index.html", "content": "<!doctype html><h1>Fluid graphic</h1>"}])
        yield {"message": {"content": body[:20]}, "done": False}
        yield {"message": {"content": body[20:]}, "done": True}

    _model(monkeypatch, writer)
    request = ("Now give me a zip file of code that has a single click run command that will output a graphic that "
               "moves with my mouse and has fluid dynamic effect. Take your time.")
    events = _events(client.post("/chat/stream", json={"message": request, "use_memory": False}))
    assert [data["message"] for name, data in events if name == "status"] == [
        "routing", "writing_archive", "saving_archive"]
    assert any(name == "archive_progress" for name, _ in events)
    filename = dict(events)["done"]["download"]["sandbox_file"]
    data = sandbox_files.read_bytes(filename)
    (sandbox_files.root() / filename).unlink()
    with ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert "Fluid graphic" in archive.read("index.html").decode()
        assert b'open "./index.html"' in archive.read("Run on Mac.command")
        assert archive.getinfo("Run on Mac.command").external_attr >> 16 & stat.S_IXUSR
        assert b'start "" "%~dp0index.html"' in archive.read("Run on Windows.cmd")


def test_zip_upload_changes_selected_source_and_preserves_other_members(client, monkeypatch):
    original = sandbox_files.pack_archive([
        ("src/main.py", b"print('old')"), ("assets/banner.bin", b"\x00\x01"), (".gitignore", b"build/\n"),
    ])

    async def writer(messages, *args, **kwargs):
        assert "print('old')" in messages[-1]["content"]
        assert "ZIP source path" in messages[-1]["content"]
        assert "PODA preserves all other original members" in messages[0]["content"]
        yield {"message": {"content": json.dumps([
            {"filename": "src/main.py", "content": "print('new')\n"},
        ])}, "done": True}

    _model(monkeypatch, writer)
    run, events = _prompt(client, "Edit the uploaded ZIP so src/main.py prints new.", [("app.zip", original)])
    name = dict(events)["done"]["download"]["sandbox_file"]
    with ZipFile(io.BytesIO(sandbox_files.read_bytes(name))) as archive:
        assert archive.read("src/main.py") == b"print('new')\n"
        assert archive.read("assets/banner.bin") == b"\x00\x01"
        assert archive.read(".gitignore") == b"build/\n"
    with ZipFile(io.BytesIO(original)) as archive:
        assert archive.read("src/main.py") == b"print('old')"
    session = client.get(f"/chat/session/{run['session_id']}/messages").json()
    assert session["messages"][0]["attachments"] == ["app.zip"]
    assert "print('old')" not in json.dumps(session)


def test_pdf_edit_exports_a_revised_copy(client, monkeypatch):
    original = io.BytesIO()
    canvas = Canvas(original)
    canvas.drawString(20, 650, "The original date is January 1.")
    canvas.save()

    async def writer(messages, *args, **kwargs):
        assert "[Page 1]" in messages[-1]["content"]
        assert "The original date is January 1." in messages[-1]["content"]
        yield {"message": {"content": "```markdown\nThe revised date is January 2.\n```"}, "done": True}

    _model(monkeypatch, writer)
    _, events = _prompt(client, "Update the date in the attached PDF to January 2.", [("agenda.pdf", original.getvalue())])
    name = dict(events)["done"]["download"]["sandbox_file"]
    from pypdf import PdfReader
    pdf = PdfReader(io.BytesIO(sandbox_files.read_bytes(name)))
    assert "The revised date is January 2." in pdf.pages[0].extract_text()


def test_image_reaches_the_verified_local_vision_model(client, monkeypatch):
    screenshot = io.BytesIO()
    Image.new("RGB", (64, 48), "red").save(screenshot, "PNG")

    async def writer(messages, model, **kwargs):
        assert model == "qwen3-vl:4b"
        photo, = messages[-1]["images"]
        with Image.open(io.BytesIO(base64.b64decode(photo))) as image:
            assert image.size == (64, 48) and image.getpixel((0, 0)) == (255, 0, 0)
        yield {"message": {"content": "The screenshot is red."}, "done": True}

    _model(monkeypatch, writer)
    monkeypatch.setattr(chat.inference, "vision_model", lambda preferred: "qwen3-vl:4b")
    _, events = _prompt(client, "Describe this screenshot.", [("bug.png", screenshot.getvalue())])
    assert dict(events).get("done", {}).get("model") == "qwen3-vl:4b", events
    assert "screenshot is red" in "".join(data.get("token", "") for _, data in events)


def test_unsafe_zip_is_rejected_before_model_inference():
    data = io.BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("../outside.py", "print('unsafe')")
    uploaded = inputs.InputFile("unsafe.zip", data.getvalue())
    result = inputs.prepare((uploaded,), "Read the uploaded ZIP.", 8192, uploaded)
    assert "ZIP paths must stay inside" in result.warnings[0]
    assert not result.context and result.original_zip is None


def test_planner_idle_error_falls_back_once(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator, "_planner_models", lambda _: ["strong", "fast", "third"])
    monkeypatch.setattr(orchestrator.tools, "model_tool_specs", lambda _: [{"function": {"name": "sandbox_read"}}])

    def infer(prompt, model, **kwargs):
        calls.append(model)
        assert kwargs["timeout"] == 75 and kwargs["streaming"] is True
        raise chat.inference.InferenceError("llama.cpp stopped sending tool planning progress.")

    monkeypatch.setattr(chat.inference, "chat", infer)
    progress = []
    result = orchestrator.plan("Read files", {}, emit=lambda name, value: progress.append((name, value)))
    assert calls == ["strong", "fast"]
    assert any(value["message"] == "planner_fallback" for _, value in progress)
    assert result["flagged"] and not result["proposal_id"]
