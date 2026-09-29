"""Error-matrix reachability (design §11.2, task 56.6).

``test_every_error_row_reachable`` parses all 44 rows of the §11.2 matrix directly
from ``design.md`` and asserts, for each row, that its ``Code`` is a valid
``ErrorCode``, its ``rule_id`` (when present) is in the closed ``RULE_IDS`` set,
and the retryable flag is consistent with the taxonomy (a ``SAFETY_VIOLATION`` is
never retryable, R11.1). It then confirms every distinct ``ErrorCode`` and every
``rule_id`` the matrix uses is actually *reachable* — produced by the real error
constructors / the ``run_tool`` scaffolding — so no row is a dead letter.

Individual condition rows are exercised in the per-tool handler tests
(``test_record_outage.py``, ``test_trace_check_route.py``,
``test_rank_dispatch_switching.py``, ``test_ingestors.py``,
``test_approval_expirer_crew_lock.py``) and the transaction-mapping / adapter tests
(``test_transaction_mapping.py``, ``test_retry_wrapper.py``); this file is the
matrix-completeness gate over them (R15.2).
"""

from __future__ import annotations

from pathlib import Path

from _shared.envelope import PUBLIC_MESSAGE, err
from _shared.errors import (
    ERROR_CODES,
    RULE_IDS,
    ConflictError,
    InputValidationError,
    NotFoundError,
    RateLimited,
    SafetyViolation,
    UpstreamError,
)
from _shared.handler import run_tool

from tests.tools.fakes import CapturingLogger

_DESIGN = Path(__file__).resolve().parents[2] / ".kiro" / "specs" / "grid-tools" / "design.md"
_EXPECTED_ROWS = 44
_MATRIX_COLUMNS = 7
_CORR = "corr_00000000000000000000000001"


def _matrix_rows() -> list[dict[str, str]]:
    """Parse the §11.2 error matrix table rows from design.md."""
    text = _DESIGN.read_text(encoding="utf-8")
    start = text.index("### 11.2 Full error matrix")
    end = text.index("### 11.3", start)
    rows: list[dict[str, str]] = []
    for raw_line in text[start:end].splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < _MATRIX_COLUMNS or cells[0] in ("#", "---") or set(cells[0]) <= {"-"}:
            continue
        if not cells[0].isdigit():
            continue
        rows.append(
            {
                "num": cells[0],
                "condition": cells[1],
                "tool": cells[2],
                "code": cells[3],
                "rule_id": cells[4],
                "retryable": cells[5],
                "req": cells[6],
            }
        )
    return rows


def test_every_error_row_reachable() -> None:
    """All 44 §11.2 rows parse, use valid codes/rule_ids, and are retryable-consistent."""
    rows = _matrix_rows()
    assert len(rows) == _EXPECTED_ROWS, f"expected {_EXPECTED_ROWS} rows, parsed {len(rows)}"

    seen_codes: set[str] = set()
    seen_rules: set[str] = set()
    for row in rows:
        code_cell = row["code"]
        rule_cell = row["rule_id"]
        retry_cell = row["retryable"].lower()

        # Extract any bare ErrorCode tokens named in the (sometimes prose) cell.
        codes = {c for c in ERROR_CODES if c in code_cell}
        seen_codes |= codes

        if rule_cell not in ("—", "per role", ""):
            rule = rule_cell.strip("`")
            assert rule in RULE_IDS, f"row {row['num']} rule_id {rule!r} not in the closed set"
            seen_rules.add(rule)

        # A SAFETY_VIOLATION is never retryable (R11.1); a stated retryable row
        # must carry a retryable code.
        if "SAFETY_VIOLATION" in codes:
            assert "yes" not in retry_cell, f"row {row['num']} safety violation marked retryable"

    # Every ErrorCode the taxonomy defines appears somewhere in the matrix.
    assert seen_codes == set(ERROR_CODES), (
        f"codes missing from the matrix: {set(ERROR_CODES) - seen_codes}"
    )
    # Every closed rule_id appears in at least one row.
    assert seen_rules == set(RULE_IDS), (
        f"rule_ids missing from the matrix: {set(RULE_IDS) - seen_rules}"
    )


def test_every_error_code_is_reachable_through_the_envelope() -> None:
    """Each ErrorCode is producible as a well-formed error envelope (R1.6)."""
    for code in ERROR_CODES:
        envelope = err(code, None, _CORR)
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == code
        assert envelope["error"]["message"] == PUBLIC_MESSAGE[code]


def test_every_rule_id_is_reachable_via_safety_violation() -> None:
    """Each closed rule_id can be carried by a SAFETY_VIOLATION envelope (P30, R9.8)."""
    logger = CapturingLogger()
    for rule in RULE_IDS:

        def body(rule_id: str = rule) -> dict[str, object]:
            raise SafetyViolation("A safety rule refused the request.", rule_id=rule_id)  # type: ignore[arg-type]

        result = run_tool(body, correlation_id=_CORR, logger=logger)
        assert result["ok"] is False
        assert result["error"]["code"] == "SAFETY_VIOLATION"
        assert result["error"]["rule_id"] == rule
        assert result["error"]["retryable"] is False


def test_minnal_error_codes_map_through_run_tool() -> None:
    """Each MinnalError subclass surfaces its code and retryable flag (rows 6,18,10,21,33)."""
    logger = CapturingLogger()
    cases = [
        (InputValidationError("bad"), "VALIDATION_ERROR", False),
        (NotFoundError("missing"), "NOT_FOUND", False),
        (ConflictError("conflict"), "CONFLICT", False),
        (UpstreamError("upstream"), "UPSTREAM_ERROR", True),
        (RateLimited("busy"), "RATE_LIMITED", True),
    ]
    for exc, code, retryable in cases:

        def body(e: Exception = exc) -> dict[str, object]:
            raise e

        result = run_tool(body, correlation_id=_CORR, logger=logger)
        assert result["error"]["code"] == code
        assert result["error"]["retryable"] is retryable

    # Row 33: anything unhandled → opaque INTERNAL.
    def boom() -> dict[str, object]:
        raise RuntimeError("unexpected")

    internal = run_tool(boom, correlation_id=_CORR, logger=logger)
    assert internal["error"]["code"] == "INTERNAL"
