"""The glass-box emitter: the six ``minnal.*`` events as AG-UI ``Custom`` events (§12.1, §12.3).

Every visible agent action becomes an AG-UI ``Custom`` event whose ``name`` is one of the six
schema names and whose ``value`` is a payload this module builds, validates against its JSON
Schema (through :mod:`agui.validate`) and only then places on the emitter's ``asyncio.Queue``.
The merge wrapper in :mod:`agui.transport` interleaves that queue with the ``ag-ui-strands``
adapter's stream, so the war room reads one ordered stream (ADR 0005, §12.2).

Three guarantees are structural rather than by convention:

* **Every payload carries ``incident_id`` and ``operational_period``** (R18.11): the emitter holds
  them and stamps them on every event, so a caller cannot forget them.
* **No personal data or raw token ever reaches a payload** (R18.9): the emitter only ever writes
  the fields the schemas name, the schemas are ``additionalProperties: false`` with no
  personal-data field and ``task_token_ref`` constrained to the ``ttr_`` form, and
  :func:`agui.validate.validate_glass_box_event` runs before emit — so a stray callback number or
  a raw Step Functions token fails validation by construction (Property 58).
* **``status`` matches the node's real state** (R18.10): the caller passes the true status; the
  emitter never fabricates ``done`` or ``waiting_approval``.

The summaries are bounded to the schema maxima here (R18.9 keeps them short and PII-free); an
over-long summary is truncated rather than rejected, because a glass-box step must never be the
thing that fails a period.

This is an edge module: it imports ``ag_ui.core`` for the ``Custom`` event type. It performs no
AWS I/O and holds no boto3 client.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal

from agui.validate import validate_glass_box_event

if TYPE_CHECKING:
    import asyncio

# The AG-UI Custom event type. Imported lazily-safe at module import; ag_ui is a runtime dep.
from ag_ui.core import CustomEvent

# Schema-driven summary maxima, kept in step with agui/schemas/*.v1.json (§12.3).
_STEP_MAX: Final[int] = 120
_DETAIL_MAX: Final[int] = 280
_SUMMARY_MAX: Final[int] = 280
_TITLE_MAX: Final[int] = 200
_URL_MAX: Final[int] = 2048
_REASON_MAX: Final[int] = 500
_APPROVAL_SUMMARY_MAX: Final[int] = 500

# Event names, one per schema (§12.3). Kept local so a typo cannot silently emit an unknown name.
_AGENT_STEP: Final[str] = "minnal.agent_step"
_TOOL_CALL: Final[str] = "minnal.tool_call"
_CITATION: Final[str] = "minnal.citation"
_VETO: Final[str] = "minnal.veto"
_APPROVAL_REQUEST: Final[str] = "minnal.approval_request"
_MAP_UPDATE: Final[str] = "minnal.map_update"

AgentStepStatus = Literal["thinking", "calling_tool", "waiting_approval", "done", "failed"]


def _clip(text: str, limit: int) -> str:
    """Bound a summary to a schema maximum without ever failing the emit (R18.9)."""
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _iso(now: datetime) -> str:
    """Render the injected clock as the ISO 8601 UTC ``Z`` string the schemas require."""
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


class GlassBoxEmitter:
    """Builds, validates and queues the six ``minnal.*`` ``Custom`` events (§12.1, §12.3).

    One emitter is created per Period_Run. It owns the ``asyncio.Queue`` the transport drains and
    carries the run's ``incident_id``, ``operational_period`` and ``correlation_id`` so every event
    is stamped with them (R18.11). The clock is injected so an offline replay produces a
    deterministic ``started_at`` / ``retrieved_at`` (R22.4).
    """

    def __init__(
        self,
        queue: asyncio.Queue[CustomEvent],
        *,
        incident_id: str,
        operational_period: int,
        correlation_id: str,
        clock: Callable[[], datetime],
    ) -> None:
        """Create the emitter for one period.

        Args:
            queue: The event queue the transport merges into the AG-UI stream.
            incident_id: The incident this period belongs to, stamped on every event.
            operational_period: The period number, stamped on every event.
            correlation_id: The one ``corr_<ULID>`` for this run, carried on every event (R20.3).
            clock: Returns the current time; injected so a replay can freeze it.
        """
        self._queue = queue
        self._incident_id = incident_id
        self._operational_period = operational_period
        self._correlation_id = correlation_id
        self._clock = clock

    @property
    def queue(self) -> asyncio.Queue[CustomEvent]:
        """The queue the transport drains (ADR 0005 merged-stream design)."""
        return self._queue

    # --- the shared envelope -----------------------------------------------

    def _base(self) -> dict[str, object]:
        """The two fields every payload carries, so no caller can omit them (R18.11)."""
        return {
            "incident_id": self._incident_id,
            "operational_period": self._operational_period,
        }

    def _emit(self, name: str, value: Mapping[str, object]) -> None:
        """Validate a payload against its schema, then queue it as a ``Custom`` event (R18.8).

        Raises:
            jsonschema.ValidationError: The payload does not match its schema. This is a
                programming error (the emitter builds the payload itself), surfaced loudly rather
                than hidden, so a personal-data or raw-token leak can never be silently emitted.
        """
        validate_glass_box_event(name, value)
        self._queue.put_nowait(CustomEvent(name=name, value=dict(value)))

    # --- the six events ----------------------------------------------------

    def agent_step(self, node: str, status: AgentStepStatus, *, detail: str = "") -> None:
        """Emit ``minnal.agent_step`` for a node lifecycle transition (R18.2, R18.10).

        ``status`` must be the node's real state; the emitter never fabricates it. ``done`` is
        emitted only after the node's output validated, ``waiting_approval`` only once a
        ``proposal_id`` exists — the caller enforces that ordering (§12.1).
        """
        value = {
            **self._base(),
            "correlation_id": self._correlation_id,
            "agent": node,
            "step": _clip(node, _STEP_MAX),
            "status": status,
            "started_at": _iso(self._clock()),
        }
        if detail:
            value["detail"] = _clip(detail, _DETAIL_MAX)
        self._emit(_AGENT_STEP, value)

    def tool_call(  # noqa: PLR0913 - the minnal.tool_call payload surface (§12.3)
        self,
        *,
        agent: str,
        tool: str,
        input_summary: str,
        output_summary: str,
        ok: bool,
        duration_ms: int = 0,
        error_code: str | None = None,
        item_id: str | None = None,
    ) -> None:
        """Emit ``minnal.tool_call`` after a Gateway tool completes (R18.3).

        Summaries are bounded and carry no personal data; a citizen note or callback number is
        never one of the fields the caller passes, and the schema would reject it (R18.9).
        """
        value: dict[str, object] = {
            **self._base(),
            "correlation_id": self._correlation_id,
            "agent": _clip(agent, 32),
            "tool": tool,
            "input_summary": _clip(input_summary, _SUMMARY_MAX),
            "output_summary": _clip(output_summary, _SUMMARY_MAX),
            "duration_ms": max(0, duration_ms),
            "ok": ok,
        }
        if error_code is not None:
            value["error_code"] = error_code
        if item_id is not None:
            value["item_id"] = item_id
        self._emit(_TOOL_CALL, value)

    def citation(self, *, agent: str, title: str, url: str, source_kind: str) -> None:
        """Emit ``minnal.citation`` for one evidence source (R18.4, R6.4).

        The title and url are bounded; they describe a public source, never citizen data.
        """
        value = {
            **self._base(),
            "agent": _clip(agent, 32),
            "title": _clip(title, _TITLE_MAX),
            "url": _clip(url, _URL_MAX),
            "retrieved_at": _iso(self._clock()),
            "source_kind": source_kind,
        }
        self._emit(_CITATION, value)

    def veto(  # noqa: PLR0913 - the minnal.veto payload surface (§12.3)
        self,
        *,
        rule_id: str | None,
        reason: str,
        proposal_id: str | None,
        source: Literal["tool", "advisory"],
        item_id: str | None = None,
        iteration: int | None = None,
        is_final: bool | None = None,
    ) -> None:
        """Emit ``minnal.veto`` for one veto (R18.5, R11.7).

        ``rule_id`` is omitted for an advisory veto (it has none); ``source`` is always present so
        the war room can distinguish a flood rule from a Safety Officer judgement (§12.3).
        """
        value: dict[str, object] = {
            **self._base(),
            "reason": _clip(reason, _REASON_MAX),
            "source": source,
        }
        if rule_id is not None:
            value["rule_id"] = rule_id
        if proposal_id is not None:
            value["proposal_id"] = proposal_id
        if item_id is not None:
            value["item_id"] = item_id
        if iteration is not None:
            value["iteration"] = iteration
        if is_final is not None:
            value["is_final"] = is_final
        self._emit(_VETO, value)

    def approval_request(  # noqa: PLR0913 - the minnal.approval_request payload surface (§12.3)
        self,
        *,
        proposal_id: str,
        kind: str,
        summary: str,
        task_token_ref: str,
        route_geojson: Mapping[str, object] | None = None,
        action: str | None = None,
        is_preventive_safety_measure: bool | None = None,
    ) -> None:
        """Emit ``minnal.approval_request`` per created proposal (R18.6, R12.3, R12.4).

        Only the ``ttr_<ULID>`` reference is carried in ``task_token_ref``; a raw Step Functions
        token cannot validate against the schema's ``^ttr_`` pattern, so it can never be emitted
        (Property 58). The raw token stays in the ``grid-tools`` vault and never reaches here.
        """
        value: dict[str, object] = {
            **self._base(),
            "proposal_id": proposal_id,
            "kind": kind,
            "summary": _clip(summary, _APPROVAL_SUMMARY_MAX),
            "task_token_ref": task_token_ref,
        }
        if route_geojson is not None:
            value["route_geojson"] = dict(route_geojson)
        if action is not None:
            value["action"] = action
        if is_preventive_safety_measure is not None:
            value["is_preventive_safety_measure"] = is_preventive_safety_measure
        self._emit(_APPROVAL_REQUEST, value)

    def map_update(
        self,
        *,
        layer: str,
        feature_collection: Mapping[str, object],
        operation: Literal["upsert", "remove"] = "upsert",
    ) -> None:
        """Emit ``minnal.map_update`` with a delta ``FeatureCollection`` (R18.7).

        The features are geometry and public labels only; no citizen data is placed on the map.
        """
        value = {
            **self._base(),
            "layer": layer,
            "operation": operation,
            "feature_collection": dict(feature_collection),
        }
        self._emit(_MAP_UPDATE, value)


__all__ = ["AgentStepStatus", "GlassBoxEmitter"]
