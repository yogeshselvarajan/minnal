"""Deterministic identifier derivation for items and idempotency keys (§6.1, §6.3).

Pure: imports nothing from ``boto3``/``botocore``/``strands`` and performs no I/O. The byte
layouts here are exact so two implementations agree, and so a re-plan reuses the same item_id.
"""

from __future__ import annotations

import hashlib

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, no I, L, O, U
UNIT_SEP = b"\x1f"  # unit separator; cannot occur inside any Minnal id
ITEM_TAG = b"minnal.item.v1"
DOMAIN_TAG = b"minnal.idem.v1"


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


def derive_idempotency_key(  # noqa: PLR0913, PLR0917 - design §6.3 fixes this byte layout
    incident_id: str,
    operational_period: int,
    node: str,
    item_id: str,
    veto_loop_iteration: int,
    safety_clearance_id: str | None = None,
) -> str:
    """Deterministic, valid ULID idempotency key (R15.1, R15.2, R15.8).

    Byte layout, joined by ``0x1F`` (unit separator, which cannot occur in any Minnal id)::

        b"minnal.idem.v1" 0x1F
        incident_id       0x1F
        str(period)       0x1F
        node "#" str(iteration)   0x1F
        item_id
        [0x1F safety_clearance_id]      # commit calls only

    BLAKE2b with ``digest_size=16`` gives 128 bits; clearing the top two bits keeps the value
    below ``2**126`` so the 48-bit timestamp field cannot overflow and the first Crockford
    character is 0 or 1, inside the valid ULID range 0-7. Including ``safety_clearance_id`` in a
    commit key makes a re-planned clearance a distinct key, so a retry cannot collide with an
    earlier attempt and provoke a spurious CONFLICT (R15.8). Including the iteration in every
    key stops a re-plan returning a stale stored route (R15.1).

    Args:
        incident_id: The incident.
        operational_period: The period.
        node: The graph node making the write (e.g. ``"dispatch_plan"``).
        item_id: The item the write is for.
        veto_loop_iteration: The current Veto_Loop iteration; distinct per iteration (R15.1).
        safety_clearance_id: The clearance bound to this commit, on commit calls only.

    Returns:
        A 26-character Crockford ULID matching ``^[0-7][0-9A-HJKMNP-TV-Z]{25}$``.
    """
    parts = [
        DOMAIN_TAG,
        incident_id.encode(),
        str(operational_period).encode(),
        f"{node}#{veto_loop_iteration}".encode(),
        item_id.encode(),
    ]
    if safety_clearance_id is not None:
        parts.append(safety_clearance_id.encode())
    digest = hashlib.blake2b(UNIT_SEP.join(parts), digest_size=16).digest()
    value = int.from_bytes(digest, "big") & ((1 << 126) - 1)
    return crockford_encode_128(value)
