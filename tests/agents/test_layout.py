"""Pattern layout, injectable factories, the single env reader, and the size limits (§3, §7.1).

Verifies the structural rules a reviewer would otherwise have to eyeball (R1.1-R1.7):

* R1.1 — each role package has ``agent.py``, ``prompt.md``, ``schemas.py`` and ``tools.py``.
  **Phase scope:** the ``safety`` role's ``agent.py`` and ``tools.py`` are built in Wave-5
  (task 50); at this phase ``safety`` ships only ``prompt.md`` and ``schemas.py``, so the layout
  assertion checks the five thinking roles for the files that exist NOW and, for ``safety``,
  asserts ``prompt.md`` and ``schemas.py`` exist while its ``agent.py``/``tools.py`` are expected
  to complete in task 50 (see ``_SAFETY_PENDING`` and its ``# TODO(wave5-safety)``). The check
  does not fail on this deliberate phase boundary.
* R1.3 — every ``build_<role>_agent`` takes all dependencies as a single ``deps`` argument and
  reads no environment variable itself.
* R1.4 — no module in this spec's runtime trees reads ``os.environ`` except ``config/settings.py``.
* R1.7 — every module is at most 400 lines and every function body at most 40 lines.

All assertions are static (source text and the AST); no model and no network.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PATTERN_ROOT = Path(__file__).resolve().parents[2] / "patterns" / "agui-minnal"
_ROLES_DIR = _PATTERN_ROOT / "roles"

# The five thinking roles (R1.1). All own a prompt.md and schemas.py at this phase.
_ROLES = ("commander", "hazard", "diagnostics", "dispatch", "safety")

# Files every fully-built role package contains (R1.1).
_ROLE_FILES = ("agent.py", "prompt.md", "schemas.py", "tools.py")

# Phase boundary: the safety role's agent.py and tools.py are written in Wave-5 task 50; only
# these two files are expected to be absent at THIS phase. Its prompt.md and schemas.py exist now.
# TODO(wave5-safety): task 50 adds roles/safety/agent.py and roles/safety/tools.py; then remove
# this exception so the safety role is checked identically to the other four.
_SAFETY_PENDING = frozenset({"agent.py", "tools.py"})

# This spec's runtime module trees (design §3). ``memory/`` and ``offline/`` are later waves;
# only trees that exist are walked. FAST-template files at the pattern root (``agent.py``,
# ``tools/``, ``utils/``) and the OQ spike scripts are NOT part of this spec's runtime and are
# excluded from the single-env-reader rule, which R1.4 scopes to the pattern this spec builds.
_RUNTIME_TREES = ("roles", "graph", "domain", "config", "gateway_clients", "agui", "memory")

# The one module allowed to read os.environ (R1.4).
_ENV_READER = _PATTERN_ROOT / "config" / "settings.py"

_MAX_MODULE_LINES = 400  # R1.7
_MAX_FUNCTION_LINES = 40  # R1.7


def _runtime_modules() -> list[Path]:
    """Every ``.py`` file in this spec's runtime trees, excluding spikes and pycache (§3)."""
    modules: list[Path] = []
    for tree in _RUNTIME_TREES:
        root = _PATTERN_ROOT / tree
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            parts = set(path.parts)
            if "__pycache__" in parts or "spikes" in parts:
                continue
            modules.append(path)
    return modules


# --- R1.1: role package layout, scoped for the safety phase boundary ------------------------


@pytest.mark.parametrize("role", _ROLES)
def test_role_packages_and_factories(role: str) -> None:
    """Each role package has its expected files; safety's pending files are the phase boundary."""
    # Arrange.
    package = _ROLES_DIR / role

    # Act + Assert: expected files exist, except the two safety files task 50 will add.
    for filename in _ROLE_FILES:
        path = package / filename
        if role == "safety" and filename in _SAFETY_PENDING:
            # TODO(wave5-safety): task 50 will create this; do not fail on the phase boundary.
            assert not path.exists(), (
                f"safety/{filename} appeared before task 50 — update _SAFETY_PENDING and this test"
            )
            continue
        assert path.is_file(), f"{role}/{filename} is missing (R1.1)"

    # The factory function exists for every role whose agent.py exists at this phase.
    if not (role == "safety" and "agent.py" in _SAFETY_PENDING):
        source = (package / "agent.py").read_text(encoding="utf-8")
        assert f"def build_{role}_agent(deps" in source, (
            f"{role}/agent.py must expose build_{role}_agent(deps) (R1.3)"
        )


def test_safety_role_has_prompt_and_schemas_now() -> None:
    """At this phase the safety role ships prompt.md and schemas.py (R1.1, phase-scoped)."""
    package = _ROLES_DIR / "safety"
    assert (package / "prompt.md").is_file()
    assert (package / "schemas.py").is_file()


# --- R1.3: factories take all deps as arguments and read no environment variable ------------


