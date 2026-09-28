---
inclusion: fileMatch
fileMatchPattern: ["**/*.py", "**/pyproject.toml", "**/requirements*.txt"]
---

# Backend standards: Python (agents, tools, simulator, voice)

## Toolchain
- Python **3.12+**, managed with **uv** (`uv sync`, `uv run`, `uv add`). One `pyproject.toml` at the root with dependency groups; the agent pattern keeps FAST's pinned `requirements.txt` for its container build.
- **Ruff** for lint + format (line length 100, rules `E,F,I,B,UP,SIM,S,ASYNC,PL,RUF`). **mypy --strict** on `patterns/agui-minnal/domain/` and `gateway/tools/*/logic.py`.
- **Pydantic v2** for every model crossing a boundary; `model_config = ConfigDict(frozen=True, extra="forbid")` for domain value objects.
- **AWS Lambda Powertools** (Logger, Tracer, Metrics, Idempotency, Parser, Event Handler) in every Lambda. **boto3** only in adapters, with clients created once at module scope.

## Layout of a Gateway tool (FAST convention + Minnal layering)
```
gateway/tools/trace_upstream_device/
  tool_spec.json                  # Gateway schema (see api-contracts.md)
  trace_upstream_device_lambda.py # handler: parse → call logic → envelope. No business rules here.
  logic.py                        # pure functions, 100% typed, no boto3, Hypothesis-tested
  adapters.py                     # DynamoDB / Location / S3 access
  models.py                       # Pydantic input/output models
```

## Handler template
```python
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.idempotency import DynamoDBPersistenceLayer, idempotent

logger, tracer, metrics = Logger(service="minnal-tools"), Tracer(), Metrics(namespace="Minnal")
persistence = DynamoDBPersistenceLayer(table_name=os.environ["IDEMPOTENCY_TABLE"])

@logger.inject_lambda_context(correlation_id_path="correlation_id")
@tracer.capture_lambda_handler
@metrics.log_metrics
@idempotent(persistence_store=persistence)          # only for write tools
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    try:
        req = RecordOutageInput.model_validate(event)
        result = logic.record_outage(req, repo=adapters.OutageRepo())
        metrics.add_metric(name="OutagesRecorded", unit=MetricUnit.Count, value=1)
        return ok(result)
    except ValidationError as e:
        return err("VALIDATION_ERROR", "Input did not match the schema", details=e.errors())
    except MinnalError as e:
        logger.warning("tool rejected request", extra={"code": e.code})
        return err(e.code, e.public_message)
```

## Strands agents
- One package per ICS role: `agent.py` (factory), `prompt.md`, `schemas.py` (structured output), `tools.py` (Gateway tool filter list).
- Agents are created by a factory taking config (model ID, Gateway URL, memory ID) so tests can inject fakes.
- Structured output via Pydantic models; validate every inter-agent message. Free text only for humans.
- Put `incident_id`, `operational_period` and `agent` into trace attributes and log context.
- Timeouts on every model and tool call; a failed sub-agent returns a typed failure, never raises through the Graph.
- Look up Strands and AgentCore APIs with the `strands` and `agentcore` MCP servers before writing code; do not guess method names.

## Style rules
- Type hints everywhere; `from __future__ import annotations`. No `Any` in domain code.
- Functions ≤ 40 lines, modules ≤ 400 lines; split when larger.
- Google-style docstrings on public functions: one line of intent, then Args/Returns/Raises only when non-obvious.
- Time: `datetime.now(UTC)`, ISO 8601 with offset on the wire. IDs: ULIDs (`python-ulid`).
- Geo: GeoJSON, **[longitude, latitude]** order, WGS84. Use `shapely` for geometry in pure logic.
- Async only where it buys concurrency (voice, streaming); do not mix sync and async in one module.
- Config from environment via a single `Settings(BaseSettings)`; no `os.environ` reads scattered around.

## Commands
`uv run ruff check --fix . && uv run ruff format . && uv run mypy patterns/agui-minnal/domain gateway/tools && uv run pytest -q`
