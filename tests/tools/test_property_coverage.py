"""The property-coverage guard: design §18 and the tests must correspond exactly.

Validates R16.1, R16.2, R16.5, R16.8, R16.9 (design §19.3).

Parses the ``### Property N: <title> [SAFETY]?`` headings and the
``**Validates: Requirements ...**`` lines from ``design.md`` §18, collects the
``test_property_P*`` tests under ``tests/tools/properties/``, and asserts:

- **Bijection (R16.1, R16.8).** The set of property numbers in the design equals
  the set of property numbers with an owning test — no property is undesigned and
  none is untested.
- **Naming rule (R16.2).** Every property test is named
  ``test_property_P<N>_<slug>`` where ``<N>`` is a designed property number and
  ``<slug>`` is a non-empty snake-case slug; and every ``P<N>`` seen in a test name
  is a real designed property.
- **Resolvable criteria (R16.9).** Every ``Requirements X.Y`` a property claims to
  validate exists as an acceptance criterion in ``requirements.md``.
- **Safety marker (R16.5).** Every ``[SAFETY]``-tagged property's owning tests all
  carry ``@pytest.mark.safety``; no non-safety property's tests carry it — with the
  single documented exception of P22, whose validation-redaction clause is marked
  on purpose (design §19.3).

The scan is AST/regex over the source, never an import, so a shadowed name, a
docstring mention of ``@pytest.mark.safety`` or a string literal never trips it,
and the check runs offline and deterministically (R16.4).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
DESIGN = REPO_ROOT / ".kiro" / "specs" / "grid-tools" / "design.md"
REQUIREMENTS = REPO_ROOT / ".kiro" / "specs" / "grid-tools" / "requirements.md"

#: The grid-tools property tests live in two places: the pure-logic properties under
#: ``tests/tools/properties/`` and the two Cedar-policy properties (P25, P26) under
#: ``tests/policy/``. The ``tests/simulator/properties/`` tree belongs to a *different*
#: spec (replay-simulator) with its own P1..P24 numbering and is deliberately excluded.
PROPERTIES_DIRS = (
    Path(__file__).resolve().parent / "properties",
    TESTS_ROOT / "policy",
)

#: ``### Property 13: The flood check ... [SAFETY]`` — number, title, optional tag.
_PROPERTY_HEADING = re.compile(r"^### Property (\d+):\s*(.+?)\s*$")
#: ``**Validates: Requirements 7.3, 7.4, 9.3**`` under a property.
_VALIDATES = re.compile(r"^\*\*Validates: Requirements ([0-9., ]+)\*\*\s*$")
#: ``test_property_P13_geometry_matches_oracle`` — property number then a slug.
_TEST_NAME = re.compile(r"^test_property_P(\d+)_(.+)$")
#: ``### Requirement 6: ...`` starts an acceptance-criteria block.
_REQUIREMENT_HEADING = re.compile(r"^### Requirement (\d+):")
#: ``4. WHEN ...`` — a numbered acceptance criterion within a requirement block.
_CRITERION = re.compile(r"^(\d+)\.\s")

#: P22's heading is not ``[SAFETY]`` but its validation-redaction clause is marked
#: on purpose (design §19.3); the guard allows marks on P22 without requiring them.
_P22 = 22
_MARK_EXCEPTION_PROPERTIES = frozenset({_P22})


class DesignedProperty:
    """One ``### Property N`` from design §18: its number, safety tag and criteria."""

    def __init__(self, number: int, is_safety: bool) -> None:
        self.number = number
        self.is_safety = is_safety
        self.validates: list[str] = []


class PropertyTest:
    """One collected ``test_property_P<N>_*`` function: its number and safety mark."""

    def __init__(self, number: int, name: str, file: Path, has_safety_mark: bool) -> None:
        self.number = number
        self.name = name
        self.file = file
        self.has_safety_mark = has_safety_mark


def _parse_designed_properties() -> dict[int, DesignedProperty]:
    """Parse §18 into ``{number: DesignedProperty}`` with safety tag and criteria."""
    properties: dict[int, DesignedProperty] = {}
    current: DesignedProperty | None = None
    for line in DESIGN.read_text(encoding="utf-8").splitlines():
        heading = _PROPERTY_HEADING.match(line)
        if heading is not None:
            number = int(heading.group(1))
            current = DesignedProperty(number, "[SAFETY]" in heading.group(2))
            properties[number] = current
            continue
        validates = _VALIDATES.match(line)
        if validates is not None and current is not None:
            current.validates.extend(_split_criteria(validates.group(1)))
            current = None  # Validates closes a property block.
    return properties


def _split_criteria(raw: str) -> list[str]:
    """Split ``7.3, 7.4, 9.3`` into ``["7.3", "7.4", "9.3"]``."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def _existing_criteria() -> set[str]:
    """Return every ``<req>.<n>`` acceptance criterion present in requirements.md."""
    criteria: set[str] = set()
    current_requirement: int | None = None
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        heading = _REQUIREMENT_HEADING.match(line)
        if heading is not None:
            current_requirement = int(heading.group(1))
            continue
        criterion = _CRITERION.match(line)
        if criterion is not None and current_requirement is not None:
            criteria.add(f"{current_requirement}.{criterion.group(1)}")
    return criteria


def _decorator_is_safety_mark(decorator: ast.expr) -> bool:
    """Whether an AST decorator node is ``@pytest.mark.safety`` (with or without call)."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "safety"
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "mark"
    )


