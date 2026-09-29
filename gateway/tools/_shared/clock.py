"""The two clocks, which are never interchangeable (design §9.1).

``wall_now()`` is real time and governs anything a human experiences: clearance
expiry, approval timeouts, latency. ``incident_now()`` is the latest ingested
simulated time and governs flood staleness and event ordering. There is no
generic ``now()``: a reviewer who sees ``wall_now()`` in a staleness check, or
``incident_now()`` in an expiry check, has found a bug (R1.11, R6.4, R11.6).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Two differently named readings; deliberately no ``now()``."""

    def wall_now(self) -> str:
        """Return real wall-clock time as ISO 8601 UTC with a ``Z`` suffix."""
        ...

    def incident_now(self, incident_id: str) -> str | None:
        """Return the latest ingested simulated time for an incident, or None."""
        ...


class FrozenClock:
    """A controllable clock for tests; both readings are set independently."""

    def __init__(
        self,
        *,
        wall: str,
        incident: dict[str, str] | None = None,
    ) -> None:
        self._wall = wall
        self._incident: dict[str, str] = dict(incident or {})

    def wall_now(self) -> str:
        return self._wall

    def incident_now(self, incident_id: str) -> str | None:
        return self._incident.get(incident_id)

    def set_wall(self, wall: str) -> None:
        self._wall = wall

    def set_incident(self, incident_id: str, sim_time: str) -> None:
        self._incident[incident_id] = sim_time
