"""Intake-construct assertions on the grid-tools template (task 73.2).

Design §16.1 / §2.1 / R18.8: a burst of citizen reports can never delay the flood picture and
FIFO ordering must be **per incident**. EventBridge cannot resolve a dynamic ``MessageGroupId``
on a rule target (a rule ``SqsParameters.MessageGroupId`` is a STATIC string, so
``$.detail.incident_id`` there would collapse every incident into one group — the Wave-6
BLOCKER). The fixed topology therefore routes each detail-type family through a FIFO **buffer**
queue and an **EventBridge Pipe** that sets ``MessageGroupId`` from ``$.body.detail.incident_id``
(resolved PER EVENT by Pipes) onto the **work** FIFO queue the ingestor consumes:

    bus → Rule (filter by detail-type + source) → BUFFER fifo
        → Pipe (MessageGroupId = $.body.detail.incident_id) → WORK fifo → EventSourceMapping

Topology: 5 SQS queues (``*-hazard-buffer.fifo``, ``*-intake-buffer.fifo`` buffers;
``*-hazard.fifo``, ``*-intake.fifo`` work; one shared ``*-events-dlq.fifo``), 2 Rules, 2 Pipes,
2 EventSourceMappings. The Flood_Ingestor consumes the hazard work queue at **batch size 1**;
the Event_Ingestor consumes the intake work queue at **batch size 10** with
``FunctionResponseTypes: ["ReportBatchItemFailures"]``.

_Req 18.8_ _Req 14.1_ _Design §16.1_ _Design §2.1_
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from tests.infra.conftest import policy_statements, resources_of_type, statement_actions

_TOTAL_QUEUE_COUNT = 5  # 2 buffers + 2 work + 1 shared DLQ
_BUFFER_QUEUE_COUNT = 2  # hazard-buffer.fifo + intake-buffer.fifo
_WORK_QUEUE_COUNT = 2  # hazard.fifo + intake.fifo
_MAX_RECEIVE_COUNT = 3  # bounded retries before the DLQ (§16.1)
_MAPPING_COUNT = 2  # one per ingestor
_RULE_COUNT = 2  # one per detail-type family
_PIPE_COUNT = 2  # one per detail-type family
_INCIDENT_GROUP_PATH = "$.body.detail.incident_id"  # resolved per event by Pipes (§2.1)


def _queue_name(body: Mapping[str, Any]) -> str:
    """The literal ``QueueName`` (the standalone synth emits plain-string names)."""
    name = body["Properties"].get("QueueName", "")
    return name if isinstance(name, str) else ""


def _queues(resources: Mapping[str, Any]) -> dict[str, Any]:
    return resources_of_type(resources, "AWS::SQS::Queue")


def _buffer_queues(resources: Mapping[str, Any]) -> dict[str, Any]:
    """The two FIFO buffer queues (``*-hazard-buffer.fifo`` / ``*-intake-buffer.fifo``)."""
    return {
        lid: body
        for lid, body in _queues(resources).items()
        if _queue_name(body).endswith("-buffer.fifo")
    }


def _work_queues(resources: Mapping[str, Any]) -> dict[str, Any]:
    """The two FIFO WORK queues (``*-hazard.fifo`` / ``*-intake.fifo``).

    Excludes the ``*-buffer.fifo`` buffers and the shared ``*-dlq.fifo``; the ingestors' event
    source mappings read from these queues.
    """
    return {
        lid: body
        for lid, body in _queues(resources).items()
        if (name := _queue_name(body)).endswith((".fifo",))
        and not name.endswith("-buffer.fifo")
        and not name.endswith("-dlq.fifo")
    }


def _dlq(resources: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """The single shared FIFO dead-letter queue (``*-dlq.fifo``)."""
    dlqs = {
        lid: body
        for lid, body in _queues(resources).items()
        if _queue_name(body).endswith("-dlq.fifo")
    }
    assert len(dlqs) == 1, f"expected exactly one shared DLQ, found {len(dlqs)}"
    return next(iter(dlqs.items()))


def test_five_queues_two_buffers_two_work_one_shared_dlq(resources: Mapping[str, Any]) -> None:
    """Two FIFO buffers, two FIFO work queues and one shared FIFO DLQ (§16.1, §2.1, R18.8)."""
    queues = _queues(resources)
    buffers = _buffer_queues(resources)
    work_queues = _work_queues(resources)
    _dlq_lid, dlq_body = _dlq(resources)

    assert len(queues) == _TOTAL_QUEUE_COUNT, (
        f"expected 5 queues (2 buffer + 2 work + 1 DLQ), found {len(queues)}"
    )
    assert len(buffers) == _BUFFER_QUEUE_COUNT, "expected two FIFO buffer queues"
    assert len(work_queues) == _WORK_QUEUE_COUNT, "expected two separate FIFO work queues"

    # Buffers and work queues are all FIFO with content-based dedup.
    for body in (*buffers.values(), *work_queues.values()):
        props = body["Properties"]
        assert props.get("FifoQueue") is True, "buffer and work queues must be FIFO (§16.1)"
        assert props.get("ContentBasedDeduplication") is True, "content-based dedup must be on"

    # The DLQ is FIFO and does not redrive to anything of its own.
    assert dlq_body["Properties"].get("FifoQueue") is True, "the shared DLQ must be FIFO (§16.1)"
    assert dlq_body["Properties"].get("RedrivePolicy") is None, (
        "the shared DLQ must not itself redrive (§16.1)"
    )


def test_buffers_and_work_queues_redrive_to_the_one_shared_dlq(
    resources: Mapping[str, Any],
) -> None:
    """All four buffer+work queues redrive to the single shared DLQ, maxReceiveCount 3 (§16.1)."""
    buffers = _buffer_queues(resources)
    work_queues = _work_queues(resources)
    dlq_lid, _ = _dlq(resources)

    dlq_targets: set[str] = set()
    for body in (*buffers.values(), *work_queues.values()):
        redrive = body["Properties"].get("RedrivePolicy")
        assert redrive is not None, "buffer and work queues must redrive to the DLQ (§16.1)"
        assert redrive["maxReceiveCount"] == _MAX_RECEIVE_COUNT, (
            "bounded retries then the DLQ (§16.1)"
        )
        target = redrive["deadLetterTargetArn"]
        # {"Fn::GetAtt": [<dlqLogicalId>, "Arn"]}
        assert target == {"Fn::GetAtt": [dlq_lid, "Arn"]}, (
            "buffers and work queues must redrive to the SAME shared DLQ (§16.1)"
        )
        dlq_targets.add(json.dumps(target, sort_keys=True))

    assert len(dlq_targets) == 1, "all four queues must redrive to the SAME shared DLQ (§16.1)"


def test_event_source_mapping_batch_sizes_and_partial_failures(
    resources: Mapping[str, Any],
) -> None:
    """Batch size 1 (hazard) and 10 + ReportBatchItemFailures (intake) (§16.1, R18.8)."""
    mappings = resources_of_type(resources, "AWS::Lambda::EventSourceMapping")
    assert len(mappings) == _MAPPING_COUNT, f"expected two mappings, found {len(mappings)}"

    by_batch: dict[int, Mapping[str, Any]] = {}
    for body in mappings.values():
        by_batch[int(body["Properties"]["BatchSize"])] = body["Properties"]

    assert set(by_batch) == {1, 10}, f"expected batch sizes {{1, 10}}, found {sorted(by_batch)}"

    hazard = by_batch[1]
    intake = by_batch[10]

    # Flood_Ingestor at batch size 1 reports no partial batch failures (a single message).
    assert not hazard.get("FunctionResponseTypes"), (
        "the hazard-side mapping (batch 1) needs no ReportBatchItemFailures (§16.1)"
    )
    # Event_Ingestor at batch size 10 uses ReportBatchItemFailures to keep FIFO order (R18.8).
    assert intake.get("FunctionResponseTypes") == ["ReportBatchItemFailures"], (
        "the intake-side mapping (batch 10) must report partial batch failures (R18.8)"
    )


def test_event_source_mappings_bind_to_the_matching_work_queue_and_ingestor(
    resources: Mapping[str, Any],
) -> None:
    """Batch-1 mapping: Flood_Ingestor ← hazard work queue; batch-10: Event_Ingestor ← intake."""
    mappings = resources_of_type(resources, "AWS::Lambda::EventSourceMapping")
    work_queues = _work_queues(resources)

    # Logical ids of the two work queues, keyed by their role (hazard / intake).
    hazard_qid = next(
        lid for lid, b in work_queues.items() if _queue_name(b).endswith("-hazard.fifo")
    )
    intake_qid = next(
        lid for lid, b in work_queues.items() if _queue_name(b).endswith("-intake.fifo")
    )

    def target_ref(body: Mapping[str, Any]) -> str:
        fn = body["Properties"]["FunctionName"]
        return str(fn["Ref"]) if isinstance(fn, Mapping) and "Ref" in fn else str(fn)

    def source_qid(body: Mapping[str, Any]) -> str:
        arn = body["Properties"]["EventSourceArn"]
        return str(arn["Fn::GetAtt"][0]) if isinstance(arn, Mapping) else str(arn)

    for body in mappings.values():
        batch = int(body["Properties"]["BatchSize"])
        ref = target_ref(body)
        if batch == 1:
            assert "FloodIngestor" in ref, "batch size 1 must feed the Flood_Ingestor (§16.1)"
            assert source_qid(body) == hazard_qid, (
                "the Flood_Ingestor must read the hazard WORK queue (§16.1)"
            )
        else:
            assert "EventIngestor" in ref, "batch size 10 must feed the Event_Ingestor (R18.8)"
            assert source_qid(body) == intake_qid, (
                "the Event_Ingestor must read the intake WORK queue (R18.8)"
            )


def test_eventbridge_rules_route_by_detail_type_and_group_by_incident(
    resources: Mapping[str, Any],
) -> None:
    """Rules filter by detail-type + source and land on buffers; Pipes group per incident.

    The per-incident FIFO grouping is applied by the **Pipe** onto the work queue
    (``MessageGroupId = $.body.detail.incident_id``, resolved per event), NOT by a literal
    ``$.detail.incident_id`` path on the rule target — EventBridge does not resolve that
    (the Wave-6 BLOCKER, §2.1, R18.8). Rules may carry any static buffer group id.
    """
    rules = resources_of_type(resources, "AWS::Events::Rule")
    pipes = resources_of_type(resources, "AWS::Pipes::Pipe")
    assert len(rules) == _RULE_COUNT, f"expected two EventBridge rules, found {len(rules)}"
    assert len(pipes) == _PIPE_COUNT, f"expected two EventBridge Pipes, found {len(pipes)}"

    buffer_qids = set(_buffer_queues(resources))
    work_by_role = {
        "hazard": next(
            lid
            for lid, b in _work_queues(resources).items()
            if _queue_name(b).endswith("-hazard.fifo")
        ),
        "intake": next(
            lid
            for lid, b in _work_queues(resources).items()
            if _queue_name(b).endswith("-intake.fifo")
        ),
    }

    # --- Rules: filter by detail-type + source, target a buffer, static (non-path) group id. ---
    detail_type_sets: list[frozenset[str]] = []
    for body in rules.values():
        pattern = body["Properties"]["EventPattern"]
        detail_type_sets.append(frozenset(pattern["detail-type"]))
        assert pattern.get("source"), "each rule must constrain the source allow-list (R14.1)"
        for target in body["Properties"]["Targets"]:
            arn = target["Arn"]
            assert isinstance(arn, Mapping) and arn["Fn::GetAtt"][0] in buffer_qids, (
                "each rule target must be a FIFO buffer queue (§2.1)"
            )
            group = target["SqsParameters"]["MessageGroupId"]
            assert group != _INCIDENT_GROUP_PATH, (
                "a rule must NOT use $.detail.incident_id — EventBridge cannot resolve it "
                "on a rule target; grouping is done by the Pipe (§2.1, R18.8)"
            )

    assert frozenset({"WeatherTick", "FloodPolygonUpdated"}) in detail_type_sets, (
        "a rule must route weather/flood events to the hazard buffer (§16.1)"
    )
    assert frozenset({"OutageReported", "MeterLastGasp", "JobCompleted"}) in detail_type_sets, (
        "a rule must route report/meter/job events to the intake buffer (§16.1)"
    )

    # --- Pipes: buffer → work queue, grouping per incident, resolved per event. ---
    for body in pipes.values():
        props = body["Properties"]
        src = props["Source"]
        tgt = props["Target"]
        assert isinstance(src, Mapping) and src["Fn::GetAtt"][0] in buffer_qids, (
            "each Pipe source must be a FIFO buffer queue (§2.1)"
        )
        tgt_qid = tgt["Fn::GetAtt"][0] if isinstance(tgt, Mapping) else tgt
        assert tgt_qid in work_by_role.values(), "each Pipe target must be a FIFO work queue (§2.1)"
        group = props["TargetParameters"]["SqsQueueParameters"]["MessageGroupId"]
        assert group == _INCIDENT_GROUP_PATH, (
            "the Pipe must group per incident via $.body.detail.incident_id, resolved per "
            "event by Pipes (§2.1, R18.8)"
        )


def test_work_queue_visibility_exceeds_six_times_consumer_timeout(
    resources: Mapping[str, Any],
) -> None:
    """Each WORK queue's visibility timeout is >= 6x its consumer timeout (§16.1).

    Filters to the WORK queues by name first (the 60 s buffers are excluded). The
    Flood_Ingestor times out at 30 s (visibility >= 180) and the Event_Ingestor at 60 s
    (visibility >= 360), so a slow batch never becomes visible again mid-processing.
    """
    work_queues = _work_queues(resources)
    visibilities = sorted(
        int(body["Properties"]["VisibilityTimeout"]) for body in work_queues.values()
    )
    assert visibilities == [180, 360], (
        f"expected work-queue visibility timeouts [180, 360] (6x 30s and 60s), found {visibilities}"
    )


def _sqs_actions_on(statement: Mapping[str, Any]) -> set[str]:
    return {a for a in statement_actions(statement) if a.startswith("sqs:")}


def test_delivery_and_pipe_roles_are_least_privilege(resources: Mapping[str, Any]) -> None:
    """Delivery and Pipe roles have only the SQS access they need (R14.1, least privilege).

    - The EventBridge-to-SQS delivery role may only ``SendMessage`` on the two buffers.
    - Each Pipe role may only Receive/Delete/GetQueueAttributes on its OWN buffer and
      ``SendMessage`` on its OWN work queue.
    """
    buffer_qids = set(_buffer_queues(resources))
    work_qids = set(_work_queues(resources))
    policies = resources_of_type(resources, "AWS::IAM::Policy")

    def get_att_id(ref: Any) -> str | None:
        return ref["Fn::GetAtt"][0] if isinstance(ref, Mapping) and "Fn::GetAtt" in ref else None

    def resource_ids(resource: Any) -> set[str]:
        items = resource if isinstance(resource, list) else [resource]
        return {rid for r in items if (rid := get_att_id(r)) is not None}

    delivery_send_seen = False
    pipe_roles_checked = 0

    for body in policies.values():
        for stmt in policy_statements(body):
            actions = _sqs_actions_on(stmt)
            if not actions:
                continue
            targets = resource_ids(stmt["Resource"])

            # Delivery role: SendMessage on both buffers only.
            if actions == {"sqs:SendMessage"} and targets == buffer_qids:
                delivery_send_seen = True

            # A Pipe SendMessage statement targets exactly one WORK queue.
            if actions == {"sqs:SendMessage"} and targets and targets <= work_qids:
                assert len(targets) == 1, "a Pipe may send to only its own work queue (R14.1)"

            # A Pipe consume statement targets exactly one buffer with no write action.
            if targets and targets <= buffer_qids and "sqs:SendMessage" not in actions:
                assert actions <= {
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes",
                    "sqs:ChangeMessageVisibility",
                }, "a Pipe may only consume from its own buffer (R14.1)"
                assert len(targets) == 1, "a Pipe consumes from only its own buffer (R14.1)"
                pipe_roles_checked += 1

    assert delivery_send_seen, (
        "the EventBridge-to-SQS delivery role must SendMessage on the two buffers only (R14.1)"
    )
    assert pipe_roles_checked == _PIPE_COUNT, (
        f"expected {_PIPE_COUNT} Pipe consume statements scoped to their buffers, "
        f"found {pipe_roles_checked}"
    )
