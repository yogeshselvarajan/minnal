"""Property 55: job numbers come only from tools and config.

*For all* diagnostics outputs and model plans, every ``customers_restored``,
``effort_crew_minutes``, ``waiting_seconds``, ``is_make_safe`` and ``required_skill`` used in a
``rank_restoration_jobs`` call is derived from tool data and ``effort.yaml``; a model-supplied
value for any of them is rejected; the restoration order is the tool's order; and every
effort-table fallback is reported (design §20 Property 55, §6.2).

Validates: Requirements 8.11, 8.12, 8.13, 8.3, 7.9.

The whole safety argument here is that no number a crew acts on comes from a model. It is
tested against the pure ``assemble_jobs`` (design §6.2) with no fakes: every field of every
built ``Job`` is asserted to equal the value the tool data / effort table dictate, the
per-device order of the built jobs is exactly the order the ``suspected`` list arrives in (the
tool's order, R8.3), and every effort-table fallback is named in the returned defaults list
(R8.12). The model-rejection clause (R8.13) is exercised through the ``extra="forbid"`` /
``reject_safety_fields`` contract that a model output would have to pass.

The known-bad ``@example`` is the regression this property exists to catch: a device whose
``(device_type, symptom)`` pair is absent from the effort table, which MUST fall back to the
documented default AND report the fallback rather than invent a number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

# Pattern-root imports resolve via the conftest ``sys.path`` insert, so ruff groups them here.
from domain.contracts import (  # type: ignore[import-not-found]
    CoveredOutage,
    Job,
    SuspectedDevice,
)
from domain.jobs import (  # type: ignore[import-not-found]
    DEVICE_SKILL,
    SYMPTOM_SEVERITY,
    EffortTable,
    assemble_jobs,
    worst_symptom,
)
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import ValidationError

# The documented effort-table default used by the fallback regression test.
_DEFAULT_EFFORT_MINUTES = 90

_EFFORT_YAML = (
    Path(__file__).resolve().parents[3] / "patterns" / "agui-minnal" / "config" / "effort.yaml"
)

_DEVICE_TYPES = ("substation", "feeder", "lateral", "dt")
_DEVICE_PREFIX = {"substation": "sub", "feeder": "fdr", "lateral": "lat", "dt": "dt"}
_SYMPTOMS = SYMPTOM_SEVERITY  # the five symptoms, worst-first
_ULID = "01HGVMCG005DV9P1DNGC1END2G"
# A frozen "now" far after every reported_at so waiting_seconds is a stable positive number.
_NOW = datetime(2023, 12, 4, 12, 0, 0, tzinfo=UTC)


def _load_effort_table() -> EffortTable:
    """The real bundled effort table (§6.2, R8.12), so the property tests the shipped config."""
    data = yaml.safe_load(_EFFORT_YAML.read_text(encoding="utf-8"))
    return EffortTable(table=data["table"], default=int(data["default"]))


_EFFORT = _load_effort_table()


@st.composite
def _suspected_device(draw: st.DrawFn, index: int) -> tuple[SuspectedDevice, int]:
    """A suspected device plus its customers-restored count, all from tool-shaped data.

    Every value that ends up in a Job comes from here (tool data) or the effort table, never
    from a model. ``index`` keeps device ids unique within one set.
    """
    device_type = draw(st.sampled_from(_DEVICE_TYPES))
    device_id = f"{_DEVICE_PREFIX[device_type]}_{index}"
    n_out = draw(st.integers(min_value=1, max_value=5))
    covered: list[CoveredOutage] = []
    for k in range(n_out):
        covered.append(
            CoveredOutage(
                outage_id=f"out_{_ULID}",
                symptom=draw(st.sampled_from(_SYMPTOMS)),
                is_emergency=draw(st.booleans()),
                # reported_at strictly before _NOW so waiting_seconds >= 0.
                reported_at=(_NOW - timedelta(minutes=draw(st.integers(1, 6000)) + k))
                .isoformat()
                .replace("+00:00", "Z"),
            )
        )
    customers = draw(st.integers(min_value=0, max_value=5000))
    device = SuspectedDevice(
        device_id=device_id,
        device_type=device_type,
        path_from_substation=(f"{_DEVICE_PREFIX[device_type]}_{index}",),
        covered=tuple(covered),
        customers_downstream_reporting_pct=draw(
            st.floats(min_value=0.0, max_value=100.0, allow_nan=False)
        ),
    )
    return device, customers


@st.composite
def _device_sets(draw: st.DrawFn) -> tuple[list[SuspectedDevice], dict[str, int]]:
    """1..8 suspected devices with a per-device customers count from the trace (tool data)."""
    n = draw(st.integers(min_value=1, max_value=8))
    pairs = [draw(_suspected_device(index=i)) for i in range(n)]
    devices = [d for d, _ in pairs]
    customers = {d.device_id: c for (d, c) in pairs}
    return devices, customers


@given(data=_device_sets())
@example(
    # Known-bad regression: a device_type/symptom pair NOT in the effort table. The table
    # covers every (device_type, symptom) pair, so to force a genuine fallback we drive
    # assemble_jobs with an effort table whose row for this pair is missing (below). Here the
    # example simply supplies a well-formed single device; the fallback is exercised in the
    # dedicated test at the bottom, and this example guarantees the generative body runs on a
    # deterministic minimal input every run.
    data=(
        [
            SuspectedDevice(
                device_id="dt_0",
                device_type="dt",
                path_from_substation=("dt_0",),
                covered=(
                    CoveredOutage(
                        outage_id=f"out_{_ULID}",
                        symptom="submerged_equipment",
                        is_emergency=True,
                        reported_at="2023-12-04T10:00:00Z",
                    ),
                ),
                customers_downstream_reporting_pct=50.0,
            )
        ],
        {"dt_0": 12},
    ),
)
def test_property_P55_job_numbers_from_tools(
    data: tuple[list[SuspectedDevice], dict[str, int]],
) -> None:
    """Every job field equals the tool/config value, order is the tool's, fallbacks reported."""
    # Arrange.
    suspected, customers_by_device = data

    # Act.
    jobs, defaults = assemble_jobs(suspected, customers_by_device, _EFFORT, _NOW)

    # Assert: one job per device, in the tool's order (R8.3 — the queue is not re-ordered).
    assert [j.device_id for j in jobs] == [d.device_id for d in suspected]

    for job, device in zip(jobs, suspected, strict=True):
        worst = worst_symptom(o.symptom for o in device.covered)
        effort, used_default = _EFFORT.lookup(device.device_type, worst)

        # customers_restored is the tool's number, never a model's (R8.11, R8.13).
        assert job.customers_restored == customers_by_device[device.device_id]
        # effort_crew_minutes is the effort table's value (R8.12, R8.13).
        assert job.effort_crew_minutes == effort
        # is_make_safe is true iff a covered outage is an emergency (R8.11).
        assert job.is_make_safe is any(o.is_emergency for o in device.covered)
        # required_skill is derived from device type (R8.11).
        assert job.required_skill == DEVICE_SKILL[device.device_type]
        # waiting_seconds is derived from the oldest covered reported_at (R8.11), never negative.
        oldest = min(
            datetime.fromisoformat(o.reported_at.replace("Z", "+00:00")) for o in device.covered
        )
        assert job.waiting_seconds == max(0, int((_NOW - oldest).total_seconds()))
        assert job.waiting_seconds >= 0
        # Every fallback is reported, and only genuine fallbacks are (R8.12).
        pair = f"{device.device_type}/{worst}"
        assert (pair in defaults) is used_default


