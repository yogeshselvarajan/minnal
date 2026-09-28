"""Deterministic event generation (pure core, no boto3).

Public surface: :func:`generate` assembles all events for a run plus the hidden
attribution map; the value types :class:`~simulator.gen_events.GenEvent` and
:class:`~simulator.gen_events.GenerationResult` live in ``simulator.gen_events``.
"""

from __future__ import annotations

from simulator.gen_events import GenerationResult, GenEvent
from simulator.generation.grid_view import GridView
from simulator.generation.pipeline import generate

__all__ = ["GenEvent", "GenerationResult", "GridView", "generate"]
