"""Start the authenticated, short-lived backend owned by PODA Desktop."""
from __future__ import annotations

import hmac
import json
import os
import socket
from threading import Thread


def main() -> None:
    token = os.environ.pop("PODA_DESKTOP_TOKEN", "")
    if not token:
        raise SystemExit("Open the PODA desktop app to start its backend.")

    with socket.socket() as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        os.environ["PODA_PORT"] = str(port)
        os.environ["PODA_CREDENTIAL_NAMESPACE"] = "PODA-Windows" if os.name == "nt" else "PODA"

        import uvicorn
        from fastapi.responses import JSONResponse
        from poda_app.main import app
        from poda_app.runtime.instance import claim_data_directory

        expected = token.encode("utf-8")

        @app.middleware("http")
        async def require_desktop(request, call_next):
            supplied = request.headers.get("x-poda-desktop", "").encode("utf-8")
            if not hmac.compare_digest(supplied, expected):
                return JSONResponse({"detail": "This backend belongs to the desktop app."}, status_code=401)
            return await call_next(request)

        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, access_log=False,
            proxy_headers=False, server_header=False, timeout_graceful_shutdown=8,
        ))

        def watch_parent() -> None:
            try:
                while os.read(0, 1024):
                    pass
            except OSError:
                pass
            finally:
                server.should_exit = True

        with claim_data_directory():
            print("PODA_DESKTOP " + json.dumps({"port": port}), flush=True)
            Thread(target=watch_parent, name="poda-desktop-parent", daemon=True).start()
            server.run(sockets=[listener])
        if not server.started:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