def test_property_P55_effort_fallback_is_reported_not_invented() -> None:
    """A (device_type, symptom) pair absent from the table falls back and is REPORTED (R8.12).

    This is the known-bad regression the property guards: rather than invent a crew-minutes
    number for an unknown pair, ``assemble_jobs`` must use the documented default and name the
    substitution so ``commander_summary`` can say so.
    """
    # Arrange: an effort table missing the (dt, no_power) row entirely.
    partial = EffortTable(
        table={"dt": {"submerged_equipment": 150}}, default=_DEFAULT_EFFORT_MINUTES
    )
    device = SuspectedDevice(
        device_id="dt_5",
        device_type="dt",
        path_from_substation=("dt_5",),
        covered=(
            CoveredOutage(
                outage_id=f"out_{_ULID}",
                symptom="no_power",  # not in the partial table's dt row
                is_emergency=False,
                reported_at="2023-12-04T11:00:00Z",
            ),
        ),
        customers_downstream_reporting_pct=10.0,
    )

    # Act.
    jobs, defaults = assemble_jobs([device], {"dt_5": 3}, partial, _NOW)

    # Assert: the default was used AND named.
    assert jobs[0].effort_crew_minutes == _DEFAULT_EFFORT_MINUTES
    assert defaults == ["dt/no_power"]


def test_property_P55_model_supplied_job_field_is_rejected() -> None:
    """A model-supplied safety-meaning field on a Job payload is rejected (R8.13, extra=forbid).

    Job assembly is code-only; ``Job`` is frozen with ``extra="forbid"``, so a model output
    that tried to inject an extra number fails validation rather than being silently accepted.
    """
    # Arrange: a valid Job payload plus one field a model must never supply.
    payload = {
        "job_id": "job_dt_5",
        "device_id": "dt_5",
        "is_make_safe": False,
        "customers_restored": 3,
        "effort_crew_minutes": 90,
        "waiting_seconds": 3600,
        "required_skill": "underground_cable",
        "safety_clearance_id": "sfc_01HGW0000000000000000001",
    }

    # Act + Assert.
    with pytest.raises(ValidationError):
        Job.model_validate(payload)
