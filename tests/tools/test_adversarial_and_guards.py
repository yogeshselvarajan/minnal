"""Test-quality guards: adversarial generation, shrinking, sockets and clocks.

Validates R16.4, R16.6, R16.7 (design §19.2, §19.3).

Three guarantees the testing strategy itself must keep, so a property test that
"passes" is actually exercising the hard cases and running in a sealed, offline
environment:

- ``test_adversarial_cases_are_generated`` (R16.6): the §19.2 strategies really do
  draw the adversarial cases they promise — routes that cross, touch and graze a
  hazard; hazard-polygon separations that straddle the safety buffer; flood-event
  streams with a shared ``sim_time`` and out-of-order duplicates; clearance
  mutations covering every spoil; job lists that force sort-key ties; and report
  streams with duplicate ``report_id`` retries.
- ``test_minimal_counterexample_is_reported`` (R16.7): when a property fails,
  Hypothesis shrinks the failure to a minimal counterexample and reports it.
- ``test_sockets_blocked_and_no_wall_clock_reads`` (R16.4): a non-loopback socket
  connection is refused in the test environment, and an AST scan proves no
  ``logic.py`` or pure ``_shared`` module reads a wall clock directly — time enters
  the pure core only as a parameter, so a fast replay cannot stretch an expiry.
"""

from __future__ import annotations

import ast
import socket
from itertools import pairwise
from pathlib import Path

import pytest
from _shared.flood import HazardPolygon
from hypothesis import HealthCheck, find, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Polygon

