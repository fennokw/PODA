"""Local PP-OCR via RapidOCR, plus page-aware extraction for uploaded documents."""
from __future__ import annotations

from contextlib import closing
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import Lock
from zipfile import ZipFile

_ocr_lock = Lock()


@lru_cache(maxsize=1)
def _engine():
    import rapidocr

    models = Path(rapidocr.__file__).with_name("models")
    weights = {
        "Det.model_path": models / "PP-OCRv6_det_small.onnx",
        "Cls.model_path": models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "Rec.model_path": models / "PP-OCRv6_rec_small.onnx",
    }
    if not all(path.is_file() for path in weights.values()):
        raise RuntimeError("PODA’s bundled OCR weights are missing.")
    return rapidocr.RapidOCR(params={
        **{key: str(path) for key, path in weights.items()},
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    })


def image_text(image) -> str:
    import numpy as np

    image = image.convert("RGB")
    image.thumbnail((2400, 2400))
    pixels = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
    with _ocr_lock:
        result = _engine()(pixels)
    if result.boxes is None or not result.txts:
        return ""
    lines = []
    for box, text, score in zip(result.boxes, result.txts, result.scores):
        if score < .45 or not text.strip():
            continue
        xs, ys = zip(*box)
        lines.append(((max(ys) + min(ys)) / 2, min(xs), max(ys) - min(ys), text.strip()))
    rows = []
    for y, x, height, text in sorted(lines):
        if rows and abs(y - rows[-1][0]) <= max(4, height * .55):
            rows[-1][1].append((x, text))
        else:
            rows.append((y, [(x, text)]))
    return "\n".join("\t".join(text for _, text in sorted(columns)) for _, columns in rows)


def pdf_text(data: bytes, limit: int, *, editing: bool) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise ValueError("Unlock this PDF before attaching it.")
    if len(reader.pages) > 120:
        raise ValueError("Attach a PDF excerpt of up to 120 pages.")
    parts = []
    remaining = limit
    scanned = 0
    raster = None
    try:
        for index, page in enumerate(reader.pages, 1):
            native = (page.extract_text(extraction_mode="layout") or "").strip()
            text = native
            label = f"Page {index}"
            if not native or (len("".join(native.split())) < 40 and len(page.images)):
                if scanned < 16:
                    try:
                        import pypdfium2 as pdfium
                        if raster is None:
                            raster = pdfium.PdfDocument(data)
                        with closing(raster[index - 1]) as raster_page:
                            width, height = raster_page.get_size()
                            scale = min(2.8, 2400 / max(width, height))
                            with closing(raster_page.render(scale=scale)) as bitmap:
                                recognized = image_text(bitmap.to_pil())
                        if recognized:
                            text, label = recognized, label + " · OCR"
                    except (ImportError, OSError, RuntimeError, ValueError):
                        pass
                    scanned += 1
                elif not native:
                    if editing:
                        raise ValueError("A single PDF edit supports OCR on up to 16 scanned pages.")
                    parts.append("[Additional scanned pages omitted. Attach a smaller page range.]")
                    break
            if not text:
                if editing:
                    raise ValueError(f"Page {index} could not be read. Attach a clearer scan before editing the entire PDF.")
                parts.append(f"[Page {index} · no text recognized]")
                continue
            section = f"[{label}]\n{text}"
            if len(section) > remaining:
                if editing:
                    raise ValueError("This PDF is too long for a full edit in the current model context. Attach a smaller excerpt.")
                parts += [section[:remaining], "[PDF excerpt ends here.]"]
                break
            parts.append(section)
            remaining -= len(section)
    finally:
        if raster is not None:
            raster.close()
    if not any(part.startswith("[Page") and "\n" in part for part in parts):
        raise ValueError("PODA could not recognize readable PDF text. Try a clearer scan.")
    return "\n\n".join(parts)


def office_text(data: bytes, suffix: str, limit: int) -> str:
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 1000 or sum(entry.file_size for entry in entries) > 96 * 1024 * 1024:
            raise ValueError("Attach a smaller Office document.")
    if suffix == ".docx":
        from docx import Document
        from docx.table import Table

        def lines():
            for item in Document(BytesIO(data)).iter_inner_content():
                if isinstance(item, Table):
                    yield "[Table]"
                    for row in item.rows:
                        yield "\t".join(cell.text for cell in row.cells)
                elif item.text.strip():
                    yield item.text

        return _bounded(lines(), limit)
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
    try:
        def lines():
            for sheet in workbook.worksheets[:50]:
                yield f"[Sheet {sheet.title}]"
                for row in sheet.iter_rows(max_row=min(sheet.max_row or 5000, 5000),
                                           max_col=min(sheet.max_column or 120, 120), values_only=True):
                    if any(value is not None for value in row):
                        yield "\t".join("" if value is None else str(value) for value in row).rstrip("\t")
        return _bounded(lines(), limit)
    finally:
        workbook.close()


def _bounded(lines, limit: int) -> str:
    result = []
    for line in lines:
        if len(line) + 1 > limit:
            result.extend((line[:limit], "[Document excerpt ends here.]"))
            break
        result.append(line)
        limit -= len(line) + 1
    return "\n".join(result)
