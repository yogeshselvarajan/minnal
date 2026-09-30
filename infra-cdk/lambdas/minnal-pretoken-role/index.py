"""Pre-token generation Lambda (V3) for Minnal's per-role machine identities.

Fires on the Client Credentials (M2M) grant only. It maps the calling app
client (``event.callerContext.clientId``) to an ICS role using a map sourced
from SSM at cold start, and injects a single custom claim ``minnal_role`` so the
AgentCore Gateway can expose it as ``principal.getTag("minnal_role")`` for the
Cedar permits (agent-team-runtime §19.3, R24.2).

This Lambda contains no secret and no business logic: a table lookup and a claim.
The client-id -> role map is written by CDK to a single SSM parameter at deploy
time; this handler only reads it. A client not in the map, or a non-M2M flow, is
passed through unchanged with no ``minnal_role`` claim, so an unmapped identity
gets no role rather than a default one.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3

# SSM client and the parameter name are created once at module scope; the map is
# read lazily and cached so the parameter is fetched at most once per container.
_SSM = boto3.client("ssm")
_ROLE_MAP_PARAM = os.environ["ROLE_MAP_PARAM"]
_ROLE_MAP_CACHE: dict[str, str] | None = None


def _role_map() -> dict[str, str]:
    """Read and cache the client-id -> role map from SSM (agent-team-runtime §19.3)."""
    global _ROLE_MAP_CACHE  # noqa: PLW0603 - one-time per-container cache
    if _ROLE_MAP_CACHE is None:
        raw = _SSM.get_parameter(Name=_ROLE_MAP_PARAM)["Parameter"]["Value"]
        parsed = json.loads(raw)
        _ROLE_MAP_CACHE = {str(k): str(v) for k, v in parsed.items()}
    return _ROLE_MAP_CACHE


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Inject ``minnal_role`` into the M2M access token for a mapped client."""
    if event.get("triggerSource") != "TokenGeneration_ClientCredentials":
        return event

    client_id = (event.get("callerContext") or {}).get("clientId", "")
    role = _role_map().get(client_id)
    if not role:
        return event

    event.setdefault("response", {})
    event["response"]["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {"claimsToAddOrOverride": {"minnal_role": role}}
    }
    return event
