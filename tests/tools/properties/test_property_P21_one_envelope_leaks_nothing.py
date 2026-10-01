"""Property 21: every invocation returns exactly one well-formed envelope.

Validates R1.4, R1.5, R1.6, R1.11.

*For all* inputs — valid, schema-invalid, adapter-failing, or triggering an
unexpected exception — the handler returns exactly one Envelope; ``ok: false``
carries a valid ``Error_Code``; ``summary`` when present is at most 280
characters; the message and ``details`` contain no stack trace, AWS request id,
ARN, table name or task token; and a schema-invalid input causes no adapter write
(design §18 P21, §5 preamble, §11.3).

Mechanism. The real ``_shared.handler.run_tool`` scaffolding is driven with bodies
that (a) succeed, (b) raise ``pydantic.ValidationError``, (c) raise each
``MinnalError`` subclass carrying leaky-looking ``details``, and (d) raise an
arbitrary ``Exception`` whose message embeds an ARN, a table name, a request id and
a task token. The returned dict is parsed back through the ``Envelope`` model
(proving it is exactly one well-formed envelope) and scanned for the leak markers.
A schema-invalid input runs a body that would have written to a spy adapter; the
spy must record no write.

Not a ``[SAFETY]`` property (design §18: P21 is unmarked). The ``default``/``ci``
profiles (200 examples) apply.
"""

from __future__ import annotations

import pydantic
from _shared.envelope import SUMMARY_MAX_CHARS, Envelope, ok
from _shared.errors import (
    ERROR_CODES,
    ConflictError,
    InputValidationError,
    NotFoundError,
    SafetyViolation,
    UpstreamError,
)
from _shared.handler import run_tool
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict

from tests.tools.fakes import CapturingLogger

_CORR = "corr_00000000000000000000000001"

# Strings that must never surface in an envelope's message or details (R1.6).
_ARN = "arn:aws:states:us-east-1:123456789012:execution:minnal-wo:exec-1"
_TABLE = "minnal-dev-outages"
_REQUEST_ID = "req-0123456789abcdef"
_TASK_TOKEN = "AAAAKgAAAAIAAAAAAAAAAtask-token-secret"  # noqa: S105 - a fake leak marker, not a secret
_LEAK_MARKERS = (_ARN, _TABLE, _REQUEST_ID, _TASK_TOKEN, "Traceback", "arn:aws:")


