"""get_or_create: the persistent named-sandbox flow (list -> create/attach)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from xshellz import Keystore, MissingKeyError, Sandbox, XshellzError
from xshellz._keys import generate_keypair

from .conftest import shell_payload


def json_response(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


def test_creates_when_name_not_found_and_persists_the_key(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return json_response(200, [shell_payload(name="other-box")])
        assert json.loads(request.content)["name"] == "my-box"
        return json_response(200, shell_payload(name="my-box"))

    sbx = Sandbox.get_or_create(
        "my-box",
        api_key="k",
        keystore=tmp_path,
        http_transport=httpx.MockTransport(handler),
    )

    assert calls == ["GET /v1/shells/agent", "POST /v1/shells/agent"]
    assert sbx.name == "my-box"
    assert sbx.private_key_openssh is not None
    assert Keystore(tmp_path).load("my-box") == sbx.private_key_openssh
    sbx.close()


def test_attaches_to_existing_running_box_using_the_keystore_key(tmp_path: Path) -> None:
    keypair = generate_keypair()
    Keystore(tmp_path).save("my-box", keypair.private_key_openssh)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(200, [shell_payload(name="my-box")])

    sbx = Sandbox.get_or_create(
        "my-box", api_key="k", keystore=tmp_path, http_transport=httpx.MockTransport(handler)
    )

    assert calls == ["GET"]  # no create, no start
    assert sbx.status == "running"
    assert sbx.private_key is not None
    assert sbx.private_key.get_base64() == keypair.private_key.get_base64()
    sbx.close()


def test_starts_a_stopped_box_before_returning(tmp_path: Path) -> None:
    keypair = generate_keypair()
    Keystore(tmp_path).save("my-box", keypair.private_key_openssh)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return json_response(200, [shell_payload(name="my-box", status="stopped")])
        return json_response(200, shell_payload(name="my-box", status="running"))

    sbx = Sandbox.get_or_create(
        "my-box", api_key="k", keystore=tmp_path, http_transport=httpx.MockTransport(handler)
    )

    assert calls == [
        "GET /v1/shells/agent",
        "POST /v1/shells/agent/11111111-2222-3333-4444-555555555555/start",
    ]
    assert sbx.status == "running"
    sbx.close()


def test_explicit_private_key_beats_the_keystore(tmp_path: Path) -> None:
    other = generate_keypair()
    Keystore(tmp_path).save("my-box", other.private_key_openssh)
    explicit = generate_keypair()

    sbx = Sandbox.get_or_create(
        "my-box",
        api_key="k",
        private_key=explicit.private_key_openssh,
        keystore=tmp_path,
        http_transport=httpx.MockTransport(
            lambda _req: json_response(200, [shell_payload(name="my-box")])
        ),
    )

    assert sbx.private_key is not None
    assert sbx.private_key.get_base64() == explicit.private_key.get_base64()
    sbx.close()


def test_existing_box_without_any_key_raises_missing_key_error(tmp_path: Path) -> None:
    with pytest.raises(MissingKeyError, match=r"my-box\.key"):
        Sandbox.get_or_create(
            "my-box",
            api_key="k",
            keystore=tmp_path,
            http_transport=httpx.MockTransport(
                lambda _req: json_response(200, [shell_payload(name="my-box")])
            ),
        )


def test_keystore_none_makes_get_or_create_create_only_or_error(tmp_path: Path) -> None:
    with pytest.raises(MissingKeyError, match="keystore is disabled"):
        Sandbox.get_or_create(
            "my-box",
            api_key="k",
            keystore=None,
            http_transport=httpx.MockTransport(
                lambda _req: json_response(200, [shell_payload(name="my-box")])
            ),
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return json_response(200, [])
        return json_response(200, shell_payload(name="my-box"))

    sbx = Sandbox.get_or_create(
        "my-box", api_key="k", keystore=None, http_transport=httpx.MockTransport(handler)
    )
    assert sbx.name == "my-box"
    assert list(tmp_path.iterdir()) == []  # nothing persisted anywhere
    sbx.close()


def test_name_match_is_exact(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return json_response(200, [shell_payload(name="my-box-2")])
        return json_response(200, shell_payload(name="my-box"))

    sbx = Sandbox.get_or_create(
        "my-box", api_key="k", keystore=tmp_path, http_transport=httpx.MockTransport(handler)
    )
    assert sbx.name == "my-box"  # "my-box-2" was not treated as a match
    sbx.close()


def test_invalid_keystore_argument_raises() -> None:
    with pytest.raises(XshellzError, match="keystore must be"):
        Sandbox.get_or_create(
            "my-box",
            api_key="k",
            keystore=42,  # type: ignore[arg-type]
            http_transport=httpx.MockTransport(lambda _req: json_response(200, [])),
        )
