"""Adapter factory: the only place ``MINNAL_BACKEND`` is read (design §4.2).

``make_ports(settings)`` builds the :class:`_shared.ports.Ports` bundle for the
configured backend. Everything else in the codebase is mode-blind: the Logic
never reads ``settings.backend`` and never imports an adapter module directly
(R17.2, R17.6, R17.7).
"""

from __future__ import annotations

from _shared.ports import Ports
from _shared.settings import Settings


def make_ports(settings: Settings) -> Ports:
    """Build the port bundle for the configured backend (§4.2, R17.6).

    Args:
        settings: The validated :class:`Settings`.

    Returns:
        A :class:`_shared.ports.Ports` bundle wired to the ``local`` or ``aws``
        adapters.

    Raises:
        ValueError: The backend is not recognised (defence in depth; the
            ``Settings`` literal already rejects it).
    """
    if settings.backend == "local":
        # Deferred so a local run never imports boto3; this is the one place
        # the backend is branched on (R17.2, R17.6).
        from _shared.adapters.local import make_local_ports  # noqa: PLC0415

        return make_local_ports(settings)
    if settings.backend == "aws":
        from _shared.adapters.aws import make_aws_ports  # noqa: PLC0415

        return make_aws_ports(settings)
    raise ValueError(f"unknown backend: {settings.backend!r}")