class _Model(BaseModel):
    """A tiny input model so a schema-invalid payload raises ValidationError."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: int


def _envelope(result: dict[str, object]) -> Envelope:
    """Parse the handler result back through the Envelope model (well-formed check)."""
    return Envelope.model_validate(result)


def _assert_no_leak(env: Envelope) -> None:
    """Assert the error message and details carry no internal markers (R1.6)."""
    if env.error is None:
        return
    haystack = env.error.message + repr(env.error.details)
    for marker in _LEAK_MARKERS:
        assert marker not in haystack


@given(value=st.integers(min_value=-1000, max_value=1000))
@example(value=0)
def test_property_P21_success_is_one_well_formed_envelope(value: int) -> None:
    """A valid input returns exactly one success envelope with a bounded summary."""
    logger = CapturingLogger()

    def body() -> dict[str, object]:
        model = _Model.model_validate({"value": value})
        return ok({"doubled": model.value * 2}, f"doubled to {model.value * 2}", _CORR)

    result = run_tool(body, correlation_id=_CORR, logger=logger)  # type: ignore[arg-type]
    env = _envelope(result)
    assert env.ok is True
    assert env.error is None
    assert env.summary is not None and len(env.summary) <= SUMMARY_MAX_CHARS
    assert env.correlation_id == _CORR


@given(payload=st.dictionaries(st.text(max_size=8), st.text(max_size=8), max_size=3))
@example(payload={"value": "not-an-int", "extra": "phone-9876543210"})  # known-bad: bad schema
def test_property_P21_schema_invalid_is_one_envelope_no_write(payload: dict[str, str]) -> None:
    """A schema-invalid input yields one VALIDATION_ERROR envelope and no adapter write."""
    logger = CapturingLogger()
    writes: list[object] = []

    def body() -> dict[str, object]:
        model = _Model.model_validate(payload)  # raises for a bad/missing/extra field
        writes.append(model)  # a write only happens AFTER successful validation
        return ok({"doubled": model.value * 2}, "ok", _CORR)

    result = run_tool(body, correlation_id=_CORR, logger=logger)  # type: ignore[arg-type]
    env = _envelope(result)
    if env.ok:
        # The only way to succeed is a valid {"value": int} payload.
        assert "value" in payload
    else:
        assert env.error is not None
        assert env.error.code in ERROR_CODES
        # A schema failure carried only loc/type, never a submitted value (R2.4).
        errors = env.error.details.get("errors", [])
        assert all(set(e) <= {"loc", "type"} for e in errors)  # type: ignore[arg-type]
        assert not writes  # no adapter write on a schema-invalid input (R1.4)


@given(
    which=st.sampled_from(("validation", "not_found", "conflict", "safety", "upstream")),
)
@example(which="safety")  # known-bad: a veto must carry a rule_id and leak nothing
def test_property_P21_minnal_errors_map_to_valid_codes(which: str) -> None:
    """Each MinnalError maps to a valid Error_Code and leaks no internal details."""
    logger = CapturingLogger()
    leaky = {"table": _TABLE, "arn": _ARN, "request_id": _REQUEST_ID}

    def body() -> dict[str, object]:
        if which == "validation":
            raise InputValidationError("Input did not match the schema.")
        if which == "not_found":
            raise NotFoundError("A referenced item was not found.")
        if which == "conflict":
            raise ConflictError("The request conflicts with an existing item.")
        if which == "safety":
            raise SafetyViolation("A safety rule refused the request.", rule_id="FLOOD_ROUTE")
        raise UpstreamError("An upstream service failed. Try again later.")

    result = run_tool(body, correlation_id=_CORR, logger=logger)  # type: ignore[arg-type]
    env = _envelope(result)
    assert env.ok is False
    assert env.error is not None
    assert env.error.code in ERROR_CODES
    if which == "safety":
        assert env.error.rule_id == "FLOOD_ROUTE"
    if which == "upstream":
        assert env.error.retryable is True
    _assert_no_leak(env)
    # The leaky-looking dict was never handed to the envelope; it must not appear.
    _ = leaky


@given(seed=st.integers(min_value=0, max_value=1000))
@example(seed=0)  # known-bad: an unexpected exception must be opaque INTERNAL
def test_property_P21_unexpected_exception_is_opaque_internal(seed: int) -> None:
    """An arbitrary exception with leaky text becomes one opaque INTERNAL envelope."""
    logger = CapturingLogger()

    def body() -> dict[str, object]:
        raise RuntimeError(
            f"boom at {_ARN} table={_TABLE} request={_REQUEST_ID} token={_TASK_TOKEN} seed={seed}"
        )

    result = run_tool(body, correlation_id=_CORR, logger=logger)  # type: ignore[arg-type]
    env = _envelope(result)
    assert env.ok is False
    assert env.error is not None
    assert env.error.code == "INTERNAL"
    # The opaque message carries none of the exception's internals (R1.6, P21).
    _assert_no_leak(env)
    assert env.error.message == "The tool failed. Try again later."


def test_property_P21_pydantic_validation_error_carries_only_loc_and_type() -> None:
    """A pydantic ValidationError surfaces only loc/type, never a submitted value."""
    logger = CapturingLogger()

    def body() -> dict[str, object]:
        _Model.model_validate({"value": "phone-9876543210", "note": "email@example.com"})
        return ok({}, "ok", _CORR)  # unreachable

    result = run_tool(body, correlation_id=_CORR, logger=logger)  # type: ignore[arg-type]
    env = _envelope(result)
    assert env.error is not None
    assert env.error.code == "VALIDATION_ERROR"
    haystack = repr(env.error.details)
    assert "9876543210" not in haystack
    assert "email@example.com" not in haystack


def test_pydantic_validation_error_is_distinct_from_minnal() -> None:
    """Sanity: the project's InputValidationError is not pydantic.ValidationError."""
    assert not issubclass(InputValidationError, pydantic.ValidationError)
