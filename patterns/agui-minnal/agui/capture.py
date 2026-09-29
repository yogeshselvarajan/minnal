"""The ``agui-stream.jsonl`` replay writer (§12.5, R22.7).

The war room reads a live stream that is only *per-source* ordered (§12.4 point 6), so a replay
needs a total order. This writer assigns a monotonic integer ``seq`` at emit time and writes one
JSON object per line in the §12.5 shape:

```json
{"seq":1,"emitted_at":"2026-09-29T04:10:00Z","type":"RUN_STARTED","value":{...}}
{"seq":2,"emitted_at":"...","type":"CUSTOM","name":"minnal.agent_step","value":{...}}
```

It is enabled by :attr:`~config.settings.Settings.capture_events`: always in offline mode (the
acceptance scenario replays the file, Property 60) and in ``aws`` mode only when
``MINNAL_EVENT_CAPTURE`` is set. Determinism: with the same fixture, seed and script the file is
byte-identical apart from ``emitted_at``, which the offline clock freezes — so the offline artefact
is byte-identical.

Edge module: it writes a file (the one I/O it does) and reads AG-UI event shapes. No AWS call.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from ag_ui.core import CustomEvent


def _event_type(event: object) -> str:
    """The AG-UI event ``type`` as a plain string, whether an enum or already a string."""
    raw = getattr(event, "type", None)
    if raw is None and isinstance(event, Mapping):
        raw = event.get("type")
    return str(getattr(raw, "value", raw))


def _event_value(event: object) -> object:
    """The AG-UI event body: an event object's ``value`` or a wire dict's ``value`` field."""
    if isinstance(event, Mapping):
        return event.get("value")
    return getattr(event, "value", None)


class StreamCapture:
    """Assigns a monotonic ``seq`` per event and appends it to ``agui-stream.jsonl`` (§12.5).

    One capture is created per Period_Run. ``record`` is called for every event the merged stream
    yields — both the adapter's AG-UI events (as wire dicts or event objects) and the emitter's
    ``Custom`` events — so the file is the exact stream the war room saw, in a single total order.
    """

    def __init__(self, sink: TextIO, *, clock: Callable[[], datetime]) -> None:
        """Create a capture writing to an open text sink.

        Args:
            sink: An open, writable text stream (a file, or a buffer in a test).
            clock: Returns the current time; injected so an offline replay freezes ``emitted_at``.
        """
        self._sink = sink
        self._clock = clock
        self._seq = 0

    @classmethod
    def open(cls, path: Path, *, clock: Callable[[], datetime]) -> StreamCapture:
        """Open ``path`` for writing and return a capture over it (§12.5).

        The parent directory is created if needed so a fresh replay run does not fail on a missing
        artefacts directory.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(path.open("w", encoding="utf-8"), clock=clock)

    def record(self, event: object) -> int:
        """Assign the next ``seq`` to ``event`` and write its JSON line (§12.5, R22.7).

        Args:
            event: An AG-UI event object or its wire dict, or a ``Custom`` event.

        Returns:
            The ``seq`` assigned to this event.
        """
        self._seq += 1
        line: dict[str, object] = {
            "seq": self._seq,
            "emitted_at": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": _event_type(event),
            "value": _event_value(event),
        }
        name = getattr(event, "name", None)
        if name is None and isinstance(event, Mapping):
            name = event.get("name")
        if name is not None:
            line["name"] = str(name)
        self._sink.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
        return self._seq

    def record_custom(self, event: CustomEvent) -> int:
        """Record an emitter ``Custom`` event (its ``name`` and ``value`` are written, §12.5)."""
        return self.record(event)

    def close(self) -> None:
        """Flush and close the sink."""
        self._sink.flush()
        self._sink.close()

    def __enter__(self) -> StreamCapture:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["StreamCapture"]
