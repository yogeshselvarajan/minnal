"""Coverage guard: property <-> test bijection and the safety gate (task 18).

Validates R20.1, R20.5, R20.8, R20.9, R20.10.

The design (``.kiro/specs/replay-simulator/design.md``) declares Correctness Properties
``Property 1 ... Property N`` (N == 24), each with a one-to-one owning Hypothesis test
``test_property_P<n>_<slug>`` under ``tests/simulator/properties/`` (R20.1). This guard
makes that mapping a *checked contract*:

- It parses the ``**Property N: ...**`` / ``**Property N [SAFETY]: ...**`` headings in
  ``design.md`` to get the declared set of property numbers (R20.9, R20.10).
- It globs the ``test_property_P<n>_*.py`` files under this directory and extracts each
  file's property number (R20.1).
- It asserts a **bijection**: every declared property owns exactly one test file, and
  every such test file maps to a declared property. A property with no test, a test with
  no property, or two tests claiming one property, all fail — reporting exactly what is
  missing (R20.8, R20.9).
- It asserts the ``[SAFETY]`` properties (P12, P13, P14, P15, P16) are marked
  ``@pytest.mark.safety`` in their owning test files, making the safety gate a required,
  checked obligation (R20.5).

This module is deliberately **not** a property test: its filename carries no
``P<digits>`` token, so the guard's own file-matching regex never counts it as one of
the property tests it is checking.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Directory holding the ``test_property_P<n>_*.py`` files (this file's directory).
_PROPERTIES_DIR = Path(__file__).resolve().parent

#: The design document that declares the Correctness Properties.
_DESIGN_DOC = (
    _PROPERTIES_DIR.parents[2] / ".kiro" / "specs" / "replay-simulator" / "design.md"
)

#: Matches a design property heading: ``**Property 12 [SAFETY]: ...**`` or ``**Property 3: ...**``.
_PROPERTY_HEADING = re.compile(r"^\*\*Property (\d+)(?: \[SAFETY\])?:", re.MULTILINE)

#: Matches an owning test filename: ``test_property_P<digits>_<slug>.py`` -> the number.
_TEST_FILE = re.compile(r"^test_property_P(\d+)_.+\.py$")

#: The expected number of declared properties (design "Correctness Properties").
_EXPECTED_PROPERTY_COUNT = 24

#: The ``[SAFETY]`` properties that must carry ``@pytest.mark.safety`` (R20.5).
_SAFETY_PROPERTIES: frozenset[int] = frozenset({12, 13, 14, 15, 16})

#: The literal marker string a safety property test file must contain.
_SAFETY_MARKER = "pytest.mark.safety"


def _declared_properties() -> set[int]:
    """Return the set of property numbers declared by headings in ``design.md``."""
    text = _DESIGN_DOC.read_text(encoding="utf-8")
    return {int(match) for match in _PROPERTY_HEADING.findall(text)}


def _test_files_by_property() -> dict[int, list[str]]:
    """Return property number -> list of owning ``test_property_P<n>_*.py`` filenames."""
    files_by_property: dict[int, list[str]] = {}
    for path in sorted(_PROPERTIES_DIR.glob("test_property_P*.py")):
        match = _TEST_FILE.match(path.name)
        if match is None:
            continue
        number = int(match.group(1))
        files_by_property.setdefault(number, []).append(path.name)
    return files_by_property


def test_design_declares_the_expected_property_count() -> None:
    """``design.md`` declares exactly the expected number of properties, contiguous 1..N (R20.9)."""
    declared = _declared_properties()

    assert declared == set(range(1, _EXPECTED_PROPERTY_COUNT + 1)), (
        "design.md property headings are not the contiguous set "
        f"1..{_EXPECTED_PROPERTY_COUNT}; found: {sorted(declared)}"
    )


def test_property_test_bijection_holds() -> None:
    """Every declared property owns exactly one test file, and vice versa (R20.1, R20.9)."""
    declared = _declared_properties()
    files_by_property = _test_files_by_property()
    tested = set(files_by_property)

    missing_tests = sorted(declared - tested)
    orphan_tests = sorted(tested - declared)
    duplicated = {n: files for n, files in files_by_property.items() if len(files) > 1}

    problems: list[str] = []
    if missing_tests:
        problems.append(f"properties with no owning test: {missing_tests}")
    if orphan_tests:
        orphan_files = sorted(f for n in orphan_tests for f in files_by_property[n])
        problems.append(f"tests with no declared property: {orphan_files}")
    if duplicated:
        problems.append(f"properties with more than one test: {duplicated}")

    assert not problems, "property <-> test bijection broken:\n" + "\n".join(problems)


def test_safety_properties_are_marked() -> None:
    """P12, P13, P14, P15, P16 test files carry ``@pytest.mark.safety`` (R20.5)."""
    files_by_property = _test_files_by_property()

    unmarked: list[str] = []
    for number in sorted(_SAFETY_PROPERTIES):
        files = files_by_property.get(number, [])
        assert files, f"safety property P{number} has no owning test file"
        for name in files:
            source = (_PROPERTIES_DIR / name).read_text(encoding="utf-8")
            if _SAFETY_MARKER not in source:
                unmarked.append(name)

    assert not unmarked, (
        "these safety-property test files are missing the "
        f"'{_SAFETY_MARKER}' marker (R20.5): {sorted(unmarked)}"
    )


def test_coverage_guard_is_not_itself_a_property_test() -> None:
    """This module must not be counted as one of the property tests it guards (R20.10)."""
    assert _TEST_FILE.match(Path(__file__).name) is None
