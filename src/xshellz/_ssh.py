"""SSH/SFTP data plane, behind a small transport interface for testability.

The real implementation (:class:`ParamikoTransport`) speaks SSH as ``root`` to
the sandbox. Host keys are auto-accepted: sandboxes are throwaway boxes whose
host keys are generated at spawn time, so there is nothing to pin against.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import paramiko

from .errors import CommandTimeoutError, XshellzError
from .models import CommandResult

__all__ = ["ParamikoTransport", "SshTransport", "build_shell_command"]

_CHUNK_SIZE = 32768
_POLL_INTERVAL = 0.02


@runtime_checkable
class SshTransport(Protocol):
    """Minimal data-plane surface the :class:`~xshellz.Sandbox` needs."""

    def exec(
        self,
        command: str,
        timeout: float | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> CommandResult: ...

    def read_file(self, path: str) -> bytes: ...

    def write_file(self, path: str, data: bytes) -> None: ...

    def upload(self, local_path: str, remote_path: str) -> None: ...

    def download(self, remote_path: str, local_path: str) -> None: ...

    def close(self) -> None: ...


def build_shell_command(
    command: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Wrap a command with optional ``cd`` and environment exports.

    Environment variable names are validated (sshd rarely honours
    ``AcceptEnv``, so variables are exported in the remote shell instead).
    """
    parts: list[str] = []
    if env:
        exports: list[str] = []
        for key, value in env.items():
            if not key.isidentifier():
                raise XshellzError(f"Invalid environment variable name: {key!r}")
            exports.append(f"{key}={shlex.quote(value)}")
        parts.append("export " + " ".join(exports))
    if cwd:
        parts.append(f"cd {shlex.quote(cwd)}")
    parts.append(command)
    return " && ".join(parts)


class ParamikoTransport:
    """Real SSH transport: exec over a session channel, files over SFTP."""

    def __init__(
        self,
        host: str,
        port: int,
        pkey: paramiko.PKey,
        username: str = "root",
        connect_timeout: float = 30.0,
    ) -> None:
        self._client = paramiko.SSHClient()
        # Auto-accept host keys: sandbox host keys are minted at spawn time,
        # so there is no out-of-band fingerprint to verify against.
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname=host,
            port=port,
            username=username,
            pkey=pkey,
            look_for_keys=False,
            allow_agent=False,
            timeout=connect_timeout,
        )
        self._sftp: paramiko.SFTPClient | None = None

    def exec(
        self,
        command: str,
        timeout: float | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> CommandResult:
        transport = self._client.get_transport()
        if transport is None:
            raise XshellzError("SSH connection is closed.")

        channel = transport.open_session()
        channel.exec_command(command)

        deadline = time.monotonic() + timeout if timeout is not None else None
        stdout_buf = bytearray()
        stderr_buf = bytearray()

        try:
            while True:
                drained = self._drain(channel, stdout_buf, stderr_buf, on_stdout, on_stderr)
                if channel.exit_status_ready() and not drained:
                    # Final drain: output can land after the exit status.
                    while self._drain(channel, stdout_buf, stderr_buf, on_stdout, on_stderr):
                        pass
                    break
                if deadline is not None and time.monotonic() > deadline:
                    raise CommandTimeoutError(
                        f"Command did not finish within {timeout} seconds: {command!r}"
                    )
                if not drained:
                    time.sleep(_POLL_INTERVAL)

            exit_code = channel.recv_exit_status()
        finally:
            channel.close()

        return CommandResult(
            stdout=stdout_buf.decode("utf-8", errors="replace"),
            stderr=stderr_buf.decode("utf-8", errors="replace"),
            exit_code=exit_code,
        )

    @staticmethod
    def _drain(
        channel: paramiko.Channel,
        stdout_buf: bytearray,
        stderr_buf: bytearray,
        on_stdout: Callable[[str], None] | None,
        on_stderr: Callable[[str], None] | None,
    ) -> bool:
        """Pull any pending output off the channel; return True if any read."""
        drained = False
        while channel.recv_ready():
            chunk = channel.recv(_CHUNK_SIZE)
            if not chunk:
                break
            drained = True
            stdout_buf.extend(chunk)
            if on_stdout is not None:
                on_stdout(chunk.decode("utf-8", errors="replace"))
        while channel.recv_stderr_ready():
            chunk = channel.recv_stderr(_CHUNK_SIZE)
            if not chunk:
                break
            drained = True
            stderr_buf.extend(chunk)
            if on_stderr is not None:
                on_stderr(chunk.decode("utf-8", errors="replace"))
        return drained

    def _get_sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            self._sftp = self._client.open_sftp()
        return self._sftp

    def read_file(self, path: str) -> bytes:
        with self._get_sftp().open(path, "rb") as handle:
            return handle.read()

    def write_file(self, path: str, data: bytes) -> None:
        with self._get_sftp().open(path, "wb") as handle:
            handle.write(data)

    def upload(self, local_path: str, remote_path: str) -> None:
        self._get_sftp().put(local_path, remote_path)

    def download(self, remote_path: str, local_path: str) -> None:
        self._get_sftp().get(remote_path, local_path)

    def close(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        self._client.close()
