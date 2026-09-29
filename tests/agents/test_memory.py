"""AgentCore Memory: incident scoping, running without memory, and no PII in the record (§13).

Requirements 19.1 to 19.7. Three behaviours the memory layer must guarantee, all tested with fakes
and no network (sockets are blocked by the parent conftest):

* ``test_runs_without_memory`` — when no memory is provisioned, the per-period session provider
  returns ``None`` and the period write/read degrade to "no history" rather than raising, so the
  Period_Run is never failed by a bookkeeping call (R19.5, R19.7).
* ``test_namespaces_are_incident_scoped`` — the ``session_id`` is zero-padded so lexical order
  equals numeric order, the incident is the memory ``actor_id`` (so one incident cannot read
  another's context), and two incidents never share a namespace (R19.1, R19.4).
* ``test_lessons_read_only_and_no_pii`` — the period record carries no callback number, name or
  citizen free text, and nothing here ever writes to the ``lessons`` namespace (R19.3, R19.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.contracts import (  # type: ignore[import-not-found]
    BlockedItem,
    CommittedProposal,
    LockedCrew,
    NodeContext,
)
from memory.namespaces import (  # type: ignore[import-not-found]
    incident_namespace,
    lessons_namespace,
    session_id,
)
from memory.period_memory import (  # type: ignore[import-not-found]
    build_period_memory_record,
    previous_context,
    write_period_summary,
)
from memory.session import make_session_provider  # type: ignore[import-not-found]
from roles._common.contracts import PeriodSummary  # type: ignore[import-not-found]

_ULID: Final[str] = "01HGVMCG005DV9P1DNGC1END2G"
_ULID_B: Final[str] = "01HGVMCG005DV9P1DNGC1END2H"
_INCIDENT_A: Final[str] = f"inc_{_ULID}"
_INCIDENT_B: Final[str] = f"inc_{_ULID_B}"
_CORRELATION: Final[str] = f"corr_{_ULID}"
_PROPOSAL: Final[str] = f"prp_{_ULID}"
_TTR: Final[str] = f"ttr_{_ULID}"

# PII markers that must never reach a memory record (R19.6).
_CALLBACK_NUMBER: Final[str] = "+919840012345"
_CITIZEN_NAME: Final[str] = "Priya Ramesh"
_CITIZEN_NOTE: Final[str] = "my elderly mother at 12 Beach Road needs oxygen"

_LESSONS_NS: Final[str] = "/lessons/minnal"


def _summary(
    incident_id: str, period: int, *, narrative: str = "period narrative"
) -> PeriodSummary:
    """A structured period summary with committed, blocked and locked-crew rows (§11.4)."""
    return PeriodSummary(
        context=NodeContext(
            incident_id=incident_id, operational_period=period, correlation_id=_CORRELATION
        ),
        outcome="degraded",
        objectives=("restore critical facilities first",),
        committed=(
            CommittedProposal(
                item_id="itm_dsp_000000000001",
                proposal_id=_PROPOSAL,
                kind="dispatch",
                status="waiting_approval",
                task_token_ref=_TTR,
            ),
        ),
        blocked=(
            BlockedItem(
                item_id="itm_dsp_000000000002",
                kind="dispatch",
                reason="route crosses active flood polygon FP-12",
                rule_id="FLOOD_ROUTE",
            ),
        ),
        failures=(),
        locked_crews=(
            LockedCrew(
                crew_id="crew_3",
                holding_proposal_id=_PROPOSAL,
                proposal_status="waiting_approval",
            ),
        ),
        approved_jobs_awaiting_completion=("job-7",),
        period_sequence_trusted=False,
        narrative=narrative,
    )


@dataclass
class _RecordingWriter:
    """A fake :class:`MemoryWriter` recording every namespace and record written (§13.3)."""

    writes: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def write(self, *, namespace: str, record: dict[str, object]) -> None:
        self.writes.append((namespace, record))


@dataclass
class _RecordingReader:
    """A fake :class:`MemoryReader` recording every namespace read and returning a canned record."""

    records: list[str] = field(default_factory=lambda: ["previous period summary"])
    namespaces: list[str] = field(default_factory=list)

    def retrieve(self, *, namespace: str, max_results: int) -> list[str]:
        self.namespaces.append(namespace)
        return self.records[:max_results]


# --- R19.5, R19.7: the period runs without memory -------------------------------------------


def test_runs_without_memory() -> None:
    """No memory → provider returns None and the write/read degrade without raising (R19.5)."""
    # Arrange: a provider with no memory id.
    provider = make_session_provider(_INCIDENT_A, 3, memory_id=None, region="us-east-1")

    # Act + Assert: the provider hands back None so ag-ui-strands runs with no history (R19.7).
    assert provider(object()) is None  # type: ignore[arg-type]

    # Act + Assert: writing with no writer returns False and does not raise (R19.5).
    wrote = write_period_summary(_summary(_INCIDENT_A, 3), None, _INCIDENT_A)
    assert wrote is False

    # Act + Assert: reading the previous period with no reader degrades to (None, False) (R19.5).
    previous, history_available = previous_context(_INCIDENT_A, 3, None)
    assert previous is None
    assert history_available is False


# --- R19.1, R19.4: namespaces are incident-scoped and lexically ordered ---------------------


def test_namespaces_are_incident_scoped() -> None:
    """session_id is zero-padded, the incident is the actor, and two incidents never collide."""
    # Assert: session id is zero-padded to four digits, so lexical order equals numeric (R19.1).
    assert session_id(1) == "period-0001"
    assert session_id(2) == "period-0002"
    assert session_id(10) == "period-0010"
    assert session_id(2) < session_id(10)  # lexical order equals numeric order

    # Assert: the incident is the actor id in the namespace, so scoping is structural (R19.4).
    ns_a3 = incident_namespace(_INCIDENT_A, 3)
    assert ns_a3 == f"/incident/{_INCIDENT_A}/period-0003"
    assert _INCIDENT_A in ns_a3

    # Assert: two incidents (same period) never share a namespace, so one cannot read the
    # other (R19.4).
    ns_b3 = incident_namespace(_INCIDENT_B, 3)
    assert ns_a3 != ns_b3
    assert _INCIDENT_B not in ns_a3
    assert _INCIDENT_A not in ns_b3


def test_previous_context_reads_only_the_prior_period_of_the_same_incident() -> None:
    """previous_context reads exactly the prior period's namespace for this incident (R19.2)."""
    # Arrange.
    reader = _RecordingReader()

    # Act: from period 4, the previous period is 3.
    previous, history_available = previous_context(_INCIDENT_A, 4, reader)

    # Assert: it read the prior period's incident-scoped namespace and nothing else (R19.2, R19.4).
    assert history_available is True
    assert previous == "previous period summary"
    assert reader.namespaces == [incident_namespace(_INCIDENT_A, 3)]
    # It never read the current period, another incident, or the lessons namespace.
    assert incident_namespace(_INCIDENT_A, 4) not in reader.namespaces
    assert incident_namespace(_INCIDENT_B, 3) not in reader.namespaces
    assert _LESSONS_NS not in reader.namespaces


