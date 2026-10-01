"""The property coverage guard (tasks 77.1, 77.2; design 21.4, 21.6; R25.1-R25.11).

This is the meta-test that keeps the property suite honest. It does not exercise product code; it
audits the *correspondence* between the design's stated correctness properties and the tests that
own them, and the profile/marker discipline every property test must follow. It exists because a
property that is stated in ``design.md`` but never written, or a ``Validates:`` line that cites a
criterion that does not exist, or a ``[SAFETY]`` property with no ``@pytest.mark.safety`` marker,
are exactly the silent gaps a coverage matrix is supposed to make impossible (design §21.4).

Two halves, matching the two sub-tasks:

* **77.1 — heading ↔ test correspondence and citation validity.** Every ``### Property N`` heading
  in ``design.md`` has exactly one owning ``test_property_P<N>_*`` test, and vice versa (no orphan
  test, no unwritten property). Every ``**Validates: Requirements X.Y**`` line cites only criteria
  that exist in ``requirements.md``. The §21.6 traceability matrix, with its ranges expanded and
  its ``[S]``/``[DEFERRED]`` markers stripped, covers every criterion in ``requirements.md`` and
  maps every ``[SAFETY]`` criterion to a ``**P<n>**`` property or a named test.
* **77.2 — profile and marker rules.** The three Hypothesis profiles are registered as design
  §21.3 requires (``default`` and ``ci`` at ≥200 examples, ``ci`` derandomised with no database,
  ``quick`` at 50, local only); the example database is gitignored so none is committed; every
  property test carries at least one known-bad ``@example`` and, when it owns a ``[SAFETY]``
  property, an ``@pytest.mark.safety`` marker; adversarial model and tool strategies exist; and
  sockets are blocked for the whole suite.

Everything is parsed from source (``design.md``, ``requirements.md``, ``conftest.py`` and the
property-test files) so this guard has no runtime dependency on the product and runs offline.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SPEC_DIR: Final[Path] = _REPO_ROOT / ".kiro" / "specs" / "agent-team-runtime"
_DESIGN: Final[Path] = _SPEC_DIR / "design.md"
_REQUIREMENTS: Final[Path] = _SPEC_DIR / "requirements.md"
_CONFTEST: Final[Path] = _REPO_ROOT / "tests" / "agents" / "conftest.py"

#: Where this spec's property tests live. P40-P59, P61 sit under ``tests/agents/properties`` and
#: P60 with them; P57 lives under ``tests/tools/properties`` beside the read-tool tests it drives.
_PROPERTY_DIRS: Final[tuple[Path, ...]] = (
    _REPO_ROOT / "tests" / "agents" / "properties",
    _REPO_ROOT / "tests" / "tools" / "properties",
)

_MIN_EXAMPLES: Final[int] = 200
_QUICK_EXAMPLES: Final[int] = 50
_STRIDE_VECTORS: Final[int] = 6

#: The en-dash (U+2013) the §21.6 matrix writes its inclusive ranges with (e.g. ``24.1-24.11``).
#: Defined as an escape so the source carries no ambiguous literal dash (ruff RUF001).
_EN_DASH: Final[str] = "\u2013"


# --------------------------------------------------------------------------- #
# Parsers (source of truth: design.md, requirements.md)
# --------------------------------------------------------------------------- #


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_property_headings(design: str) -> dict[int, str]:
    """``{40: "no_commit_without...", ...}`` from every ``### Property N: <name>`` heading."""
    headings: dict[int, str] = {}
    for match in re.finditer(r"^### Property (\d+):\s*(.+?)\s*$", design, re.MULTILINE):
        number = int(match.group(1))
        title = match.group(2).replace("[SAFETY]", "").strip()
        headings[number] = title
    return headings


