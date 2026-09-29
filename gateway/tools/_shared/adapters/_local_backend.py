"""In-memory and file-backed key-value store for the local adapters (§15.1, §15.2).

One primitive store underpins every local ``*Store`` port. It offers exactly the
three operations the AWS store adapter uses — ``put_if_not_exists``,
``update_if(condition)`` and all-or-nothing ``transact_write`` — plus plain
``get``/``delete``/``query`` for reads, all under a re-entrant lock so
interleaved appliers see a consistent view and the concurrency properties (P14,
P17, P23) are deterministic offline. A failed condition raises
:class:`ConditionFailed`, the same failure the AWS adapter maps from
``TransactionCanceledException`` (§7.4.8), so the port contract tests (§15.5)
can compare the two implementations.

Two backends share the interface:

* :class:`InMemoryStore` keeps items in a dict — fast property tests, no I/O.
* :class:`FileStore` persists items under ``local_store_dir`` in the §15.2
  layout, writing each file atomically (``*.tmp`` then :func:`os.replace`) so an
  interrupted replay leaves valid files. It caches items in memory and holds the
  same lock, so its conditional semantics are identical.

No socket is opened; the module imports no ``boto3``/``botocore`` (R17.1).
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

Item = dict[str, object]
Condition = Callable[["Item | None"], bool]


class ConditionFailed(Exception):
    """A conditional write failed its predicate (mirrors §7.4.8)."""


@dataclass(frozen=True, slots=True)
class TransactItem:
    """One element of an all-or-nothing ``transact_write`` (§15.1).

    ``delete`` writes a tombstone (removes the key) instead of an item, so a
    close-and-delete transaction (§7.4.6) is one atomic unit.
    """

    key: str
    item: Item | None = None
    condition: Condition | None = None
    delete: bool = False


class LocalStore(Protocol):
    """The three-primitive store the local ports build on (§15.1)."""

    def get(self, key: str) -> Item | None: ...
    def query(self, prefix: str) -> list[Item]: ...
    def put_if_not_exists(self, key: str, item: Item) -> None: ...
    def update_if(self, key: str, item: Item, condition: Condition) -> None: ...
    def delete_if(self, key: str, condition: Condition) -> None: ...
    def transact_write(self, items: Sequence[TransactItem]) -> None: ...


class InMemoryStore:
    """A dict-backed :class:`LocalStore` with conditional and transactional writes."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Item | None:
        """Return a copy of the item at ``key``, or None."""
        with self._lock:
            item = self._items.get(key)
            return dict(item) if item is not None else None

    def query(self, prefix: str) -> list[Item]:
        """Return copies of every item whose key starts with ``prefix``, sorted."""
        with self._lock:
            return [dict(v) for k, v in sorted(self._items.items()) if k.startswith(prefix)]

    def put_if_not_exists(self, key: str, item: Item) -> None:
        """Create ``key`` only if absent, else raise :class:`ConditionFailed`."""
        with self._lock:
            if key in self._items:
                raise ConditionFailed(f"item already exists: {key}")
            self._items[key] = dict(item)

    def update_if(self, key: str, item: Item, condition: Condition) -> None:
        """Write ``item`` at ``key`` only if ``condition`` holds on the current value."""
        with self._lock:
            if not condition(self._peek(key)):
                raise ConditionFailed(f"condition failed for {key}")
            self._items[key] = dict(item)

    def delete_if(self, key: str, condition: Condition) -> None:
        """Delete ``key`` only if ``condition`` holds on the current value."""
        with self._lock:
            if not condition(self._peek(key)):
                raise ConditionFailed(f"delete condition failed for {key}")
            self._items.pop(key, None)

    def transact_write(self, items: Sequence[TransactItem]) -> None:
        """Check every condition, then apply every write or delete (§15.1)."""
        with self._lock:
            for entry in items:
                if entry.condition is not None and not entry.condition(self._peek(entry.key)):
                    raise ConditionFailed(f"transaction condition failed for {entry.key}")
            for entry in items:
                self._apply(entry)

    def _apply(self, entry: TransactItem) -> None:
        if entry.delete:
            self._items.pop(entry.key, None)
            return
        assert entry.item is not None  # noqa: S101 - a non-delete item is present by construction
        self._items[entry.key] = dict(entry.item)

    def _peek(self, key: str) -> Item | None:
        item = self._items.get(key)
        return dict(item) if item is not None else None


