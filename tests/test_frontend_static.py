"""Static checks for the bundled frontend: no remote assets, syntax-valid JS, required controls present, canonical viewer link."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "poda_app" / "static"
JS_FILES = sorted(STATIC.glob("*.js"))
TEXT_FILES = sorted(list(STATIC.glob("*.js")) + list(STATIC.glob("*.css")) + list(STATIC.glob("*.html")))


def test_expected_files_exist():
    for name in ["index.html", "memory.html", "styles.css", "tokens.css", "memory.css", "ui.js", "markdown.js", "app.js", "memory.js", "memory-worker.js", "favicon.svg"]:
        assert (STATIC / name).exists(), name


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_js_syntax(path: Path):
    subprocess.run(["node", "--check", str(path)], check=True, capture_output=True)


def test_no_remote_assets_or_cdns():
    pattern = re.compile(r"https?://(?!127\.0\.0\.1|localhost)[^\s'\"()]+")
    for path in TEXT_FILES:
        text = path.read_text(encoding="utf-8")
        hits = [m for m in pattern.findall(text) if "notion.so" not in m and "notion.com" not in m]
        assert not hits, f"{path.name} references remote resources: {hits}"
        assert "fonts.googleapis" not in text and "cdn." not in text and "unpkg" not in text and "jsdelivr" not in text


def test_home_button_opens_canonical_viewer():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="memoryBtn"' in index
    assert "window.location.assign('/memory-viewer')" in app_js


def test_viewer_controls_always_present():
    html = (STATIC / "memory.html").read_text(encoding="utf-8")
    for needle in ["RETURN TO PODA CHAT", "COMMIT MEMORY CHANGES", "DISCARD DRAFT", 'id="memorySearchInput"', 'id="memorySearchPrev"', 'id="memorySearchNext"', "memory-worker"]:
        assert needle in html or needle in (STATIC / "memory.js").read_text(encoding="utf-8"), needle


def test_viewer_semantics_preserved():
    js = (STATIC / "memory.js").read_text(encoding="utf-8")
    assert "idleRotateDelayMs = 15000" in js and "autoRotateRate = 0.0544" in js
    assert "gl.drawArrays(gl.POINTS" in js and "create_sphere" not in js  # sphere geometry comes from the backend cloud_volume
    assert "/memory/transaction/commit" in js and "deleted_ids" in js
    assert "LOD_FULL_GRAPH_MAX = 400" in js and "new Worker('/static/memory-worker.js" in js
    assert "prefers-reduced-motion" in js


def test_no_inline_scripts_for_csp():
    for name in ["index.html", "memory.html"]:
        html = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), f"{name} has an inline script, which the CSP blocks"
        assert "onclick=" not in html


def test_no_em_dashes_in_visible_copy():
    for path in TEXT_FILES:
        text = path.read_text(encoding="utf-8")
        assert "—" not in text, f"{path.name} contains an em dash"


def test_reduced_motion_and_tokens():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    tokens = (STATIC / "tokens.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css and "prefers-reduced-motion" in tokens
    assert "--accent:" in tokens and "--r-panel" in tokens and "--ease-out" in tokens


# ---- v0.5.0: Mail tab + two-tier memory viewer ----

def test_mail_tab_present_and_wired():
    assert (STATIC / "mail.js").exists()
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-route="mail"' in index and "/static/mail.js" in index
    mail = (STATIC / "mail.js").read_text(encoding="utf-8")
    for endpoint in ["/mail/status", "/mail/apple/connect", "/mail/gmail/connect", "/mail/mailboxes", "/mail/messages", "/mail/snippets", "/mail/search", "/mail/drafts", "/mail/send", "/extract", "/propose-notion"]:
        assert endpoint in mail, endpoint
    # bodies are rendered as text nodes only; no innerHTML of message content
    assert "document.createTextNode(part)" in mail and "innerHTML = m.body" not in mail
    assert "AUTOMATION_DENIED" in mail and "CONFIRM_REQUIRED" in mail or "confirm: !!opts.confirm" in mail
    assert "requireText: 'SEND'" in mail  # sending needs a typed confirmation


def test_memory_viewer_two_tier_controls():
    html = (STATIC / "memory.html").read_text(encoding="utf-8")
    js = (STATIC / "memory.js").read_text(encoding="utf-8")
    assert 'id="surfacesShown"' in html and 'id="surfacesHidden"' in html and "Surface memories" in html
    assert 'id="tiersHelp"' in html
    assert "SURFACES_KEY = 'poda_surfaces_shown'" in js and "localStorage.setItem(SURFACES_KEY" in js
    assert "REVEAL_DISTANCE_FACTOR" in js and "pinnedReveal" in js
    assert "body.dissolve = true" in js and "/memory/surfaces/" in js and "/distill" in js
    assert "pair-block you" in js and "pair-block poda" in js
    worker = (STATIC / "memory-worker.js").read_text(encoding="utf-8")
    assert "membersBySurface" in worker and "surfaceOf" in worker


def test_ask_recall_shows_tiers():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "Surface memories read first" in app_js and "depth_used" in app_js and "memory_dive" in app_js


def test_personalize_import_screen_is_shipped():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "Personalize" in index
    assert "Import your main AI data export" in app
    assert "/imports/llm-export" in app
    assert "Open second brain" in app
