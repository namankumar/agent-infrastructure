"""A small, dependency-free extraction of durable work-continuity mechanics.

This module intentionally demonstrates only local state controls: source
references, optimistic concurrency, idempotent receipts, and independent
review. It is not an authorization or identity system.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Union


SCHEMA_VERSION = 1


class Rejected(ValueError):
    """Raised when a requested state transition cannot be safely applied."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Rejected(message)


class ContinuityStore:
    """SQLite-backed state with compare-before-write and immutable receipts."""

    def __init__(self, database: Union[str, Path]):
        self.path = Path(database)
        require(self.path.parent.is_dir(), "database parent must already exist")
        self.conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        require(version in (0, SCHEMA_VERSION), "unsupported schema version; preserve database before migrating")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS work_items (
              id TEXT PRIMARY KEY,
              revision INTEGER NOT NULL,
              state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
              idempotency_key TEXT PRIMARY KEY,
              request_hash TEXT NOT NULL,
              response TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        require(self.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok", "SQLite integrity check failed")

    def close(self) -> None:
        self.conn.close()

    def _load(self, item_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT state FROM work_items WHERE id=?", (item_id,)).fetchone()
        return json.loads(row["state"]) if row else None

    @staticmethod
    def _source(value: Any) -> dict[str, str]:
        require(isinstance(value, dict) and set(value) == {"source_id", "quote", "sha256"}, "source must contain source_id, quote, sha256")
        require(all(isinstance(value[key], str) and value[key] for key in value), "source values must be nonempty strings")
        require(value["sha256"] == sha256_text(value["quote"]), "source quote hash does not match")
        return dict(value)

    @staticmethod
    def _common(payload: Any) -> tuple[str, int, str, str]:
        require(isinstance(payload, dict), "payload must be an object")
        required = {"item_id", "expected_revision", "idempotency_key", "actor"}
        require(required <= set(payload), "missing item_id, expected_revision, idempotency_key, or actor")
        item_id, expected, key, actor = (payload["item_id"], payload["expected_revision"], payload["idempotency_key"], payload["actor"])
        require(isinstance(item_id, str) and item_id, "item_id must be a nonempty string")
        require(type(expected) is int and expected >= 0, "expected_revision must be a nonnegative integer")
        require(isinstance(key, str) and key, "idempotency_key must be a nonempty string")
        require(isinstance(actor, str) and actor, "actor must be a nonempty string")
        return item_id, expected, key, actor

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def apply(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        require(operation in {"record", "review"}, "unsupported operation")
        item_id, expected, key, actor = self._common(payload)
        request_hash = sha256_text(canonical({"operation": operation, "payload": payload}))
        with self._transaction():
            previous = self.conn.execute(
                "SELECT request_hash, response FROM operations WHERE idempotency_key=?", (key,)
            ).fetchone()
            if previous:
                require(previous["request_hash"] == request_hash, "idempotency key reused with different content")
                return json.loads(previous["response"])

            state = self._load(item_id)
            actual_revision = state["revision"] if state else 0
            require(actual_revision == expected, "stale revision; refresh status before retrying")
            if operation == "record":
                next_state = self._record(state, payload, actor)
            else:
                next_state = self._review(state, payload, actor)
            next_state["revision"] = expected + 1
            next_state["updated_at"] = utc_now()
            response = self._receipt(next_state, operation, replayed=False)
            self.conn.execute(
                "INSERT OR REPLACE INTO work_items(id, revision, state) VALUES (?, ?, ?)",
                (item_id, next_state["revision"], canonical(next_state)),
            )
            self.conn.execute(
                "INSERT INTO operations(idempotency_key, request_hash, response, created_at) VALUES (?, ?, ?, ?)",
                (key, request_hash, canonical(response), utc_now()),
            )
            return response

    def _record(self, prior: Optional[dict[str, Any]], payload: dict[str, Any], actor: str) -> dict[str, Any]:
        allowed = {"item_id", "expected_revision", "idempotency_key", "actor", "summary", "source"}
        require(set(payload) == allowed, "record accepts only summary and source beyond common fields")
        require(isinstance(payload["summary"], str) and payload["summary"], "summary must be a nonempty string")
        source = self._source(payload["source"])
        if prior:
            require(prior["owner"] == actor, "only the original owner may revise this item in this example")
            reviews = prior["reviews"]
        else:
            reviews = []
        return {
            "item_id": payload["item_id"],
            "owner": actor,
            "summary": payload["summary"],
            "source": source,
            "reviews": reviews,
            "state": "recorded",
        }

    def _review(self, prior: Optional[dict[str, Any]], payload: dict[str, Any], actor: str) -> dict[str, Any]:
        allowed = {"item_id", "expected_revision", "idempotency_key", "actor", "verdict", "source"}
        require(set(payload) == allowed, "review accepts only verdict and source beyond common fields")
        require(prior is not None, "cannot review an item that has not been recorded")
        require(actor != prior["owner"], "reviewer must be independent of the item owner")
        require(payload["verdict"] in {"verified", "needs_changes"}, "verdict must be verified or needs_changes")
        review = {"actor": actor, "verdict": payload["verdict"], "source": self._source(payload["source"]), "at": utc_now()}
        reviews = [*prior["reviews"], review]
        return {
            **{key: prior[key] for key in ("item_id", "owner", "summary", "source")},
            "reviews": reviews,
            "state": "verified" if review["verdict"] == "verified" else "needs_changes",
        }

    @staticmethod
    def _receipt(state: dict[str, Any], operation: str, replayed: bool) -> dict[str, Any]:
        return {
            "item_id": state["item_id"],
            "revision": state["revision"],
            "operation": operation,
            "state": state["state"],
            "receipt_only": True,
            "replayed": replayed,
            "recorded_at": state["updated_at"],
        }

    def status(self, item_id: str) -> dict[str, Any]:
        require(isinstance(item_id, str) and item_id, "item_id must be a nonempty string")
        state = self._load(item_id)
        require(state is not None, "work item not found")
        return {
            "item_id": state["item_id"],
            "revision": state["revision"],
            "state": state["state"],
            "owner": state["owner"],
            "source": state["source"],
            "reviews": state["reviews"],
            "current_read": True,
            "receipt_only": False,
            "read_at": utc_now(),
        }

    def recover(self) -> dict[str, Any]:
        integrity = self.conn.execute("PRAGMA quick_check").fetchone()[0]
        return {"database": str(self.path), "integrity": integrity, "schema_version": self.conn.execute("PRAGMA user_version").fetchone()[0], "recovery_action": "restore a known-good SQLite backup if integrity is not ok"}
