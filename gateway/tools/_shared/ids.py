"""ULID generation and typed-prefix helpers (design §4.3).

Every id on the wire is a ULID with a type prefix, so a reader can tell what an
id refers to and a validator can reject a mismatched one fast (R1.11). The real
check always happens against the store; the prefix is only a cheap first gate.
"""

from __future__ import annotations

import re
from typing import Final

from ulid import ULID

# Crockford base32 alphabet used by ULID: no I, L, O or U.
_ULID_BODY: Final[str] = r"[0-9A-HJKMNP-TV-Z]{26}"

PREFIXES: Final[frozenset[str]] = frozenset(
    {"out", "fck", "sfc", "prp", "wo", "ttr", "rte", "corr", "inc"}
)

_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    prefix: re.compile(rf"^{prefix}_{_ULID_BODY}$") for prefix in PREFIXES
}


def new_ulid() -> str:
    """Return a fresh 26-character Crockford base32 ULID with no prefix."""
    return str(ULID())


def new_id(prefix: str) -> str:
    """Return a fresh prefixed id such as ``out_01J...`` (R1.11)."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix!r}")
    return f"{prefix}_{new_ulid()}"


def pattern_for(prefix: str) -> str:
    """Return the anchored regex string for a prefixed id, for Pydantic fields."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix!r}")
    return rf"^{prefix}_{_ULID_BODY}$"


def is_valid(prefix: str, value: str) -> bool:
    """Return whether ``value`` is a well-formed id for ``prefix``."""
    matcher = _PATTERNS.get(prefix)
    return matcher is not None and matcher.match(value) is not None


def require(prefix: str, value: str) -> str:
    """Return ``value`` if it matches ``prefix``, else raise ``ValueError``."""
    if not is_valid(prefix, value):
        raise ValueError(f"expected a {prefix}_ id, got {value!r}")
    return value
