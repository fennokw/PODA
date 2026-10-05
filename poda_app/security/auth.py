"""Local session + origin protection for the loopback API.

Threat addressed: a web page in the same browser (or any process that can make HTTP requests to
127.0.0.1) driving PODA's API. Mitigations: a per-process random session secret delivered as an
HttpOnly SameSite=Strict cookie when the UI loads, strict Origin/Sec-Fetch-Site checks on every
non-public route, and a restrictive Content-Security-Policy with no remote sources.

Honest limit: another process running as the same macOS user could still fetch the UI page and
obtain a cookie. Full isolation from same-user processes requires a sandboxed native shell; this
layer stops cross-site requests and casual localhost probing, not a determined local attacker.
"""
from __future__ import annotations

import hmac
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from ..runtime import config

SESSION_COOKIE = "poda_session"
SESSION_SECRET = secrets.token_urlsafe(32)
PUBLIC_PATHS = {"/health", "/system/build", "/favicon.ico"}
UI_PATHS = {"/", "/memory-viewer", "/memory-viewer/"}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self'; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _allowed_origins() -> set[str]:
    return {f"http://127.0.0.1:{config.PORT}", f"http://localhost:{config.PORT}"}


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site")
    if origin:
        return origin in _allowed_origins()
    if fetch_site in {"same-origin", "none"}:
        return True
    referer = request.headers.get("referer")
    if referer:
        return any(referer.startswith(o + "/") or referer == o for o in _allowed_origins())
    # Non-browser local tooling (curl, the launcher) sends none of these headers. Those callers
    # still need the session cookie for protected routes, so this is not an open door.
    return True


def _session_ok(request: Request) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE, "")
    return bool(cookie) and hmac.compare_digest(cookie, SESSION_SECRET)


async def security_middleware(request: Request, call_next):
    path = request.url.path
    host = request.headers.get("host", "")
    if host and host.split(":")[0] not in {"127.0.0.1", "localhost"}:
        return JSONResponse({"detail": "PODA only serves loopback hosts"}, status_code=421)
    is_public = path in PUBLIC_PATHS or path in UI_PATHS or path.startswith("/static/")
    if not is_public:
        if not _origin_ok(request):
            return JSONResponse({"detail": "Cross-origin requests to PODA are rejected"}, status_code=403)
        if not _session_ok(request):
            return JSONResponse({"detail": "Missing or stale PODA session. Reload the PODA page to start a new local session."}, status_code=401)
    response: Response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-PODA-Build"] = config.BUILD_ID
    if path in UI_PATHS or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    if path in UI_PATHS:
        response.set_cookie(SESSION_COOKIE, SESSION_SECRET, httponly=True, samesite="strict", secure=False, path="/")
    return response
