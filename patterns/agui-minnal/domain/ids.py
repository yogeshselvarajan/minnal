"""Deterministic identifier derivation for items and idempotency keys (§6.1, §6.3).

Pure: imports nothing from ``boto3``/``botocore``/``strands`` and performs no I/O. The byte
layouts here are exact so two implementations agree, and so a re-plan reuses the same item_id.
"""

from __future__ import annotations

import hashlib

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, no I, L, O, U
UNIT_SEP = b"\x1f"  # unit separator; cannot occur inside any Minnal id
ITEM_TAG = b"minnal.item.v1"


def derive_item_id(incident_id: str, operational_period: int, kind: str, subject: str) -> str:
    """Stable item id: ``itm_dsp_<12 hex>`` or ``itm_swi_<12 hex>`` (R8.9).

    ``subject`` is the job_id for a dispatch item and the device_id for a switching item.
    Deliberately excludes crew_id and route_id so that re-planning an item with a different
    crew keeps the same item_id, which is what makes per-item veto counting meaningful across
    Veto_Loop iterations (R8.9).

    Args:
        incident_id: The incident this item belongs to.
        operational_period: The period this item is planned in.
        kind: ``"dispatch"`` or ``"switching"``.
        subject: The job_id (dispatch) or device_id (switching).

    Returns:
        The stable ``itm_<dsp|swi>_<12 hex>`` id.
    """
    prefix = "dsp" if kind == "dispatch" else "swi"
    payload = UNIT_SEP.join(
        [
            ITEM_TAG,
            incident_id.encode(),
            str(operational_period).encode(),
            kind.encode(),
            subject.encode(),
        ]
    )
    digest = hashlib.blake2b(payload, digest_size=6).hexdigest()
    return f"itm_{prefix}_{digest}"


def crockford_encode_128(value: int) -> str:
    """Encode a 128-bit integer as 26 Crockford base32 characters, most significant first.

    26 x 5 = 130 bits, so the top two bits of the first character are padding.

    Args:
        value: A non-negative integer below ``2**128``.

    Returns:
        The 26-character Crockford base32 string.
    """
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in range(25, -1, -1))
