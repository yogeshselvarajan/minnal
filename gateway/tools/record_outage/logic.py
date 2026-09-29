"""Pure logic for ``record_outage`` (design §5.1, §8.9).

The intake identity rules live here, free of any store: how a report becomes an
Outage_Key (§8.9), the initial Outage draft, the fixed emergency advice, and the
attach-and-escalate fold that keeps ``is_emergency`` sticky and raises
``symptom_most_severe`` to the worst symptom ever seen for an Outage (R4.13).
The Handler and the Event_Ingestor share these functions verbatim, so intake
through the Gateway and intake through an event behave identically (P33).

The module imports no ``boto3``/``botocore`` and performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from _shared.geometry import snap_to_cell
from _shared.models import EmergencyEscalation, Symptom
from record_outage.models import RecordOutageInput

_EMERGENCY_SYMPTOMS: frozenset[str] = frozenset({"downed_wire", "sparking", "submerged_equipment"})
"""Symptoms that force ``is_emergency`` and carry safety advice (R4.5, R4.13)."""

_SEVERITY_ORDER: tuple[Symptom, ...] = (
    "no_power",
    "partial_power",
    "sparking",
    "downed_wire",
    "submerged_equipment",
)
"""Least to most severe (R4.13). ``submerged_equipment`` is the worst."""

_SEVERITY_RANK: dict[Symptom, int] = {sym: rank for rank, sym in enumerate(_SEVERITY_ORDER)}


@dataclass(frozen=True, slots=True)
class OutageKey:
    """The server-derived identity of an Outage (R4.10, §8.9).

    ``value`` is the canonical string form used as the store's uniqueness key:
    ``meter:<meter_id>`` for a meter, otherwise
    ``dt:<supplying_dt or none>:<cell_x>:<cell_y>``.
    """

    value: str


@dataclass(frozen=True, slots=True)
class OutageDraft:
    """The initial ``open`` Outage a new report creates (R4.1, R4.5)."""

    outage_key: str
    source: str
    symptom: Symptom
    supplying_dt_id: str | None
    location: tuple[float, float]
    reported_at: str
    is_emergency: bool
    symptom_most_severe: Symptom
    emergency_advice: str | None
    untrusted_note: str | None
    callback_ref: str | None


def is_emergency_symptom(symptom: Symptom) -> bool:
    """Return whether a symptom forces the emergency flag (R4.5, R4.13)."""
    return symptom in _EMERGENCY_SYMPTOMS


def symptom_most_severe(current: Symptom, incoming: Symptom) -> Symptom:
    """Return the more severe of two symptoms under the R4.13 order.

    The order, most to least severe, is ``submerged_equipment`` > ``downed_wire``
    > ``sparking`` > ``partial_power`` > ``no_power``.
    """
    return incoming if _SEVERITY_RANK[incoming] > _SEVERITY_RANK[current] else current


def emergency_advice(symptom: Symptom, emergency_number: str) -> str | None:
    """Return the fixed safety advice for an emergency symptom, else None (R4.5).

    The text is assembled from configuration, never generated: it always states
    the 10 m clearance, that the wire and anything it touches (including water)
    must not be approached, and the configured emergency number.

    Args:
        symptom: The reported symptom.
        emergency_number: The configured emergency number (never empty in a
            validated ``Settings``).

    Returns:
        The advice string for an emergency symptom, or None for a non-emergency.
    """
    if not is_emergency_symptom(symptom):
        return None
    return (
        "Stay at least 10 m away from the wire and anything it touches, including water. "
        "Do not touch it. "
        f"Call the emergency number {emergency_number}."
    )


def derive_outage_key(sig: RecordOutageInput, supplying_dt: str | None) -> OutageKey:
    """Derive the Outage_Key server-side, never from agent input (R4.10, §8.9).

    A meter report is keyed by its ``meter_id``; a citizen/UI report is keyed by
    the Supplying_DT (or the literal ``none``) plus the location snapped to a
    ``outage_cell_m`` cell in projected metres.

    Args:
        sig: The validated report.
        supplying_dt: The resolved Supplying_DT id, or None when unresolved.

    Returns:
        The canonical :class:`OutageKey`.

    Raises:
        ValueError: A meter report is missing its ``meter_id`` (the Handler
            rejects this earlier as ``VALIDATION_ERROR``).
    """
    if sig.source == "meter":
        if sig.meter_id is None:
            raise ValueError("meter report requires meter_id")
        return OutageKey(value=f"meter:{sig.meter_id}")
    return OutageKey(value=f"dt:{supplying_dt or 'none'}:{_cell_suffix(sig)}")


def _cell_suffix(sig: RecordOutageInput, cell_m: int = 40) -> str:
    """Return the ``<cell_x>:<cell_y>`` suffix for a citizen/UI report location."""
    lon, lat = sig.location.coordinates
    cell_x, cell_y = snap_to_cell(lon, lat, cell_m)
    return f"{cell_x}:{cell_y}"


def outage_key_for(sig: RecordOutageInput, supplying_dt: str | None, cell_m: int) -> OutageKey:
    """Derive the Outage_Key with a configurable cell size (R4.10, §8.9).

    ``derive_outage_key`` uses the default 40 m cell; the Handler passes the
    configured ``outage_cell_m`` here so a tuned deployment snaps consistently.
    """
    if sig.source == "meter":
        if sig.meter_id is None:
            raise ValueError("meter report requires meter_id")
        return OutageKey(value=f"meter:{sig.meter_id}")
    return OutageKey(value=f"dt:{supplying_dt or 'none'}:{_cell_suffix(sig, cell_m)}")


def build_draft(
    sig: RecordOutageInput,
    key: OutageKey,
    supplying_dt: str | None,
    emergency_number: str,
) -> OutageDraft:
    """Build the initial ``open`` Outage a new report creates (R4.1, R4.5).

    ``is_emergency`` is forced from the symptom and the supplied value is
    ignored; the advice text is attached for an emergency symptom;
    ``symptom_most_severe`` starts at this report's symptom. The note is stored
    as ``untrusted_note`` (the Handler truncates to 500 characters; the model
    already caps it).

    Args:
        sig: The validated report.
        key: The derived Outage_Key.
        supplying_dt: The resolved Supplying_DT id, or None.
        emergency_number: The configured emergency number.

    Returns:
        The :class:`OutageDraft` for a new Outage.
    """
    emergency = is_emergency_symptom(sig.symptom)
    return OutageDraft(
        outage_key=key.value,
        source=sig.source,
        symptom=sig.symptom,
        supplying_dt_id=supplying_dt,
        location=sig.location.coordinates,
        reported_at=sig.reported_at,
        is_emergency=emergency,
        symptom_most_severe=sig.symptom,
        emergency_advice=emergency_advice(sig.symptom, emergency_number),
        untrusted_note=sig.note,
        callback_ref=sig.callback_ref,
    )


def escalation_on_attach(
    stored_is_emergency: bool,
    stored_most_severe: Symptom,
    incoming: Symptom,
) -> EmergencyEscalation:
    """Fold an attaching report into the stored emergency state (R4.13).

    ``is_emergency`` is sticky: once true it stays true, and a new emergency
    symptom turns it on. ``symptom_most_severe`` rises to the worst symptom seen
    but never falls. The returned record is applied to the stored Outage by the
    store adapter in one conditional update.

    Args:
        stored_is_emergency: The Outage's current emergency flag.
        stored_most_severe: The Outage's current worst symptom.
        incoming: The attaching report's symptom.

    Returns:
        The :class:`_shared.models.EmergencyEscalation` to apply.
    """
    return EmergencyEscalation(
        is_emergency=stored_is_emergency or is_emergency_symptom(incoming),
        symptom_most_severe=symptom_most_severe(stored_most_severe, incoming),
    )
