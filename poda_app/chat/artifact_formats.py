"""Render local documents from text. Never execute content supplied by a model."""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from xml.sax.saxutils import escape

DOCUMENT_INPUT = {"pdf": "markdown", "docx": "markdown", "xlsx": "csv"}


def _document_lines(source: str):
    for line in source.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        yield (len(heading[1]), heading[2]) if heading else (0, line)


def render_document(extension: str, source: str) -> bytes:
    output = io.BytesIO()
    if extension == "pdf":
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
        import reportlab

        fonts = Path(reportlab.__file__).parent / "fonts"
        pdfmetrics.registerFont(TTFont("PODA-Vera", str(fonts / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("PODA-Vera-Bold", str(fonts / "VeraBd.ttf")))
        styles = getSampleStyleSheet()
        for name in ("BodyText", "Heading1", "Heading2", "Heading3"):
            styles[name].fontName = "PODA-Vera" if name == "BodyText" else "PODA-Vera-Bold"
        parts = [Paragraph(escape(text), styles[f"Heading{min(level, 3)}"] if level else styles["BodyText"])
                 if text else Spacer(1, 7) for level, text in _document_lines(source)]
        SimpleDocTemplate(output).build(parts)
    elif extension == "docx":
        from docx import Document

        document = Document()
        for level, text in _document_lines(source):
            if level:
                document.add_heading(text, level=level)
            else:
                document.add_paragraph(text)
        document.save(output)
    elif extension == "xlsx":
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        width = None
        for index, values in enumerate(csv.reader(io.StringIO(source), strict=True), 1):
            if not values:
                continue
            if index > 10000 or len(values) > 128:
                raise ValueError("Use at most 10,000 spreadsheet rows and 128 columns.")
            if width is None:
                width = len(values)
            if len(values) != width:
                raise ValueError("Every CSV row must have the same number of columns.")
            for column, value in enumerate(values, 1):
                if len(value) > 32767:
                    raise ValueError("A spreadsheet cell is too long.")
                cell = sheet.cell(index, column, value)
                # Treat generated cell text literally, including leading '='.
                cell.data_type = "s"
        if width is None:
            raise ValueError("Provide CSV data for the workbook.")
        workbook.save(output)
    else:
        raise ValueError("Unsupported document format.")
    return output.getvalue()
