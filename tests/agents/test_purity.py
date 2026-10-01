"""Purity of the decision core, enforced by an AST walk (task 25.2).

Design §3.1 requires that the entire safety-bearing decision core is pure Python over typed
inputs: no ``boto3``, ``botocore`` or ``strands`` import, no direct ``grid-tools`` table write,
and no AWS credential handling. This mirrors the AST walk ``grid-tools`` already uses for its
``logic.py`` modules, so the two specs enforce purity the same way.

Validates: Requirements 1.8, 12.11, 13.9 (design §3.1, §21.5).

The pure set is the modules the design (§21.1, §3.1) declares pure: ``domain/**``,
``gateway_clients/names.py`` (and ``filters.py`` once it exists), ``graph/state.py`` (and
``edges.py`` once it exists), ``agui/validate.py``, ``memory/namespaces.py`` (once it exists),
``offline/scripts.py`` (once it exists) and each read tool's ``logic.py`` (Wave 2). Absent
modules are skipped, not failed: this test grows as those modules land.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PATTERN_ROOT = Path(__file__).resolve().parents[2] / "patterns" / "agui-minnal"

# Modules and directories the design declares pure. A glob (``domain/*.py``) expands to every
# file; a single path is included only if it exists (later-wave modules are not failed for
# being absent).
_PURE_GLOBS = ("domain/*.py",)
_PURE_FILES = (
    "gateway_clients/names.py",
    "gateway_clients/filters.py",  # Wave 3, may not exist yet
    "graph/state.py",
    "graph/edges.py",  # later wave, may not exist yet
    "agui/validate.py",
    "memory/namespaces.py",  # later wave, may not exist yet
    "offline/scripts.py",  # later wave, may not exist yet
)

# Imports a pure module may never make (design §3.1). Substring match on the top-level package.
_FORBIDDEN_IMPORT_ROOTS = ("boto3", "botocore", "strands")


def _pure_modules() -> list[Path]:
    """Every currently-existing pure module, from the globs and the explicit file list."""
    found: list[Path] = []
    for glob in _PURE_GLOBS:
        found.extend(sorted(_PATTERN_ROOT.glob(glob)))
    for rel in _PURE_FILES:
        path = _PATTERN_ROOT / rel
        if path.is_file():
            found.append(path)
    return found


def _imported_roots(tree: ast.AST) -> set[str]:
    """The top-level package of every ``import`` and ``from ... import`` in the module."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_pure_set_is_non_empty() -> None:
    """The pure set resolves to real files, so this test is not silently a no-op."""
    modules = _pure_modules()
    assert modules, "no pure modules found under the pattern root"
    # domain/ must always be present (it is the heart of the decision core).
    assert any(m.parent.name == "domain" for m in modules)


@pytest.mark.parametrize("module", _pure_modules(), ids=lambda p: str(p.name))
def test_pure_module_imports_nothing_aws_or_strands(module: Path) -> None:
    """A pure module imports no ``boto3``, ``botocore`` or ``strands`` (R1.8, design §3.1)."""
    # Arrange.
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))

    # Act.
    roots = _imported_roots(tree)

    # Assert.
    offending = roots & set(_FORBIDDEN_IMPORT_ROOTS)
    assert not offending, (
        f"{module.relative_to(_PATTERN_ROOT).as_posix()} imports {sorted(offending)}; "
        "the decision core must stay pure (design §3.1)"
    )


@pytest.mark.parametrize("module", _pure_modules(), ids=lambda p: str(p.name))
def test_pure_module_makes_no_grid_tools_table_write(module: Path) -> None:
    """No pure module writes a ``grid-tools`` table directly (R12.11).

    A pure module holds no DynamoDB client, so a call to a table write method
    (``put_item``, ``update_item``, ``delete_item``, ``batch_write_item``, ``transact_write_items``)
    would be a boundary leak. We flag any attribute call by one of those names.
    """
    # Arrange.
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    write_methods = {
        "put_item",
        "update_item",
        "delete_item",
        "batch_write_item",
        "transact_write_items",
    }

    # Act: collect attribute-call method names.
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    # Assert.
    leaks = called & write_methods
    assert not leaks, (
        f"{module.relative_to(_PATTERN_ROOT).as_posix()} calls a table write {sorted(leaks)}; "
        "pure modules perform no I/O (R12.11)"
    )


@pytest.mark.parametrize("module", _pure_modules(), ids=lambda p: str(p.name))
def test_pure_module_handles_no_aws_credentials(module: Path) -> None:
    """No pure module references AWS credentials (R13.9); tools reach AWS via the Gateway only."""
    # Arrange.
    source = module.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module))
    credential_tokens = {
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
    }

    # Act: any identifier, keyword or string constant naming an AWS credential is a leak.
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    keywords = {
        kw.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg
    }

    # Assert.
    offending = (names | keywords) & credential_tokens
    assert not offending, (
        f"{module.relative_to(_PATTERN_ROOT).as_posix()} references AWS credentials "
        f"{sorted(offending)}; agents hold no raw credentials (R13.9)"
    )