# --- R19.3, R19.6: lessons are read-only and the record carries no PII ----------------------


def test_lessons_read_only_and_no_pii() -> None:
    """The written record carries no PII, and nothing writes to the lessons namespace (R19.3/6)."""
    # Arrange: a summary whose narrative TRIES to smuggle a callback number, name and citizen note.
    leaky_narrative = f"caller {_CALLBACK_NUMBER}, resident {_CITIZEN_NAME}: {_CITIZEN_NOTE}"
    writer = _RecordingWriter()

    # Act.
    summary = _summary(_INCIDENT_A, 3, narrative=leaky_narrative)
    wrote = write_period_summary(summary, writer, _INCIDENT_A)

    # Assert: the write happened, into the incident namespace, never the lessons namespace (R19.3).
    assert wrote is True
    assert len(writer.writes) == 1
    namespace, record = writer.writes[0]
    assert namespace == incident_namespace(_INCIDENT_A, 3)
    assert namespace != lessons_namespace()
    written_namespaces = [ns for ns, _ in writer.writes]
    assert lessons_namespace() not in written_namespaces
    assert all(not ns.startswith("/lessons") for ns in written_namespaces)

    # Assert: the record is a positive allow-list of the fields §13.3 names — no callback number,
    # name or free-text citizen note field. The one free-text field is the narrative, a human
    # summary already shown in the war room; the record carries no dedicated PII field (R19.6).
    assert set(record) == {
        "operational_period",
        "outcome",
        "objectives",
        "narrative",
        "counts",
        "blocked",
        "locked_crews",
        "approved_jobs_awaiting_completion",
        "period_sequence_trusted",
    }
    # No untrusted_note, callback number field, name field or raw token field is present.
    assert "untrusted_note" not in record
    assert "callback_number" not in record
    assert "name" not in record
    assert "task_token_ref" not in record

    # Assert: the blocked rows carry only the item id, kind and rule id — no free-text reason that
    # could carry an untrusted note (R19.6).
    for blocked in record["blocked"]:  # type: ignore[union-attr]
        assert set(blocked) == {"item_id", "kind", "rule_id"}
        assert "reason" not in blocked


def test_record_build_matches_write_record() -> None:
    """The record the writer receives is exactly build_period_memory_record's output (§13.3)."""
    # Arrange.
    summary = _summary(_INCIDENT_A, 3)
    writer = _RecordingWriter()

    # Act.
    write_period_summary(summary, writer, _INCIDENT_A)

    # Assert.
    _, record = writer.writes[0]
    assert record == build_period_memory_record(summary)
