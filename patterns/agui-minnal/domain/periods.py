"""Operational-period validation and numbering (§6.4, §11.3).

Pure: no ``boto3``/``botocore``/``strands`` and no I/O. The single-flight lease is acquired
separately (§11.2); this module only decides whether a requested period number is admissible.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PeriodRequest:
    """A request to run one operational period for an incident."""

    incident_id: str
    operational_period: int
    correlation_id: str | None = None


@dataclass(frozen=True)
class PeriodValidation:
    """The verdict on a :class:`PeriodRequest`.

    ``sequence_trusted`` is ``False`` when history was unavailable and the request was trusted
    anyway, so the period summary can show the numbering was not verified (R3.15).
    """

    ok: bool
    error_code: str | None = None  # VALIDATION_ERROR
    message: str | None = None
    sequence_trusted: bool = True


def validate_period_request(
    request: PeriodRequest,
    last_completed_period: int | None,
    history_available: bool,
) -> PeriodValidation:
    """Validate a period request against incident history (R3.14, R3.15).

    The lease is acquired separately (§11.2). ``history_available`` is ``False`` when the period
    table cannot be read or holds no record for the incident; in that case the request is
    trusted and the summary says so, rather than blocking a storm response on a bookkeeping
    read.

    Args:
        request: The requested incident, period and correlation id.
        last_completed_period: The last terminal period for the incident, or ``None``.
        history_available: Whether the period history could be read.

    Returns:
        A :class:`PeriodValidation`; ``ok`` is ``False`` with an ``error_code`` on rejection.
    """
    if request.operational_period < 1:
        return PeriodValidation(False, "VALIDATION_ERROR", "operational_period must be >= 1")
    if not history_available:
        return PeriodValidation(True, sequence_trusted=False)
    expected = (last_completed_period or 0) + 1
    if request.operational_period != expected:
        return PeriodValidation(
            False,
            "VALIDATION_ERROR",
            f"operational_period must be {expected}, the last completed period plus 1",
        )
    return PeriodValidation(True)
