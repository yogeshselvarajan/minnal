"""Package entry point: ``python -m simulator`` dispatches to the CLI.

The CLI module is imported lazily inside :func:`main` so that importing this
module (and the ``simulator`` package) never requires ``cli.py`` to exist yet.
"""

from __future__ import annotations

import importlib
from typing import cast


def main() -> int:
    """Run the simulator CLI and return its process exit code.

    Raises:
        ModuleNotFoundError: If ``simulator.cli`` has not been authored yet.
    """
    # Imported lazily so that importing this module never requires ``cli.py``,
    # which a later task authors; until then ``main()`` raises on invocation.
    cli = importlib.import_module("simulator.cli")
    return cast("int", cli.main())


if __name__ == "__main__":
    raise SystemExit(main())