def _module_has_safety_pytestmark(tree: ast.Module) -> bool:
    """Whether a module-level ``pytestmark`` assignment carries the safety mark."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            continue
        candidates = node.value.elts if isinstance(node.value, ast.List) else [node.value]
        if any(_decorator_is_safety_mark(value) for value in candidates):
            return True
    return False


def _collect_property_tests() -> list[PropertyTest]:
    """Discover every ``test_property_P<N>_*`` function under the properties dir."""
    tests: list[PropertyTest] = []
    paths = sorted(
        path for directory in PROPERTIES_DIRS for path in directory.glob("test_property_P*.py")
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        module_safety = _module_has_safety_pytestmark(tree)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            match = _TEST_NAME.match(node.name)
            if match is None:
                continue
            has_mark = module_safety or any(
                _decorator_is_safety_mark(dec) for dec in node.decorator_list
            )
            tests.append(PropertyTest(int(match.group(1)), node.name, path, has_mark))
    return tests


def test_designed_and_tested_property_numbers_are_a_bijection() -> None:
    """Every §18 property has ≥1 owning test and every test maps to a §18 property (R16.1)."""
    designed = set(_parse_designed_properties())
    tested = {test.number for test in _collect_property_tests()}
    assert designed, "design §18 must declare at least one property"
    assert designed == tested, (
        "property numbers in design §18 and the collected tests must correspond one to one\n"
        f"designed only: {sorted(designed - tested)}\n"
        f"tested only:   {sorted(tested - designed)}"
    )


def test_property_test_names_follow_the_naming_rule() -> None:
    """Every property test is named test_property_P<N>_<slug> for a designed N (R16.2)."""
    designed = set(_parse_designed_properties())
    offenders: list[str] = []
    for test in _collect_property_tests():
        match = _TEST_NAME.match(test.name)
        assert match is not None  # collection guaranteed the prefix
        slug = match.group(2)
        if not slug or not re.fullmatch(r"[a-z0-9_]+", slug):
            offenders.append(f"{test.file.name}::{test.name}: slug '{slug}' is not snake_case")
        if test.number not in designed:
            offenders.append(f"{test.file.name}::{test.name}: P{test.number} is not designed")
    assert not offenders, "property test names must follow the rule (R16.2):\n" + "\n".join(
        offenders
    )


def test_every_validated_criterion_resolves_to_a_requirement() -> None:
    """Every 'Validates: Requirements X.Y' criterion exists in requirements.md (R16.9)."""
    criteria = _existing_criteria()
    assert criteria, "requirements.md must contain acceptance criteria"
    offenders: list[str] = []
    for prop in _parse_designed_properties().values():
        offenders.extend(
            f"P{prop.number} validates {ref}, which is not a criterion in requirements.md"
            for ref in prop.validates
            if ref not in criteria
        )
    assert not offenders, "unresolvable Validates criteria (R16.9):\n" + "\n".join(offenders)


def test_every_property_declares_at_least_one_validated_criterion() -> None:
    """Every designed property carries a resolvable Validates line (R16.8, R16.9)."""
    offenders = [
        f"P{prop.number} has no 'Validates: Requirements' line"
        for prop in _parse_designed_properties().values()
        if not prop.validates
    ]
    assert not offenders, "\n".join(offenders)


def test_safety_marker_matches_the_design_safety_set() -> None:
    """[SAFETY] properties carry the marker on every test; non-safety ones do not (R16.5)."""
    designed = _parse_designed_properties()
    tests_by_property: dict[int, list[PropertyTest]] = {}
    for test in _collect_property_tests():
        tests_by_property.setdefault(test.number, []).append(test)

    offenders: list[str] = []
    for number, prop in designed.items():
        prop_tests = tests_by_property.get(number, [])
        if prop.is_safety:
            offenders.extend(
                f"P{number} is [SAFETY] but {t.name} lacks @pytest.mark.safety (R16.5)"
                for t in prop_tests
                if not t.has_safety_mark
            )
        elif number not in _MARK_EXCEPTION_PROPERTIES:
            offenders.extend(
                f"P{number} is not [SAFETY] but {t.name} carries @pytest.mark.safety (R16.5)"
                for t in prop_tests
                if t.has_safety_mark
            )
    assert not offenders, "safety marker mismatch (R16.5):\n" + "\n".join(offenders)


def test_p22_validation_clause_is_marked_safety() -> None:
    """P22's documented exception: its validation-redaction clause carries the marker (§19.3)."""
    p22_tests = [t for t in _collect_property_tests() if t.number == _P22]
    assert p22_tests, "P22 must have owning tests"
    assert any(t.has_safety_mark for t in p22_tests), (
        "P22's validation-redaction clause must be @pytest.mark.safety (design §19.3)"
    )


def test_heading_scanner_reads_the_safety_tag() -> None:
    """The heading scanner distinguishes a [SAFETY] property from a plain one."""
    designed = _parse_designed_properties()
    safety_example, plain_example = 1, 3
    assert designed[safety_example].is_safety is True  # "### Property 1: ... [SAFETY]"
    assert designed[plain_example].is_safety is False  # "### Property 3: ..." (no tag)
