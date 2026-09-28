"""Assert the simulator pure core imports neither ``boto3`` nor ``botocore`` (R7.4).

Per ``design.md`` (Layering — pure core, thin edges), only the edge modules touch I/O and may
import boto3/botocore. Every other ``*.py`` under ``simulator/`` is pure decision logic and must
import neither package (org security rule, R7.4). This is an AST scan so that a shadowed name or a
string mentioning "boto3" does not trip it, and only real ``import``/``from`` statements do.

The scan covers whatever pure-core modules exist today and keeps passing as more are added; edge
modules are excluded by their known relative paths.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SIMULATOR_DIR = REPO_ROOT / "simulator"

FORBIDDEN_ROOTS: frozenset[str] = frozenset({"boto3", "botocore"})

#: Edge modules that ARE allowed to touch boto3/botocore (design.md "Edges"), as POSIX paths
#: relative to ``simulator/``. Everything else under ``simulator/`` is pure core.
EDGE_MODULES: frozenset[str] = frozenset(
    {
        "cli.py",
        "engine.py",
        "clock.py",
        "run_store.py",
        "sinks/eventbridge_sink.py",
    }
)


def _pure_core_modules() -> list[Path]:
    """Return every existing pure-core ``*.py`` under ``simulator/`` (edges excluded)."""
    if not SIMULATOR_DIR.is_dir():
        return []
    modules: list[Path] = []
    for path in sorted(SIMULATOR_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(SIMULATOR_DIR).as_posix()
        if relative in EDGE_MODULES:
            continue
        modules.append(path)
    return modules


def _forbidden_import_lines(source: str) -> list[tuple[int, str]]:
    """Return ``(line, module)`` for every import of a forbidden root in ``source``."""
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if _root(alias.name) in FORBIDDEN_ROOTS
            )
        # ``node.module`` is None for a bare relative import (``from . import x``); those
        # never name boto3/botocore, so skip them safely.
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and _root(node.module) in FORBIDDEN_ROOTS
        ):
            hits.append((node.lineno, node.module))
    return hits


def _root(dotted_name: str) -> str:
    """Return the top-level package of a dotted module name (``botocore.client`` → ``botocore``)."""
    return dotted_name.split(".", 1)[0]


def test_pure_core_modules_exist_to_scan() -> None:
    # The simulator package is scaffolded (task 1b), so at least the __init__ modules are present.
    assert _pure_core_modules(), "no pure-core simulator modules found to scan"


def test_pure_core_imports_no_boto3_or_botocore() -> None:
    offenders: list[str] = []
    for module in _pure_core_modules():
        source = module.read_text(encoding="utf-8")
        for line_number, imported in _forbidden_import_lines(source):
            shown = module.relative_to(REPO_ROOT).as_posix()
            offenders.append(f"{shown}:{line_number}: imports {imported}")

    assert not offenders, (
        "simulator pure-core modules must not import boto3/botocore (R7.4; see design.md edges):\n"
        + "\n".join(offenders)
    )


def test_scanner_flags_a_known_bad_import(tmp_path: Path) -> None:
    bad = tmp_path / "leaky.py"
    bad.write_text("from __future__ import annotations\nimport botocore.client\n", encoding="utf-8")

    hits = _forbidden_import_lines(bad.read_text(encoding="utf-8"))

    assert hits == [(2, "botocore.client")]


def test_scanner_allows_a_clean_module(tmp_path: Path) -> None:
    clean = tmp_path / "pure.py"
    clean.write_text(
        "from __future__ import annotations\nimport json\nfrom shapely import Polygon\n",
        encoding="utf-8",
    )

    assert _forbidden_import_lines(clean.read_text(encoding="utf-8")) == []
