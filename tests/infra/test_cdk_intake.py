"""Intake-construct assertions on the grid-tools template (task 73.2).

Design §16.1 / §2.1 / R18.8: two SEPARATE FIFO queues (``hazard.fifo`` and ``intake.fifo``)
so a burst of citizen reports can never delay the flood picture; one shared DLQ with a redrive
policy on both; and two event source mappings — the Flood_Ingestor on the hazard queue at
**batch size 1**, and the Event_Ingestor on the intake queue at **batch size 10** with
``FunctionResponseTypes: ["ReportBatchItemFailures"]``. The EventBridge rules route by
``detail-type`` and set the per-incident ``MessageGroupId`` from ``$.detail.incident_id``.

_Req 18.8_ _Design §16.1_
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from tests.infra.conftest import resources_of_type

_TOTAL_QUEUE_COUNT = 3  # hazard.fifo + intake.fifo + shared DLQ
_WORK_QUEUE_COUNT = 2  # hazard.fifo + intake.fifo
_MAX_RECEIVE_COUNT = 3  # bounded retries before the DLQ (§16.1)
_MAPPING_COUNT = 2  # one per ingestor
_RULE_COUNT = 2  # one per queue


def _queues(resources: Mapping[str, Any]) -> dict[str, Any]:
    return resources_of_type(resources, "AWS::SQS::Queue")


def _fifo_intake_queues(resources: Mapping[str, Any]) -> dict[str, Any]:
    """The two work queues (hazard + intake), excluding the DLQ (no redrive policy)."""
    return {
        lid: body
        for lid, body in _queues(resources).items()
        if body["Properties"].get("RedrivePolicy") is not None
    }


def test_two_separate_fifo_work_queues_plus_one_dlq(resources: Mapping[str, Any]) -> None:
    """Exactly two FIFO work queues and one shared DLQ (§16.1, R18.8)."""
    queues = _queues(resources)
    work_queues = _fifo_intake_queues(resources)

    # hazard.fifo, intake.fifo and the shared DLQ.
    assert len(queues) == _TOTAL_QUEUE_COUNT, (
        f"expected 3 queues (2 work + 1 DLQ), found {len(queues)}"
    )
    assert len(work_queues) == _WORK_QUEUE_COUNT, "expected two separate work queues"

    for body in work_queues.values():
        props = body["Properties"]
        assert props.get("FifoQueue") is True, "work queues must be FIFO (§16.1)"
        assert props.get("ContentBasedDeduplication") is True, "content-based dedup must be on"


def test_both_work_queues_redrive_to_the_one_shared_dlq(resources: Mapping[str, Any]) -> None:
    """Both work queues redrive to the single shared DLQ with maxReceiveCount 3 (§16.1, §16.4)."""
    work_queues = _fifo_intake_queues(resources)

    dlq_targets: set[str] = set()
    for body in work_queues.values():
        redrive = body["Properties"]["RedrivePolicy"]
        assert redrive["maxReceiveCount"] == _MAX_RECEIVE_COUNT, (
            "bounded retries then the DLQ (§16.1)"
        )
        target = redrive["deadLetterTargetArn"]
        # {"Fn::GetAtt": [<dlqLogicalId>, "Arn"]}
        dlq_targets.add(json.dumps(target, sort_keys=True))

    assert len(dlq_targets) == 1, "both work queues must redrive to the SAME shared DLQ (§16.1)"


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


def test_event_source_mappings_bind_to_the_matching_ingestors(
    resources: Mapping[str, Any],
) -> None:
    """The batch-1 mapping feeds the Flood_Ingestor; the batch-10 mapping feeds Event_Ingestor."""
    mappings = resources_of_type(resources, "AWS::Lambda::EventSourceMapping")

    def target_ref(body: Mapping[str, Any]) -> str:
        fn = body["Properties"]["FunctionName"]
        return str(fn["Ref"]) if isinstance(fn, Mapping) and "Ref" in fn else str(fn)

    for body in mappings.values():
        batch = int(body["Properties"]["BatchSize"])
        ref = target_ref(body)
        if batch == 1:
            assert "FloodIngestor" in ref, "batch size 1 must feed the Flood_Ingestor (§16.1)"
        else:
            assert "EventIngestor" in ref, "batch size 10 must feed the Event_Ingestor (R18.8)"


def test_eventbridge_rules_route_by_detail_type_and_group_by_incident(
    resources: Mapping[str, Any],
) -> None:
    """Two rules route weather/flood and outage/meter/job to their queues, per incident."""
    rules = resources_of_type(resources, "AWS::Events::Rule")
    assert len(rules) == _RULE_COUNT, f"expected two EventBridge rules, found {len(rules)}"

    detail_type_sets: list[frozenset[str]] = []
    for body in rules.values():
        pattern = body["Properties"]["EventPattern"]
        detail_type_sets.append(frozenset(pattern["detail-type"]))
        for target in body["Properties"]["Targets"]:
            group = target["SqsParameters"]["MessageGroupId"]
            assert group == "$.detail.incident_id", (
                "each SQS target must group by incident so ordering is per incident (§16.1)"
            )

    assert frozenset({"WeatherTick", "FloodPolygonUpdated"}) in detail_type_sets, (
        "a rule must route weather/flood events to the hazard queue (§16.1)"
    )
    assert frozenset({"OutageReported", "MeterLastGasp", "JobCompleted"}) in detail_type_sets, (
        "a rule must route report/meter/job events to the intake queue (§16.1)"
    )


def test_work_queue_visibility_exceeds_six_times_consumer_timeout(
    resources: Mapping[str, Any],
) -> None:
    """Each work queue's visibility timeout is >= 6x its consumer timeout (§16.1).

    The Flood_Ingestor times out at 30 s (visibility >= 180) and the Event_Ingestor at 60 s
    (visibility >= 360), so a slow batch never becomes visible again mid-processing.
    """
    work_queues = _fifo_intake_queues(resources)
    visibilities = sorted(
        int(body["Properties"]["VisibilityTimeout"]) for body in work_queues.values()
    )
    assert visibilities == [180, 360], (
        f"expected visibility timeouts [180, 360] (6x 30s and 60s), found {visibilities}"
    )
