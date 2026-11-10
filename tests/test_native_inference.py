from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from poda_app.chat import document_ocr
from poda_app.runtime import inference, models


def test_native_protocol_keeps_tool_ids_image_bytes_and_schema():
    messages = [
        {"role": "user", "content": "Inspect this image", "images": ["YQ=="]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_abc", "function": {"name": "read", "arguments": {"id": "one"}}},
        ]},
        {"role": "tool", "content": "result"},
    ]
    schema = {"type": "array", "items": {"type": "string"}}
    payload = inference._payload(messages, "local-model", {"num_predict": 100}, None, False, schema, True)
    assert payload["messages"][0]["content"][1]["image_url"]["url"] == "data:image/png;base64,YQ=="
    assert payload["messages"][2]["tool_call_id"] == "call_abc"
    assert payload["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"id": "one"}'
    assert payload["response_format"]["schema"] == schema
    assert payload["reasoning_effort"] == "none"
    assert payload["stream_options"]["include_usage"]
    assert "reasoning_budget_tokens" not in payload
    thinking = inference._payload(messages, "local-model", {"reasoning_budget": 256}, None, True, None, True)
    assert thinking["reasoning_budget_tokens"] == 256


def test_reasoning_budget_routing_for_simple_and_hard_requests():
    reg = {"profiles": {name: {"model": name, "num_ctx": 8192, "num_predict": 1600,
                               "temperature": 0.1, "think": name != "fast"} for name in models.PROFILE_NAMES}}
    quick = models.resolve("Write a Python script to print hello", "auto", "balanced", reg)
    hard = models.resolve("Debug and refactor this Python algorithm", "auto", "deep", reg)
    debug = models.resolve("Debug this Python script", "auto", "balanced", reg)
    analysis = models.resolve("Analyze, compare and prioritize these approaches", "auto", "balanced", reg)
    simple = models.resolve("List my calendar events", "auto", "balanced", reg)
    assert quick["profile"] == "fast_coding" and not quick["think"]
    assert hard["profile"] == "deep_coding" and hard["think"] and hard["num_predict"] >= 3200
    assert debug["profile"] == "deep_coding" and debug["options"]["reasoning_budget"] == 768
    assert analysis["options"]["reasoning_budget"] == 256
    assert simple["profile"] == "balanced" and not simple["think"]


def test_scanned_pdf_ocr_preserves_page_and_table_value():
    image = Image.new("RGB", (800, 380), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=41)
    draw.text((26, 35), "INVOICE 84621", font=font, fill="black")
    draw.text((26, 148), "ITEM", font=font, fill="black")
    draw.text((515, 148), "AMOUNT", font=font, fill="black")
    draw.text((26, 245), "Machine parts", font=font, fill="black")
    draw.text((515, 245), "127.50", font=font, fill="black")
    data = BytesIO()
    image.save(data, format="PDF")
    result = document_ocr.pdf_text(data.getvalue(), 3000, editing=False)
    assert "[Page 1 · OCR]" in result
    assert "84621" in result and "127.50" in result

    mixed = BytesIO()
    canvas = Canvas(mixed, pagesize=(800, 560))
    heading = "Departmental procurement record and supporting invoice"
    canvas.drawString(26, 500, heading)
    canvas.drawImage(ImageReader(image), 0, 0, width=800, height=380)
    canvas.save()
    result = document_ocr.pdf_text(mixed.getvalue(), 3000, editing=False)
    assert heading.lower() in " ".join(result.lower().split())
    assert "84621" in result and "127.50" in result
