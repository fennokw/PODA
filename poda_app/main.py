"""PODA application entrypoint. Thin: wiring only. Behaviour lives in the domain packages."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .runtime import config
from .runtime.db import init_database, connect
from .runtime import capability
from .memory import store
from .security.auth import security_middleware
from .runtime.router import router as runtime_router
from .memory.router import router as memory_router
from .chat.router import router as chat_router
from .agent.router import router as agent_router
from .planner.router import router as planner_router
from .security.router import router as privacy_router
from .connectors.notion.router import router as notion_router
from .connectors.email.router import router as email_router
from .agent.software_router import router as software_router  # registers software tools at import

STARTUP_REPORT: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    STARTUP_REPORT.update(init_database())
    conn = connect()
    try:
        store.ensure_seed(conn)
        conn.commit()
    finally:
        conn.close()
    try:
        capability.sync_ledger()
    except Exception as exc:  # Ollama may be down; the ledger records that truthfully
        print(f"[PODA capability ledger] startup refresh failed: {exc}")
    print(f"[PODA] v{config.VERSION} build {config.BUILD_ID} data={config.DATA_DIR} encryption={STARTUP_REPORT.get('encryption_status', {}).get('database_encrypted_on_disk')}")
    yield


app = FastAPI(title=config.APP_NAME, version=config.VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(security_middleware)

for r in (runtime_router, memory_router, chat_router, agent_router, planner_router, privacy_router, notion_router, email_router, software_router):
    app.include_router(r)


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/memory-viewer")
@app.get("/memory-viewer/")
def memory_viewer():
    """The single canonical PODA memory editor. No legacy modal viewer is served."""
    return FileResponse(config.STATIC_DIR / "memory.html")


@app.get("/favicon.ico")
def favicon():
    icon = config.STATIC_DIR / "favicon.svg"
    if icon.exists():
        return FileResponse(icon, media_type="image/svg+xml")
    return Response(status_code=204)


@app.get("/system/startup")
def startup_report():
    return STARTUP_REPORT


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
