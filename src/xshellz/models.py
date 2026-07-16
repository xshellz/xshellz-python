"""Data objects returned by the xShellz SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["CommandResult", "SandboxInfo"]


@dataclass(frozen=True)
class CommandResult:
    """The outcome of a single ``Sandbox.run()`` invocation.

    A non-zero ``exit_code`` does NOT raise - it is data, exactly like a local
    subprocess call.
    """

    stdout: str
    stderr: str
    exit_code: int

    @property
    def ok(self) -> bool:
        """True when the command exited with status 0."""
        return self.exit_code == 0


@dataclass(frozen=True)
class SandboxInfo:
    """A sandbox as reported by the control plane (snake_case wire shape)."""

    uuid: str
    name: str
    status: str
    ssh_command: str | None
    ssh_host: str | None
    ssh_port: int | None
    web_terminal_ready: bool
    always_on: bool
    trial_hours_remaining: float
    spawned_at: str | None
    created_at: str | None
    isolation: str | None
    gvisor: bool

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> SandboxInfo:
        """Build a ``SandboxInfo`` from the API's JSON payload.

        Tolerant of missing keys so a newer/older API version never breaks
        deserialization.
        """
        port = payload.get("ssh_port")
        return cls(
            uuid=str(payload.get("uuid", "")),
            name=str(payload.get("name", "")),
            status=str(payload.get("status", "")),
            ssh_command=payload.get("ssh_command"),
            ssh_host=payload.get("ssh_host"),
            ssh_port=int(port) if port is not None else None,
            web_terminal_ready=bool(payload.get("web_terminal_ready", False)),
            always_on=bool(payload.get("always_on", False)),
            trial_hours_remaining=float(payload.get("trial_hours_remaining", 0.0)),
            spawned_at=payload.get("spawned_at"),
            created_at=payload.get("created_at"),
            isolation=payload.get("isolation"),
            gvisor=bool(payload.get("gvisor", False)),
        )
