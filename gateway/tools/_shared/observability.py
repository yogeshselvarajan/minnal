"""Powertools observability builders with a no-op tracing fallback (design §13).

Every handler wants the design's decorator stack — Logger, Tracer, Metrics
(§5 preamble) — and the real Powertools ``Tracer`` is used in the Lambda runtime,
where the X-Ray SDK it depends on is present. In a unit-test or local process the
X-Ray SDK may be absent, and constructing a Powertools ``Tracer`` imports it
eagerly; rather than force the whole test suite to install a Lambda-only tracing
dependency, :func:`build_tracer` returns a no-op tracer with the same surface
(``capture_lambda_handler``, ``capture_method``, ``put_annotation``,
``put_metadata``) when the SDK cannot be imported.

This changes no behaviour that a test or a safety rule depends on: annotations and
subsegments are observability only (R2.2). In ``aws`` the real tracer is always
built, so production tracing is unaffected. The Logger and Metrics builders are
thin pass-throughs kept here so every handler constructs the trio identically.

For typing, :func:`build_tracer` is declared to return the real ``Tracer`` type
(the no-op duck-types it at runtime), so ``@tracer.capture_lambda_handler`` stays
correctly typed on the handler it decorates.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from aws_lambda_powertools import Logger, Metrics, Tracer

_F = TypeVar("_F", bound=Callable[..., object])


class _NoopTracer:
    """A tracer with the Powertools surface that records nothing (§13.3 fallback)."""

    def capture_lambda_handler(self, handler: _F | None = None, **_kwargs: object) -> _F:
        """Return the handler unchanged (no subsegment when X-Ray is absent)."""
        assert handler is not None  # noqa: S101 - always used as a plain decorator
        return handler

    def capture_method(self, method: _F | None = None, **_kwargs: object) -> _F:
        """Return the method unchanged."""
        assert method is not None  # noqa: S101 - always used as a plain decorator
        return method

    def put_annotation(self, key: str, value: object) -> None:
        """Ignore the annotation."""

    def put_metadata(self, key: str, value: object, namespace: str | None = None) -> None:
        """Ignore the metadata."""


def tracing_available() -> bool:
    """Return whether the Powertools ``Tracer`` can be constructed (X-Ray present)."""
    return importlib.util.find_spec("aws_xray_sdk") is not None


def build_logger(service: str = "minnal-grid-tools") -> Logger:
    """Return the shared Powertools :class:`Logger` (R2.1)."""
    from aws_lambda_powertools import Logger  # noqa: PLC0415

    return Logger(service=service)


def build_metrics(namespace: str = "Minnal") -> Metrics:
    """Return the shared Powertools :class:`Metrics` (R2.3)."""
    from aws_lambda_powertools import Metrics  # noqa: PLC0415

    return Metrics(namespace=namespace)


def build_tracer() -> Tracer:
    """Return a real Powertools :class:`Tracer`, or a no-op when X-Ray is absent (§13.3).

    The real tracer is used in the Lambda runtime; the no-op keeps every handler
    module importable in a test or local process without the X-Ray SDK. The no-op
    duck-types the ``Tracer`` surface the handlers use, and is cast to ``Tracer`` so
    the handler decorators stay typed. Tracing is observability only, so the
    fallback changes no rule a test depends on (R2.2).
    """
    if not tracing_available():
        return cast("Tracer", _NoopTracer())
    from aws_lambda_powertools import Tracer  # noqa: PLC0415

    return Tracer()
