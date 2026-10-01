"""The period table adapter: the single-flight lease and the period record (§11.2, §11.3).

This spec owns exactly one DynamoDB table, ``minnal-<env>-periods``, and this module is the only
place it is written; the spec never writes a ``grid-tools`` table (R12.11). The table holds two
item shapes under one incident partition:

* the **lease** (``sk = "LEASE"``) — a single-flight guard acquired with a conditional put that
  succeeds only when no lease exists or the existing lease has expired (the crash-recovery arm),
  and released with a conditional delete on the lease token so a run that overran and lost its
  lease to expiry cannot delete its successor's lease (§11.2, R3.15);
* the **period record** (``sk = "PERIOD#<zero-padded period>"``) — the terminal status, the
  ``PeriodSummary`` JSON and the audit list, queried oldest-last to find the last completed period
  (§11.3).

This is an **edge adapter**: ``boto3`` lives here, the client is created once at module import,
and every safety-relevant decision is made in pure code elsewhere. Tests drive it with ``moto``
or a botocore ``Stubber``; no live AWS call is made.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from ulid import ULID

#: Lease lifetime, comfortably above the 240 s period wall-clock budget so a healthy run never
#: loses its lease mid-period, while a crashed run's lease still frees on expiry (§11.2).
LEASE_SECONDS: Final[int] = 600

#: Period-record TTL: two days past acquisition, so stale rows self-clean (§11.2).
_RECORD_TTL_DAYS: Final[int] = 2

_TIME_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"
_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "degraded", "truncated", "failed"}
)


class ConflictError(RuntimeError):
    """A live lease already exists for the incident (maps to the ``CONFLICT`` envelope, R3.15)."""


def _iso(moment: datetime) -> str:
    """Render a datetime as ISO 8601 UTC ``Z`` (the on-the-wire time format, §9.1)."""
    return moment.astimezone(UTC).strftime(_TIME_FORMAT)


def _pk(incident_id: str) -> str:
    return f"INC#{incident_id}"


def _period_sk(period: int) -> str:
    """The zero-padded period sort key so lexical order equals numeric order (§11.3)."""
    return f"PERIOD#{period:06d}"


class PeriodTable:
    """The one table this spec writes: the single-flight lease and the period record (§11.2)."""

    def __init__(self, table: Any) -> None:
        """Create the adapter over a DynamoDB ``Table`` resource (injected for tests).

        Args:
            table: A boto3 DynamoDB ``Table`` resource, or a moto/Stubber-backed stand-in.
        """
        self._table = table

    def acquire_lease(self, incident_id: str, period: int, now: datetime) -> str:
        """Conditional put; succeed when no lease exists or the lease has expired (R3.15).

        The condition's second arm is the crash-recovery path: a runtime that dies without
        releasing its lease blocks new periods only until ``expires_at`` passes, not forever.

        Args:
            incident_id: The incident to lock.
            period: The operational period being started.
            now: The wall clock, injected so a replay can freeze time.

        Returns:
            The freshly minted ``lt_<ULID>`` lease token.

        Raises:
            ConflictError: A live (unexpired) lease already exists for the incident.
        """
        token = f"lt_{ULID()}"
        expires = now + timedelta(seconds=LEASE_SECONDS)
        try:
            self._table.put_item(
                Item={
                    "pk": _pk(incident_id),
                    "sk": "LEASE",
                    "lease_token": token,
                    "operational_period": period,
                    "acquired_at": _iso(now),
                    "expires_at": _iso(expires),
                    "ttl": int((now + timedelta(days=_RECORD_TTL_DAYS)).timestamp()),
                },
                ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
                ExpressionAttributeValues={":now": _iso(now)},
            )
        except self._conditional_failed():
            raise ConflictError("a period is already running for this incident") from None
        return token

    def release_lease(self, incident_id: str, token: str) -> None:
        """Delete only our own lease, conditional on the lease token (§11.2).

        A run whose lease expired mid-period must not delete its successor's lease; the
        conditional delete makes that impossible. A conditional-check failure is swallowed,
        because it means the lease is no longer ours to release.

        Args:
            incident_id: The incident whose lease to release.
            token: The lease token returned by :meth:`acquire_lease`.
        """
        try:
            self._table.delete_item(
                Key={"pk": _pk(incident_id), "sk": "LEASE"},
                ConditionExpression="lease_token = :t",
                ExpressionAttributeValues={":t": token},
            )
        except self._conditional_failed():
            return  # the lease is no longer ours; nothing to release

    def write_period_record(  # noqa: PLR0913, PLR0917 - the period-record columns (§11.2)
        self,
        incident_id: str,
        period: int,
        status: str,
        summary: dict[str, Any],
        audit: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        """Write the terminal period record with its summary and audit list (§11.2, R12.11).

        This is the only write besides the lease; it never touches a ``grid-tools`` table.

        Args:
            incident_id: The incident.
            period: The operational period.
            status: One of ``completed``, ``degraded``, ``truncated`` or ``failed``.
            summary: The ``PeriodSummary`` as a JSON-safe mapping.
            audit: The audit-entry list as JSON-safe mappings.
            now: The wall clock.
        """
        self._table.put_item(
            Item={
                "pk": _pk(incident_id),
                "sk": _period_sk(period),
                "operational_period": period,
                "status": status,
                "summary": summary,
                "audit": audit,
                "ttl": int((now + timedelta(days=_RECORD_TTL_DAYS)).timestamp()),
            }
        )

    def last_completed_period(self, incident_id: str) -> int | None:
        """Return the last terminal period for the incident, or ``None`` (R3.15, §11.3).

        Queries ``pk = INC#<id>, sk begins_with PERIOD#`` newest-first and returns the first
        record whose status is terminal. A read failure is the caller's cue to treat history as
        unavailable (``history_available=False``); this method surfaces the boto3 error rather
        than hiding it, so the caller decides.

        Args:
            incident_id: The incident to query.

        Returns:
            The highest terminal ``operational_period``, or ``None`` when there is no history.
        """
        response = self._table.query(
            KeyConditionExpression=("pk = :pk AND begins_with(sk, :prefix)"),
            ExpressionAttributeValues={":pk": _pk(incident_id), ":prefix": "PERIOD#"},
            ScanIndexForward=False,
        )
        for item in response.get("Items", []):
            if str(item.get("status")) in _TERMINAL_STATUSES:
                return int(item["operational_period"])
        return None

    def _conditional_failed(self) -> type[BaseException]:
        """The ``ConditionalCheckFailedException`` class for the injected table's client."""
        exc: type[BaseException] = (
            self._table.meta.client.exceptions.ConditionalCheckFailedException
        )
        return exc


__all__ = ["LEASE_SECONDS", "ConflictError", "PeriodTable"]
