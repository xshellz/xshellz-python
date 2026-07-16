"""Configuration resolution: explicit argument > environment > default."""

from __future__ import annotations

import os

from .errors import AuthError

DEFAULT_API_URL = "https://api.xshellz.com/v1"
API_KEY_ENV = "XSHELLZ_API_KEY"
API_URL_ENV = "XSHELLZ_API_URL"


def resolve_api_key(api_key: str | None = None) -> str:
    """Resolve the API key or raise a helpful :class:`AuthError`."""
    key = api_key or os.environ.get(API_KEY_ENV) or ""
    key = key.strip()
    if not key:
        raise AuthError(
            "No xShellz API key found. Pass api_key=... or set the "
            f"{API_KEY_ENV} environment variable. Create a personal access "
            "token with `read` and `write` scopes from your xShellz dashboard "
            "(Settings -> API tokens) or via POST /v1/auth/tokens."
        )
    return key


def resolve_api_url(api_url: str | None = None) -> str:
    """Resolve the API base URL (no trailing slash)."""
    url = api_url or os.environ.get(API_URL_ENV) or DEFAULT_API_URL
    return url.rstrip("/")
