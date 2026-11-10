"""Stop the native child if its owning PODA process exits unexpectedly."""
from __future__ import annotations

import os
import subprocess
import sys
from threading import Event, Thread


def main() -> int:
    child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)
    parent_gone = Event()

    def watch_parent() -> None:
        os.read(0, 1)
        parent_gone.set()

    Thread(target=watch_parent, daemon=True, name="poda-owner-pipe").start()
    try:
        while child.poll() is None:
            if parent_gone.wait(.2):
                break
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
