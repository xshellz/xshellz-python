"""Typed exceptions raised by the xShellz SDK."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ApiError",
    "AuthError",
    "CommandTimeoutError",
    "QuotaError",
    "SandboxNotRunningError",
    "XshellzError",
]


class XshellzError(Exception):
    """Base class for every error raised by the xShellz SDK."""


class AuthError(XshellzError):
    """Authentication or authorization failed (HTTP 401/403).

    Raised for a missing/invalid API key, insufficient token scopes, account
    verification requirements, and other access gates.
    """


class QuotaError(XshellzError):
    """The account's sandbox quota or plan entitlement blocks the operation.

    The control plane returns HTTP 403 both when the plan's concurrent sandbox
    limit is reached ("agent shell limit") and when the plan does not include
    sandboxes at all. On the free tier the limit is 1 concurrent box - use
    ``Sandbox.list()`` + ``Sandbox.connect()`` to attach to the existing one
    instead of creating a new box.
    """


class SandboxNotRunningError(XshellzError):
    """The sandbox is not in the ``running`` state (or no longer exists)."""


class CommandTimeoutError(XshellzError):
    """A command executed with ``run(timeout=...)`` exceeded its deadline."""


class ApiError(XshellzError):
    """Any other non-success API response (4xx/5xx).

    Attributes:
        status_code: The HTTP status code.
        body: The parsed JSON body if the response was JSON, else the raw text.
    """

    def __init__(self, message: str, status_code: int, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
