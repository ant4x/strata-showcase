# Excerpt from Strata: strata/ops/lock.py (full file). Source is private.
"""One running copy per bot token (and one worker per data folder).

Two pollers on the same Telegram token make Telegram answer 409 Conflict to
one of them at random, and the bot goes quiet without an obvious error. So each
process holds a lock file in `DATA_DIR/locks/` for as long as it runs:

- the file is locked with `fcntl.flock`, which the kernel releases when the
  process dies, however it dies;
- it holds the pid, so a refused process can say who has the lock;
- a lock file left by a process that is gone is simply taken over, with a log
  line naming the old pid;
- the bot's file is named by a hash of the token, never the token itself.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import logging
import os
import time
from pathlib import Path
from types import TracebackType

log = logging.getLogger(__name__)

LOCKS_DIR = "locks"
POLL_SECONDS = 0.1
# Exit code of a process that refuses to start because another one holds its
# lock. The service units list it in RestartPreventExitStatus= (deploy/).
EXIT_ALREADY_RUNNING = 3


class AlreadyRunning(Exception):
    """Another live process holds the lock."""

    def __init__(self, path: Path, pid: int | None) -> None:
        holder = f"pid {pid}" if pid is not None else "a process that has not written its pid"
        super().__init__(f"{path.name} is held by {holder}")
        self.path = path
        self.pid = pid


def token_key(token: str) -> str:
    """A short name for a bot token that does not reveal it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None
        self.took_over: int | None = None  # pid of a dead holder whose file was taken over

    @classmethod
    def for_bot(cls, data_dir: Path, token: str) -> InstanceLock:
        return cls(data_dir / LOCKS_DIR / f"bot-{token_key(token)}.lock")

    @classmethod
    def for_process(cls, data_dir: Path, name: str) -> InstanceLock:
        return cls(data_dir / LOCKS_DIR / f"{name}.lock")

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, *, wait: float = 0.0) -> None:
        """Take the lock, waiting up to `wait` seconds. Raises `AlreadyRunning`."""
        if self._fd is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + wait
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                        raise
                    if time.monotonic() >= deadline:
                        raise AlreadyRunning(self.path, _read_pid(fd)) from None
                    time.sleep(POLL_SECONDS)
            previous = _read_pid(fd)
            if previous is not None and previous != os.getpid():
                self.took_over = previous
                log.warning(
                    "Took over %s: pid %s held it and is no longer running", self.path, previous
                )
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.ftruncate(fd, 0)  # no pid left behind for the next start to report
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def _read_pid(fd: int) -> int | None:
    raw = os.pread(fd, 32, 0).decode("ascii", "replace").strip()
    return int(raw) if raw.isdigit() else None
