"""The :class:`Sandbox` - a throwaway, gVisor-isolated Linux box."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any

import httpx
import paramiko

from ._http import ApiClient
from ._keys import generate_keypair, load_private_key
from ._ssh import ParamikoTransport, SshTransport, build_shell_command
from .errors import ApiError, SandboxNotRunningError, XshellzError
from .models import CommandResult, SandboxInfo

__all__ = ["Sandbox"]

_STATUS_RUNNING = "running"


class Sandbox:
    """A remote sandbox: control plane over HTTPS, data plane over SSH.

    Create one with :meth:`Sandbox.create`, attach to an existing one with
    :meth:`Sandbox.connect`, or enumerate them with :meth:`Sandbox.list`.

    Used as a context manager, the sandbox is destroyed on exit unless
    :meth:`detach` was called::

        with Sandbox.create() as sbx:
            result = sbx.run("echo hello")
    """

    def __init__(
        self,
        info: SandboxInfo,
        api: ApiClient,
        private_key: paramiko.PKey | None = None,
        private_key_openssh: str | None = None,
        transport: SshTransport | None = None,
    ) -> None:
        self._info = info
        self._api = api
        self._private_key = private_key
        self._private_key_openssh = private_key_openssh
        self._transport = transport
        self._detached = False
        self._killed = False

    # ------------------------------------------------------------------ #
    # Constructors (control plane)
    # ------------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        name: str | None = None,
        api_key: str | None = None,
        api_url: str | None = None,
        timeout: float = 120.0,
        *,
        http_transport: httpx.BaseTransport | None = None,
    ) -> Sandbox:
        """Spawn a new sandbox and return it once it is RUNNING.

        An in-memory ed25519 keypair is generated for the box; the private key
        never leaves this process and the server never sees it. Spawning is
        synchronous - the box is reachable when this returns.

        Raises:
            AuthError: missing/invalid API key, insufficient scope, or an
                account gate (verification, preview).
            QuotaError: the plan's concurrent sandbox limit is reached or the
                plan has no sandbox entitlement.
            ApiError: other API failures (throttle 429, host capacity 503, ...).
        """
        keypair = generate_keypair()
        api = ApiClient(api_key=api_key, api_url=api_url, timeout=timeout, transport=http_transport)
        try:
            body: dict[str, Any] = {"ssh_public_key": keypair.public_key_line}
            if name is not None:
                body["name"] = name
            payload = api.post("/shells/agent", json=body)
        except BaseException:
            api.close()
            raise

        return cls(
            info=SandboxInfo.from_api(payload),
            api=api,
            private_key=keypair.private_key,
            private_key_openssh=keypair.private_key_openssh,
        )

    @classmethod
    def connect(
        cls,
        uuid: str,
        private_key: str | bytes | paramiko.PKey,
        api_key: str | None = None,
        api_url: str | None = None,
        *,
        http_transport: httpx.BaseTransport | None = None,
    ) -> Sandbox:
        """Attach to an existing sandbox by UUID.

        ``private_key`` is the key whose public half the box was created with:
        an OpenSSH-format string/bytes, or a paramiko key object (e.g. the
        :attr:`private_key` of the original ``Sandbox``).
        """
        pkey = load_private_key(private_key)
        api = ApiClient(api_key=api_key, api_url=api_url, transport=http_transport)
        try:
            info = cls._find(api, uuid)
        except BaseException:
            api.close()
            raise
        return cls(info=info, api=api, private_key=pkey)

    @staticmethod
    def list(
        api_key: str | None = None,
        api_url: str | None = None,
        *,
        http_transport: httpx.BaseTransport | None = None,
    ) -> list[SandboxInfo]:
        """List the account's active sandboxes (a bare array on the wire)."""
        api = ApiClient(api_key=api_key, api_url=api_url, transport=http_transport)
        try:
            payload = api.get("/shells/agent")
            return [SandboxInfo.from_api(item) for item in payload]
        finally:
            api.close()

    @staticmethod
    def _find(api: ApiClient, uuid: str) -> SandboxInfo:
        """Resolve one sandbox via the list endpoint (there is no GET show)."""
        payload = api.get("/shells/agent")
        for item in payload:
            info = SandboxInfo.from_api(item)
            if info.uuid == uuid:
                return info
        raise SandboxNotRunningError(
            f"Sandbox {uuid} was not found among this account's active sandboxes."
        )

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def info(self) -> SandboxInfo:
        """The last-known control-plane state of the sandbox."""
        return self._info

    @property
    def uuid(self) -> str:
        return self._info.uuid

    @property
    def name(self) -> str:
        return self._info.name

    @property
    def status(self) -> str:
        return self._info.status

    @property
    def ssh_host(self) -> str | None:
        return self._info.ssh_host

    @property
    def ssh_port(self) -> int | None:
        return self._info.ssh_port

    @property
    def ssh_command(self) -> str | None:
        return self._info.ssh_command

    @property
    def private_key(self) -> paramiko.PKey | None:
        """The in-memory private key authenticating this sandbox's SSH."""
        return self._private_key

    @property
    def private_key_openssh(self) -> str | None:
        """OpenSSH serialization of the private key (persist it to reconnect
        later via :meth:`connect`); ``None`` when attached with a key object."""
        return self._private_key_openssh

    # ------------------------------------------------------------------ #
    # Data plane (SSH/SFTP)
    # ------------------------------------------------------------------ #

    def run(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Run a shell command in the sandbox and wait for it to finish.

        A non-zero exit code does NOT raise - inspect ``result.exit_code``.
        ``on_stdout``/``on_stderr`` receive decoded output chunks as they
        arrive (the full output is still returned in the result).

        Raises:
            SandboxNotRunningError: the box is not in the ``running`` state.
            CommandTimeoutError: ``timeout`` seconds elapsed before exit.
        """
        transport = self._get_transport()
        full_command = build_shell_command(command, cwd=cwd, env=env)
        return transport.exec(
            full_command, timeout=timeout, on_stdout=on_stdout, on_stderr=on_stderr
        )

    def write_file(self, path: str, data: bytes) -> None:
        """Write ``data`` to ``path`` inside the sandbox (SFTP)."""
        self._get_transport().write_file(path, data)

    def read_file(self, path: str) -> bytes:
        """Read and return the contents of ``path`` inside the sandbox (SFTP)."""
        return self._get_transport().read_file(path)

    def upload(self, local_path: str, remote_path: str) -> None:
        """Upload a local file into the sandbox (SFTP)."""
        self._get_transport().upload(local_path, remote_path)

    def download(self, remote_path: str, local_path: str) -> None:
        """Download a file from the sandbox to a local path (SFTP)."""
        self._get_transport().download(remote_path, local_path)

    # ------------------------------------------------------------------ #
    # Lifecycle (control plane)
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Resume an idle-stopped box (``POST /shells/agent/{uuid}/start``).

        Free-tier boxes idle-stop after ~30 minutes; this brings the same box
        (same /home, same authorized key) back to ``running``.
        """
        try:
            payload = self._api.post(f"/shells/agent/{self.uuid}/start")
        except ApiError as exc:
            if exc.status_code == 404:
                raise SandboxNotRunningError(
                    f"Sandbox {self.uuid} has no stopped box to start - it may "
                    "already be running, suspended, or deleted."
                ) from exc
            raise
        self._info = SandboxInfo.from_api(payload)
        self._close_transport()

    def kill(self) -> None:
        """Destroy the sandbox (``DELETE /shells/agent/{uuid}``). Idempotent:
        a 404 (already gone) is swallowed."""
        self._close_transport()
        if self._killed:
            return
        try:
            self._api.delete(f"/shells/agent/{self.uuid}")
        except ApiError as exc:
            if exc.status_code != 404:
                raise
        self._killed = True

    def detach(self) -> None:
        """Keep the sandbox alive when the context manager exits.

        Persist :attr:`private_key_openssh` and :attr:`uuid` to re-attach
        later with :meth:`Sandbox.connect`.
        """
        self._detached = True

    def refresh(self) -> SandboxInfo:
        """Re-fetch this sandbox's state from the control plane."""
        self._info = self._find(self._api, self.uuid)
        return self._info

    def close(self) -> None:
        """Close the SSH connection and the HTTP client (keeps the box alive)."""
        self._close_transport()
        self._api.close()

    # ------------------------------------------------------------------ #
    # Context manager
    # ------------------------------------------------------------------ #

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._detached:
                self.kill()
        finally:
            self.close()

    def __repr__(self) -> str:
        return (
            f"<Sandbox uuid={self.uuid!r} status={self.status!r} "
            f"ssh={self.ssh_host}:{self.ssh_port}>"
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _get_transport(self) -> SshTransport:
        if self._transport is not None:
            return self._transport

        if self.status != _STATUS_RUNNING:
            raise SandboxNotRunningError(
                f"Sandbox {self.uuid} is {self.status!r}, not 'running'. "
                "Call start() to resume an idle-stopped box."
            )
        if self.ssh_host is None or self.ssh_port is None:
            raise SandboxNotRunningError(
                f"Sandbox {self.uuid} has no SSH endpoint yet (host/port unknown)."
            )
        if self._private_key is None:
            raise XshellzError(
                "No private key available for this sandbox - attach with "
                "Sandbox.connect(uuid, private_key=...)."
            )

        self._transport = ParamikoTransport(
            host=self.ssh_host,
            port=self.ssh_port,
            pkey=self._private_key,
        )
        return self._transport

    def _close_transport(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
