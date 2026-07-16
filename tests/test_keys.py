from __future__ import annotations

import base64
import io
import re

import paramiko

from xshellz._keys import generate_keypair, load_private_key

PUBLIC_KEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]+={0,2} xshellz-sdk$")


def test_public_key_line_is_valid_openssh_format() -> None:
    keypair = generate_keypair()

    assert PUBLIC_KEY_RE.match(keypair.public_key_line), keypair.public_key_line

    # The base64 blob decodes and declares the ed25519 key type.
    blob_b64 = keypair.public_key_line.split(" ")[1]
    blob = base64.b64decode(blob_b64)
    assert b"ssh-ed25519" in blob


def test_public_key_matches_server_side_validation_regex() -> None:
    """The control plane validates against this exact regex (CreateAgentShellRequest)."""
    server_regex = re.compile(
        r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+|sk-ssh-ed25519@openssh\.com"
        r"|sk-ecdsa-sha2-[a-z0-9-]+@openssh\.com)\s+[A-Za-z0-9+/=]+(\s+.*)?$"
    )
    keypair = generate_keypair()
    assert server_regex.match(keypair.public_key_line)


def test_paramiko_loads_the_private_half_and_it_matches_the_public() -> None:
    keypair = generate_keypair()

    loaded = paramiko.Ed25519Key.from_private_key(io.StringIO(keypair.private_key_openssh))

    public_blob_b64 = keypair.public_key_line.split(" ")[1]
    assert loaded.get_base64() == public_blob_b64
    assert keypair.private_key.get_base64() == public_blob_b64


def test_each_keypair_is_unique() -> None:
    assert generate_keypair().public_key_line != generate_keypair().public_key_line


def test_load_private_key_accepts_str_bytes_and_pkey() -> None:
    keypair = generate_keypair()

    from_str = load_private_key(keypair.private_key_openssh)
    from_bytes = load_private_key(keypair.private_key_openssh.encode())
    from_obj = load_private_key(keypair.private_key)

    assert from_str.get_base64() == keypair.private_key.get_base64()
    assert from_bytes.get_base64() == keypair.private_key.get_base64()
    assert from_obj is keypair.private_key


def test_load_private_key_rejects_garbage() -> None:
    import pytest

    with pytest.raises(ValueError, match="Could not load the private key"):
        load_private_key("not a key at all")
