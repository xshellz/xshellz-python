from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from xshellz import CommandResult, Sandbox
from xshellz._http import ApiClient
from xshellz.models import SandboxInfo


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never pick up a developer's real credentials or URL."""
    monkeypatch.delenv("XSHELLZ_API_KEY", raising=False)
    monkeypatch.delenv("XSHELLZ_API_URL", raising=False)


def shell_payload(**overrides: Any) -> dict[str, Any]:
    """A canonical AgentShellResponse wire payload (snake_case)."""
    payload: dict[str, Any] = {
        "uuid": "11111111-2222-3333-4444-555555555555",
        "name": "agent-shell",
        "status": "running",
        "ssh_command": "ssh -p 42001 root@shellus1.xshellz.com",
        "ssh_host": "shellus1.xshellz.com",
        "ssh_port": 42001,
        "web_terminal_ready": True,
        "trial_ends_at": None,
        "always_on": True,
        "trial_hours_remaining": 0.0,
        "spawned_at": "2026-07-16T12:00:00+00:00",
        "created_at": "2026-07-16T12:00:00+00:00",
        "isolation": "runsc",
        "gvisor": True,
    }
    payload.update(overrides)
    return payload


class FakeTransport:
    """In-memory stand-in for the SSH data plane."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}
        self.closed = False
        self.next_result = CommandResult(stdout="out", stderr="", exit_code=0)
        self.stream_chunks: list[tuple[str, str]] = []  # (stream, chunk)
        # Optional per-command behaviour; wins over next_result when set.
        self.exec_handler: Callable[[str], CommandResult] | None = None

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
        if self.exec_handler is not None:
            return self.exec_handler(command)
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
