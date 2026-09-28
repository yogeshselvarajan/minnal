"""Guard test for models.md rule 6: no Anthropic model IDs in Minnal runtime code or config.

The vendor and model-family tokens are assembled from parts at import time so this file never
contains a literal Anthropic model ID (and does not trip the guard hook or its own scanner).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_YAML = REPO_ROOT / "patterns" / "agui-minnal" / "config" / "models.yaml"

SCAN_TARGETS: tuple[str, ...] = (
    "patterns",
    "gateway",
    "infra-cdk/lib",
    "infra-cdk/config.yaml",
    "frontend/src",
    "voice",
    "simulator",
)
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {"node_modules", ".venv", "__pycache__", "cdk.out", "dist", "build", ".git"}
)
BINARY_SUFFIXES: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".tgz",
        ".whl",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".pyc",
        ".so",
        ".dll",
        ".exe",
        ".bin",
        ".parquet",
        ".pmtiles",
    }
)
BINARY_SNIFF_BYTES = 8192

_VENDOR = "anthrop" + "ic"
_FAMILY = "cla" + "ude"
_REGION_PREFIX = r"(?:(?:us|eu|apac|jp|global)\.)?"
_BEDROCK_ID = _REGION_PREFIX + re.escape(_VENDOR) + r"\." + _FAMILY + r"[\w.:-]*"
_BARE_NAME = _FAMILY + r"-(?:\d|v\d|instant|sonnet|opus|haiku)[\w.:-]*"
ANTHROPIC_MODEL_ID = re.compile(rf"\b(?:{_BEDROCK_ID}|{_BARE_NAME})", re.IGNORECASE)

_MODEL_ID_LINE = re.compile(r"""model_id\s*:\s*["']?([^"'\s,}]+)""")


@dataclass(frozen=True)
class Hit:
    path: Path
    line_number: int
    match: str

    def describe(self, base: Path) -> str:
        try:
            shown = self.path.relative_to(base).as_posix()
        except ValueError:
            shown = self.path.as_posix()
        return f"{shown}:{self.line_number}: {self.match}"


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return True
    with path.open("rb") as handle:
        return b"\x00" in handle.read(BINARY_SNIFF_BYTES)


def _iter_files(targets: Iterable[Path]) -> Iterator[Path]:
    for target in targets:
        if target.is_file():
            yield target
        elif target.is_dir():
            for path in sorted(target.rglob("*")):
                relative_parts = path.relative_to(target).parts
                if any(part in SKIP_DIR_NAMES for part in relative_parts):
                    continue
                if path.is_file():
                    yield path


def find_anthropic_model_ids(targets: Iterable[Path]) -> list[Hit]:
    """Return every Anthropic model-ID match in the text files under ``targets``.

    Missing targets are skipped so the scan works before the FAST template is imported.
    """
    hits: list[Hit] = []
    for path in _iter_files(targets):
        if _is_binary(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            hits.extend(
                Hit(path, line_number, match.group(0))
                for match in ANTHROPIC_MODEL_ID.finditer(line)
            )
    return hits


def read_model_ids(models_yaml: Path) -> list[str]:
    """Extract every ``model_id`` value from models.yaml without needing a YAML dependency."""
    text = models_yaml.read_text(encoding="utf-8")
    return [match.group(1) for match in _MODEL_ID_LINE.finditer(text)]


def _known_bad_ids() -> list[str]:
    return [
        f"us.{_VENDOR}.{_FAMILY}-3-5-sonnet-20240620-v1:0",
        f"global.{_VENDOR}.{_FAMILY}-sonnet-4-v1:0",
        f"{_VENDOR}.{_FAMILY}-v2",
        f"{_FAMILY.capitalize()}-3-Haiku",
        f"{_FAMILY}-sonnet-4",
    ]


def test_runtime_code_and_config_contain_no_anthropic_model_ids() -> None:
    hits = find_anthropic_model_ids(REPO_ROOT / target for target in SCAN_TARGETS)

    assert not hits, "Anthropic model IDs found (see .kiro/steering/models.md):\n" + "\n".join(
        hit.describe(REPO_ROOT) for hit in hits
    )


@pytest.mark.parametrize("bad_id", _known_bad_ids())
def test_scanner_reports_file_and_line_for_a_known_bad_model_id(
    tmp_path: Path, bad_id: str
) -> None:
    agent_file = tmp_path / "agent.py"
    agent_file.write_text(f"import os\nMODEL = '{bad_id}'\n", encoding="utf-8")

    hits = find_anthropic_model_ids([tmp_path])

    assert [(hit.path, hit.line_number) for hit in hits] == [(agent_file, 2)]
    assert "agent.py:2:" in hits[0].describe(tmp_path)


def test_scanner_ignores_approved_model_ids_from_models_yaml(tmp_path: Path) -> None:
    if not MODELS_YAML.exists():
        pytest.skip("models.yaml not created yet")
    approved_ids = read_model_ids(MODELS_YAML)
    assert approved_ids, "models.yaml has no model_id entries"
    (tmp_path / "approved.py").write_text(
        "\n".join(f"MODEL_{i} = '{model_id}'" for i, model_id in enumerate(approved_ids)),
        encoding="utf-8",
    )

    hits = find_anthropic_model_ids([tmp_path])

    assert hits == []


def test_scanner_skips_dependency_folders_and_binary_files(tmp_path: Path) -> None:
    bad_id = _known_bad_ids()[0]
    vendored = tmp_path / "node_modules" / "sdk"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text(f"const m = '{bad_id}';", encoding="utf-8")
    (tmp_path / "blob.dat").write_bytes(b"\x00\x01" + bad_id.encode())

    hits = find_anthropic_model_ids([tmp_path])

    assert hits == []


def test_models_yaml_exists_and_every_model_id_is_approved() -> None:
    assert MODELS_YAML.exists(), f"missing {MODELS_YAML.relative_to(REPO_ROOT).as_posix()}"
    model_ids = read_model_ids(MODELS_YAML)

    assert model_ids, "models.yaml has no model_id entries"
    offending = [model_id for model_id in model_ids if ANTHROPIC_MODEL_ID.search(model_id)]
    assert offending == [], f"Anthropic model IDs in models.yaml: {offending}"
