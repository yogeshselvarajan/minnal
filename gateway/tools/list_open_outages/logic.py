"""Pure logic for ``list_open_outages`` (agent-team-runtime §8.6.2).

No I/O, no ``boto3``/``botocore`` (R14.12). Three concerns:

* **Total ordering.** Open outages are ordered by ``(reported_at, outage_id)``
  ascending. ``outage_id`` is a unique ULID, so the order is total; pages are
  therefore complete, disjoint and stable under concurrent inserts (a later
  insert sorts after the current page's last key, and ties break by id).
* **Paging.** Given the full ordered list, a bounded slice after the last key
  from the previous token is returned, plus the last key of that slice as the
  next token's payload.
* **The opaque continuation token.** ``{"i": incident, "f": filter_hash, "k":
  last_key}`` is JSON then base64url, with a keyed BLAKE2b tag appended over the
  payload. The tag is an **integrity** guard, not a secret: it detects a token
  edited to page a different incident or filter, which is rejected upstream as
  ``VALIDATION_ERROR``. The payload is not confidential — it is derived from the
  request the agent already made.

The projection that keeps personal data out of the wire (``untrusted_note`` only,
never a callback number/token/name, R14.7) is applied in the handler over the
:class:`OutageView` this module returns.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Sequence
from dataclasses import dataclass

from _shared.errors import InputValidationError
from _shared.models import Symptom

_TOKEN_KEY = b"minnal/list_open_outages/continuation/v1"
"""Fixed application key for the token's BLAKE2b tag (integrity, not a secret)."""

_TAG_BYTES = 16
"""BLAKE2b digest size for the token tag (128 bits is ample for tamper detection)."""

_NO_FILTER = "*"
"""Filter-hash input when no substation filter is set (distinct from any sub id)."""


@dataclass(frozen=True, slots=True)
class OutageView:
    """One open outage reduced to the fields the tool may return (R14.6, R14.7)."""

    outage_id: str
    supplying_dt_id: str | None
    symptom: Symptom
    is_emergency: bool
    reported_at: str
    untrusted_note: str | None


@dataclass(frozen=True, slots=True)
class Page:
    """A bounded, ordered slice of open outages and the next-page cursor."""

    outages: tuple[OutageView, ...]
    next_key: str | None  # None when this is the last page


def sort_key(outage: OutageView) -> tuple[str, str]:
    """Return the total-order key ``(reported_at, outage_id)`` (§8.6.2)."""
    return (outage.reported_at, outage.outage_id)


def _encoded_key(outage: OutageView) -> str:
    """Return the opaque last-key string for a token: ``reported_at|outage_id``."""
    return f"{outage.reported_at}|{outage.outage_id}"


def filter_hash(substation_id: str | None) -> str:
    """Return a stable hash of the filter, binding a token to its query (§8.6.2)."""
    material = substation_id if substation_id is not None else _NO_FILTER
    return hashlib.blake2b(material.encode("utf-8"), digest_size=_TAG_BYTES).hexdigest()


def paginate(outages: Sequence[OutageView], page_size: int, after_key: str | None) -> Page:
    """Return the page of ``page_size`` outages after ``after_key`` (§8.6.2).

    Args:
        outages: The open outages to page (any order; sorted here).
        page_size: The bounded page size (already validated to 1..500).
        after_key: The last key from the previous token, or None for page one.

    Returns:
        A :class:`Page` with the ordered slice and the next cursor (None when the
        slice reaches the end).
    """
    ordered = sorted(outages, key=sort_key)
    start = 0
    if after_key is not None:
        start = _first_index_after(ordered, after_key)
    window = ordered[start : start + page_size]
    reached_end = start + page_size >= len(ordered)
    next_key = None if reached_end or not window else _encoded_key(window[-1])
    return Page(outages=tuple(window), next_key=next_key)


def _first_index_after(ordered: Sequence[OutageView], after_key: str) -> int:
    """Return the index of the first outage strictly after ``after_key``."""
    for index, outage in enumerate(ordered):
        if _encoded_key(outage) > after_key:
            return index
    return len(ordered)


def encode_token(incident_id: str, filter_hash_hex: str, last_key: str) -> str:
    """Opaque, incident-scoped continuation token (§8.6.2).

    Payload ``{"i": incident_id, "f": filter_hash, "k": last_key}``, JSON, then
    base64url; a keyed BLAKE2b tag over the payload is appended so a tampered
    token is rejected with ``VALIDATION_ERROR`` rather than silently paging a
    different incident.
    """
    payload = json.dumps(
        {"i": incident_id, "f": filter_hash_hex, "k": last_key},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    tag = _tag(payload)
    body = base64.urlsafe_b64encode(payload).decode("ascii")
    return f"{body}.{tag}"


def decode_token(token: str, incident_id: str, filter_hash_hex: str) -> str:
    """Return the ``last_key`` from a token, or reject a bad/foreign one (§8.6.2).

    Raises:
        InputValidationError: The token is malformed, its tag does not verify, or
            it was minted for a different incident or filter (R14.6).
    """
    body, _, tag = token.partition(".")
    if not body or not tag:
        raise InputValidationError("Malformed continuation token.")
    try:
        payload = base64.urlsafe_b64decode(body.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise InputValidationError("Malformed continuation token.") from exc
    if not hmac.compare_digest(tag, _tag(payload)):
        raise InputValidationError("Continuation token failed its integrity check.")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InputValidationError("Malformed continuation token.") from exc
    if (
        not isinstance(data, dict)
        or data.get("i") != incident_id
        or data.get("f") != filter_hash_hex
    ):
        raise InputValidationError("Continuation token does not match this query.")
    last_key = data.get("k")
    if not isinstance(last_key, str):
        raise InputValidationError("Malformed continuation token.")
    return last_key


def _tag(payload: bytes) -> str:
    """Return the keyed BLAKE2b tag (hex) over a token payload."""
    return hashlib.blake2b(payload, key=_TOKEN_KEY, digest_size=_TAG_BYTES).hexdigest()
