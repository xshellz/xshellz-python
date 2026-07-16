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
    QuotaError,
    SandboxNotRunningError,
    XshellzError,
)
from .models import CommandResult, SandboxInfo
from .sandbox import Sandbox

__version__ = "0.1.0"

__all__ = [
    "ApiError",
    "AuthError",
    "CommandResult",
    "CommandTimeoutError",
    "QuotaError",
    "Sandbox",
    "SandboxInfo",
    "SandboxNotRunningError",
    "XshellzError",
    "__version__",
]
