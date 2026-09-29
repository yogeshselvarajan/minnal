"""AST scans that keep the pure core pure (design §2.4, §15.5; R14.4, R17.2).

Two structural guarantees, enforced by walking the AST rather than grepping, so
a shadowed name or a string literal never trips them and only real statements
do:

- ``test_pure_modules_import_no_boto3``: every ``_shared/*`` module (except the
  Powertools idempotency wrapper) and every ``*/logic.py`` imports neither
  ``boto3`` nor ``botocore`` (R14.4). I/O lives only in the adapters.
- ``test_logic_never_reads_backend_setting``: no ``logic.py`` reads
  ``settings.backend`` (or ``MINNAL_BACKEND``), so the run mode can never
  influence a decision — only ``make_ports`` branches on it (R17.2, §15.5).

Both scans pass today (no ``logic.py`` exists yet) and stay honest as Wave 2
adds them, because they discover modules dynamically.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "gateway" / "tools"
SHARED_DIR = TOOLS_DIR / "_shared"

FORBIDDEN_ROOTS: frozenset[str] = frozenset({"boto3", "botocore"})

#: The one ``_shared`` module allowed to touch boto3, because it wraps Powertools
#: idempotency (design §2.4). It does not exist yet; listing it keeps the scan
#: correct once Wave 4 adds it.
SHARED_EDGE_MODULES: frozenset[str] = frozenset({"idempotency.py"})


def _pure_modules() -> list[Path]:
    """Return every pure module to scan: ``_shared/*`` (minus edges) and ``*/logic.py``."""
    modules: list[Path] = []
    if SHARED_DIR.is_dir():
        for path in sorted(SHARED_DIR.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if path.name in SHARED_EDGE_MODULES:
                continue
            if "adapters" in path.relative_to(SHARED_DIR).parts:
                continue  # adapters are edges, not pure core (§4.2)
            modules.append(path)
    modules.extend(_logic_modules())
    return modules


def _logic_modules() -> list[Path]:
    """Return every ``gateway/tools/<tool>/logic.py`` that exists."""
    if not TOOLS_DIR.is_dir():
        return []
    return sorted(path for path in TOOLS_DIR.glob("*/logic.py") if "__pycache__" not in path.parts)


def _root(dotted_name: str) -> str:
    """Return the top-level package of a dotted module name."""
    return dotted_name.split(".", 1)[0]


def _forbidden_import_lines(source: str) -> list[tuple[int, str]]:
    """Return ``(line, module)`` for every import of a forbidden root."""
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if _root(alias.name) in FORBIDDEN_ROOTS
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and _root(node.module) in FORBIDDEN_ROOTS
        ):
            hits.append((node.lineno, node.module))
    return hits


def _backend_reads(source: str) -> list[tuple[int, str]]:
    """Return ``(line, expr)`` for every read of the backend setting or boto3.

    Flags an attribute access ending in ``.backend`` (e.g. ``settings.backend``),
    any reference to ``MINNAL_BACKEND`` as a name or string, and any ``boto3`` or
    ``botocore`` name reference, so a decision can never branch on the run mode.
    """
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "backend":
            hits.append((node.lineno, "attribute .backend"))
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_ROOTS:
            hits.append((node.lineno, node.id))
        elif isinstance(node, ast.Constant) and node.value == "MINNAL_BACKEND":
            hits.append((node.lineno, "MINNAL_BACKEND"))
    return hits


def test_pure_modules_import_no_boto3() -> None:
    """No pure ``_shared`` module or ``logic.py`` imports boto3/botocore (R14.4)."""
    offenders: list[str] = []
    for module in _pure_modules():
        for line_number, imported in _forbidden_import_lines(module.read_text(encoding="utf-8")):
            shown = module.relative_to(REPO_ROOT).as_posix()
            offenders.append(f"{shown}:{line_number}: imports {imported}")
    assert not offenders, "pure core must not import boto3/botocore (R14.4):\n" + "\n".join(
        offenders
    )


def test_shared_modules_exist_to_scan() -> None:
    """At least the Wave 1 ``_shared`` modules are present to scan."""
    names = {m.name for m in _pure_modules()}
    assert {"geometry.py", "flood.py", "grid.py"} <= names


def test_logic_never_reads_backend_setting() -> None:
    """No ``logic.py`` reads ``settings.backend``, ``MINNAL_BACKEND`` or boto3 (R17.2)."""
    offenders: list[str] = []
    for module in _logic_modules():
        for line_number, expr in _backend_reads(module.read_text(encoding="utf-8")):
            shown = module.relative_to(REPO_ROOT).as_posix()
            offenders.append(f"{shown}:{line_number}: reads {expr}")
    assert not offenders, (
        "logic.py must never branch on the backend setting (R17.2, §15.5):\n" + "\n".join(offenders)
    )


def test_boto3_scanner_flags_a_known_bad_import(tmp_path: Path) -> None:
    """The import scanner catches a real boto3 import."""
    bad = tmp_path / "leaky.py"
    bad.write_text("import boto3\nfrom botocore.client import BaseClient\n", encoding="utf-8")
    hits = _forbidden_import_lines(bad.read_text(encoding="utf-8"))
    assert hits == [(1, "boto3"), (2, "botocore.client")]


def test_backend_scanner_flags_a_known_bad_read(tmp_path: Path) -> None:
    """The backend scanner catches ``settings.backend`` and a ``MINNAL_BACKEND`` literal."""
    bad = tmp_path / "leaky_logic.py"
    bad.write_text(
        "def decide(settings):\n    mode = settings.backend\n    key = 'MINNAL_BACKEND'\n"
        "    return mode, key\n",
        encoding="utf-8",
    )
    hits = _backend_reads(bad.read_text(encoding="utf-8"))
    assert (2, "attribute .backend") in hits
    assert (3, "MINNAL_BACKEND") in hits


def test_scanners_pass_a_clean_module(tmp_path: Path) -> None:
    """A clean pure module trips neither scanner."""
    clean = tmp_path / "pure.py"
    clean.write_text(
        "from __future__ import annotations\nimport json\nfrom shapely import Polygon\n"
        "def score(x: int) -> int:\n    return x + 1\n",
        encoding="utf-8",
    )
    source = clean.read_text(encoding="utf-8")
    assert _forbidden_import_lines(source) == []
    assert _backend_reads(source) == []
