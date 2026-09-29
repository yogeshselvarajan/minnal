"""Powertools Idempotency configuration and a local fake (design §5 preamble, §11.7).

Write tools wrap their execution body in Powertools ``@idempotent_function`` keyed
on ``incident_id`` plus the tool's key field, with a ``payload_validation_jmespath``
selecting the whole request body, so the same key with the same payload returns the
stored result and a changed payload raises ``IdempotencyValidationError``, which
:func:`wrap` maps to a ``CONFLICT`` (R1.9). The critical rule is P34: **a retryable
outcome must not be cached.** Powertools deletes the in-progress record when the
wrapped function raises, so the body raises :class:`_shared.errors.UpstreamError`
(and its subclasses) on a transient failure rather than returning an error
envelope; only deterministic results — success, veto, validation error — are
returned and therefore cached (§11.7).

This module wraps Powertools; it is the one ``_shared`` module allowed to import
it. It does not import ``boto3`` directly (the persistence layer does, at the
edge). In ``local`` mode no persistence layer is built: the conditional writes in
§7.4 already make every write idempotent, so the local fake simply executes the
body (R17.1).

The exact Powertools API used (v3): ``idempotent_function(data_keyword_argument=...,
persistence_store=..., config=IdempotencyConfig(...))`` and
``DynamoDBPersistenceLayer(table_name=...)``. Verified against the installed
package (3.35.0) and the Powertools idempotency documentation
(https://docs.aws.amazon.com/powertools/python/3.14.0/utilities/idempotency/).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING

from _shared.settings import Settings

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.idempotency import (
        BasePersistenceLayer,
        IdempotencyConfig,
    )

# Powertools stores idempotency records for this long; long enough that a retry
# of a deterministic result replays rather than re-executes, short enough that a
# stale key is reclaimed (§11.7). A day covers an operational period comfortably.
_EXPIRES_AFTER_SECONDS = 24 * 60 * 60


# Validate the whole request body under one key. The JMESPath ``@`` selects the
# entire payload, so a second call under the same idempotency key with any changed
# argument fails Powertools' payload validation (``IdempotencyValidationError``,
# mapped to ``CONFLICT``) instead of silently replaying the first result (R1.9).
# The key fields themselves are equal by definition under one key, so including
# them is harmless; an identical payload still replays without re-executing.
_PAYLOAD_VALIDATION_JMESPATH = "@"


def build_config(key_jmespath: str) -> IdempotencyConfig:
    """Return the :class:`IdempotencyConfig` for a write tool (§11.7, R1.9).

    Args:
        key_jmespath: A JMESPath selecting ``[incident_id, <key field>]`` from the
            event, e.g. ``"[incident_id, report_id]"``.

    Returns:
        A configured :class:`IdempotencyConfig` with payload validation on, so a
        changed payload under the same key raises ``IdempotencyValidationError``
        (mapped to ``CONFLICT`` by :func:`wrap`) rather than replaying (R1.9).
    """
    from aws_lambda_powertools.utilities.idempotency import IdempotencyConfig  # noqa: PLC0415

    return IdempotencyConfig(
        event_key_jmespath=key_jmespath,
        payload_validation_jmespath=_PAYLOAD_VALIDATION_JMESPATH,
        expires_after_seconds=_EXPIRES_AFTER_SECONDS,
        raise_on_no_idempotency_key=True,
    )


def build_persistence(settings: Settings) -> BasePersistenceLayer:
    """Return the DynamoDB persistence layer for ``aws`` mode (§11.7).

    Args:
        settings: The validated :class:`Settings`; ``idempotency_table_name`` is
            required in ``aws`` mode (enforced at start-up, R14.5).

    Returns:
        A :class:`DynamoDBPersistenceLayer` over the idempotency table.

    Raises:
        ValueError: The idempotency table is not configured.
    """
    from aws_lambda_powertools.utilities.idempotency.persistence.dynamodb import (  # noqa: PLC0415
        DynamoDBPersistenceLayer,
    )

    if not settings.idempotency_table_name:
        raise ValueError("aws backend requires MINNAL_IDEMPOTENCY_TABLE for a write tool")
    return DynamoDBPersistenceLayer(table_name=settings.idempotency_table_name)


def wrap[R](
    body: Callable[..., R],
    *,
    settings: Settings,
    key_jmespath: str,
    data_keyword_argument: str,
) -> Callable[..., R]:
    """Wrap a write tool's execution body in Powertools idempotency (§11.7, R1.9).

    In ``aws`` mode the body is wrapped in ``@idempotent_function`` so a repeat of
    the same key returns the stored result. In ``local`` mode the body is returned
    unwrapped: the §7.4 conditional writes are the idempotency guarantee there, and
    no DynamoDB persistence layer exists (R17.1).

    The body must **raise** on a retryable outcome (``UpstreamError``) rather than
    return an error envelope, so Powertools discards the in-progress record and the
    next retry re-executes instead of replaying a transient failure (P34, R1.12).

    Args:
        body: The single-argument execution body, e.g. ``_execute(req=...)``.
        settings: The validated :class:`Settings`.
        key_jmespath: The idempotency key JMESPath (see :func:`build_config`).
        data_keyword_argument: The keyword the body receives its input under, e.g.
            ``"req"``. Powertools hashes that argument.

    Returns:
        The wrapped (aws) or original (local) callable.
    """
    if settings.backend != "aws":
        return body
    from _shared.errors import ConflictError  # noqa: PLC0415
    from aws_lambda_powertools.utilities.idempotency import idempotent_function  # noqa: PLC0415
    from aws_lambda_powertools.utilities.idempotency.exceptions import (  # noqa: PLC0415
        IdempotencyValidationError,
    )

    idempotent: Callable[..., R] = idempotent_function(
        data_keyword_argument=data_keyword_argument,
        persistence_store=build_persistence(settings),
        config=build_config(key_jmespath),
    )(body)

    @functools.wraps(body)
    def wrapped(*args: object, **kwargs: object) -> R:
        try:
            return idempotent(*args, **kwargs)
        except IdempotencyValidationError as exc:
            # Same key, different payload: a deterministic CONFLICT, not retryable
            # (R1.9). ``IdempotencyAlreadyInProgressError`` and the body's own
            # ``UpstreamError`` are distinct and still propagate unchanged (P34).
            raise ConflictError(
                "This idempotency key was already used with a different request."
            ) from exc

    return wrapped
