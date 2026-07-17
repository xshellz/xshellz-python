"""Thin HTTP client for the xShellz control plane with typed error mapping."""

from __future__ import annotations

from typing import Any

import httpx

from ._config import resolve_api_key, resolve_api_url
from .errors import ApiError, AuthError, QuotaError

__all__ = ["ApiClient"]

_USER_AGENT = "xshellz-python/0.2.0"

# 403 message fragments emitted by the control plane's guard chain. All guards
# abort(403, message); quota/entitlement are distinguished by message text.
_QUOTA_FRAGMENTS = (
    "agent shell limit",  # "You've reached your plan's agent shell limit (N)."
    "plan does not include agent shells",  # entitlement gate
)


class ApiClient:
    """Bearer-authenticated JSON client for ``https://api.xshellz.com/v1``."""

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_url = resolve_api_url(api_url)
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {resolve_api_key(api_key)}",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
            timeout=timeout,
            transport=transport,
        )

    def request(self, method: str, path: str, json: Any | None = None) -> Any:
        """Issue a request; return the parsed JSON body or raise a typed error."""
        response = self._client.request(method, f"{self.api_url}{path}", json=json)
        if response.status_code >= 400:
            raise self._map_error(response)
        if not response.content:
            return None
        return response.json()

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, json: Any | None = None) -> Any:
        return self.request("POST", path, json=json)

    def put(self, path: str, json: Any | None = None) -> Any:
        return self.request("PUT", path, json=json)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _map_error(response: httpx.Response) -> ApiError | AuthError | QuotaError:
        status = response.status_code
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text

        message = ""
        error_code = ""
        if isinstance(body, dict):
            message = str(body.get("message") or "")
            error_code = str(body.get("error") or "")
        if not message:
            message = response.text or f"HTTP {status}"

        if status == 401:
            return AuthError(
                "Authentication failed (401): the API key is missing, invalid, "
                "expired, or revoked. Create a personal access token with `read` "
                "and `write` scopes from your xShellz dashboard (Settings -> API "
                f"tokens) or via POST /v1/auth/tokens. Server said: {message}"
            )

        if status == 403:
            lowered = message.lower()
            if any(fragment in lowered for fragment in _QUOTA_FRAGMENTS):
                return QuotaError(
                    f"{message} Tip: on the free tier only one sandbox may exist "
                    "at a time - use Sandbox.list() and Sandbox.connect() to "
                    "attach to the existing box, or kill() it first."
                )
            if error_code == "verification_required":
                return AuthError(f"Account verification required (403): {message}")
            return AuthError(f"Forbidden (403): {message}")

        if status == 429:
            return ApiError(
                f"Rate limited (429): {message or 'too many requests'} - sandbox "
                "creation is throttled to 10 requests/minute.",
                status_code=status,
                body=body,
            )

        return ApiError(f"HTTP {status}: {message}", status_code=status, body=body)
