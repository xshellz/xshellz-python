"""Background jobs inside a sandbox: :meth:`xshellz.Sandbox.spawn` and friends.

A job is a ``nohup``-detached process whose output is redirected to
``~/.xshellz/jobs/<job_id>.log`` inside the box (with the PID recorded next to
it in ``<job_id>.pid``). Jobs survive the SSH connection - and your process -
ending; they do NOT survive the box stopping or restarting.
"""

from __future__ import annotations

import secrets
import shlex
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import XshellzError
from .keystore import sanitize_name

if TYPE_CHECKING:
    from .sandbox import Sandbox

__all__ = ["JobHandle", "JobInfo"]

JOBS_DIR = "~/.xshellz/jobs"

_STOP_POLL_INTERVAL = 0.2

_LIST_JOBS_SCRIPT = (
    f"cd {JOBS_DIR} 2>/dev/null || exit 0; "
    'for f in *.log; do [ -e "$f" ] || continue; '
    'id="${f%.log}"; pid="$(cat "$id.pid" 2>/dev/null)"; '
    'if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then running=1; else running=0; fi; '
    'echo "$id|$pid|$running"; done'
)


@dataclass(frozen=True)
class JobInfo:
    """One entry from :meth:`xshellz.Sandbox.jobs`."""

    id: str
    pid: int | None
    log_path: str
    running: bool


def new_job_id(name: str | None = None) -> str:
    """A short random job id, prefixed with the sanitized name when given."""
    prefix = sanitize_name(name) if name else "job"
    return f"{prefix}-{secrets.token_hex(4)}"


def build_spawn_command(command: str, job_id: str) -> str:
    """The remote shell line that detaches ``command`` and echoes its PID."""
    log_path = f"{JOBS_DIR}/{job_id}.log"
    pid_path = f"{JOBS_DIR}/{job_id}.pid"
    return (
        f"mkdir -p {JOBS_DIR} && "
        f"nohup bash -c {shlex.quote(command)} > {log_path} 2>&1 < /dev/null & "
        f"echo $! > {pid_path}; echo $!"
    )


def parse_jobs_output(stdout: str) -> list[JobInfo]:
    """Parse the ``id|pid|running`` lines emitted by the jobs listing script."""
    jobs: list[JobInfo] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.count("|") != 2:
            continue
        job_id, pid_text, running_text = line.split("|")
        jobs.append(
            JobInfo(
                id=job_id,
                pid=int(pid_text) if pid_text.isdigit() else None,
                log_path=f"{JOBS_DIR}/{job_id}.log",
                running=running_text == "1",
            )
        )
    return jobs


class JobHandle:
    """A handle to one background process started with ``Sandbox.spawn()``.

    Attributes:
        id: The job id (log/pid filename stem).
        pid: The process id inside the sandbox.
        log_path: Path (inside the sandbox) of the combined stdout+stderr log.
    """

    def __init__(self, sandbox: Sandbox, job_id: str, pid: int) -> None:
        self._sandbox = sandbox
        self.id = job_id
        self.pid = pid
        self.log_path = f"{JOBS_DIR}/{job_id}.log"

    def is_running(self) -> bool:
        """True while the process is alive (``kill -0`` probe)."""
        return self._sandbox.run(f"kill -0 {self.pid} 2>/dev/null").ok

    def logs(self, tail_lines: int = 100) -> str:
        """The last ``tail_lines`` lines of the job's combined output log."""
        if tail_lines < 1:
            raise XshellzError("tail_lines must be >= 1")
        # log_path is built from a sanitized job id, so it is shell-safe; it is
        # deliberately NOT quoted so the leading ~ expands remotely.
        return self._sandbox.run(f"tail -n {int(tail_lines)} {self.log_path}").stdout

    def stop(self, grace: float = 5.0) -> None:
        """Stop the job: SIGTERM, then SIGKILL if still alive after ``grace``s."""
        self._sandbox.run(f"kill {self.pid} 2>/dev/null")
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not self.is_running():
                return
            time.sleep(_STOP_POLL_INTERVAL)
        if self.is_running():
            self._sandbox.run(f"kill -9 {self.pid} 2>/dev/null")

    def __repr__(self) -> str:
        return f"<JobHandle id={self.id!r} pid={self.pid} log={self.log_path!r}>"
