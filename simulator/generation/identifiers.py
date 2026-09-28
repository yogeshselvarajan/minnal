"""Deterministic payload-level identifiers and callback tokens (pure core, no boto3).

Payload-level identifiers — report ids, meter ids, ``idempotency_key`` values and
callback tokens — are derived from the Seed and Scenario ID **only**, never from the
reset count (A12), so a reset run replays byte-identical payloads (R13.7) while the
envelope identity changes per reset (R8.12). Derivation uses ``BLAKE2b`` over
length-prefixed key material, matching the hash family used elsewhere (ADR-2).

Callback tokens are synthetic and safe (R9.7): a fixed ``cb-`` prefix followed by
BLAKE2b hex, so no token contains a run of seven or more consecutive digits (hex
digits interleave letters ``a``-``f``) and every token is well under 64 characters.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

_TOKEN_HEX_LEN: Final[int] = 16
"""Hex characters in a callback token body (8 BLAKE2b bytes)."""

_ID_HEX_LEN: Final[int] = 24
"""Hex characters in a derived payload id body (12 BLAKE2b bytes)."""

_MAX_CALLBACK_LEN: Final[int] = 64
"""Callback token length ceiling (R9.3)."""

# A run of seven or more digits, optionally led by ``+`` (R9.7). Used to assert
# callback tokens never look like a phone number.
_DIGIT_RUN = re.compile(r"\+?\d{7,}")


def _digest(*parts: str, size: int) -> str:
    """Return a lowercase hex BLAKE2b digest over length-prefixed string parts."""
    hasher = hashlib.blake2b(digest_size=size)
    for part in parts:
        encoded = part.encode("utf-8")
        hasher.update(len(encoded).to_bytes(4, "big"))
        hasher.update(encoded)
    return hasher.hexdigest()


def _derive(kind: str, seed: int, scenario_id: str, *discriminators: str) -> str:
    """Return a deterministic hex body from Seed + Scenario + discriminators (A12)."""
    return _digest(kind, str(seed), scenario_id, *discriminators, size=_ID_HEX_LEN // 2)


def report_id(seed: int, scenario_id: str, source_report_id: str) -> str:
    """Derive the run-unique ``report_id`` for a scenario report (A12).

    Args:
        seed: The run seed.
        scenario_id: The Scenario ID.
        source_report_id: The report's ``id`` in the Scenario (unique in-file).

    Returns:
        A ``rep_<hex>`` identifier, deterministic in Seed + Scenario only.
    """
    return f"rep_{_derive('report', seed, scenario_id, source_report_id)}"


def noise_report_id(seed: int, scenario_id: str, ordinal: int) -> str:
    """Derive the ``report_id`` for the ``ordinal``-th noise report (A12).

    Args:
        seed: The run seed.
        scenario_id: The Scenario ID.
        ordinal: The 0-based index of this noise report within the run.

    Returns:
        A ``rep_<hex>`` identifier disjoint from scenario-report ids.
    """
    return f"rep_{_derive('noise-report', seed, scenario_id, str(ordinal))}"


def idempotency_key(seed: int, scenario_id: str, source_report_id: str) -> str:
    """Derive the ``idempotency_key`` for a distinct (non-duplicate) report (A12, R9.8).

    Distinct reports get distinct keys; a duplicate report reuses its original's
    key (handled by ``duplicates.py``), so the key is derived from the *original*
    report's scenario id.

    Args:
        seed: The run seed.
        scenario_id: The Scenario ID.
        source_report_id: The originating report's Scenario ``id``.

    Returns:
        An ``idk_<hex>`` idempotency key, deterministic in Seed + Scenario only.
    """
    return f"idk_{_derive('idempotency', seed, scenario_id, source_report_id)}"


def meter_id(seed: int, scenario_id: str, dt_id: str) -> str:
    """Derive the ``meter_id`` for the meter on ``dt_id`` (A12; one gasp per meter).

    Args:
        seed: The run seed.
        scenario_id: The Scenario ID.
        dt_id: The supplying DT id.

    Returns:
        An ``mtr_<hex>`` identifier, deterministic in Seed + Scenario only.
    """
    return f"mtr_{_derive('meter', seed, scenario_id, dt_id)}"


def callback_token(seed: int, scenario_id: str, discriminator: str) -> str:
    """Derive a synthetic, PII-free callback token (R9.3, R9.7, A12).

    The token is ``cb-`` plus a 16-char BLAKE2b hex body: well under 64 characters
    and free of any 7+ digit run (hex mixes letters with digits).

    Args:
        seed: The run seed.
        scenario_id: The Scenario ID.
        discriminator: A per-report discriminator (its Scenario or run id).

    Returns:
        A ``cb-<hex>`` callback token.
    """
    body = _digest("callback", str(seed), scenario_id, discriminator, size=_TOKEN_HEX_LEN // 2)
    # Group the hex body into 4-char blocks separated by '-' so the token can never
    # contain a run of 7+ digits (max digit run is 4), satisfying R9.7 by construction.
    grouped = "-".join(body[i : i + 4] for i in range(0, len(body), 4))
    token = f"cb-{grouped}"
    # Defensive: the derivation cannot exceed the ceiling, but assert the contract.
    if len(token) > _MAX_CALLBACK_LEN:  # pragma: no cover - impossible for fixed lengths
        token = token[:_MAX_CALLBACK_LEN]
    return token


def has_forbidden_digit_run(value: str) -> bool:
    """Return whether ``value`` contains a run of 7+ digits, optionally led by ``+`` (R9.7).

    Args:
        value: The candidate payload string field value.

    Returns:
        ``True`` when a forbidden phone-number-like digit run is present.
    """
    return _DIGIT_RUN.search(value) is not None
