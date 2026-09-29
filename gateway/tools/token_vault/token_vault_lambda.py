"""Handler for the Token_Vault (design §5.9, §12.3, §6.6).

The ``.waitForTaskToken`` target of the Work_Order state machine. Step Functions
invokes it with the execution input plus the task token
(``{incident_id, proposal_id, task_token_ref, task_token}``); the vault writes the
``TTR#`` item — the **one** place the raw token lives — linked to its Proposal so
``ProposalStore.record_decision`` can resolve it, then returns. The token exists in
no envelope, event, log or UI payload; the outside world only ever sees
``ttr_<ULID>`` (R9.8, R11.1, §12.3). A second vaulting for one execution is ignored
(``attribute_not_exists(task_token)``, §11.6).

This module writes only through the :class:`_shared.ports.TokenVault`; it holds no
business logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.observability import build_logger, build_tracer
from _shared.ports import Ports
from _shared.settings import Settings

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = build_logger()
tracer = build_tracer()
SETTINGS = Settings()
PORTS: Ports = make_ports(SETTINGS)


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: Mapping[str, object], context: LambdaContext) -> dict[str, str]:
    """Store the task token under its ``TTR#`` item, once (§12.3, R11.1)."""
    incident_id = _require(event, "incident_id")
    ttr = _require(event, "task_token_ref")
    proposal_id = _require(event, "proposal_id")
    task_token = _require(event, "task_token")
    logger.append_keys(incident_id=incident_id)
    PORTS.tokens.store(incident_id, ttr, task_token, proposal_id)
    # Return the ref only — never the raw token (R9.8).
    return {"task_token_ref": ttr, "proposal_id": proposal_id}


def _require(event: Mapping[str, object], name: str) -> str:
    """Return a required string field from the Step Functions payload."""
    value = event.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"token_vault payload missing {name}")
    return value
