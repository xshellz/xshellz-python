"""The :class:`Sandbox` - a throwaway, gVisor-isolated Linux box."""

from __future__ import annotations

import contextlib
import secrets
import shlex
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx
import paramiko

from ._http import ApiClient
from ._keys import generate_keypair, load_private_key
from ._ssh import ParamikoTransport, SshTransport, build_shell_command
from .errors import (
    ApiError,
    MissingKeyError,
    SandboxNotRunningError,
    UnsupportedLanguageError,
    XshellzError,
)
from .jobs import _LIST_JOBS_SCRIPT, JobHandle, JobInfo, build_spawn_command, new_job_id
from .jobs import parse_jobs_output as _parse_jobs_output
from .keystore import Keystore
from .models import CommandResult, SandboxInfo, SandboxProcs, SandboxStats

__all__ = ["Sandbox"]

_STATUS_RUNNING = "running"
_STATUS_STOPPED = "stopped"

_DEFAULT_KEYSTORE = object()

# language -> (interpreter, temp-file extension) for run_code()
_INTERPRETERS: dict[str, tuple[str, str]] = {
    "python": ("python3", "py"),
    "node": ("node", "js"),
    "bash": ("bash", "sh"),
    "ruby": ("ruby", "rb"),
    "php": ("php", "php"),
}


