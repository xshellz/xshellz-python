"""Wire tests for stats / procs / restart / terminal_url / boxfile."""

from __future__ import annotations

import json
from typing import Any

import httpx

from xshellz import ProcessInfo, Sandbox

from .conftest import FakeTransport, make_sandbox, shell_payload

UUID = "11111111-2222-3333-4444-555555555555"

STATS_PAYLOAD: dict[str, Any] = {
    "mem_used_mb": 123,
    "mem_limit_mb": 512,
    "mem_allowed_mb": 512,
    "cpu_percent": 42.5,
    "cpu_allowed_vcpus": 1.0,
    "cpu_throttled_periods": 7,
    "pids_current": 12,
    "pids_allowed": 128,
    "disk_used_mb": 900,
    "disk_allowed_mb": 5120,
    "net_rx_mb": 10,
    "net_tx_mb": 2,
    "blk_read_mb": 30,
    "blk_write_mb": 4,
}

PROCS_PAYLOAD: dict[str, Any] = {
    "procs": [
        {"pid": 1, "comm": "bash", "cpu": 0.1, "mem": 0.5},
        {"pid": 42, "comm": "node", "cpu": 55.0, "mem": 12.0},
    ],
    "sessions": 2,
    "agents": ["claude"],
    "disk_used_mb": 900,
    "disk_allowed_mb": 5120,
}


def test_stats_mirrors_the_wire_fields() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=STATS_PAYLOAD)

    sbx = make_sandbox(FakeTransport(), handler=handler)
    stats = sbx.stats()

    assert seen == [f"GET /v1/shells/agent/{UUID}/stats"]
    assert stats.mem_used_mb == 123
    assert stats.mem_allowed_mb == 512
    assert stats.cpu_percent == 42.5
    assert stats.cpu_allowed_vcpus == 1.0
    assert stats.cpu_throttled_periods == 7
    assert stats.pids_current == 12
    assert stats.pids_allowed == 128
    assert stats.disk_used_mb == 900
    assert stats.disk_allowed_mb == 5120
    assert stats.net_rx_mb == 10
    assert stats.net_tx_mb == 2
    assert stats.blk_read_mb == 30
    assert stats.blk_write_mb == 4
    sbx.close()


def test_procs_parses_processes_sessions_agents_and_disk() -> None:
    sbx = make_sandbox(
        FakeTransport(), handler=lambda _req: httpx.Response(200, json=PROCS_PAYLOAD)
    )
    procs = sbx.procs()

    assert procs.procs == [
        ProcessInfo(pid=1, comm="bash", cpu=0.1, mem=0.5),
        ProcessInfo(pid=42, comm="node", cpu=55.0, mem=12.0),
    ]
    assert procs.sessions == 2
    assert procs.agents == ["claude"]
    assert procs.disk_used_mb == 900
    assert procs.disk_allowed_mb == 5120
    sbx.close()


def test_restart_posts_updates_info_and_resets_the_transport() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=shell_payload(ssh_port=42099))

    fake = FakeTransport()
    sbx = make_sandbox(fake, handler=handler)
    sbx.restart()

    assert seen == [f"POST /v1/shells/agent/{UUID}/restart"]
    assert sbx.ssh_port == 42099  # refreshed from the response
    assert fake.closed is True  # stale SSH connection dropped
    sbx.close()


def test_terminal_url_returns_the_freshly_minted_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(
            200, json={"url": "https://shellus1.xshellz.com:8443/t/abc?token=1752.deadbeef"}
        )

    sbx = make_sandbox(FakeTransport(), handler=handler)

    assert sbx.terminal_url() == "https://shellus1.xshellz.com:8443/t/abc?token=1752.deadbeef"
    assert seen == [f"GET /v1/shells/agent/{UUID}/terminal"]
    sbx.close()


def test_get_boxfile_reads_the_account_manifest() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"manifest": "apt: jq ripgrep\npip: httpx"})

    manifest = Sandbox.get_boxfile(api_key="k", http_transport=httpx.MockTransport(handler))

    assert seen == ["GET /v1/shells/agent/boxfile"]
    assert manifest == "apt: jq ripgrep\npip: httpx"


def test_get_boxfile_returns_none_when_unset() -> None:
    manifest = Sandbox.get_boxfile(
        api_key="k",
        http_transport=httpx.MockTransport(
            lambda _req: httpx.Response(200, json={"manifest": None})
        ),
    )
    assert manifest is None


def test_set_boxfile_puts_the_manifest_and_returns_it_as_stored() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["call"] = f"{request.method} {request.url.path}"
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"manifest": "apt: jq"})

    stored = Sandbox.set_boxfile(
        "apt: jq", api_key="k", http_transport=httpx.MockTransport(handler)
    )

    assert seen["call"] == "PUT /v1/shells/agent/boxfile"
    assert seen["body"] == {"manifest": "apt: jq"}
    assert stored == "apt: jq"


def test_set_boxfile_none_clears_the_manifest() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"manifest": None}
        return httpx.Response(200, json={"manifest": None})

    assert (
        Sandbox.set_boxfile(None, api_key="k", http_transport=httpx.MockTransport(handler)) is None
    )
