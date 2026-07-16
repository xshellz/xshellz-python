# xshellz

Official Python SDK for [xShellz](https://xshellz.com) sandboxes: throwaway,
gVisor-isolated Linux boxes you can spawn and run commands in from your own
program - in three lines.

```bash
pip install xshellz
```

```python
from xshellz import Sandbox

with Sandbox.create() as sbx:
    result = sbx.run("python3 -c 'print(6*7)'")
    print(result.stdout)  # 42
# the box is destroyed when the block exits
```

Each sandbox is a real Linux box (root shell, package manager, network) running
under [gVisor](https://gvisor.dev) kernel isolation. Spawning is synchronous -
`Sandbox.create()` returns once the box is running, typically in a few seconds.

## Authentication

The SDK authenticates with an xShellz personal access token (PAT) carrying the
`read` and `write` scopes:

1. Create a token from your [xShellz dashboard](https://app.xshellz.com)
   (Settings -> API tokens), or via the API: `POST /v1/auth/tokens`.
2. Export it:

```bash
export XSHELLZ_API_KEY="your-token"
```

or pass it explicitly: `Sandbox.create(api_key="your-token")`.

Config precedence: explicit argument > `XSHELLZ_API_KEY` / `XSHELLZ_API_URL`
environment variables > default (`https://api.xshellz.com/v1`).

To target a staging or self-hosted control plane:

```bash
export XSHELLZ_API_URL="https://api.staging.example.com/v1"
```

## Usage

### Run commands

```python
with Sandbox.create(name="build-box") as sbx:
    r = sbx.run("apt-get update && apt-get install -y jq", timeout=300)
    print(r.exit_code, r.stdout, r.stderr)

    # A non-zero exit code does NOT raise - it's data:
    r = sbx.run("false")
    assert r.exit_code == 1

    # cwd and env:
    sbx.run("make test", cwd="/srv/app", env={"CI": "1"})

    # Stream long-running output as it arrives:
    sbx.run("npm run build", on_stdout=print, on_stderr=print)
```

### Files (SFTP)

```python
sbx.write_file("/tmp/config.json", b'{"debug": true}')
data: bytes = sbx.read_file("/tmp/config.json")

sbx.upload("local.txt", "/tmp/remote.txt")
sbx.download("/tmp/remote.txt", "out.txt")
```

### Lifecycle

```python
sbx.uuid         # sandbox id
sbx.ssh_host     # e.g. "shellus1.xshellz.com"
sbx.ssh_port     # e.g. 42001
sbx.ssh_command  # ready-to-copy "ssh -p 42001 root@..."
sbx.status       # "running", "stopped", ...

sbx.detach()     # keep the box alive after the `with` block
sbx.kill()       # destroy the box explicitly
sbx.start()      # resume an idle-stopped box

# Re-attach later (persist sbx.private_key_openssh + sbx.uuid for this):
sbx = Sandbox.connect(uuid, private_key=saved_private_key)

# Enumerate your sandboxes:
for info in Sandbox.list():
    print(info.uuid, info.status)
```

### Typed errors

```python
from xshellz import AuthError, QuotaError, Sandbox, SandboxNotRunningError

try:
    sbx = Sandbox.create()
except QuotaError:
    # plan limit reached - attach to the existing box instead
    existing = Sandbox.list()[0]
    sbx = Sandbox.connect(existing.uuid, private_key=saved_key)
except AuthError as e:
    print(e)  # missing/invalid token, scope, or account verification
```

- `XshellzError` - base class for everything the SDK raises
- `AuthError` - 401/403: bad or missing token, scopes, account gates
- `QuotaError` - plan sandbox limit reached / plan has no sandbox entitlement
- `SandboxNotRunningError` - operation needs a `running` box
- `CommandTimeoutError` - `run(timeout=...)` exceeded
- `ApiError` - any other 4xx/5xx (carries `.status_code` and `.body`)

## How it works

- **Control plane**: HTTPS to `api.xshellz.com/v1` (create / list / start /
  destroy), authenticated by your PAT.
- **Data plane**: SSH directly to the box as `root`. `Sandbox.create()`
  generates an in-memory ed25519 keypair per sandbox; the private key never
  leaves your process and the server never sees it - only the public half is
  installed in the box's `authorized_keys`.
- **Host keys are auto-accepted.** Sandbox host keys are generated at spawn
  time, so there is no out-of-band fingerprint to pin. If your threat model
  requires host-key verification, connect manually with your own SSH tooling
  using `sbx.ssh_command`.

## v0 limits

- **Free tier: 1 concurrent sandbox.** A second `Sandbox.create()` raises
  `QuotaError` while one exists - use `Sandbox.list()` + `Sandbox.connect()`
  to attach to the existing box, or `kill()` it first. Paid plans raise the
  limit.
- **Free boxes idle-stop after ~30 minutes.** The box (its `/home` and your
  key) is preserved; call `sbx.start()` to resume it.
- Sandbox creation is throttled to 10 requests/minute per account.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
```

### Local development (Docker)

No local Python needed — run the full lint + test suite in a container
(`python:3.12-slim`, pip cache persisted in a named volume):

```bash
docker compose run --rm test
```

The repo is mounted at `/work`; the container installs the package with the
`[dev]` extra, then runs `ruff check .` and `pytest`. Your host `.venv/` is
masked inside the container, so host and container environments never mix.

## License

MIT
