"""Local on-disk keystore for sandbox private keys.

Used by :meth:`xshellz.Sandbox.get_or_create` to persist the SSH private key of
a named sandbox so a later process (or machine reboot) can re-attach to the
same box without you shuttling keys around yourself.

Security note: keys are stored in **plaintext** on the local filesystem, one
file per sandbox name, with ``0600`` permissions (owner read/write only) inside
a ``0700`` directory. Anyone with access to your user account can read them.
Delete a key file (``Keystore.delete``) to revoke local access - the key itself
stays authorized on the box until the box is destroyed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

__all__ = ["DEFAULT_KEYSTORE_DIR", "Keystore"]

DEFAULT_KEYSTORE_DIR = Path("~/.xshellz/keys")

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_name(name: str) -> str:
    """Reduce a sandbox name to a safe filename: ``[A-Za-z0-9._-]`` only."""
    return _SANITIZE_RE.sub("_", name) or "_"


class Keystore:
    """A directory of ``<sanitized-name>.key`` OpenSSH private key files."""

    def __init__(self, path: str | Path = DEFAULT_KEYSTORE_DIR) -> None:
        self.path = Path(path).expanduser()

    def path_for(self, name: str) -> Path:
        """The file where the key for ``name`` is (or would be) stored."""
        return self.path / f"{sanitize_name(name)}.key"

    def save(self, name: str, private_key_openssh: str) -> Path:
        """Persist a private key for ``name`` (0600 file in a 0700 directory)."""
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        key_path = self.path_for(name)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(private_key_openssh)
        # A pre-existing file keeps its old mode through os.open - re-tighten.
        key_path.chmod(0o600)
        return key_path

    def load(self, name: str) -> str | None:
        """Return the stored private key for ``name``, or ``None`` if absent."""
        key_path = self.path_for(name)
        if not key_path.is_file():
            return None
        return key_path.read_text()

    def delete(self, name: str) -> bool:
        """Remove the stored key for ``name``; True if a file was deleted."""
        key_path = self.path_for(name)
        if not key_path.is_file():
            return False
        key_path.unlink()
        return True

    def __repr__(self) -> str:
        return f"<Keystore path={str(self.path)!r}>"
