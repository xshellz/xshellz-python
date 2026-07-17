"""ParamikoTransport unit tests with faked paramiko internals. No network."""

from __future__ import annotations

import io
from typing import Any

import pytest

from xshellz import CommandTimeoutError, XshellzError
from xshellz._ssh import ParamikoTransport


class FakeChannel:
    """Feeds queued stdout/stderr chunks, then reports the exit status."""

    def __init__(self, stdout: list[bytes], stderr: list[bytes], exit_code: int) -> None:
        self._stdout = list(stdout)
        self._stderr = list(stderr)
        self._exit_code = exit_code
        self.exec_commands: list[str] = []
        self.closed = False
        self.never_exits = False

    def exec_command(self, command: str) -> None:
        self.exec_commands.append(command)

    def recv_ready(self) -> bool:
        return bool(self._stdout)

    def recv(self, _size: int) -> bytes:
        return self._stdout.pop(0)

    def recv_stderr_ready(self) -> bool:
        return bool(self._stderr)

    def recv_stderr(self, _size: int) -> bytes:
        return self._stderr.pop(0)

    def exit_status_ready(self) -> bool:
        if self.never_exits:
            return False
        return not self._stdout and not self._stderr

    def recv_exit_status(self) -> int:
        return self._exit_code

    def close(self) -> None:
        self.closed = True


class FakeSshTransport:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel

    def open_session(self) -> FakeChannel:
        return self._channel


class FakeSftpHandle(io.BytesIO):
    def __init__(self, files: dict[str, bytes], path: str, mode: str) -> None:
        super().__init__(files.get(path, b"") if "r" in mode else b"")
        self._files = files
        self._path = path
        self._mode = mode

    def __enter__(self) -> FakeSftpHandle:
        return self

    def __exit__(self, *_exc: Any) -> None:
        if "w" in self._mode:
            self._files[self._path] = self.getvalue()


class FakeSftp:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.puts: list[tuple[str, str]] = []
        self.gets: list[tuple[str, str]] = []
        self.closed = False

    def open(self, path: str, mode: str) -> FakeSftpHandle:
        return FakeSftpHandle(self.files, path, mode)

    def put(self, local: str, remote: str) -> None:
        self.puts.append((local, remote))

    def get(self, remote: str, local: str) -> None:
        self.gets.append((remote, local))

    def close(self) -> None:
        self.closed = True


class FakeSshClient:
    def __init__(self, channel: FakeChannel | None, sftp: FakeSftp | None = None) -> None:
        self._transport = FakeSshTransport(channel) if channel is not None else None
        self._sftp = sftp or FakeSftp()
        self.closed = False

    def get_transport(self) -> FakeSshTransport | None:
        return self._transport

    def open_sftp(self) -> FakeSftp:
        return self._sftp

    def close(self) -> None:
        self.closed = True


def make_transport(
    channel: FakeChannel | None, sftp: FakeSftp | None = None
) -> tuple[ParamikoTransport, FakeSshClient]:
    """Build a ParamikoTransport without connecting (bypass __init__)."""
    transport = object.__new__(ParamikoTransport)
    client = FakeSshClient(channel, sftp)
    transport._client = client
    transport._sftp = None
    return transport, client


def test_exec_collects_stdout_stderr_and_exit_code_with_streaming() -> None:
    channel = FakeChannel([b"one", b"two"], [b"warn"], exit_code=3)
    transport, _ = make_transport(channel)

    out: list[str] = []
    result = transport.exec("do-thing", on_stdout=out.append)

    assert channel.exec_commands == ["do-thing"]
    assert result.stdout == "onetwo"
    assert result.stderr == "warn"
    assert result.exit_code == 3
    assert out == ["one", "two"]
    assert channel.closed is True


def test_exec_times_out_and_closes_the_channel() -> None:
    channel = FakeChannel([], [], exit_code=0)
    channel.never_exits = True
    transport, _ = make_transport(channel)

    with pytest.raises(CommandTimeoutError, match="did not finish"):
        transport.exec("sleep 999", timeout=0.05)
    assert channel.closed is True


def test_exec_on_closed_connection_raises() -> None:
    transport, _ = make_transport(None)

    with pytest.raises(XshellzError, match="closed"):
        transport.exec("true")


def test_sftp_file_helpers_and_close() -> None:
    sftp = FakeSftp()
    transport, client = make_transport(FakeChannel([], [], 0), sftp)

    transport.write_file("/tmp/a", b"data")
    assert transport.read_file("/tmp/a") == b"data"

    transport.upload("local.txt", "/tmp/b")
    transport.download("/tmp/a", "out.txt")
    assert sftp.puts == [("local.txt", "/tmp/b")]
    assert sftp.gets == [("/tmp/a", "out.txt")]

    transport.close()
    assert sftp.closed is True
    assert client.closed is True
