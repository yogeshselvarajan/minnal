"""The ``pio`` slot Code_Node: a typed ``not_implemented`` stub (§5.5, R21.6).

The Public Information Officer slot exists in the Graph, fully typed via
:class:`~roles._common.contracts.PioIn`, so ``public-information`` can fill it later without
reshaping the Graph. In this spec it makes no model call and no tool call, holds no Gateway
client, and returns a :class:`~roles._common.contracts.SlotResult` while recording a typed
``not_implemented`` failure (R21.5). Its input carries the situation picture, the committed
proposals, the blocked items and the preventive ``de_energise`` shutdowns a later PIO needs.
"""

from __future__ import annotations

from graph.nodes._slot import SlotNode

#: The invocation-state key the graph adapter assembles the :class:`PioIn` under.
PIO_INPUT_KEY = "pio_in"


class PioSlotNode(SlotNode):
    """The ``pio`` slot Code_Node (§5.5)."""

    def __init__(self) -> None:
        super().__init__("pio", PIO_INPUT_KEY)


__all__ = ["PIO_INPUT_KEY", "PioSlotNode"]
