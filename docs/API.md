# xshellz Python SDK — API reference

Everything the SDK exports, with parameters, return shapes, and the errors
each call can raise. All symbols are importable from the top-level package:

```python
from xshellz import (
    Sandbox, Keystore, JobHandle, JobInfo,
    CommandResult, SandboxInfo, SandboxStats, SandboxProcs, ProcessInfo,
    XshellzError, AuthError, QuotaError, SandboxNotRunningError,
    CommandTimeoutError, MissingKeyError, UnsupportedLanguageError, ApiError,
    DEFAULT_KEYSTORE_DIR,
)
```

Shared parameters (accepted by every constructor/static method that talks to
the API): `api_key` (defaults to `$XSHELLZ_API_KEY`), `api_url` (defaults to
`$XSHELLZ_API_URL`, then `https://api.xshellz.com/v1`).

---

## class `Sandbox`

A remote sandbox: control plane over HTTPS, data plane over SSH. Used as a
context manager, the box is destroyed on exit unless `detach()` was called.

### Constructors

#### `Sandbox.create(name=None, api_key=None, api_url=None, timeout=120.0) -> Sandbox`

Spawn a new sandbox and return it once it is running. Generates a fresh
in-memory ed25519 keypair; only the public half is sent to the server.

- `name` — optional display name (used by `get_or_create` matching).
- `timeout` — HTTP timeout in seconds for the (synchronous) spawn call.
- Raises `AuthError` (bad key/scopes/account gates), `QuotaError` (plan
  sandbox limit / no entitlement), `ApiError` (429 throttle, 503 capacity, ...).

#### `Sandbox.get_or_create(name, api_key=None, api_url=None, timeout=120.0, *, private_key=None, keystore=<default>) -> Sandbox`

"Permanent mode": return the sandbox named `name`, creating it if it doesn't
exist. On create, the generated private key is persisted to the keystore; on
attach, the key is loaded (explicit `private_key` argument wins, then the
keystore). A `stopped` box is started before returning.

- `private_key` — OpenSSH string/bytes or a paramiko key; overrides the
  keystore lookup.
- `keystore` — a `Keystore`, a directory path, or `None` to disable
  persistence (then attaching to an existing box requires `private_key`).
  Default: `Keystore()` at `~/.xshellz/keys/`.
- Raises `MissingKeyError` (box exists, no key found — the message says where
  a key was expected), plus everything `create()` raises.

#### `Sandbox.connect(uuid, private_key, api_key=None, api_url=None) -> Sandbox`

Attach to an existing sandbox by UUID. `private_key` is the key whose public
half the box was created with (OpenSSH string/bytes or paramiko key).
Raises `SandboxNotRunningError` if the UUID isn't among your sandboxes.

#### `Sandbox.list(api_key=None, api_url=None) -> list[SandboxInfo]`

Your account's sandboxes (a bare JSON array on the wire).

### Account-level template (boxfile)

#### `Sandbox.get_boxfile(api_key=None, api_url=None) -> str | None`

The saved `xshellz.box` provisioning manifest, or `None` if unset.
Wire: `GET /v1/shells/agent/boxfile` → `{"manifest": string|null}`.

#### `Sandbox.set_boxfile(content, api_key=None, api_url=None) -> str | None`

Save (or clear, with `None`/blank) the manifest; returns it as stored (CRLF
normalized to LF, blank stored as `None`). Max 16 KiB. **Applied only when a
NEW box is created** — think of it as a template that preinstalls your
dependencies; existing boxes are not re-provisioned.
Wire: `PUT /v1/shells/agent/boxfile` with `{"manifest": ...}`.

### Properties

| Property | Type | Meaning |
|---|---|---|
| `info` | `SandboxInfo` | Last-known control-plane state |
| `uuid` | `str` | Sandbox id |
| `name` | `str` | Display name |
| `status` | `str` | `"running"`, `"stopped"`, ... |
| `ssh_host` / `ssh_port` | `str \| None` / `int \| None` | SSH endpoint |
| `ssh_command` | `str \| None` | Copy-paste `ssh -p ... root@...` line |
| `private_key` | `paramiko.PKey \| None` | In-memory SSH key |
| `private_key_openssh` | `str \| None` | OpenSSH serialization (persist to reconnect) |

### Commands & code

#### `run(command, cwd=None, env=None, timeout=None, on_stdout=None, on_stderr=None) -> CommandResult`

Run a shell command and wait. A non-zero exit code does **not** raise — check
`result.exit_code` / `result.ok`. `on_stdout`/`on_stderr` receive decoded
chunks as they arrive. Raises `SandboxNotRunningError` (box not running),
`CommandTimeoutError` (`timeout` exceeded), `XshellzError` (no key attached).

#### `run_code(language, code, cwd=None, env=None, timeout=None, on_stdout=None, on_stderr=None) -> CommandResult`

Write `code` to a temp file in the box, execute it with the matching
interpreter, always delete the temp file. Languages: `python` (python3),
`node`, `bash`, `ruby`, `php`. Raises `UnsupportedLanguageError` for anything
else; otherwise identical semantics to `run()`.

### Background jobs

#### `spawn(command, name=None) -> JobHandle`

