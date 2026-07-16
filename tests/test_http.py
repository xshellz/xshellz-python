"""Control-plane tests against a mocked httpx transport. No network."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from xshellz import (
    ApiError,
    AuthError,
    QuotaError,
    Sandbox,
    SandboxNotRunningError,
)
from xshellz._keys import generate_keypair

from .conftest import shell_payload

PUBLIC_KEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/=]+( .*)?$")


def json_response(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


# --------------------------------------------------------------------- #
# create()
# --------------------------------------------------------------------- #


def test_create_posts_generated_public_key_and_returns_running_sandbox() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return json_response(200, shell_payload(name="my-box"))

    sbx = Sandbox.create(
        name="my-box",
        api_key="pat-123",
        api_url="https://api.staging.example/v1",
        http_transport=httpx.MockTransport(handler),
    )

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/shells/agent"
    assert seen["auth"] == "Bearer pat-123"
    assert seen["body"]["name"] == "my-box"
    assert PUBLIC_KEY_RE.match(seen["body"]["ssh_public_key"])

    assert sbx.uuid == "11111111-2222-3333-4444-555555555555"
    assert sbx.status == "running"
    assert sbx.ssh_host == "shellus1.xshellz.com"
    assert sbx.ssh_port == 42001
    assert sbx.ssh_command == "ssh -p 42001 root@shellus1.xshellz.com"
    assert sbx.name == "my-box"
    assert sbx.private_key is not None
    assert sbx.private_key_openssh is not None
    assert "OPENSSH PRIVATE KEY" in sbx.private_key_openssh
    sbx.close()


def test_create_omits_name_when_not_given() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "name" not in body
        return json_response(200, shell_payload())

    Sandbox.create(api_key="k", http_transport=httpx.MockTransport(handler)).close()


def test_missing_api_key_raises_helpful_auth_error() -> None:
    with pytest.raises(AuthError, match="XSHELLZ_API_KEY"):
        Sandbox.create()


def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XSHELLZ_API_KEY", "env-key")
    monkeypatch.setenv("XSHELLZ_API_URL", "https://api.staging.example/v1")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer env-key"
        assert request.url.host == "api.staging.example"
        return json_response(200, [])

    assert Sandbox.list(http_transport=httpx.MockTransport(handler)) == []


def test_explicit_args_beat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XSHELLZ_API_KEY", "env-key")
    monkeypatch.setenv("XSHELLZ_API_URL", "https://env-host.example/v1")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer arg-key"
        assert request.url.host == "arg-host.example"
        return json_response(200, [])

    Sandbox.list(
        api_key="arg-key",
        api_url="https://arg-host.example/v1",
        http_transport=httpx.MockTransport(handler),
    )


# --------------------------------------------------------------------- #
# Error mapping (guard chain -> typed errors)
# --------------------------------------------------------------------- #


def _create_failing_with(status: int, body: Any) -> None:
    Sandbox.create(
        api_key="k",
        http_transport=httpx.MockTransport(lambda _req: json_response(status, body)),
    )


def test_401_maps_to_auth_error() -> None:
    with pytest.raises(AuthError, match="401"):
        _create_failing_with(401, {"message": "Unauthenticated."})


def test_403_quota_limit_maps_to_quota_error() -> None:
    with pytest.raises(QuotaError, match="agent shell limit"):
        _create_failing_with(
            403, {"message": "You've reached your plan's agent shell limit (1)."}
        )


def test_403_entitlement_maps_to_quota_error() -> None:
    with pytest.raises(QuotaError, match="does not include agent shells"):
        _create_failing_with(
            403, {"message": "Your plan does not include agent shells. Upgrade to add one."}
        )


def test_403_verification_required_maps_to_auth_error() -> None:
    with pytest.raises(AuthError, match="verification"):
        _create_failing_with(
            403,
            {
                "error": "verification_required",
                "message": "Verify your account with a card (free - nothing is charged) "
                "to create a shell.",
            },
        )


def test_403_limited_preview_maps_to_auth_error() -> None:
    with pytest.raises(AuthError, match="limited preview"):
        _create_failing_with(403, {"message": "Agent Shell is in limited preview."})


def test_429_maps_to_api_error_with_status() -> None:
    with pytest.raises(ApiError) as excinfo:
        _create_failing_with(429, {"message": "Too Many Attempts."})
    assert excinfo.value.status_code == 429


def test_503_maps_to_api_error_with_body() -> None:
    with pytest.raises(ApiError) as excinfo:
        _create_failing_with(503, {"message": "Agent shells are not available yet."})
    assert excinfo.value.status_code == 503
    assert excinfo.value.body == {"message": "Agent shells are not available yet."}


# --------------------------------------------------------------------- #
# list() / connect()
# --------------------------------------------------------------------- #


def test_list_parses_the_bare_top_level_array() -> None:
    payload = [shell_payload(), shell_payload(uuid="deadbeef", name="second", status="stopped")]

    infos = Sandbox.list(
        api_key="k", http_transport=httpx.MockTransport(lambda _req: json_response(200, payload))
    )

    assert len(infos) == 2
    assert infos[0].uuid == "11111111-2222-3333-4444-555555555555"
    assert infos[0].gvisor is True
    assert infos[1].uuid == "deadbeef"
    assert infos[1].status == "stopped"


def test_connect_finds_the_sandbox_in_the_list() -> None:
    keypair = generate_keypair()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/shells/agent"
        return json_response(200, [shell_payload()])

    sbx = Sandbox.connect(
        "11111111-2222-3333-4444-555555555555",
        private_key=keypair.private_key_openssh,
        api_key="k",
        http_transport=httpx.MockTransport(handler),
    )

    assert sbx.uuid == "11111111-2222-3333-4444-555555555555"
    assert sbx.status == "running"
    assert sbx.private_key is not None
    sbx.close()


def test_connect_unknown_uuid_raises() -> None:
    keypair = generate_keypair()

    with pytest.raises(SandboxNotRunningError, match="not found"):
        Sandbox.connect(
            "no-such-uuid",
            private_key=keypair.private_key,
            api_key="k",
            http_transport=httpx.MockTransport(lambda _req: json_response(200, [])),
        )


# --------------------------------------------------------------------- #
# kill() / start()
# --------------------------------------------------------------------- #


def _running_sandbox(handler: Any) -> Sandbox:
    from xshellz._http import ApiClient
    from xshellz.models import SandboxInfo

    return Sandbox(
        info=SandboxInfo.from_api(shell_payload()),
        api=ApiClient(api_key="k", transport=httpx.MockTransport(handler)),
    )


def test_kill_issues_delete_and_is_idempotent() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return json_response(200, {"deleted": True})

    sbx = _running_sandbox(handler)
    sbx.kill()
    sbx.kill()  # second call is a no-op

    assert calls == ["DELETE /v1/shells/agent/11111111-2222-3333-4444-555555555555"]
    sbx.close()


def test_kill_swallows_404_already_gone() -> None:
    sbx = _running_sandbox(
        lambda _req: json_response(404, {"message": "Agent shell not found."})
    )
    sbx.kill()  # must not raise
    sbx.close()


def test_start_posts_and_updates_info() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/start")
        return json_response(200, shell_payload(status="running"))

    sbx = _running_sandbox(handler)
    sbx.start()
    assert sbx.status == "running"
    sbx.close()


def test_start_404_maps_to_sandbox_not_running() -> None:
    sbx = _running_sandbox(
        lambda _req: json_response(404, {"message": "Stopped agent shell not found."})
    )
    with pytest.raises(SandboxNotRunningError):
        sbx.start()
    sbx.close()
