"""In-memory ed25519 SSH keypair generation and private-key loading.

The keypair is generated per ``Sandbox.create()`` and never touches disk; the
control plane only ever sees the public half.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

__all__ = ["SshKeyPair", "generate_keypair", "load_private_key"]

DEFAULT_KEY_COMMENT = "xshellz-sdk"


@dataclass(frozen=True)
class SshKeyPair:
    """An in-memory ed25519 keypair.

    Attributes:
        private_key: The paramiko key object used to authenticate SSH sessions.
        private_key_openssh: The private key serialized in OpenSSH PEM format
            (useful to persist if you plan to ``detach()`` and ``connect()``
            again later from another process).
        public_key_line: The single-line OpenSSH public key
            (``ssh-ed25519 <base64> <comment>``) sent to the control plane.
    """

    private_key: paramiko.Ed25519Key
    private_key_openssh: str
    public_key_line: str


def generate_keypair(comment: str = DEFAULT_KEY_COMMENT) -> SshKeyPair:
    """Generate a fresh in-memory ed25519 keypair."""
    key = Ed25519PrivateKey.generate()

    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    public_line = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode("ascii")
    )
    if comment:
        public_line = f"{public_line} {comment}"

    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_pem))

    return SshKeyPair(
        private_key=pkey,
        private_key_openssh=private_pem,
        public_key_line=public_line,
    )


def load_private_key(private_key: str | bytes | paramiko.PKey) -> paramiko.PKey:
    """Load a private key from an OpenSSH-format string/bytes or a paramiko key.

    Tries ed25519 first (the SDK's native key type), then RSA and ECDSA.
    """
    if isinstance(private_key, paramiko.PKey):
        return private_key

    text = private_key.decode("utf-8") if isinstance(private_key, bytes) else private_key

    errors: list[str] = []
    key_classes: tuple[type[paramiko.PKey], ...] = (
        paramiko.Ed25519Key,
        paramiko.RSAKey,
        paramiko.ECDSAKey,
    )
    for key_cls in key_classes:
        try:
            return key_cls.from_private_key(io.StringIO(text))
        except paramiko.SSHException as exc:  # try the next key type
            errors.append(f"{key_cls.__name__}: {exc}")

    raise ValueError(
        "Could not load the private key as ed25519, RSA, or ECDSA. "
        "Expected an unencrypted OpenSSH-format private key. "
        f"Details: {'; '.join(errors)}"
    )