def _resolve_keystore(keystore: Any) -> Keystore | None:
    if keystore is _DEFAULT_KEYSTORE:
        return Keystore()
    if keystore is None:
        return None
    if isinstance(keystore, Keystore):
        return keystore
    if isinstance(keystore, (str, Path)):
        return Keystore(keystore)
    raise XshellzError(
        f"keystore must be a Keystore, a path, or None - got {type(keystore).__name__}."
    )


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

    @classmethod
    def get_or_create(
        cls,
        name: str,
        api_key: str | None = None,
        api_url: str | None = None,
        timeout: float = 120.0,
        *,
        private_key: str | bytes | paramiko.PKey | None = None,
        keystore: Keystore | str | Path | None | Any = _DEFAULT_KEYSTORE,
        http_transport: httpx.BaseTransport | None = None,
    ) -> Sandbox:
        """Attach to the sandbox named ``name``, creating it if it doesn't exist.

        This is "permanent mode": call it with the same name from any process
        and you get the same box back, started if it was idle-stopped.

        - **Not found**: a new box is created with that name; if a keystore is
          enabled (the default: ``~/.xshellz/keys/``) the generated private key
          is persisted there so future calls can reconnect.
        - **Found**: the private key is loaded (an explicit ``private_key``
          argument wins, else the keystore) and the sandbox is attached. A
          ``stopped`` box is started first.

        ``keystore`` accepts a :class:`~xshellz.Keystore`, a directory path, or
        ``None`` to disable persistence entirely - in which case attaching to
        an existing box requires an explicit ``private_key``.

        Raises:
            MissingKeyError: the box exists but no private key was found.
            AuthError / QuotaError / ApiError: as for :meth:`create`.
        """
        store = _resolve_keystore(keystore)
        api = ApiClient(api_key=api_key, api_url=api_url, timeout=timeout, transport=http_transport)
        try:
            match: SandboxInfo | None = None
            for item in api.get("/shells/agent"):
                info = SandboxInfo.from_api(item)
                if info.name == name:
                    match = info
                    break

            if match is None:
                api.close()
                sbx = cls.create(
                    name=name,
                    api_key=api_key,
                    api_url=api_url,
                    timeout=timeout,
                    http_transport=http_transport,
                )
                if store is not None and sbx.private_key_openssh is not None:
                    store.save(name, sbx.private_key_openssh)
                return sbx

            if private_key is not None:
                pkey = load_private_key(private_key)
            else:
                saved = store.load(name) if store is not None else None
                if saved is None:
                    expected = (
                        f"keystore file {store.path_for(name)}"
                        if store is not None
                        else "the private_key argument (the keystore is disabled)"
                    )
                    raise MissingKeyError(
                        f"Sandbox {name!r} already exists but no private key was "
                        f"found - expected it in {expected}. Pass private_key=..., "
                        "or kill() the box to let get_or_create() recreate it."
                    )
                pkey = load_private_key(saved)

            sbx = cls(info=match, api=api, private_key=pkey)
            if sbx.status == _STATUS_STOPPED:
                sbx.start()
            return sbx
        except BaseException:
            api.close()
            raise

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
    def get_boxfile(
        api_key: str | None = None,
        api_url: str | None = None,
        *,
        http_transport: httpx.BaseTransport | None = None,
    ) -> str | None:
        """The account's saved ``xshellz.box`` provisioning manifest, or None.

        The boxfile is applied when a NEW box is created (it is seeded into
        ``~/xshellz.box`` and executed by the box's provisioning step), so it
        acts as a template: preinstall packages, tools, dotfiles - and a
        destroy+recreate reproduces your environment.
        """
        api = ApiClient(api_key=api_key, api_url=api_url, transport=http_transport)
        try:
            payload = api.get("/shells/agent/boxfile")
            return payload.get("manifest")
        finally:
            api.close()

    @staticmethod
    def set_boxfile(
        content: str | None,
        api_key: str | None = None,
        api_url: str | None = None,
        *,
        http_transport: httpx.BaseTransport | None = None,
    ) -> str | None:
        """Save (or clear, with ``None``/blank) the account's ``xshellz.box``.

        Returns the manifest as stored (the server normalizes CRLF to LF and
        stores blank input as ``None``). Applies to the NEXT box created -
        existing boxes are not re-provisioned. Max size 16 KiB.
        """
        api = ApiClient(api_key=api_key, api_url=api_url, transport=http_transport)
        try:
            payload = api.put("/shells/agent/boxfile", json={"manifest": content})
            return payload.get("manifest")
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

    def run_code(
        self,
        language: str,
        code: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Execute a snippet of ``language`` source code and return the result.

        The code is written to a temp file in the sandbox, run with the
        matching interpreter (``python`` -> python3, ``node``, ``bash``,
        ``ruby``, ``php``), and the temp file is always deleted afterwards.
        Semantics are otherwise identical to :meth:`run` (non-zero exit code
        is data, not an exception).

        Raises:
            UnsupportedLanguageError: ``language`` is not one of the supported
                interpreters.
        """
        try:
            interpreter, extension = _INTERPRETERS[language]
        except KeyError:
            raise UnsupportedLanguageError(
                f"Unsupported language {language!r}. Supported: "
                f"{', '.join(sorted(_INTERPRETERS))}."
            ) from None

        remote_path = f"/tmp/xshellz-run-{secrets.token_hex(4)}.{extension}"
        self.write_file(remote_path, code.encode("utf-8"))
        try:
            return self.run(
                f"{interpreter} {shlex.quote(remote_path)}",
                cwd=cwd,
                env=env,
                timeout=timeout,
                on_stdout=on_stdout,
                on_stderr=on_stderr,
            )
        finally:
            with contextlib.suppress(Exception):
                self._get_transport().exec(f"rm -f {shlex.quote(remote_path)}")

    def spawn(self, command: str, name: str | None = None) -> JobHandle:
        """Start ``command`` as a detached background job and return a handle.

        The job keeps running after this SSH session (and your process) ends;
        its combined stdout+stderr goes to ``~/.xshellz/jobs/<job_id>.log``
        inside the box. Jobs do not survive the box stopping or restarting.

        ``name`` (optional) prefixes the generated job id for readability.
        """
        job_id = new_job_id(name)
        result = self.run(build_spawn_command(command, job_id))
        pid_text = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if not result.ok or not pid_text.isdigit():
            raise XshellzError(
                f"Failed to spawn background job (exit {result.exit_code}): "
                f"{result.stderr or result.stdout}"
            )
        return JobHandle(sandbox=self, job_id=job_id, pid=int(pid_text))

    def jobs(self) -> list[JobInfo]:
        """List background jobs (log files under ``~/.xshellz/jobs``) and
        whether each one's process is still alive."""
        return _parse_jobs_output(self.run(_LIST_JOBS_SCRIPT).stdout)

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

    def restart(self) -> None:
        """Reboot a running box (``POST /shells/agent/{uuid}/restart``).

        Re-runs the box's entrypoint; ``/home`` is preserved. Background jobs
        and any processes are killed by the reboot. The SSH transport is
        reconnected lazily on the next data-plane call.
        """
        payload = self._api.post(f"/shells/agent/{self.uuid}/restart")
        self._info = SandboxInfo.from_api(payload)
        self._close_transport()

    def stats(self) -> SandboxStats:
        """Live resource usage (``GET /shells/agent/{uuid}/stats``).

        Memory, CPU, pids, disk, network and block-IO readings, each paired
        with the plan-allowed ceiling (``*_allowed_*``). Poll politely - this
        hits the box's host on every call.
        """
        return SandboxStats.from_api(self._api.get(f"/shells/agent/{self.uuid}/stats"))

    def procs(self) -> SandboxProcs:
        """Top processes + active session count + disk usage
        (``GET /shells/agent/{uuid}/procs``)."""
        return SandboxProcs.from_api(self._api.get(f"/shells/agent/{self.uuid}/procs"))

    def terminal_url(self) -> str:
        """Mint a fresh signed web-terminal URL for this box.

        The URL embeds a short-lived HMAC token (expires after ~1 hour), so
        call this each time you open the terminal rather than storing the URL.
        Anyone with the URL gets a root shell in the box until it expires -
        treat it like a credential.
        """
        payload = self._api.get(f"/shells/agent/{self.uuid}/terminal")
        return str(payload["url"])

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