def parse_validates(design: str) -> dict[int, list[str]]:
    """``{40: ["9.1", "9.2", ...], ...}`` pairing each property with the criteria it cites.

    Each ``**Validates: Requirements ...**`` line follows its property heading, so we walk the
    file and attach each Validates line to the most recent heading.
    """
    validates: dict[int, list[str]] = {}
    current: int | None = None
    heading = re.compile(r"^### Property (\d+):")
    cites = re.compile(r"^\*\*Validates: Requirements\s+(.+?)\*\*\s*$")
    for line in design.splitlines():
        if m := heading.match(line):
            current = int(m.group(1))
        elif (m := cites.match(line)) and current is not None:
            validates[current] = [c.strip() for c in m.group(1).split(",") if c.strip()]
            current = None
    return validates


def parse_criteria(requirements: str) -> set[str]:
    """Every ``N.M`` acceptance criterion in ``requirements.md``.

    A criterion is an ``M.`` numbered acceptance item under ``### Requirement N``; the id is
    ``N.M``. Numbering restarts at 1 under each requirement heading.
    """
    criteria: set[str] = set()
    current: int | None = None
    req = re.compile(r"^### Requirement (\d+)")
    item = re.compile(r"^(\d+)\.\s+\S")
    for line in requirements.splitlines():
        if m := req.match(line):
            current = int(m.group(1))
        elif (m := item.match(line)) and current is not None:
            criteria.add(f"{current}.{int(m.group(1))}")
    return criteria


def _matrix_block(design: str) -> str:
    """The §21.6 traceability-matrix table text (between its header and the closing sentence)."""
    start = design.index("### 21.6 Requirements traceability matrix")
    end = design.index("All 248 criteria appear above", start)
    return design[start:end]


def parse_matrix_criteria(design: str) -> tuple[set[str], dict[str, str]]:
    """Expand the §21.6 matrix first cells into ``(all_criteria, {criterion: verified_by_cell})``.

    Per the design's format contract: the first cell is a comma-separated list of criterion ids
    each optionally followed by `` `[S]` ``, or an inclusive range written with an en-dash and no
    marker inside the range. Ranges are expanded; `` `[S]` `` and `` `[DEFERRED]` `` are stripped.
    The second column ("Verified by") is returned per criterion so the ``[SAFETY]`` mapping check
    can see whether it names a ``**P<n>**`` property or a named test.
    """
    all_criteria: set[str] = set()
    verified_by: dict[str, str] = {}
    row = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")
    for line in _matrix_block(design).splitlines():
        m = row.match(line)
        if not m:
            continue
        first, verified = m.group(1), m.group(2)
        if first in {"Criterion", "---"} or set(first) <= {"-", " "}:
            continue
        for criterion in _expand_first_cell(first):
            all_criteria.add(criterion)
            verified_by[criterion] = verified
    return all_criteria, verified_by


def _expand_first_cell(cell: str) -> list[str]:
    """Turn one matrix first-cell into its list of criterion ids (ranges expanded, markers off)."""
    cleaned = cell.replace("`[S]`", "").replace("`[DEFERRED]`", "")
    ids: list[str] = []
    for token in (t.strip() for t in cleaned.split(",")):
        if not token or token == _EN_DASH:
            continue
        if _EN_DASH in token:  # an inclusive range N.a-N.b written with an en-dash
            lo, hi = token.split(_EN_DASH)
            major = lo.split(".")[0].strip()
            start = int(lo.split(".")[1])
            end = int(hi.split(".")[1])
            ids.extend(f"{major}.{n}" for n in range(start, end + 1))
        else:
            ids.append(token)
    return ids


# --------------------------------------------------------------------------- #
# Collectors (source of truth: the property-test files)
# --------------------------------------------------------------------------- #


