"""The ``scribe`` slot Code_Node: a typed stub with no Memory write access (§5.5, R21.6).

The Documentation-unit slot exists in the Graph, fully typed via
:class:`~roles._common.contracts.ScribeIn`, so ``public-information`` can fill it later without
reshaping the Graph. In this spec it makes no model call and no tool call, holds no Gateway client
and has **no Memory write access** (R21.6), and returns a
:class:`~roles._common.contracts.SlotResult` while recording a typed ``not_implemented`` failure
(R21.5). Its input carries the objectives, items, vetoes, audit trail and citations a later Scribe
needs to write the SITREP.
"""

from __future__ import annotations

from graph.nodes._slot import SlotNode

#: The invocation-state key the graph adapter assembles the :class:`ScribeIn` under.
SCRIBE_INPUT_KEY = "scribe_in"


class ScribeSlotNode(SlotNode):
    """The ``scribe`` slot Code_Node (§5.5)."""

    def __init__(self) -> None:
        super().__init__("scribe", SCRIBE_INPUT_KEY)


__all__ = ["SCRIBE_INPUT_KEY", "ScribeSlotNode"]