Start `command` as a `nohup`-detached background process. Output goes to
`~/.xshellz/jobs/<job_id>.log` in the box (PID recorded in `<job_id>.pid`).
`name` prefixes the generated job id. Jobs survive disconnects, not box
stops/restarts. Raises `XshellzError` if the process could not be started.

#### `jobs() -> list[JobInfo]`

All job log files under `~/.xshellz/jobs` with each process's liveness.

### Files (SFTP)

| Method | Direction |
|---|---|
| `write_file(path, data: bytes)` | bytes → box |
| `read_file(path) -> bytes` | box → bytes |
| `upload(local_path, remote_path)` | local file → box |
| `download(remote_path, local_path)` | box → local file |

### Introspection

#### `stats() -> SandboxStats`

Live resource usage (`GET /v1/shells/agent/{uuid}/stats`): memory, CPU, pids,
disk, network, block-IO — each paired with the plan ceiling. Poll politely.

#### `procs() -> SandboxProcs`

Top processes, active SSH session count, detected agents, disk usage
(`GET /v1/shells/agent/{uuid}/procs`).

#### `terminal_url() -> str`

Mint a fresh signed web-terminal URL (`GET /v1/shells/agent/{uuid}/terminal`).
The embedded HMAC token expires after **~1 hour**; the URL grants a root shell
until then, so treat it like a credential and mint fresh rather than storing.

### Lifecycle

| Method | Effect |
|---|---|
| `start()` | Resume an idle-stopped box (same `/home`, same key). Raises `SandboxNotRunningError` if there is nothing to start. |
| `restart()` | Reboot a running box (re-runs the entrypoint; `/home` preserved; processes and jobs are killed). |
| `kill()` | Destroy the box (`DELETE`). Idempotent — a 404 is swallowed. |
| `detach()` | Keep the box alive when the `with` block exits. |
| `refresh() -> SandboxInfo` | Re-fetch state from the control plane. |
| `close()` | Close SSH + HTTP connections (box stays alive). |

---

## class `Keystore`

Local plaintext key storage for `get_or_create`. One `<sanitized-name>.key`
file per sandbox name, `0600` perms inside a `0700` directory.

| Member | Description |
|---|---|
| `Keystore(path=DEFAULT_KEYSTORE_DIR)` | `path` is expanded (`~` ok). Default `~/.xshellz/keys`. |
| `save(name, private_key_openssh) -> Path` | Write (0600) and return the key file path. |
| `load(name) -> str \| None` | The stored key, or `None`. |
| `delete(name) -> bool` | Remove the key file; `True` if one existed. |
| `path_for(name) -> Path` | Where the key for `name` lives. |

Names are sanitized to `[A-Za-z0-9._-]` for the filename. **Security:** keys
are plaintext on disk; deleting the file revokes local access only — the key
stays authorized on the box until the box is destroyed.

---

## class `JobHandle`

Returned by `Sandbox.spawn()`.

| Member | Description |
|---|---|
| `id` / `pid` / `log_path` | Job id, process id, log file path (in the box) |
| `is_running() -> bool` | `kill -0` probe |
| `logs(tail_lines=100) -> str` | Tail of the combined stdout+stderr log |
| `stop(grace=5.0)` | SIGTERM; SIGKILL if still alive after `grace` seconds |

## Data classes

All are frozen dataclasses mirroring the snake_case wire shapes.

- **`CommandResult`** — `stdout: str`, `stderr: str`, `exit_code: int`,
  `ok: bool` (property, `exit_code == 0`).
- **`SandboxInfo`** — `uuid`, `name`, `status`, `ssh_command`, `ssh_host`,
  `ssh_port`, `web_terminal_ready`, `always_on`, `trial_hours_remaining`,
  `spawned_at`, `created_at`, `isolation`, `gvisor`.
- **`SandboxStats`** — `mem_used_mb`, `mem_limit_mb`, `mem_allowed_mb`,
  `cpu_percent`, `cpu_allowed_vcpus`, `cpu_throttled_periods`, `pids_current`,
  `pids_allowed`, `disk_used_mb`, `disk_allowed_mb`, `net_rx_mb`, `net_tx_mb`,
  `blk_read_mb`, `blk_write_mb`.
- **`SandboxProcs`** — `procs: list[ProcessInfo]`, `sessions: int`,
  `agents: list[str]`, `disk_used_mb`, `disk_allowed_mb`.
- **`ProcessInfo`** — `pid`, `comm`, `cpu`, `mem`.
- **`JobInfo`** — `id`, `pid: int | None`, `log_path`, `running: bool`.

## Errors

Hierarchy: everything inherits from `XshellzError`.

| Error | Raised when |
|---|---|
| `XshellzError` | Base class; also misc SDK failures (no key attached, spawn failed, bad keystore argument) |
| `AuthError` | 401/403 — missing/invalid/expired token, scopes, verification gates |
| `QuotaError` | 403 — plan's concurrent sandbox limit reached, or no sandbox entitlement |
| `SandboxNotRunningError` | Box not `running` / not found / nothing to start |
| `CommandTimeoutError` | `run(timeout=...)` deadline exceeded |
| `MissingKeyError` | `get_or_create` found the box but no private key (message says where it looked) |
| `UnsupportedLanguageError` | `run_code` language not in: bash, node, php, python, ruby |
| `ApiError` | Any other 4xx/5xx; carries `.status_code` and `.body` |