def _property_test_files() -> dict[int, Path]:
    """``{40: Path(...), ...}`` for every ``test_property_P<N>_*.py`` in this spec's owned trees.

    Only property numbers this design defines (its ``### Property N`` headings) are considered, so
    the grid-tools/simulator P3-P32 files that share the ``tests/tools`` and ``tests/simulator``
    trees are ignored — each spec's design owns its own number range (design D7).
    """
    design_numbers = set(parse_property_headings(_read(_DESIGN)))
    found: dict[int, Path] = {}
    pattern = re.compile(r"^test_property_P(\d+)_.*\.py$")
    for directory in _PROPERTY_DIRS:
        for path in directory.glob("test_property_P*.py"):
            m = pattern.match(path.name)
            if not m:
                continue
            number = int(m.group(1))
            if number in design_numbers:
                assert number not in found, (
                    f"two files own Property {number}: {found.get(number)}, {path}"
                )
                found[number] = path
    return found


def _property_test_functions(path: Path) -> list[str]:
    """The ``test_property_P<N>_*`` function names defined at module level in one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_property_P")
    ]


def _has_known_bad_example(path: Path) -> bool:
    """True if any function in the file carries an ``@example`` decorator (a known-bad case)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                if isinstance(target, ast.Name) and target.id == "example":
                    return True
                if isinstance(target, ast.Attribute) and target.attr == "example":
                    return True
    return False


def _has_safety_marker(path: Path) -> bool:
    """True if the file marks the whole module (or its property function) ``pytest.mark.safety``."""
    source = path.read_text(encoding="utf-8")
    return "pytest.mark.safety" in source


# --------------------------------------------------------------------------- #
# 77.1 — heading ↔ test correspondence and citation validity
# --------------------------------------------------------------------------- #


def test_property_coverage_guard() -> None:
    """Every design property has exactly one owning test, and every citation is real (R25.10)."""
    props = set(parse_property_headings(_read(_DESIGN)))
    tests = set(_property_test_files())

    assert props, "no Property headings parsed from design.md — the guard would be vacuous"
    assert props == tests, (
        f"property/test mismatch — properties without a test: {sorted(props - tests)}; "
        f"tests without a property heading: {sorted(tests - props)}"
    )

    # Each owning file actually defines a test_property_P<N>_* function (not just a matching name).
    for number, path in _property_test_files().items():
        functions = _property_test_functions(path)
        assert any(fn.startswith(f"test_property_P{number}_") for fn in functions), (
            f"{path.name} owns Property {number} but defines no test_property_P{number}_* function"
        )


def test_every_validates_line_cites_real_criteria() -> None:
    """No ``Validates:`` line may cite a criterion absent from requirements.md (R25.11)."""
    criteria = parse_criteria(_read(_REQUIREMENTS))
    assert criteria, "no criteria parsed from requirements.md — the guard would be vacuous"
    for number, cited in parse_validates(_read(_DESIGN)).items():
        missing = [c for c in cited if c not in criteria]
        assert not missing, f"Property {number} cites missing criteria {missing}"


def test_matrix_covers_every_criterion() -> None:
    """The §21.6 matrix, ranges expanded, names every requirements.md criterion (R25.1, R25.10)."""
    criteria = parse_criteria(_read(_REQUIREMENTS))
    matrix_criteria, _ = parse_matrix_criteria(_read(_DESIGN))
    absent = sorted(criteria - matrix_criteria, key=_criterion_sort_key)
    assert not absent, f"criteria absent from the §21.6 traceability matrix: {absent}"


def test_every_safety_criterion_maps_to_a_property_or_named_test() -> None:
    """Every ``[SAFETY]`` criterion maps to a ``**P<n>**`` property or a named test (design §21.6).

    The ``[S]`` marker in the matrix first cell tags a safety criterion; its "Verified by" cell
    must name at least one ``**P<n>**`` property or a ``test_*``/``.py`` named test, never a dash.
    """
    design = _read(_DESIGN)
    safety_criteria = _safety_criteria(design)
    _, verified_by = parse_matrix_criteria(design)
    for criterion in sorted(safety_criteria, key=_criterion_sort_key):
        cell = verified_by.get(criterion, "")
        maps = bool(re.search(r"\*\*P\d+\*\*", cell)) or "test" in cell or ".py" in cell
        assert maps and cell.strip() != _EN_DASH, (
            f"[SAFETY] criterion {criterion} maps to neither a property nor a named test: {cell!r}"
        )