@pytest.mark.parametrize("role", ("commander", "hazard", "diagnostics", "dispatch"))
def test_factory_takes_deps_argument_only(role: str) -> None:
    """``build_<role>_agent`` has a single ``deps`` parameter and no ``os.environ`` read (R1.3)."""
    # Arrange.
    tree = ast.parse((_ROLES_DIR / role / "agent.py").read_text(encoding="utf-8"))
    factory = _find_function(tree, f"build_{role}_agent")
    assert factory is not None, f"build_{role}_agent not found in {role}/agent.py"

    # Act + Assert: exactly the ``deps`` parameter (dependency injection, no globals).
    params = [a.arg for a in factory.args.args]
    assert params == ["deps"], f"build_{role}_agent must take only deps, got {params}"
    assert not _reads_environ(factory), f"build_{role}_agent must not read os.environ (R1.3)"


# --- R1.4: settings.py is the only environment reader in the runtime trees ------------------


def test_only_settings_reads_environ() -> None:
    """No runtime module outside ``config/settings.py`` reads ``os.environ`` or ``os.getenv``."""
    # Arrange + Act: find every module whose AST actually reads the environment.
    offenders = [
        path.relative_to(_PATTERN_ROOT).as_posix()
        for path in _runtime_modules()
        if path != _ENV_READER and _reads_environ(ast.parse(path.read_text(encoding="utf-8")))
    ]

    # Assert: only settings.py may read the environment (R1.4).
    assert offenders == [], f"modules other than settings.py read the environment: {offenders}"


def test_env_reader_detector_is_sound() -> None:
    """Sanity-check the detector: it flags direct env reads and ignores plain code (R1.4).

    ``config/settings.py`` reads the environment through ``pydantic_settings.BaseSettings`` rather
    than a literal ``os.environ`` access, so the detector is validated here against synthetic
    snippets: it must catch ``os.environ``, ``os.getenv`` and a bare ``getenv`` while leaving a
    module that merely imports ``os`` untouched.
    """
    assert _reads_environ(ast.parse("import os\nx = os.environ.get('A')\n"))
    assert _reads_environ(ast.parse("import os\nx = os.getenv('A')\n"))
    assert _reads_environ(ast.parse("from os import getenv\nx = getenv('A')\n"))
    assert not _reads_environ(ast.parse("import os\nx = os.path.join('a', 'b')\n"))


# --- R1.7: module and function size limits --------------------------------------------------


def test_modules_within_line_limit() -> None:
    """Every runtime module is at most 400 lines (R1.7)."""
    oversized = {
        path.relative_to(_PATTERN_ROOT).as_posix(): _line_count(path)
        for path in _runtime_modules()
        if _line_count(path) > _MAX_MODULE_LINES
    }
    assert oversized == {}, f"modules over {_MAX_MODULE_LINES} lines: {oversized}"


def test_functions_within_line_limit() -> None:
    """Every function body is at most 40 statements (R1.7, measured as ruff PLR0915 does)."""
    offenders: dict[str, int] = {}
    for path in _runtime_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in _iter_functions(tree):
            span = _body_span(func)
            if span > _MAX_FUNCTION_LINES:
                key = f"{path.relative_to(_PATTERN_ROOT).as_posix()}::{func.name}"
                offenders[key] = span
    assert offenders == {}, f"functions over {_MAX_FUNCTION_LINES} statements: {offenders}"


# --- helpers --------------------------------------------------------------------------------


def _line_count(path: Path) -> int:
    """The number of physical lines in a file."""
    return len(path.read_text(encoding="utf-8").splitlines())


def _iter_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function and coroutine definition anywhere in the module."""
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The first function definition with ``name``, or ``None``."""
    for func in _iter_functions(tree):
        if func.name == name:
            return func
    return None


def _body_span(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count a function's logical statements (§7.1), the ruff/pylint measure of function length.

    "At most 40 lines" is a function-size limit, not a formatter measure: a single call whose
    arguments ruff wraps across several physical lines is one statement, not several. Counting
    statements (as ``PLR0915`` does) catches a genuinely long function while ignoring the
    argument-per-line wrapping the formatter produces, and excludes the leading docstring.
    """
    body = list(func.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # drop the docstring
    return sum(_count_statements(node) for node in body)


def _count_statements(node: ast.stmt) -> int:
    """The number of statements in a statement node, recursing into compound statements."""
    total = 1
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            total += _count_statements(child)
    return total


def _reads_environ(tree: ast.AST) -> bool:
    """``True`` if the AST reads ``os.environ`` or calls ``os.getenv`` (ignores docstrings)."""
    for node in ast.walk(tree):
        # os.environ  (Attribute access), including os.environ.get(...)
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            return True
        # os.getenv(...) or a bare getenv(...) call
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "getenv":
                return True
            if isinstance(func, ast.Name) and func.id == "getenv":
                return True
    return False
