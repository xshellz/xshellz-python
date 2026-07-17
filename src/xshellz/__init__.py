"""xShellz Python SDK - throwaway, gVisor-isolated Linux sandboxes.

Quickstart::

    from xshellz import Sandbox

    with Sandbox.create() as sbx:
        result = sbx.run("python3 -c 'print(6*7)'")
        print(result.stdout)  # "42"
"""

from .errors import (
    ApiError,
    AuthError,
    CommandTimeoutError,
    MissingKeyError,
    QuotaError,
    SandboxNotRunningError,
    UnsupportedLanguageError,
    XshellzError,
)
from .jobs import JobHandle, JobInfo
from .keystore import DEFAULT_KEYSTORE_DIR, Keystore
from .models import CommandResult, ProcessInfo, SandboxInfo, SandboxProcs, SandboxStats
from .sandbox import Sandbox

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_KEYSTORE_DIR",
    "ApiError",
    "AuthError",
    "CommandResult",
    "CommandTimeoutError",
    "JobHandle",
    "JobInfo",
    "Keystore",
    "MissingKeyError",
    "ProcessInfo",
    "QuotaError",
    "Sandbox",
    "SandboxInfo",
    "SandboxProcs",
    "SandboxStats",
    "SandboxNotRunningError",
    "UnsupportedLanguageError",
    "XshellzError",
    "__version__",
]
