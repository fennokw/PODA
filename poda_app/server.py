"""Launch PODA under a data-directory lease from the desktop or source launcher."""
from __future__ import annotations

import argparse
import json
import os
import socket
from threading import Thread


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", action="store_true", help="Use an Electron-owned ephemeral backend")
    desktop = parser.parse_args().desktop
    if desktop and not os.environ.get("PODA_DESKTOP_TOKEN"):
        raise SystemExit("Launch desktop mode through the PODA app.")

    # Hold the actual socket before reporting its port. Import config only
    # after setting the selected port so all origin checks use that address.
    with socket.socket() as listener:
        if not desktop:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0 if desktop else int(os.environ.get("PODA_PORT", "8787"))))
        listener.listen()
        port = listener.getsockname()[1]
        os.environ["PODA_PORT"] = str(port)

        import uvicorn
        from .main import app
        from .runtime.instance import claim_data_directory

        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, access_log=False,
            proxy_headers=False, server_header=False, timeout_graceful_shutdown=8,
        ))
        with claim_data_directory():
            if desktop:
                print("PODA_DESKTOP " + json.dumps({"port": port}), flush=True)

            if desktop and os.environ.get("PODA_DESKTOP_PARENT_PIPE") == "1":
                def watch_parent() -> None:
                    # The Electron-owned write end closes on crash or force quit.
                    while os.read(0, 1024):
                        pass
                    server.should_exit = True

                Thread(target=watch_parent, name="poda-desktop-parent", daemon=True).start()

            server.run(sockets=[listener])
        if not server.started:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
