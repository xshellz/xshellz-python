from __future__ import annotations

from typing import Any

import pytest


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
