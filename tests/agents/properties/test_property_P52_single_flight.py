"""Property 52: single-flight per incident.

*For all* interleavings of concurrent start requests for one incident, exactly one acquires the
lease and proceeds; every other request is rejected with ``CONFLICT`` and starts no Graph; an
expired lease is reclaimable; and a run whose lease expired cannot delete its successor's lease
(design §20 Property 52, §11.2, §11.3).

Validates: Requirements 3.15, 3.14.

Two halves, both pure:

* The **numbering** half (R3.14, R3.15) is asserted directly against ``validate_period_request``
  (design §6.4): a period below 1 is a ``VALIDATION_ERROR``; with history it must equal
  ``last_completed + 1``; without history it is trusted with ``sequence_trusted=False``.
* The **single-flight** half (R3.15) is asserted against a pure in-test model of the §11.2 lease
  algorithm — a conditional put that succeeds only when there is no lease or the existing one has
  expired, and a conditional delete that only removes the caller's own token. The lease adapter
  itself is DynamoDB I/O built in a later wave; modelling the exact conditional-write semantics
  here lets Property 52 be proved now without importing ``boto3``. When the adapter lands it is a
  thin wrapper over these same two conditions.

The known-bad ``@example`` is the interleaving the property exists to catch: two concurrent
requests for the same incident with no pre-existing lease — exactly one must win, the other must
get ``CONFLICT``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Pattern-root import resolves via the conftest ``sys.path`` insert (ruff third-party group).
from domain.periods import (  # type: ignore[import-not-found]
    PeriodRequest,
    validate_period_request,
)
from hypothesis import example, given
from hypothesis import strategies as st

_LEASE_SECONDS = 600  # design §11.2 LEASE_SECONDS, comfortably above the period budget.


# ---------------------------------------------------------------------------
# A pure model of the §11.2 lease table for one incident. No I/O, no boto3.
# ---------------------------------------------------------------------------
class ConflictError(Exception):
    """Raised when a live lease already exists (the CONFLICT the real adapter returns)."""


@dataclass
class LeaseModel:
    """The exact conditional-write semantics of §11.2, as a pure in-memory model."""

    holder_token: str | None = None
    expires_at: int | None = None  # seconds since an arbitrary epoch
    _counter: int = field(default=0)

    def acquire(self, now: int) -> str:
        """Conditional put: succeeds only when no lease exists or the existing one expired."""
        has_lease = self.holder_token is not None and self.expires_at is not None
        live = has_lease and now < (self.expires_at or 0)
        if live:
            raise ConflictError("a period is already running for this incident")
        self._counter += 1
        token = f"lt_{self._counter:026d}"
        self.holder_token = token
        self.expires_at = now + _LEASE_SECONDS
        return token

    def release(self, token: str) -> bool:
        """Conditional delete: removes the lease only when the caller owns the current token."""
        if self.holder_token != token:
            return False  # a run that lost its lease cannot delete its successor's
        self.holder_token = None
        self.expires_at = None
        return True


# ---------------------------------------------------------------------------
# Single-flight half (R3.15)
# ---------------------------------------------------------------------------
@given(
    n_requests=st.integers(min_value=2, max_value=6),
    pre_existing=st.sampled_from(["none", "live", "expired"]),
)
@example(n_requests=2, pre_existing="none")  # known-bad: two racers, no lease
def test_property_P52_single_flight(n_requests: int, pre_existing: str) -> None:
    """Exactly one concurrent request wins the lease; the rest get CONFLICT (R3.15)."""
    # Arrange: a lease, optionally holding a pre-existing live or expired lease.
    lease = LeaseModel()
    now = 1_000_000
    if pre_existing == "live":
        lease.acquire(now)  # a live lease held by an earlier run
        request_now = now + 1  # requests arrive while it is still live
        expected_wins = 0
    elif pre_existing == "expired":
        lease.acquire(now)
        request_now = now + _LEASE_SECONDS + 1  # the earlier lease has expired -> reclaimable
        expected_wins = 1
    else:
        request_now = now
        expected_wins = 1

    # Act: n concurrent start requests, serialised in an arbitrary interleaving. A conditional
    # put is atomic, so any interleaving reduces to some serial order; we try each request once.
    wins = 0
    conflicts = 0
    for _ in range(n_requests):
        try:
            lease.acquire(request_now)
            wins += 1
        except ConflictError:
            conflicts += 1

    # Assert: at most one winner among the racers, and the rest are CONFLICTs.
    assert wins == expected_wins
    assert conflicts == n_requests - expected_wins


def test_property_P52_expired_lease_is_reclaimable_but_successor_is_safe() -> None:
    """An expired lease is reclaimable, and the overrun run cannot delete its successor (R3.15)."""
    # Arrange: run A takes the lease, then overruns past expiry.
    lease = LeaseModel()
    now = 2_000_000
    token_a = lease.acquire(now)

    # Act: run B reclaims the expired lease after expiry.
    token_b = lease.acquire(now + _LEASE_SECONDS + 5)

    # Assert: B holds a distinct token; A's late release is refused (owns nothing now); B's
    # release succeeds. This is the "cannot delete its successor's lease" guarantee.
    assert token_b != token_a
    assert lease.release(token_a) is False
    assert lease.release(token_b) is True


# ---------------------------------------------------------------------------
# Numbering half (R3.14, R3.15)
# ---------------------------------------------------------------------------
@given(
    period=st.integers(min_value=-5, max_value=50),
    last_completed=st.one_of(st.none(), st.integers(min_value=0, max_value=50)),
    history_available=st.booleans(),
)
@example(period=0, last_completed=3, history_available=True)  # known-bad: period below 1
def test_property_P52_period_numbering_is_last_completed_plus_one(
    period: int, last_completed: int | None, history_available: bool
) -> None:
    """Period numbering obeys R3.14/R3.15: >=1, last+1 with history, trusted without."""
    # Arrange.
    request = PeriodRequest(incident_id="inc_x", operational_period=period)

    # Act.
    verdict = validate_period_request(request, last_completed, history_available)

    # Assert.
    if period < 1:
        assert not verdict.ok and verdict.error_code == "VALIDATION_ERROR"
    elif not history_available:
        # History unavailable: trust the request but mark the sequence unverified (R3.15).
        assert verdict.ok and verdict.sequence_trusted is False
    else:
        expected = (last_completed or 0) + 1
        if period == expected:
            assert verdict.ok and verdict.sequence_trusted is True
        else:
            assert not verdict.ok and verdict.error_code == "VALIDATION_ERROR"
