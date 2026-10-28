from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agent_continuity.cli import main
from agent_continuity.store import ContinuityStore, Rejected, sha256_text


def source(source_id: str, quote: str) -> dict[str, str]:
    return {"source_id": source_id, "quote": quote, "sha256": sha256_text(quote)}


class ContinuityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ContinuityStore(Path(self.tmp.name) / "state.sqlite")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def record(self, key: str = "record-1", revision: int = 0, summary: str = "Keep state source-backed.") -> dict:
        return self.store.apply(
            "record",
            {
                "item_id": "item-1",
                "expected_revision": revision,
                "idempotency_key": key,
                "actor": "owner",
                "summary": summary,
                "source": source("turn-1", summary),
            },
        )

    def test_persists_source_and_current_receipt_is_distinct_from_status(self) -> None:
        receipt = self.record()
        status = self.store.status("item-1")
        self.assertTrue(receipt["receipt_only"])
        self.assertFalse(status["receipt_only"])
        self.assertTrue(status["current_read"])
        self.assertEqual(status["source"]["source_id"], "turn-1")
        self.assertEqual(status["revision"], 1)

    def test_exact_replay_returns_original_receipt_without_new_revision(self) -> None:
        first = self.record()
        replay = self.record()
        self.assertEqual(replay, first)
        self.assertEqual(self.store.status("item-1")["revision"], 1)

    def test_same_idempotency_key_with_different_payload_is_rejected(self) -> None:
        self.record()
        with self.assertRaisesRegex(Rejected, "idempotency key reused"):
            self.record(summary="Different scope")
        self.assertEqual(self.store.status("item-1")["revision"], 1)

    def test_stale_writer_is_rejected_without_overwriting_newer_state(self) -> None:
        self.record()
        self.record(key="record-2", revision=1, summary="Revised scope")
        with self.assertRaisesRegex(Rejected, "stale revision"):
            self.record(key="stale-write", revision=1, summary="Old writer overwrite")
        self.assertEqual(self.store.status("item-1")["revision"], 2)
        self.assertEqual(self.store.status("item-1")["source"]["quote"], "Revised scope")

    def test_owner_cannot_review_own_work_but_independent_reviewer_can(self) -> None:
        self.record()
        same_actor = {
            "item_id": "item-1", "expected_revision": 1, "idempotency_key": "review-owner",
            "actor": "owner", "verdict": "verified", "source": source("review-1", "Looks good"),
        }
        with self.assertRaisesRegex(Rejected, "independent"):
            self.store.apply("review", same_actor)
        accepted = {**same_actor, "actor": "reviewer", "idempotency_key": "review-independent"}
        receipt = self.store.apply("review", accepted)
        self.assertEqual(receipt["state"], "verified")
        self.assertEqual(self.store.status("item-1")["reviews"][0]["actor"], "reviewer")

    def test_invalid_source_hash_is_rejected_before_state_write(self) -> None:
        payload = {
            "item_id": "item-1", "expected_revision": 0, "idempotency_key": "invalid-source",
            "actor": "owner", "summary": "Keep state source-backed.",
            "source": {"source_id": "turn-1", "quote": "Keep state source-backed.", "sha256": "0" * 64},
        }
        with self.assertRaisesRegex(Rejected, "hash"):
            self.store.apply("record", payload)
        with self.assertRaisesRegex(Rejected, "not found"):
            self.store.status("item-1")

    def test_recovery_receipt_reports_integrity_check(self) -> None:
        self.record()
        receipt = self.store.recover()
        self.assertEqual(receipt["integrity"], "ok")
        self.assertEqual(receipt["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()


class CorruptDatabaseCliTests(unittest.TestCase):
    def test_corrupt_database_is_rejected_without_restore_claim_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "broken.sqlite"
            original = b"not a sqlite database"
            database.write_bytes(original)
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                result = main(["--db", str(database), "recover"])
            self.assertEqual(result, 2)
            self.assertIs(json.loads(error.getvalue())["ok"], False)
            self.assertEqual(database.read_bytes(), original)