class FileStore:
    """A file-backed :class:`LocalStore` in the §15.2 layout with atomic writes.

    Items are cached in memory and mirrored to ``<root>/<key-path>.json``; a key
    such as ``INC#inc_x#OUT#out_y`` maps to ``incident_inc_x/OUT_out_y.json``.
    Every file is written to ``*.tmp`` then :func:`os.replace`-d, which is atomic
    on POSIX, so an interrupted replay never leaves a half-written file (§15.2).
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._items: dict[str, Item] = {}
        self._lock = threading.RLock()
        self._load()

    def get(self, key: str) -> Item | None:
        with self._lock:
            item = self._items.get(key)
            return dict(item) if item is not None else None

    def query(self, prefix: str) -> list[Item]:
        with self._lock:
            return [dict(v) for k, v in sorted(self._items.items()) if k.startswith(prefix)]

    def put_if_not_exists(self, key: str, item: Item) -> None:
        with self._lock:
            if key in self._items:
                raise ConditionFailed(f"item already exists: {key}")
            self._items[key] = dict(item)
            self._flush(key)

    def update_if(self, key: str, item: Item, condition: Condition) -> None:
        with self._lock:
            if not condition(self._peek(key)):
                raise ConditionFailed(f"condition failed for {key}")
            self._items[key] = dict(item)
            self._flush(key)

    def delete_if(self, key: str, condition: Condition) -> None:
        with self._lock:
            if not condition(self._peek(key)):
                raise ConditionFailed(f"delete condition failed for {key}")
            self._items.pop(key, None)
            self._unlink(key)

    def transact_write(self, items: Sequence[TransactItem]) -> None:
        with self._lock:
            for entry in items:
                if entry.condition is not None and not entry.condition(self._peek(entry.key)):
                    raise ConditionFailed(f"transaction condition failed for {entry.key}")
            for entry in items:
                self._apply_and_flush(entry)

    def _apply_and_flush(self, entry: TransactItem) -> None:
        if entry.delete:
            self._items.pop(entry.key, None)
            self._unlink(entry.key)
            return
        assert entry.item is not None  # noqa: S101 - a non-delete item is present by construction
        self._items[entry.key] = dict(entry.item)
        self._flush(entry.key)

    def _peek(self, key: str) -> Item | None:
        item = self._items.get(key)
        return dict(item) if item is not None else None

    def _path(self, key: str) -> Path:
        """Map an opaque ``pk#sk`` key to a file path under the store root."""
        safe = key.replace("#", "/").replace(":", "_")
        return self._root / f"{safe}.json"

    def _flush(self, key: str) -> None:
        """Write one item atomically: to ``*.tmp`` then :func:`os.replace`."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._items[key], sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def _unlink(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)

    def _load(self) -> None:
        """Rebuild the cache from any existing files under the store root."""
        for path in self._iter_files():
            key = self._key_of(path)
            self._items[key] = json.loads(path.read_text(encoding="utf-8"))

    def _iter_files(self) -> Iterator[Path]:
        if not self._root.exists():
            return
        for path in self._root.rglob("*.json"):
            if path.name.endswith(".json.tmp"):
                continue
            yield path

    def _key_of(self, path: Path) -> str:
        rel = path.relative_to(self._root).with_suffix("")
        return str(rel).replace(os.sep, "#")


def make_local_store(root: Path | None) -> LocalStore:
    """Return a file-backed store when ``root`` is given, else an in-memory one."""
    if root is None:
        return InMemoryStore()
    return FileStore(root)


def key(*parts: str) -> str:
    """Compose an opaque store key from ``pk``/``sk`` parts (``a#b#c``)."""
    return "#".join(parts)


def encode_json(value: Mapping[str, object]) -> str:
    """Serialise a mapping canonically (sorted keys) for a diffable file."""
    return json.dumps(dict(value), sort_keys=True)