def _safety_criteria(design: str) -> set[str]:
    """Criteria tagged `` `[S]` `` in the §21.6 matrix first cells."""
    tagged: set[str] = set()
    row = re.compile(r"^\|\s*(.+?)\s*\|")
    for line in _matrix_block(design).splitlines():
        m = row.match(line)
        if not m:
            continue
        cell = m.group(1)
        # Split on commas but keep the `[S]` attached to the id it follows.
        for token in (t.strip() for t in cell.split(",")):
            if token.endswith("`[S]`"):
                ident = token.replace("`[S]`", "").strip()
                if re.fullmatch(r"\d+\.\d+", ident):
                    tagged.add(ident)
    return tagged


def _criterion_sort_key(criterion: str) -> tuple[int, int]:
    major, minor = criterion.split(".")
    return int(major), int(minor)


# --------------------------------------------------------------------------- #
# 77.2 — profile and marker rules (design §21.2, §21.3, §21.4)
# --------------------------------------------------------------------------- #


def test_hypothesis_profiles_meet_the_design_contract() -> None:
    """default/ci run at least 200 examples, ci is derandomised with no db, quick is 50 (R25.3)."""
    source = _read(_CONFTEST)
    tree = ast.parse(source)
    profiles = _registered_profiles(tree)

    assert profiles.keys() >= {"default", "ci", "quick"}, (
        f"missing a required profile; found {sorted(profiles)}"
    )
    for name in ("default", "ci"):
        assert profiles[name].get("max_examples", 0) >= _MIN_EXAMPLES, (
            f"profile {name!r} must run at least {_MIN_EXAMPLES} examples (R25.3)"
        )
    assert profiles["ci"].get("derandomize") is True, "the ci profile must be derandomised (R25.3)"
    assert profiles["ci"].get("database_none") is True, (
        "the ci profile must set database=None (R25.3)"
    )
    assert profiles["quick"].get("max_examples") == _QUICK_EXAMPLES, (
        f"the quick profile must run {_QUICK_EXAMPLES} examples (local only, R25.3)"
    )


def _registered_profiles(tree: ast.Module) -> dict[str, dict[str, object]]:
    """Parse ``settings.register_profile(name, settings(...))`` calls into a small fact dict."""
    profiles: dict[str, dict[str, object]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_register_profile(node.func)):
            continue
        if not node.args:
            continue
        name = _profile_name_of(node.args[0])
        if name is None:
            continue
        facts: dict[str, object] = {}
        inner = node.args[1] if len(node.args) > 1 else None
        if isinstance(inner, ast.Call):
            for kw in inner.keywords:
                if kw.arg == "max_examples":
                    facts["max_examples"] = _int_of(kw.value)
                elif kw.arg == "derandomize" and isinstance(kw.value, ast.Constant):
                    facts["derandomize"] = bool(kw.value.value)
                elif kw.arg == "database":
                    facts["database_none"] = (
                        isinstance(kw.value, ast.Constant) and kw.value.value is None
                    )
        profiles[name] = facts
    return profiles


def _is_register_profile(func: ast.expr) -> bool:
    return isinstance(func, ast.Attribute) and func.attr == "register_profile"


def _profile_name_of(node: ast.expr) -> str | None:
    """Resolve a profile-name argument: a string literal or a conftest name constant."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        constants = {
            "DEFAULT_PROFILE": "default",
            "CI_PROFILE": "ci",
            "QUICK_PROFILE": "quick",
        }
        return constants.get(node.id)
    return None


def _int_of(node: ast.expr) -> int:
    """Resolve an int literal or a module-level ``UPPER_SNAKE`` constant used in conftest."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Name):  # FULL_MAX_EXAMPLES / QUICK_MAX_EXAMPLES constants
        constants = {"FULL_MAX_EXAMPLES": _MIN_EXAMPLES, "QUICK_MAX_EXAMPLES": _QUICK_EXAMPLES}
        return constants.get(node.id, 0)
    return 0


