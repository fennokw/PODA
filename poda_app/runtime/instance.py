"""Only one scheduler and API process may serve a PODA data directory."""
from contextlib import contextmanager
import errno
import os
import time

from . import config

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class DataDirectoryInUse(RuntimeError):
    pass


@contextmanager
def claim_data_directory(*, wait_seconds: float = 0):
    config.ensure_dirs()
    lock = os.open(config.DATA_DIR / ".server.lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                if os.name == "nt":
                    msvcrt.locking(lock, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if not isinstance(exc, BlockingIOError) and exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DataDirectoryInUse(
                        "This data folder is already in use by another PODA server. Quit the other PODA app "
                        "or stop its local server, then open PODA again."
                    ) from exc
                time.sleep(min(0.2, remaining))
        yield
    finally:
        os.close(lock)
