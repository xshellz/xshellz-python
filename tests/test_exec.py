"""Data-plane tests: Sandbox exec/files against a fake SSH transport. No network."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from xshellz import CommandResult, Sandbox, SandboxNotRunningError, XshellzError
from xshellz._http import ApiClient
from xshellz._ssh import SshTransport, build_shell_command
from xshellz.models import SandboxInfo

from .conftest import shell_payload


class FakeTransport:
    """In-memory stand-in for the SSH data plane."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}
        self.closed = False
        self.next_result = CommandResult(stdout="out", stderr="", exit_code=0)
        self.stream_chunks: list[tuple[str, str]] = []  # (stream, chunk)

    def exec(
        self,
        command: str,
        timeout: float | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> CommandResult:
        self.commands.append(command)
        for stream, chunk in self.stream_chunks:
            if stream == "stdout" and on_stdout is not None:
                on_stdout(chunk)
            if stream == "stderr" and on_stderr is not None:
                on_stderr(chunk)
        return self.next_result

    def read_file(self, path: str) -> bytes:
        return self.files[path]

    def write_file(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def upload(self, local_path: str, remote_path: str) -> None:
        with open(local_path, "rb") as handle:
            self.files[remote_path] = handle.read()

    def download(self, remote_path: str, local_path: str) -> None:
        with open(local_path, "wb") as handle:
            handle.write(self.files[remote_path])

    def close(self) -> None:
        self.closed = True


def make_sandbox(
    fake: FakeTransport | None,
    status: str = "running",
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> Sandbox:
    handler = handler or (lambda _req: httpx.Response(200, json={"deleted": True}))
    return Sandbox(
        info=SandboxInfo.from_api(shell_payload(status=status)),
        api=ApiClient(api_key="k", transport=httpx.MockTransport(handler)),
        transport=fake,
    )


def test_fake_transport_satisfies_the_protocol() -> None:
    assert isinstance(FakeTransport(), SshTransport)


def test_run_returns_command_result_and_nonzero_exit_does_not_raise() -> None:
    fake = FakeTransport()
    fake.next_result = CommandResult(stdout="", stderr="boom", exit_code=2)
    sbx = make_sandbox(fake)

    result = sbx.run("false")

    assert result.exit_code == 2
    assert result.stderr == "boom"
    assert result.ok is False
    assert fake.commands == ["false"]


def test_run_wraps_cwd_and_env() -> None:
    fake = FakeTransport()
    sbx = make_sandbox(fake)

    sbx.run("make build", cwd="/srv/app dir", env={"FOO": "a b", "BAR": "1"})

    assert fake.commands == ["export FOO='a b' BAR=1 && cd '/srv/app dir' && make build"]


def test_run_rejects_invalid_env_names() -> None:
    sbx = make_sandbox(FakeTransport())
    with pytest.raises(XshellzError, match="environment variable name"):
        sbx.run("true", env={"BAD-NAME": "x"})


def test_run_streams_to_callbacks() -> None:
    fake = FakeTransport()
    fake.stream_chunks = [("stdout", "line1\n"), ("stderr", "warn\n"), ("stdout", "line2\n")]
    fake.next_result = CommandResult(stdout="line1\nline2\n", stderr="warn\n", exit_code=0)
    sbx = make_sandbox(fake)

    out: list[str] = []
    err: list[str] = []
    result = sbx.run("build", on_stdout=out.append, on_stderr=err.append)

    assert out == ["line1\n", "line2\n"]
    assert err == ["warn\n"]
    assert result.stdout == "line1\nline2\n"


def test_file_roundtrip_and_upload_download(tmp_path) -> None:
    fake = FakeTransport()
    sbx = make_sandbox(fake)

    sbx.write_file("/tmp/a.bin", b"\x00\x01data")
    assert sbx.read_file("/tmp/a.bin") == b"\x00\x01data"

    local = tmp_path / "local.txt"
    local.write_bytes(b"hello")
    sbx.upload(str(local), "/tmp/remote.txt")
    assert fake.files["/tmp/remote.txt"] == b"hello"

    out = tmp_path / "out.txt"
    sbx.download("/tmp/remote.txt", str(out))
    assert out.read_bytes() == b"hello"


def test_run_on_non_running_sandbox_raises() -> None:
    sbx = make_sandbox(None, status="stopped")
    with pytest.raises(SandboxNotRunningError, match="stopped"):
        sbx.run("true")


def test_run_without_private_key_raises() -> None:
    sbx = make_sandbox(None, status="running")  # no transport, no key
    with pytest.raises(XshellzError, match="private key"):
        sbx.run("true")


def test_context_exit_kills_the_sandbox() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"deleted": True})

    fake = FakeTransport()
    with make_sandbox(fake, handler=handler) as sbx:
        sbx.run("true")

    assert calls == ["DELETE /v1/shells/agent/11111111-2222-3333-4444-555555555555"]
    assert fake.closed is True


def test_detach_skips_the_kill() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json={"deleted": True})

    fake = FakeTransport()
    with make_sandbox(fake, handler=handler) as sbx:
        sbx.detach()

    assert calls == []
    assert fake.closed is True  # SSH still closed; box kept alive


def test_build_shell_command_plain() -> None:
    assert build_shell_command("echo hi") == "echo hi"
    assert build_shell_command("echo hi", cwd="/tmp") == "cd /tmp && echo hi"