def test_example_database_is_not_committed() -> None:
    """No Hypothesis example database is checked into version control (R25.3)."""
    hypothesis_dir = _REPO_ROOT / ".hypothesis"
    if not hypothesis_dir.exists():
        return  # nothing to leak
    gitignore = hypothesis_dir / ".gitignore"
    assert gitignore.exists(), ".hypothesis must carry a .gitignore so no database is committed"
    assert "*" in gitignore.read_text(encoding="utf-8"), ".hypothesis/.gitignore must ignore all"


def test_every_property_test_has_a_known_bad_example() -> None:
    """Every ``test_property_P<N>`` file carries at least one ``@example`` (R25.4)."""
    for number, path in sorted(_property_test_files().items()):
        assert _has_known_bad_example(path), (
            f"Property {number} test {path.name} has no known-bad @example (R25.4)"
        )


def test_every_safety_property_test_is_marked_safety() -> None:
    """Every file owning a ``[SAFETY]`` property carries ``@pytest.mark.safety`` (R25.8)."""
    safety_numbers = _safety_property_numbers(_read(_DESIGN))
    files = _property_test_files()
    for number in sorted(safety_numbers):
        path = files[number]
        assert _has_safety_marker(path), (
            f"Property {number} is [SAFETY] but {path.name} has no pytest.mark.safety (R25.8)"
        )


def _safety_property_numbers(design: str) -> set[int]:
    """Property numbers whose ``### Property N`` heading carries the ``[SAFETY]`` tag."""
    return {
        int(m.group(1))
        for m in re.finditer(r"^### Property (\d+):.*\[SAFETY\]\s*$", design, re.MULTILINE)
    }


def test_adversarial_model_and_tool_strategies_exist() -> None:
    """Adversarial model and tool strategies are present in the suite (R25.6, R25.7, design §21.2).

    The design's adversarial coverage lives in the offline STRIDE scripts (one per vector) and in
    the injection payloads the safety property tests draw from; this asserts both are real, so a
    property suite that quietly dropped its adversarial inputs is caught.
    """
    adversarial_scripts = (
        _REPO_ROOT / "patterns" / "agui-minnal" / "offline" / "_scripts_adversarial.py"
    )
    assert adversarial_scripts.exists(), "the adversarial offline scripts are missing (§18.1)"
    vectors = set(re.findall(r"adversarial_[a-z_]+", adversarial_scripts.read_text("utf-8")))
    assert len(vectors) >= _STRIDE_VECTORS, (
        f"expected the six STRIDE adversarial vectors, found {sorted(vectors)}"
    )

    injection = [
        path
        for path in (_REPO_ROOT / "tests" / "agents" / "properties").glob("test_property_P*.py")
        if "injection" in path.read_text("utf-8").lower()
        or "ignore previous instructions" in path.read_text("utf-8").lower()
    ]
    assert injection, "no property test draws from an adversarial injection strategy (§21.2)"


def test_sockets_are_blocked_for_the_whole_suite() -> None:
    """The parent conftest installs a session-wide network block (R25.5).

    Property 60's own socket-guard test proves the offline runner refuses an INET socket; this
    asserts the suite-wide guarantee that no test may open a non-loopback socket, which the root
    ``tests/conftest.py`` owns.
    """
    root_conftest = _REPO_ROOT / "tests" / "conftest.py"
    assert root_conftest.exists(), "tests/conftest.py must exist to install the network block"
    source = root_conftest.read_text(encoding="utf-8")
    assert "socket" in source and ("_block_network" in source or "disable_socket" in source), (
        "the root conftest must block sockets for the whole suite (R25.5)"
    )
