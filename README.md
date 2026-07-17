# xshellz

[![CI](https://github.com/xshellz/xshellz-python/actions/workflows/ci.yml/badge.svg)](https://github.com/xshellz/xshellz-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/xshellz)](https://pypi.org/project/xshellz/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

The official Python SDK for [xShellz](https://xshellz.com) sandboxes - spin up
a real Linux box from your code, run anything in it, throw it away.

**What is a sandbox?** A sandbox is a small, isolated Linux computer that lives
in the cloud and belongs only to you: it has a root shell, a package manager,
its own files and network, and it is walled off from everything else by
[gVisor](https://gvisor.dev) kernel isolation. Because it's disposable, it's the
safe place to run untrusted or AI-generated code, heavy builds, or experiments
you don't want anywhere near your own machine.

## Quickstart

1. **Install the SDK:**

   ```bash
   pip install xshellz
   ```

2. **Get an API key.** Sign up at [app.xshellz.com](https://app.xshellz.com),
   then create a personal access token with `read` and `write` scopes
   (Settings -> API tokens, or via the API: `POST /v1/auth/tokens`). Export it:

   ```bash
   export XSHELLZ_API_KEY="your-token"
   ```

3. **Run your first command in a sandbox:**

   ```python
   from xshellz import Sandbox

   with Sandbox.create() as sbx:
       result = sbx.run("echo hello from $(hostname)")
       print(result.stdout)
   # the box is destroyed when the block exits
   ```

`Sandbox.create()` returns once the box is running - typically a few seconds.

## Recipes

### Run a command

```python
with Sandbox.create() as sbx:
    r = sbx.run("apt-get update && apt-get install -y jq", timeout=300)
    print(r.exit_code, r.stdout, r.stderr)

    # A non-zero exit code does NOT raise - it's data, like subprocess:
    assert sbx.run("false").exit_code == 1

    # Working directory and environment variables:
    sbx.run("make test", cwd="/srv/app", env={"CI": "1"})

    # Stream long-running output as it happens:
    sbx.run("npm run build", on_stdout=print, on_stderr=print)
```

### A permanent named box that survives restarts

`get_or_create` gives you the same box back every time you call it with the
same name - from any process, any day. The SSH private key is saved to a local
keystore (`~/.xshellz/keys/`, file permissions `0600`) on first creation and
loaded from there on every reconnect. If the box was idle-stopped, it is
started for you.

```python
sbx = Sandbox.get_or_create("my-dev-box")
sbx.run("echo 'this file survives' >> ~/notes.txt")
sbx.close()  # closes connections; the box stays alive
```

Security note: the key sits in plaintext on your disk (0600, owner-only).
Delete the file (or `Keystore().delete("my-dev-box")`) to revoke local access.
Pass `keystore=None` to disable persistence, or `keystore="/path"` to relocate
it.

### Background job (keeps running after you disconnect)

```python
sbx = Sandbox.get_or_create("worker-box")
job = sbx.spawn("python3 train.py", name="train")

job.is_running()        # True while the process is alive
print(job.logs(50))     # last 50 lines of its combined output
job.stop()              # SIGTERM, then SIGKILL after a grace period

for info in sbx.jobs(): # every job's log file + liveness
    print(info.id, info.pid, info.running)
```

Jobs survive your script exiting; they do not survive the box stopping or
restarting.

### Run AI-generated code safely

`run_code` writes the code to a temp file inside the sandbox, runs the right
interpreter, and always cleans the file up. The code executes in the sandbox,
never on your machine.

```python
llm_output = 'print(sum(range(101)))'

with Sandbox.create() as sbx:
    result = sbx.run_code("python", llm_output, timeout=30)
    print(result.stdout)  # "5050"
```

Supported languages: `python`, `node`, `bash`, `ruby`, `php`. Anything else
raises `UnsupportedLanguageError`.

### Files: upload & download

```python
sbx.write_file("/tmp/config.json", b'{"debug": true}')   # bytes -> box
data = sbx.read_file("/tmp/config.json")                 # box -> bytes

sbx.upload("local.txt", "/tmp/remote.txt")               # local file -> box
sbx.download("/tmp/results.csv", "results.csv")          # box -> local file
```

### Check resource usage

```python
stats = sbx.stats()
print(f"mem {stats.mem_used_mb}/{stats.mem_allowed_mb} MB, cpu {stats.cpu_percent}%")

top = sbx.procs()
for p in top.procs:
    print(p.pid, p.comm, p.cpu, p.mem)
```

### Open a web terminal in the browser

```python
url = sbx.terminal_url()  # fresh signed URL, valid ~1 hour
print(f"Open this in a browser: {url}")
```

The URL grants a root shell until it expires - treat it like a password. Mint
a fresh one each time instead of storing it.

### Provision every new box the same way (boxfile template)

The account-level *boxfile* is a provisioning manifest applied when a **new**
box is created - use it to preinstall your dependencies so destroy+recreate
reproduces your environment:

```python
Sandbox.set_boxfile("apt: jq ripgrep\npip: httpx rich")
print(Sandbox.get_boxfile())
Sandbox.set_boxfile(None)  # clear it
```

## API reference

Every public class, method, parameter, return shape, and error is documented
in **[docs/API.md](docs/API.md)**.

## Configuration

| Environment variable | Meaning | Default |
|---|---|---|
| `XSHELLZ_API_KEY` | Your personal access token | (required) |
| `XSHELLZ_API_URL` | Control-plane base URL | `https://api.xshellz.com/v1` |

Precedence: explicit argument > environment variable > default.

## Errors

All SDK errors inherit from `XshellzError`, so `except XshellzError` catches
everything.

| Error | When it's raised |
|---|---|
| `AuthError` | 401/403: missing/invalid token, scopes, account gates |
| `QuotaError` | Plan sandbox limit reached, or plan has no sandbox entitlement |
| `SandboxNotRunningError` | The operation needs a `running` box |
| `CommandTimeoutError` | `run(timeout=...)` exceeded its deadline |
| `MissingKeyError` | `get_or_create` found the box but no private key |
| `UnsupportedLanguageError` | `run_code` got an unknown language |
| `ApiError` | Any other 4xx/5xx (carries `.status_code` and `.body`) |

## How it works

- **Control plane**: HTTPS to `api.xshellz.com/v1` (create / list / start /
  destroy / stats), authenticated with your token.
- **Data plane**: SSH directly to the box as `root`. `Sandbox.create()`
  generates an in-memory ed25519 keypair per sandbox; the private key never
  leaves your machine and the server only ever sees the public half.
- **Host keys are auto-accepted.** Sandbox host keys are generated at spawn
  time, so there is no out-of-band fingerprint to pin. If your threat model
  requires host-key verification, connect manually with your own SSH tooling
  using `sbx.ssh_command`.

## v0 limits

- **Free tier: 1 concurrent sandbox.** A second `Sandbox.create()` raises
  `QuotaError` - attach to the existing box (`Sandbox.list()` +
  `Sandbox.connect()`, or `get_or_create`) or `kill()` it first. Paid plans
  raise the limit.
- **Free boxes idle-stop after ~30 minutes.** The box (its `/home` and your
  key) is preserved; `sbx.start()` - or simply `get_or_create` - resumes it.
- Sandbox creation is throttled to 10 requests/minute per account.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest --cov=xshellz --cov-fail-under=80
```

### Local development (Docker)

No local Python needed - run the full lint + test + coverage suite in a
container (`python:3.12-slim`, pip cache persisted in a named volume):

```bash
docker compose run --rm test
```

The repo is mounted at `/work`; the container installs the package with the
`[dev]` extra, then runs `ruff check .` and `pytest` with the 80% coverage
gate. Your host `.venv/` is masked inside the container, so host and container
environments never mix.

## License

MIT
