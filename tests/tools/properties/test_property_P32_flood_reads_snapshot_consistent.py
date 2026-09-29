"""Property 32 [SAFETY]: flood reads are snapshot-consistent, or they fail.

Validates R3.11, R6.7.

*For all* interleavings of flood applies and flood reads, every ``FloodSet`` a
tool receives is a snapshot that genuinely existed: its ``version`` matches the
head at both the start and the end of the read, and no polygon in it records a
``changed_in_version`` greater than that head. When no such snapshot can be
obtained within the attempt budget, the read raises and the tool surfaces
``UPSTREAM_ERROR`` rather than a mixture, and **no** inconsistent snapshot is
ever placed in the hazard-index cache (design §18 P32, §7.4.7, §8.5).

The generator drives the real ``DynamoFloodStore.get_flood_set`` snapshot loop
over a store whose ``get``/``query`` are backed by a scripted sequence of
``(head1, polygons, head2)`` triples — one per attempt — so an apply landing
between the reader's head read and its polygon query (the exact race §7.4.7
guards) is modelled directly. Every triple is a genuine store state; the store
never invents a version. moto is not needed because the snapshot logic is pure
over the three reads; both backends share the same ``_assemble`` code (P27), and
task 35.4 exercises the moto-backed path with fixed cases.

This is a ``[SAFETY]`` property (design §18 safety set), so it carries
``@pytest.mark.safety`` and runs under ``uv run pytest -m safety``. The
``default``/``ci`` Hypothesis profiles (200 examples) are loaded by the suite
``conftest.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from _shared import flood
from _shared.adapters._aws_dynamo import DynamoFloodStore
from _shared.errors import FloodSnapshotUnstable
from hypothesis import example, given
from hypothesis import strategies as st

_INCIDENT = "inc_00000000000000000000000000"
_PK = f"INC#{_INCIDENT}"
_SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [[80.20, 13.00], [80.21, 13.00], [80.21, 13.01], [80.20, 13.01], [80.20, 13.00]]
    ],
}

# One "attempt" the store makes is a (head_version, polygon_change_versions,
# head_version_again) triple. ``None`` head means the incident has no flood set
# yet (an empty, version-0 set — always consistent).
Attempt = tuple[int | None, tuple[int, ...], int | None]


class _ScriptedTable:
    """A stand-in DynamoTable whose reads replay a scripted attempt sequence.

    Each call to ``get_flood_set`` consumes three reads (head, polygons, head);
    this table serves them from the next scripted :data:`Attempt`, so a race that
    changes the head or advances a polygon mid-read is expressed as a triple that
    fails one of the §7.4.7 checks. Reads beyond the script repeat the last
    attempt, so an exhausted budget is deterministic.
    """

    def __init__(self, attempts: Sequence[Attempt]) -> None:
        self._attempts = list(attempts)
        self._head_reads = 0  # head reads seen; two per attempt (head1, head2)

    def _attempt_for_head(self) -> Attempt:
        index = min(self._head_reads // 2, len(self._attempts) - 1)
        return self._attempts[index]

    def _attempt_for_poly(self) -> Attempt:
        # A polygon query happens after head1 of the current attempt: the number
        # of completed head reads is odd, so the current attempt index is n//2.
        index = min(self._head_reads // 2, len(self._attempts) - 1)
        return self._attempts[index]

    def get(self, pk: str, sk: str, *, consistent: bool = True) -> dict[str, object] | None:
        assert consistent is True  # safety reads must be consistent (§7.4.7)
        if sk != "FLOODSET":
            return None
        attempt = self._attempt_for_head()
        is_head1 = self._head_reads % 2 == 0
        head_version = attempt[0] if is_head1 else attempt[2]
        self._head_reads += 1
        return None if head_version is None else _head_item(head_version)

    def query_prefix(
        self, pk: str, sk_prefix: str, *, consistent: bool = True
    ) -> list[dict[str, object]]:
        assert consistent is True  # safety reads must be consistent (§7.4.7)
        changes = self._attempt_for_poly()[1]
        return [_polygon_item(f"FP-{i + 1}", cv) for i, cv in enumerate(changes)]


def _head_item(version: int) -> dict[str, object]:
    return {
        "version": version,
        "last_feed_at": "2023-12-05T06:00:00Z",
        "incident_clock": "2023-12-05T06:00:00Z",
        "feed_mode": "replay",
        "last_feed_received_wall_at": "2023-12-05T06:00:00Z",
    }


def _polygon_item(fp: str, changed_in_version: int) -> dict[str, object]:
    return {
        "flood_polygon_id": fp,
        "geometry": _SQUARE,
        "status": "active",
        "last_sequence": 1,
        "changed_in_version": changed_in_version,
    }


def _store(attempts: Sequence[Attempt], budget: int) -> DynamoFloodStore:
    """A flood store reading from a scripted table with a given attempt budget."""
    table = _ScriptedTable(attempts)
    return DynamoFloodStore(
        table,  # type: ignore[arg-type]
        default_feed_mode="replay",
        snapshot_attempts=budget,
        apply_attempts=5,
    )


# An attempt is consistent iff both heads are present and equal (or both absent),
# and no polygon changed after the head version.
def _is_consistent(attempt: Attempt) -> bool:
    head1, changes, head2 = attempt
    # ``_assemble`` accepts an absent head as the empty, version-0 set (nothing
    # ingested yet); otherwise both heads must agree and no polygon may be newer.
    if head1 is None or head2 is None:
        return True
    return head1 == head2 and all(cv <= head1 for cv in changes)


_versions = st.integers(min_value=0, max_value=8)
_attempt_strategy = st.tuples(
    st.one_of(st.none(), _versions),
    st.lists(_versions, max_size=4).map(tuple),
    st.one_of(st.none(), _versions),
)


@pytest.mark.safety
@given(
    attempts=st.lists(_attempt_strategy, min_size=1, max_size=6),
    budget=st.integers(min_value=1, max_value=6),
)
@example(
    # Known-bad: a polygon whose changed_in_version (5) exceeds a consistent head
    # (2) — a torn read the double-head check alone would miss. Every attempt is
    # this same torn state, so the read must raise, never return the mixture.
    attempts=[(2, (5,), 2)],
    budget=3,
)
def test_property_P32_read_is_consistent_snapshot_or_raises(
    attempts: list[Attempt], budget: int
) -> None:
    """Every returned Flood_Set genuinely existed, or the read fails closed."""
    flood.clear_index_cache()
    store = _store(attempts, budget)

    considered = attempts[:budget]
    any_consistent = any(_is_consistent(a) for a in considered)

    if not any_consistent:
        # No attempt could yield a genuine snapshot: the read must raise, and
        # the cache must be untouched (no torn snapshot cached).
        with pytest.raises(FloodSnapshotUnstable) as exc:
            store.get_flood_set(_INCIDENT)
        assert exc.value.code == "UPSTREAM_ERROR"
        assert not flood._INDEX_CACHE
        return

    fs = store.get_flood_set(_INCIDENT)

    # The returned snapshot is internally consistent: no polygon is newer than
    # the version the reader accepted.
    assert all(p.changed_in_version <= fs.version for p in fs.polygons)

    # Building an index for it caches under exactly that version — never a
    # version that never existed.
    flood.hazard_index(fs, buffer_m=25.0)
    assert all(key[0] != _INCIDENT or key[1] == fs.version for key in flood._INDEX_CACHE)
    flood.clear_index_cache()
