"""Per-role OAuth2 client-credentials identity for the Gateway (§8.2).

Each ICS role authenticates to the Gateway as its **own** machine identity, so the token carries
a ``minnal_role`` claim whose value is the role name and the ``grid-tools`` Cedar permits scoped to
``dispatch`` and ``commander`` apply (R13.1). :class:`RoleIdentityProvider` fetches and caches one
client-credentials access token per role and refreshes it at 80 percent of its lifetime, so a long
Operational_Period never presents an expired token mid-commit.

This is an **edge adapter**: it holds a ``boto3`` Secrets Manager client (created once at module
scope) and makes an HTTPS token request. The client secret is read from Secrets Manager **by ARN**
and never appears in config or code (security.md rule 7, R13.9). The provider holds no raw AWS
credentials for tool access; the token it mints is the only credential a role presents, and every
Minnal tool is reached through the Gateway (R13.9).
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Protocol

import boto3
import requests

# One Secrets Manager client for the process (backend-python.md: boto3 clients at module scope).
# Region is resolved by boto3 from the standard AWS configuration of the runtime; this adapter
# never reads MINNAL_* settings, which Settings owns exclusively.
_SECRETS = boto3.client("secretsmanager")

#: Successful OAuth2 token response status.
_HTTP_OK = 200

#: HTTP request timeout for the token endpoint, in seconds (no reliance on a client default).
_TOKEN_REQUEST_TIMEOUT_SECONDS = 30


class Clock(Protocol):
    """Wall-clock source, injectable so tests can freeze time. Returns epoch seconds."""

    def wall_now(self) -> float: ...


class SystemClock:
    """The default wall clock: ``time.time()`` in epoch seconds."""

    def wall_now(self) -> float:
        return time.time()


@dataclass(frozen=True)
class RoleClientConfig:
    """The OAuth2 client credentials for one role, secret referenced by ARN only (R13.9).

    Attributes:
        client_id: The role's Cognito app-client id.
        client_secret_arn: The Secrets Manager ARN of the role's app-client secret. The secret
            value is read at token time and never stored in config or code.
        token_url: The Cognito ``/oauth2/token`` endpoint.
        scope: The OAuth2 scope string requested for the Gateway.
    """

    client_id: str
    client_secret_arn: str
    token_url: str
    scope: str


@dataclass(frozen=True)
class _CachedToken:
    """A minted access token with the data needed to refresh it early."""

    token: str
    lifetime: float  # seconds the token is valid for (from ``expires_in``)
    expires_at: float  # wall-clock epoch second at which it expires


class RoleIdentityProvider:
    """Client-credentials token cache, one entry per role (§8.2).

    Refreshes at 80 percent of the token lifetime (``SKEW``) so a token never expires mid-commit.
    The client secret is read from Secrets Manager by ARN; it never appears in config or code
    (security.md rule 7, R13.9).
    """

    #: Fraction of the lifetime a token may be used for before a refresh is forced.
    SKEW = 0.8

    def __init__(self, configs: dict[str, RoleClientConfig], clock: Clock | None = None) -> None:
        """Create the provider.

        Args:
            configs: Per-role OAuth2 client configuration, keyed by role name.
            clock: Wall-clock source; defaults to :class:`SystemClock`.
        """
        self._configs = configs
        self._clock = clock or SystemClock()
        self._cache: dict[str, _CachedToken] = {}

    def token(self, role: str) -> str:
        """Return a valid access token for ``role``, refreshing before it expires.

        A cached token is reused while it is inside its usable window (``SKEW`` of its lifetime
        still remaining below the skew boundary); otherwise a fresh token is fetched.

        Args:
            role: The ICS role name.

        Returns:
            A bearer access token carrying the role's ``minnal_role`` claim.

        Raises:
            KeyError: If ``role`` has no configured client.
            RuntimeError: If the token endpoint rejects the request.
        """
        entry = self._cache.get(role)
        if entry is not None:
            usable_until = entry.expires_at - entry.lifetime * (1 - self.SKEW)
            if usable_until > self._clock.wall_now():
                return entry.token
        return self._fetch(role).token

    def _fetch(self, role: str) -> _CachedToken:
        """Fetch a fresh token for ``role`` from the Cognito token endpoint and cache it."""
        config = self._configs[role]
        secret = self._read_secret(config.client_secret_arn)
        credentials = base64.b64encode(f"{config.client_id}:{secret}".encode()).decode()
        response = requests.post(
            url=config.token_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": config.scope},
            timeout=_TOKEN_REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != _HTTP_OK:
            raise RuntimeError(
                f"token request for role {role} failed with status {response.status_code}"
            )
        body = response.json()
        access_token = body.get("access_token")
        if not access_token:
            raise RuntimeError(f"token response for role {role} carried no access_token")
        lifetime = float(body.get("expires_in", 0))
        entry = _CachedToken(
            token=access_token,
            lifetime=lifetime,
            expires_at=self._clock.wall_now() + lifetime,
        )
        self._cache[role] = entry
        return entry

    @staticmethod
    def _read_secret(secret_arn: str) -> str:
        """Read a client secret from Secrets Manager by ARN (R13.9, security.md rule 7)."""
        response = _SECRETS.get_secret_value(SecretId=secret_arn)
        return str(response["SecretString"])
