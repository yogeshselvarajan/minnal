"""The eight worked idempotency-key examples from design §6.3 reproduce exactly (task 15.2).

The byte layout of ``derive_idempotency_key`` is fixed so that two implementations agree
(design §6.3). This test pins the eight worked examples the design computed, so the layout
cannot drift silently: a change to the tag, the separator, the field order or the bit-clearing
would change at least one of these digests and fail here.

Validates: Requirements 15.1, 15.2, 15.7.
"""

from __future__ import annotations

import re

# Pattern-root import resolves via the conftest ``sys.path`` insert (ruff third-party group).
from domain.ids import derive_idempotency_key  # type: ignore[import-not-found]

_KEY_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")

# Shared inputs for every worked example (design §6.3).
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_PERIOD = 3
_ITEM = "itm_dsp_0007"

# (node, iteration, clearance, expected_key) — the eight rows of the §6.3 table, verbatim.
_WORKED_EXAMPLES: tuple[tuple[str, int, str | None, str], ...] = (
    ("safety", 0, None, "0W8AKBSFNYVAN91KPA9NDS5TCE"),
    ("safety", 1, None, "12B2TMQCZZRCN19HPHMVF2FS8J"),
    ("dispatch_plan", 0, None, "1571ESZ5WFVHSB8W3SZ6PVY79T"),
    ("dispatch_plan", 1, None, "1N1N5N6TPA793TVAHBHK3ZFEGC"),
    ("dispatch_commit", 0, "sfc_01HGW0000000000000000001", "1X7FSKGAZ0M33KD3YJFD040YC2"),
    ("dispatch_commit", 1, "sfc_01HGW0000000000000000001", "1RYFGCKN8DJRFNRQTDZ4PSR9YT"),
    ("dispatch_commit", 0, "sfc_01HGW0000000000000000002", "1Y5AD6DXNZWN72VSWF779VJCNS"),
    ("dispatch_commit", 1, "sfc_01HGW0000000000000000002", "1CVH02K64EJDD64MY1GNK7CC4Y"),
)


def test_worked_examples_reproduce_exactly() -> None:
    """Each of the eight §6.3 rows derives its exact key, so the byte layout cannot drift."""
    for node, iteration, clearance, expected in _WORKED_EXAMPLES:
        # Act.
        derived = derive_idempotency_key(_INCIDENT, _PERIOD, node, _ITEM, iteration, clearance)
        # Assert: exact reproduction (R15.1, R15.7) and valid ULID form (R15.2).
        assert derived == expected, (
            f"{node} it={iteration} clearance={clearance}: "
            f"derived {derived!r}, design §6.3 says {expected!r}"
        )
        assert _KEY_RE.match(derived)


def test_all_eight_keys_are_distinct() -> None:
    """All eight worked keys are distinct, as §6.3 states (no two attempts collide)."""
    keys = [row[3] for row in _WORKED_EXAMPLES]
    assert len(set(keys)) == len(keys)
