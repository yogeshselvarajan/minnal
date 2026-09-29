"""Property 16 [SAFETY]: an unreadable flood store never reports "clear".

Validates R6.7, R1.10.

*For all* failure modes of the flood store (timeout, throttle, 5xx, malformed
item), ``check_flood_geofence`` returns ``UPSTREAM_ERROR`` and never
``intersects: false``; no clearance is issued; and no proposal tool creates a
Work_Order (design §18 P16, §7.4.7, §11.2).

The invariant that makes all of that true lives in the flood store and the
check→clearance flow: a failed read **raises** — it never returns a ``FloodSet``
a caller could read as clear — and the raised error is transient (``ClientError``
the handler maps to ``UPSTREAM_ERROR`` per §11.2, or an ``UpstreamError``
subclass that already carries a retryable ``UPSTREAM_ERROR``/``RATE_LIMITED``
code). Because ``check_flood_geofence`` only builds an outcome from a
successfully read ``FloodSet``, and ``clearance_for`` mints a clearance only for
a clear outcome, a failed read can reach neither ``intersects: false`` nor a
clearance. This test drives the real ``DynamoFloodStore`` over every failure
mode and asserts it never returns, and models the read→check→clearance flow to
assert no clearance and no "clear" verdict escapes a failed read.

This is a ``[SAFETY]`` property (design §18 safety set): ``@pytest.mark.safety``,
``uv run pytest -m safety``, and the 200-example ``default``/``ci`` profiles from
the suite ``conftest.py``.
"""

from __future__ import annotations

import pytest
from _shared.adapters._aws_dynamo import DynamoFloodStore
from _shared.errors import FloodSnapshotUnstable, MinnalError, UpstreamError
from botocore.exceptions import ClientError
from check_flood_geofence.logic import CheckOutcome, clearance_for
from hypothesis import example, given
from hypothesis import strategies as st

_INCIDENT = "inc_00000000000000000000000000"

# The failure modes the property names, each as the AWS error code / behaviour a
# read of the flood store can hit.
_TRANSIENT_CODES = (
    "ThrottlingException",
    "ProvisionedThroughputExceededException",
    "RequestTimeout",
    "InternalServerError",
    "ServiceUnavailable",
)
_FAILURE_MODES = (*_TRANSIENT_CODES, "malformed_head", "malformed_polygon", "torn_forever")


def _client_error(code: str) -> ClientError:
    """A botocore ``ClientError`` with a 500 status for the transient codes."""
    status = 500 if code in ("InternalServerError", "ServiceUnavailable") else 400
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "GetItem",
    )  # type: ignore[arg-type]


class _FailingTable:
    """A stand-in DynamoTable whose reads reproduce one flood-store failure mode."""

    def __init__(self, mode: str) -> None:
        self._mode = mode
        self._head_reads = 0

    def get(self, pk: str, sk: str, *, consistent: bool = True) -> dict[str, object] | None:
        if self._mode in _TRANSIENT_CODES:
            # A persistent transient error: the bounded retry exhausts and the
            # raw ClientError escapes GetItem (the handler maps it, §11.2).
            raise _client_error(self._mode)
        if sk != "FLOODSET":
            return None
        if self._mode == "malformed_head":
            return {"last_feed_at": "2023-12-05T06:00:00Z"}  # no "version": KeyError on parse
        if self._mode == "torn_forever":
            # Head changes on every read, so no attempt ever agrees (§7.4.7).
            self._head_reads += 1
            return {"version": self._head_reads, "feed_mode": "replay"}
        return {"version": 1, "feed_mode": "replay"}  # malformed_polygon: valid head

    def query_prefix(
        self, pk: str, sk_prefix: str, *, consistent: bool = True
    ) -> list[dict[str, object]]:
        if self._mode == "malformed_polygon":
            return [{"flood_polygon_id": "FP-1"}]  # missing status/geometry: KeyError on parse
        return []


def _store(mode: str) -> DynamoFloodStore:
    return DynamoFloodStore(
        _FailingTable(mode),  # type: ignore[arg-type]
        default_feed_mode="replay",
        snapshot_attempts=2,
        apply_attempts=5,
    )


@pytest.mark.safety
@given(
    mode=st.sampled_from(_FAILURE_MODES),
    budget=st.integers(min_value=1, max_value=6),
    incident=st.text(alphabet="0123456789ABCDEFGHJKMNPQRSTVWXYZ", min_size=26, max_size=26).map(
        lambda body: f"inc_{body}"
    ),
)
@example(mode="ThrottlingException", budget=3, incident=_INCIDENT)  # known-bad: throttle != clear
def test_property_P16_failed_read_never_returns_a_clear_set(
    mode: str, budget: int, incident: str
) -> None:
    """Every flood-store failure mode raises; no read returns a usable set."""
    store = DynamoFloodStore(
        _FailingTable(mode),  # type: ignore[arg-type]
        default_feed_mode="replay",
        snapshot_attempts=budget,
        apply_attempts=5,
    )

    with pytest.raises((ClientError, MinnalError, KeyError, TypeError)) as exc:
        store.get_flood_set(incident)

    # A domain error from a failed read is always transient: the handler surfaces
    # UPSTREAM_ERROR / RATE_LIMITED, never a clear verdict (§11.2, R1.10).
    if isinstance(exc.value, MinnalError):
        assert exc.value.retryable is True
        assert exc.value.code in ("UPSTREAM_ERROR", "RATE_LIMITED")
        assert isinstance(exc.value, UpstreamError)


@pytest.mark.safety
@given(
    mode=st.sampled_from(_FAILURE_MODES),
    purpose=st.sampled_from(("route", "switching")),
    lifetime=st.integers(min_value=1, max_value=240),
    wall_minute=st.integers(min_value=0, max_value=1439),
)
@example(
    mode="malformed_head", purpose="route", lifetime=30, wall_minute=360
)  # known-bad: no clearance from a broken read
def test_property_P16_no_clearance_and_no_clear_verdict_on_failure(
    mode: str, purpose: str, lifetime: int, wall_minute: int
) -> None:
    """The read→check→clearance flow yields neither ``intersects: false`` nor a
    clearance when the flood read fails."""
    store = _store(mode)
    minted: object | None = None
    clear_verdict_seen = False
    wall_now = f"2023-12-05T{wall_minute // 60:02d}:{wall_minute % 60:02d}:00Z"

    try:
        fs = store.get_flood_set(_INCIDENT)
    except (ClientError, MinnalError, KeyError, TypeError):
        fs = None  # the handler stops here and returns UPSTREAM_ERROR

    if fs is not None:  # pragma: no cover - the failing table never yields a set
        outcome = CheckOutcome(intersects=False, bound_to="dt_0001")
        clear_verdict_seen = not outcome.intersects
        minted = clearance_for(outcome, purpose, fs, wall_now, lifetime)  # type: ignore[arg-type]

    # No clear verdict escaped and no clearance was minted from a failed read.
    assert clear_verdict_seen is False
    assert minted is None


def test_flood_snapshot_unstable_is_upstream_error() -> None:
    """The torn-read failure is an UPSTREAM_ERROR that fails closed everywhere."""
    with pytest.raises(FloodSnapshotUnstable) as exc:
        _store("torn_forever").get_flood_set(_INCIDENT)
    assert exc.value.code == "UPSTREAM_ERROR"
    assert exc.value.retryable is True
