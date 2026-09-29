"""Step Functions and EventBridge adapters (§5.9, §11.5, R11.1, R13.2, R13.3).

* :class:`StepFunctionsWorkOrder` — ``StartExecution`` to begin a Work_Order and
  ``SendTaskSuccess``/``SendTaskFailure`` to settle one. Per §12.1 the tool roles
  hold only ``StartExecution`` and the Approval_Handler/Expirer roles hold the
  ``SendTask*`` actions; this class exposes all three, but IAM decides which a
  given function may actually call.
* :class:`EventBridgePublisher` — validates an event against its JSON Schema
  **before** ``PutEvents`` (a schema-invalid event is withheld and logged), then
  inspects ``FailedEntryCount`` and per-entry error codes rather than trusting
  the HTTP status. A publish failure is logged and the call still succeeds,
  because the Proposal is the source of truth (§11.5, R13.3, R13.4).

boto3 clients are created once at module scope by the factory in ``aws.py`` and
passed in, so this module opens no client itself.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from _shared import events as event_builder
from _shared.adapters._aws_retry import with_retry
from _shared.ids import new_id
from _shared.ports import Proposal, StartedWorkOrder

if TYPE_CHECKING:
    from botocore.client import BaseClient

_LOGGER = logging.getLogger("minnal-grid-tools")


class StepFunctionsWorkOrder:
    """A :class:`_shared.ports.WorkOrderStarter` over Step Functions (§5.6)."""

    def __init__(self, client: BaseClient, state_machine_arn: str) -> None:
        self._client = client
        self._state_machine_arn = state_machine_arn

    def start(self, incident_id: str, proposal: Proposal, timeout_seconds: int) -> StartedWorkOrder:
        """Start the Work_Order execution and return its id and token ref (§6.6).

        The Task_Token_Ref is minted here and passed into the execution input so
        the token-vault target can store the token against it; the execution's
        ``TimeoutSeconds`` is rendered from ``timeout_seconds`` at deploy time.
        """
        ttr = proposal.task_token_ref or new_id("ttr")
        wo_id = new_id("wo")
        payload = {
            "incident_id": incident_id,
            "proposal_id": proposal.proposal_id,
            "task_token_ref": ttr,
            "kind": proposal.kind,
            "timeout_seconds": timeout_seconds,
        }
        import json  # noqa: PLC0415 - only the AWS adapter serialises the input

        with_retry(
            lambda: self._client.start_execution(
                stateMachineArn=self._state_machine_arn,
                name=wo_id,
                input=json.dumps(payload),
            )
        )
        return StartedWorkOrder(wo_id=wo_id, task_token_ref=ttr)

    def succeed(self, ttr: str, payload: Mapping[str, object]) -> None:
        """Resume the execution as approved (``SendTaskSuccess``)."""
        import json  # noqa: PLC0415 - only the AWS adapter serialises the output

        with_retry(
            lambda: self._client.send_task_success(
                taskToken=self._token(ttr), output=json.dumps(dict(payload))
            )
        )

    def fail(self, ttr: str, error: str, cause: str) -> None:
        """Fail the execution (``SendTaskFailure``)."""
        with_retry(
            lambda: self._client.send_task_failure(
                taskToken=self._token(ttr), error=error, cause=cause
            )
        )

    def _token(self, ttr: str) -> str:
        """Return the raw task token for a ref.

        The Approval_Handler takes the token from the vault and passes it here as
        the ``ttr`` argument at the boundary; this adapter never stores or logs a
        raw token (§12.3). The parameter is the token string itself.
        """
        return ttr


class EventBridgePublisher:
    """A :class:`_shared.ports.EventPublisher` over EventBridge (§11.5)."""

    def __init__(self, client: BaseClient, event_bus_name: str) -> None:
        self._client = client
        self._event_bus_name = event_bus_name

    def publish(
        self,
        event_name: str,
        payload: Mapping[str, object],
        incident_id: str,
        correlation_id: str,
    ) -> None:
        """Validate then publish, inspecting per-entry failure (§11.5, R13.3)."""
        event = event_builder.build_event(event_name, payload, incident_id, correlation_id)
        if not event_builder.is_valid_event(event):
            _LOGGER.warning(
                "event_withheld_schema_invalid",
                extra={"event_type": event_name, "correlation_id": correlation_id},
            )
            return
        self._put(event, event_name, correlation_id)

    def _put(self, event: Mapping[str, object], event_name: str, correlation_id: str) -> None:
        import json  # noqa: PLC0415 - only the AWS adapter serialises the detail

        entry = {
            "Source": event_builder.EVENT_SOURCE,
            "DetailType": event_name,
            "Detail": json.dumps(dict(event)),
            "EventBusName": self._event_bus_name,
        }
        try:
            response = with_retry(lambda: self._client.put_events(Entries=[entry]))
        except Exception:
            _LOGGER.warning(
                "event_publish_failed",
                extra={"event_type": event_name, "correlation_id": correlation_id},
            )
            return
        if int(response.get("FailedEntryCount", 0)) > 0:
            _LOGGER.warning(
                "event_publish_partial_failure",
                extra={"event_type": event_name, "correlation_id": correlation_id},
            )
