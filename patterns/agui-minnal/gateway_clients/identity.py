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

When per-role identities cannot be provisioned, :class:`SharedIdentityProvider` implements the
R13.6 fallback (§8.4): one shared machine identity for every role, per-agent restriction resting on
the ``ToolFilters`` of §8.1. :func:`make_identity_provider` selects between the two by deployment
configuration. The consequence for Property 46 is recorded in ADR 0007.
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


class IdentityProvider(Protocol):
    """The single method the client registry needs: a bearer token for a role.

    Both :class:`RoleIdentityProvider` (per-role identities) and
    :class:`SharedIdentityProvider` (the R13.6 fallback) satisfy it, so the registry is
    unchanged by which one is configured.
    """

    def token(self, role: str) -> str: ...


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
        entry = _mint_token(self._configs[role], self._clock, label=role)
        self._cache[role] = entry
        return entry


def _read_secret(secret_arn: str) -> str:
    """Read a client secret from Secrets Manager by ARN (R13.9, security.md rule 7)."""
    response = _SECRETS.get_secret_value(SecretId=secret_arn)
    return str(response["SecretString"])


def _mint_token(config: RoleClientConfig, clock: Clock, *, label: str) -> _CachedToken:
    """Mint one client-credentials access token from the Cognito token endpoint.

    Args:
        config: The OAuth2 client credentials; the secret is read from Secrets Manager by ARN.
        clock: The wall clock used to stamp the token's expiry.
        label: A name used only in error messages (a role, or the shared identity's label).

    Returns:
        The minted token with its lifetime and absolute expiry.

    Raises:
        RuntimeError: If the token endpoint rejects the request or returns no token.
    """
    secret = _read_secret(config.client_secret_arn)
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
        raise RuntimeError(f"token request for {label} failed with status {response.status_code}")
    body = response.json()
    access_token = body.get("access_token")
    if not access_token:
        raise RuntimeError(f"token response for {label} carried no access_token")
    lifetime = float(body.get("expires_in", 0))
    return _CachedToken(
        token=access_token,
        lifetime=lifetime,
        expires_at=clock.wall_now() + lifetime,
    )


#: The label under which the shared machine identity's single token is cached.
_SHARED_LABEL = "shared"


class SharedIdentityProvider:
    """The R13.6 fallback: one shared machine identity for every role (§8.4).

    Used when per-role identities cannot be provisioned (the user pool is not on the Essentials
    plan, or the ``V3_0`` pre-token trigger cannot be registered). Every role receives the same
    token, which carries a single ``minnal_role`` tag; the three ``grid-tools`` Cedar permits are
    collapsed into one permit for ``principal.hasTag("minnal_role")`` with every ``forbid``
    unchanged (``grid-tools`` §10.3). Per-agent restriction then rests entirely on the
    ``ToolFilters`` of §8.1, which the client registry applies per role regardless of identity.

    Consequence recorded in ADR 0007: under this fallback Property 46 weakens from "the identity
    matches the permit" to "the client selected matches ``TOOL_IDENTITY``", because every identity
    carries the same claim. No safety property is lost: ``grid-tools`` P1, P2 and P26 rest on the
    ``forbid`` rules and the tool-side re-checks, not on which agent called.
    """

    #: Same early-refresh fraction as the per-role provider.
    SKEW = 0.8

    def __init__(self, config: RoleClientConfig, clock: Clock | None = None) -> None:
        """Create the shared provider.

        Args:
            config: The single shared machine identity's OAuth2 client configuration.
            clock: Wall-clock source; defaults to :class:`SystemClock`.
        """
        self._config = config
        self._clock = clock or SystemClock()
        self._entry: _CachedToken | None = None

    def token(self, role: str) -> str:
        """Return the shared access token, refreshing before it expires (``role`` is ignored)."""
        entry = self._entry
        if entry is not None:
            usable_until = entry.expires_at - entry.lifetime * (1 - self.SKEW)
            if usable_until > self._clock.wall_now():
                return entry.token
        self._entry = _mint_token(self._config, self._clock, label=_SHARED_LABEL)
        return self._entry.token


def make_identity_provider(
    *,
    per_role_configs: dict[str, RoleClientConfig] | None,
    shared_config: RoleClientConfig | None,
    use_shared_identity: bool,
    clock: Clock | None = None,
) -> IdentityProvider:
    """Select the identity provider by configuration (R13.6, §8.4).

    The choice is a deployment-configuration decision, not a runtime one: ``use_shared_identity``
    is set by the CDK/config when per-role identities could not be provisioned. The registry is
    unaffected — both providers satisfy :class:`IdentityProvider`.

    Args:
        per_role_configs: Per-role OAuth2 client configuration, required when not using the shared
            identity.
        shared_config: The single shared machine identity's configuration, required for the
            fallback.
        use_shared_identity: When ``True``, build the R13.6 shared-identity fallback.
        clock: Wall-clock source; defaults to :class:`SystemClock`.

    Returns:
        A :class:`RoleIdentityProvider` normally, or a :class:`SharedIdentityProvider` under the
        fallback.

    Raises:
        ValueError: If the configuration required for the selected mode is missing.
    """
    if use_shared_identity:
        if shared_config is None:
            raise ValueError("use_shared_identity is set but no shared_config was provided")
        return SharedIdentityProvider(shared_config, clock)
    if per_role_configs is None:
        raise ValueError("per-role identity selected but no per_role_configs were provided")
    return RoleIdentityProvider(per_role_configs, clock)
