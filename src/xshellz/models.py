"""Data objects returned by the xShellz SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["CommandResult", "ProcessInfo", "SandboxInfo", "SandboxProcs", "SandboxStats"]


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


@dataclass(frozen=True)
class SandboxStats:
    """Live resource usage for a running sandbox (``Sandbox.stats()``).

    ``*_allowed_*`` fields are the plan ceilings; the ``used``/``current``
    fields are the live readings. Mirrors the control plane's snake_case wire
    shape exactly.
    """

    mem_used_mb: int
    mem_limit_mb: int
    mem_allowed_mb: int
    cpu_percent: float
    cpu_allowed_vcpus: float
    cpu_throttled_periods: int
    pids_current: int
    pids_allowed: int
    disk_used_mb: int
    disk_allowed_mb: int
    net_rx_mb: int
    net_tx_mb: int
    blk_read_mb: int
    blk_write_mb: int

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> SandboxStats:
        """Build from the API JSON payload; tolerant of missing keys."""
        return cls(
            mem_used_mb=int(payload.get("mem_used_mb", 0)),
            mem_limit_mb=int(payload.get("mem_limit_mb", 0)),
            mem_allowed_mb=int(payload.get("mem_allowed_mb", 0)),
            cpu_percent=float(payload.get("cpu_percent", 0.0)),
            cpu_allowed_vcpus=float(payload.get("cpu_allowed_vcpus", 0.0)),
            cpu_throttled_periods=int(payload.get("cpu_throttled_periods", 0)),
            pids_current=int(payload.get("pids_current", 0)),
            pids_allowed=int(payload.get("pids_allowed", 0)),
            disk_used_mb=int(payload.get("disk_used_mb", 0)),
            disk_allowed_mb=int(payload.get("disk_allowed_mb", 0)),
            net_rx_mb=int(payload.get("net_rx_mb", 0)),
            net_tx_mb=int(payload.get("net_tx_mb", 0)),
            blk_read_mb=int(payload.get("blk_read_mb", 0)),
            blk_write_mb=int(payload.get("blk_write_mb", 0)),
        )


@dataclass(frozen=True)
class ProcessInfo:
    """One process row from ``Sandbox.procs()``."""

    pid: int
    comm: str
    cpu: float
    mem: float

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ProcessInfo:
        return cls(
            pid=int(payload.get("pid", 0)),
            comm=str(payload.get("comm", "")),
            cpu=float(payload.get("cpu", 0.0)),
            mem=float(payload.get("mem", 0.0)),
        )


@dataclass(frozen=True)
class SandboxProcs:
    """Top processes + sessions + disk usage (``Sandbox.procs()``)."""

    procs: list[ProcessInfo]
    sessions: int
    agents: list[str]
    disk_used_mb: int
    disk_allowed_mb: int

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> SandboxProcs:
        """Build from the API JSON payload; tolerant of missing keys."""
        return cls(
            procs=[ProcessInfo.from_api(item) for item in payload.get("procs", [])],
            sessions=int(payload.get("sessions", 0)),
            agents=[str(agent) for agent in payload.get("agents", [])],
            disk_used_mb=int(payload.get("disk_used_mb", 0)),
            disk_allowed_mb=int(payload.get("disk_allowed_mb", 0)),
        )
