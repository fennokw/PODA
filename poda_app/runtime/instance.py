"""Only one scheduler and API process may serve a PODA data directory."""
from contextlib import contextmanager
import os

from . import config

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def claim_data_directory():
    config.ensure_dirs()
    lock = os.open(config.DATA_DIR / ".server.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            if os.name == "nt":
                msvcrt.locking(lock, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("This PODA data directory is already in use. Quit the other PODA server and retry.") from exc
        yield
    finally:
        os.close(lock)