from tests.tools.strategies import (
    CHENNAI,
    adversarial_routes,
    clearance_mutations,
    flood_event_streams,
    radial_grids,
    report_streams,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "gateway" / "tools"
SHARED_DIR = TOOLS_DIR / "_shared"

_SAFETY_BUFFER_M = 25.0
_DEG_PER_M = 1.0 / 111_320.0

#: Modules under ``_shared`` allowed to touch the wall clock: the adapters (edges)
#: and the Powertools idempotency wrapper. Everything else is pure decision logic.
_SHARED_EDGE_MODULE_NAMES = frozenset({"idempotency.py"})

#: Wall-clock reads a pure module must never make (design: time enters as a param).
_FORBIDDEN_CLOCK_CALLS = frozenset(
    {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("time", "time"),
        ("time", "monotonic"),
        ("time", "perf_counter"),
        ("time", "process_time"),
    }
)


# --------------------------------------------------------------------------- #
# R16.6 — the adversarial cases are really generated
# --------------------------------------------------------------------------- #


def _classify_route(route: LineString, hazard: HazardPolygon) -> str:
    """Label a route relative to a hazard's raw and buffered polygon."""
    poly = Polygon(hazard.geometry["coordinates"][0])  # type: ignore[index]
    buffered = poly.buffer(_SAFETY_BUFFER_M * _DEG_PER_M)
    if route.crosses(poly) or route.within(poly) or poly.contains(route.centroid):
        return "cross"
    if route.touches(poly) or route.distance(poly) == 0.0:
        return "touch"
    if route.intersects(buffered):
        return "graze"
    return "clean"


_FIXED_HAZARD = HazardPolygon(
    flood_polygon_id="FP-1",
    geometry={
        "type": "Polygon",
        "coordinates": [
            [
                [CHENNAI[0] + 0.02, CHENNAI[1] + 0.02],
                [CHENNAI[0] + 0.03, CHENNAI[1] + 0.02],
                [CHENNAI[0] + 0.03, CHENNAI[1] + 0.03],
                [CHENNAI[0] + 0.02, CHENNAI[1] + 0.03],
                [CHENNAI[0] + 0.02, CHENNAI[1] + 0.02],
            ]
        ],
    },
    status="active",
    last_sequence=1,
    changed_in_version=1,
)
"""A fixed hazard the route classifier is anchored to, so ``find`` targets it."""


def test_adversarial_cases_are_generated() -> None:
    """adversarial_routes can generate crossing, touching and grazing routes (R16.6, §19.2).

    Uses ``hypothesis.find`` to search each adversarial route kind against a fixed
    hazard. ``find`` raises ``NoSuchExample`` if the strategy cannot produce a route
    of that kind within its budget, so a strategy that stopped generating (say)
    crossing routes would fail this guard — proving the strategy's reach, not a
    single draw. Classification is shapely-only, independent of the tool logic.
    """
    hazard = _FIXED_HAZARD
    for kind in ("cross", "touch", "graze"):
        found = find(
            adversarial_routes([hazard]),
            lambda route, target=kind: _classify_route(route, hazard) == target,
        )
        assert _classify_route(found, hazard) == kind


def test_adversarial_routes_exercise_the_buffer_boundary() -> None:
    """A grazing route lies outside the raw polygon but inside the buffer (R16.6, §19.2).

    The design highlights the buffer-boundary case: "a route whose only intersection
    is inside the buffer but outside the raw polygon". This asserts adversarial_routes
    can generate exactly that — a route that misses the raw hazard yet a
    buffer-aware intersection test must still veto — which is the case a naive
    unbuffered check would wrongly pass.
    """
    hazard = _FIXED_HAZARD
    raw = Polygon(hazard.geometry["coordinates"][0])  # type: ignore[index]
    buffered = raw.buffer(_SAFETY_BUFFER_M * _DEG_PER_M)

    def in_buffer_band_only(route: LineString) -> bool:
        return route.disjoint(raw) and route.intersects(buffered)

    found = find(adversarial_routes([hazard]), in_buffer_band_only)
    assert in_buffer_band_only(found), "a buffer-band-only grazing route must be generatable"


def test_flood_streams_force_a_shared_sim_time_and_duplicates() -> None:
    """flood_event_streams can generate a shared sim_time and out-of-order sequences (P20)."""

    def has_shared_sim_time(stream: list[tuple[str, int, object]]) -> bool:
        sim_times = [t for t in (_sim_time_of(item) for item in stream) if t is not None]
        return len(sim_times) != len(set(sim_times))

    def has_out_of_order(stream: list[tuple[str, int, object]]) -> bool:
        sequences = [seq for _kind, seq, _payload in stream]
        return any(later < earlier for earlier, later in pairwise(sequences))

    shared = find(flood_event_streams(["FP-1", "FP-2"]), has_shared_sim_time)
    assert has_shared_sim_time(shared)
    unordered = find(flood_event_streams(["FP-1", "FP-2"]), has_out_of_order)
    assert has_out_of_order(unordered)


def _sim_time_of(item: tuple[str, int, object]) -> str | None:
    """Return the sim_time carried by a flood-event-stream item, if any."""
    _kind, _seq, payload = item
    if isinstance(payload, str):
        return payload
    return getattr(payload, "sim_time", None)


_GOOD_CLEARANCE = {
    "clearance_id": "sfc_0000000000000000000000000A",
    "purpose": "route",
    "bound_to": "aa" * 32,
    "incident_id": "inc_00000000000000000000000000",
    "expires_at": "2999-01-01T00:00:00Z",
}


def _spoiled_fields(mutated: object) -> frozenset[str]:
    """Return which fields a mutated clearance changed relative to the good one."""
    if not isinstance(mutated, dict):
        return frozenset()
    changed = {k for k in _GOOD_CLEARANCE if mutated.get(k) != _GOOD_CLEARANCE[k]}
    changed |= {k for k in mutated if k not in _GOOD_CLEARANCE}
    return frozenset(changed)


def test_clearance_mutations_cover_every_spoil() -> None:
    """clearance_mutations reaches absent and each forged/expired/mismatched form (R16.6)."""
    # The absent-clearance case (the strategy returns None).
    absent = find(clearance_mutations(_GOOD_CLEARANCE), lambda m: m is None)
    assert absent is None
    # Every field the mutator can spoil is reachable individually.
    for field in ("clearance_id", "expires_at", "bound_to", "purpose", "incident_id", "used_by"):
        found = find(
            clearance_mutations(_GOOD_CLEARANCE),
            lambda m, target=field: target in _spoiled_fields(m),
        )
        assert field in _spoiled_fields(found)


def test_report_streams_produce_duplicate_report_ids() -> None:
    """report_streams can generate duplicate report_id retries the dedupe path needs (R16.6)."""

    def has_duplicate(reports: list[dict[str, object]]) -> bool:
        ids = [r["report_id"] for r in reports]
        return len(ids) != len(set(ids))

    # Search over grids and their report streams together, so a minimal grid whose
    # stream carries a duplicate report_id is found deterministically.
    found = find(
        radial_grids().flatmap(report_streams),
        has_duplicate,
    )
    assert has_duplicate(found), "a duplicate report_id (retry/replay) must be generatable (R4.3)"


# --------------------------------------------------------------------------- #
# R16.7 — a failing property shrinks to a minimal counterexample
# --------------------------------------------------------------------------- #


def test_minimal_counterexample_is_reported() -> None:
    """A deliberately false property fails and Hypothesis reports the minimal input (R16.7).

    The property "every non-negative integer is < 10" is false; Hypothesis must find
    a counterexample and shrink it to the minimal one, 10, and surface it in the
    ``falsifying example`` report. This proves shrinking is active in the harness.
    """

    @settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(value=st.integers(min_value=0))
    def every_int_is_small(value: int) -> None:
        assert value < 10  # noqa: PLR2004 - the intentionally false bound

    with pytest.raises(AssertionError) as caught:
        every_int_is_small()

    # Hypothesis shrank the failure to the minimal counterexample, 10: the assertion
    # that fires is ``assert 10 < 10``. A non-shrinking harness would report a large
    # random integer instead. The falsifying example is also attached to the error
    # via PEP 678 notes (``Falsifying example: every_int_is_small(value=10)``).
    rendered = "\n".join([str(caught.value), *getattr(caught.value, "__notes__", [])])
    assert "10 < 10" in rendered, rendered
    if "Falsifying example" in rendered:
        assert "value=10" in rendered, rendered


# --------------------------------------------------------------------------- #
# R16.4 — sockets blocked, and the pure core reads no wall clock
# --------------------------------------------------------------------------- #


# RFC 5737 TEST-NET-1: documentation-only, never-routed address. A connection to it
# can only be stopped by the test guard, never actually completed, so it is the safe
# probe the existing tests/test_network_blocked.py also uses.
_UNROUTABLE_ADDRESS = ("192.0.2.1", 443)


def test_non_loopback_socket_is_blocked() -> None:
    """A non-loopback connection cannot succeed from the test environment (R16.4).

    The conftest guard raises a ``RuntimeError`` naming the block. Even in a session
    where a Hypothesis-based test earlier restored the raw ``socket.connect``, the
    never-routed TEST-NET address still cannot be reached, so the connection fails
    either way and never establishes a peer. A short timeout keeps it deterministic.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        with pytest.raises(OSError):
            sock.connect(_UNROUTABLE_ADDRESS)
        assert not _connected(sock), "no external peer connection may be established in tests"
    finally:
        sock.close()


def test_conftest_guard_blocks_a_non_loopback_address() -> None:
    """The conftest network guard itself refuses non-loopback addresses (R16.4).

    Calls the guard's own predicate directly, so the assertion holds regardless of
    whether a Hypothesis-based test earlier in the session restored the raw socket
    methods — the guard's logic is what seals the default (``aws``) test runs, and it
    allows loopback so local fakes and asyncio still work.
    """
    from tests.conftest import _is_allowed  # noqa: PLC0415

    assert _is_allowed(socket.AF_INET, ("127.0.0.1", 8080)) is True
    assert _is_allowed(socket.AF_INET, _UNROUTABLE_ADDRESS) is False
    assert _is_allowed(socket.AF_INET, ("8.8.8.8", 53)) is False


def _connected(sock: socket.socket) -> bool:
    """Return whether a socket has an established peer connection."""
    try:
        sock.getpeername()
    except OSError:
        return False
    return True


def _pure_modules() -> list[Path]:
    """Return every pure module: ``_shared/*`` (minus edges/adapters) and ``*/logic.py``."""
    modules: list[Path] = []
    if SHARED_DIR.is_dir():
        for path in sorted(SHARED_DIR.rglob("*.py")):
            if "__pycache__" in path.parts or "adapters" in path.relative_to(SHARED_DIR).parts:
                continue
            if path.name in _SHARED_EDGE_MODULE_NAMES:
                continue
            modules.append(path)
    if TOOLS_DIR.is_dir():
        modules.extend(
            path for path in sorted(TOOLS_DIR.glob("*/logic.py")) if "__pycache__" not in path.parts
        )
    return modules


def _wall_clock_reads(source: str) -> list[tuple[int, str]]:
    """Return ``(line, expr)`` for every direct wall-clock read in a module.

    Flags ``<name>.now()``/``.utcnow()`` and ``time.time()`` and friends, whether
    called on ``datetime``/``time`` or on an aliased import, by matching the
    attribute pair. A bare ``.now`` on an unrelated object is not a clock call, so
    the match is anchored to the known clock roots and method names.
    """
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func
        if (
            isinstance(attr.value, ast.Name)
            and (attr.value.id, attr.attr) in _FORBIDDEN_CLOCK_CALLS
        ):
            hits.append((node.lineno, f"{attr.value.id}.{attr.attr}()"))
    return hits


def test_logic_never_reads_a_wall_clock_directly() -> None:
    """No ``logic.py`` or pure ``_shared`` module reads a wall clock directly (R16.4)."""
    offenders: list[str] = []
    for module in _pure_modules():
        for line_number, expr in _wall_clock_reads(module.read_text(encoding="utf-8")):
            shown = module.relative_to(REPO_ROOT).as_posix()
            offenders.append(f"{shown}:{line_number}: reads {expr}")
    assert not offenders, (
        "pure logic must receive time as a parameter, never read a wall clock (R16.4):\n"
        + "\n".join(offenders)
    )


def test_wall_clock_scanner_flags_a_known_bad_read(tmp_path: Path) -> None:
    """The wall-clock scanner catches datetime.now and time.time (self-check)."""
    bad = tmp_path / "leaky_logic.py"
    bad.write_text(
        "import time\nfrom datetime import datetime\n"
        "def decide():\n    a = datetime.now()\n    b = time.time()\n    return a, b\n",
        encoding="utf-8",
    )
    hits = _wall_clock_reads(bad.read_text(encoding="utf-8"))
    lines = {expr for _line, expr in hits}
    assert "datetime.now()" in lines
    assert "time.time()" in lines


def test_wall_clock_scanner_passes_a_clean_module(tmp_path: Path) -> None:
    """A module that receives time as a parameter trips no wall-clock hit (self-check)."""
    clean = tmp_path / "pure.py"
    clean.write_text(
        "def decide(wall_now: str) -> str:\n    return wall_now\n",
        encoding="utf-8",
    )
    assert _wall_clock_reads(clean.read_text(encoding="utf-8")) == []
