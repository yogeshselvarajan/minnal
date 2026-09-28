"""Total event ordering and public/truth sequence numbering (pure core, no boto3).

Implements the replay-simulator design section "Ordering (``ordering.py``)" and
requirements R11.1-R11.4 and R15.3. Two responsibilities, both pure:

1. :func:`total_order` imposes the criterion-11.2 **total order** on generated
   events: by ``sim_time``, then the fixed event-type rank, then the payload's
   Canonical_Serialisation in ascending code-point order (A13), then the
   Generation_Key (A14). Because every :class:`~simulator.gen_events.GenEvent`
   in a run has a distinct Generation_Key, no two events compare equal, so the
   order is total and deterministic for equal inputs (R11.2), and ``sim_time``
   is non-decreasing (R11.1).

2. :func:`assign_sequences` numbers the events. Public events (every type except
   ``DeviceTripped``) get a **contiguous** 1..N public sequence with no gaps at
   ``DeviceTripped`` positions (R11.3, R15.3): a truth ``DeviceTripped`` never
   consumes a public sequence number. Each ``DeviceTripped`` is paired with the
   public sequence of the last public event before it in the total order, or 0
   if none (R11.4).

## Truth-sequence / attribution seam (design decision, documented per R11.4)

R11.4 gives Truth_Store *entries* — both ``DeviceTripped`` records **and**
per-signal attribution records — a single shared 1..M truth sequence, each
recording the last public sequence emitted before it. Attribution records are
produced by the engine (task 14) from ``GenerationResult.attributions`` and are
**not** visible to this pure module. Assigning a truth sequence here would force
the engine to renumber once it interleaves attributions.

Decision: **this module does not assign the unified truth sequence.** It instead
returns everything the engine needs to assign it while walking a single ordered
pass:

- ``SequencedEvents.public``: the ordered public events, each annotated with its
  final 1-based ``public_sequence`` (gap-free 1..N).
- ``SequencedEvents.truth``: the ordered ``DeviceTripped`` events, each paired
  with its ``last_public_sequence`` at its position in the total order.
- ``SequencedEvents.total``: the full total order (public and truth together), so
  the engine can walk it once, weave attribution writes in at the right
  positions, and assign the unified truth sequence + ``last_public_sequence`` to
  ``DeviceTripped`` records and attribution records alike.

The engine, not this module, therefore owns the unified truth sequence. This
keeps ordering focused on the two things it can decide from events alone — the
total order and the public numbering — and gives task 14 an ergonomic contract.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from simulator.gen_events import GenerationKey, GenEvent, GenEventType

EVENT_TYPE_RANK: Final[dict[GenEventType, int]] = {
    "WeatherTick": 0,
    "FloodPolygonUpdated": 1,
    "DeviceTripped": 2,
    "MeterLastGasp": 3,
    "OutageReported": 4,
}
"""Fixed equal-``sim_time`` event-type ordering (R11.2)."""


def _payload_codepoints(payload: dict[str, object]) -> str:
    """Return the payload's Canonical_Serialisation for the code-point tie-break.

    Uses the same canonicalisation semantics as
    :func:`simulator.envelope.canonical` — UTF-8 JSON, sorted keys, minimal
    whitespace, non-ASCII preserved — but of the ``payload`` alone and without the
    trailing newline or size guard, because R11.2/A13 compares payload strings, not
    whole envelopes. Comparing the returned ``str`` compares Unicode code points in
    ascending order, which is what R11.2 requires.

    Args:
        payload: The event payload as a plain ``dict``.

    Returns:
        The canonical JSON string of the payload.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _order_key(event: GenEvent) -> tuple[str, int, str, GenerationKey]:
    """Return the criterion-11.2 total-order sort key for one event.

    The key is ``(sim_time, event-type rank, payload code points, Generation_Key)``.
    ``sim_time`` is compared as its on-the-wire whole-second string via
    ``isoformat`` on the timezone-aware value, which sorts identically to the
    instants themselves for the UTC times the simulator uses.

    Args:
        event: The event to key.

    Returns:
        A tuple ordered so that :func:`sorted` yields the total order.
    """
    return (
        event.sim_time.isoformat(),
        EVENT_TYPE_RANK[event.event_type],
        _payload_codepoints(event.payload),
        event.generation_key,
    )


def total_order(events: list[GenEvent]) -> list[GenEvent]:
    """Return ``events`` in the criterion-11.2 total order (R11.1, R11.2).

    Sorts by ``sim_time`` (non-decreasing, R11.1), then the fixed event-type rank,
    then the payload's Canonical_Serialisation in ascending code-point order (A13),
    then the Generation_Key (A14). Because every event in a run has a distinct
    Generation_Key, the order is total — no two events compare equal — and the same
    Scenario, Synthetic_Grid and Seed always give the same order (R11.2).

    Args:
        events: The generated events, in any order.

    Returns:
        A new list holding the same events in total order. The input is unchanged.
    """
    return sorted(events, key=_order_key)


@dataclass(frozen=True, slots=True)
class TruthOrder:
    """One ``DeviceTripped`` event in total order with its public-sequence anchor.

    Attributes:
        gen_event: The ordered ``DeviceTripped`` (truth) event.
        last_public_sequence: The public ``sequence`` of the last public event
            emitted before this truth event in the total order, or 0 if no public
            event precedes it in the run (R11.4).
    """

    gen_event: GenEvent
    last_public_sequence: int


@dataclass(frozen=True, slots=True)
class SequencedEvents:
    """The totally-ordered events with public numbering and truth anchors.

    See the module docstring for the truth-sequence / attribution seam: the engine
    assigns the unified 1..M truth sequence by walking :attr:`total`; this object
    supplies the public numbering and each ``DeviceTripped``'s
    ``last_public_sequence``.

    Attributes:
        total: Every event (public and truth) in the criterion-11.2 total order.
        public: The ordered public events, each paired with its gap-free 1-based
            ``public_sequence`` (R11.3, R15.3).
        truth: The ordered ``DeviceTripped`` events, each paired with the public
            sequence of the last public event before it (R11.4).
    """

    total: list[GenEvent]
    public: list[tuple[int, GenEvent]]
    truth: list[TruthOrder]


def assign_sequences(ordered: list[GenEvent]) -> SequencedEvents:
    """Assign the public sequence and each truth event's public-sequence anchor.

    Walks ``ordered`` once. Public events (``kind == "public"``, i.e. every type
    except ``DeviceTripped``) get a contiguous 1..N ``public_sequence`` with no gaps
    at ``DeviceTripped`` positions — a truth event consumes no public number (R11.3,
    R15.3). Each ``DeviceTripped`` records the public sequence emitted just before
    it, or 0 if none yet (R11.4).

    Args:
        ordered: Events already in the criterion-11.2 total order (from
            :func:`total_order`). Passing an unordered list yields a numbering that
            does not match the run; callers should order first.

    Returns:
        A :class:`SequencedEvents` with the full total order, the numbered public
        events and the anchored truth events.
    """
    public: list[tuple[int, GenEvent]] = []
    truth: list[TruthOrder] = []
    last_public_sequence = 0
    for event in ordered:
        if event.kind == "truth":
            truth.append(TruthOrder(gen_event=event, last_public_sequence=last_public_sequence))
        else:
            last_public_sequence += 1
            public.append((last_public_sequence, event))
    return SequencedEvents(total=list(ordered), public=public, truth=truth)
