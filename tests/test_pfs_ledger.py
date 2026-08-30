import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from pfs_agent.ledger import (
    ClaimRecord,
    EvidenceCandidate,
    EvidenceLedger,
    LedgerError,
    evidence_identity,
    normalize_http_url,
    PersistentEvidenceLedger,
)


class PfsLedgerTests(unittest.TestCase):
    def test_url_identity_removes_only_non_substantive_differences(self):
        self.assertEqual(
            "https://example.com/report?month=1",
            normalize_http_url(" HTTPS://Example.com:443/report/?month=1#summary "),
        )
        self.assertEqual(
            evidence_identity("http://example.com:80/a/", "  sales  "),
            evidence_identity("HTTP://EXAMPLE.COM/a", "sales"),
        )
        with self.assertRaises(LedgerError):
            normalize_http_url("ftp://example.com/report")

    def test_batch_registration_is_idempotent(self):
        ledger = EvidenceLedger()
        candidate = EvidenceCandidate(
            source_url="https://example.com/report/",
            snippet="  Revenue was 100.  ",
            task_id="task-1",
            title="Monthly report",
            source_type="report",
            trust_level="high",
        )
        first = ledger.register_batch((candidate, candidate))
        second = ledger.register_batch((candidate,))
        self.assertEqual((1, 1), (first.created, first.deduplicated))
        self.assertEqual((0, 1), (second.created, second.deduplicated))
        self.assertEqual(1, len(ledger.list_evidence("task-1")))

    def test_claim_detail_exposes_support_refute_and_conflict(self):
        ledger = EvidenceLedger()
        entries = ledger.register_batch(
            (
                EvidenceCandidate("https://a.example/report", "Revenue increased", "task-1"),
                EvidenceCandidate("https://b.example/report", "Revenue decreased", "task-1"),
            )
        ).entries
        ledger.create_claim(ClaimRecord("claim-1", "task-1", "Revenue increased"))
        ledger.link_claim(
            "claim-1",
            evidence_id=entries[0].evidence_id,
            relation="supports",
            confidence=0.8,
            verification_reason="来源 A 支持该结论",
        )
        claim = ledger.link_claim(
            "claim-1",
            evidence_id=entries[1].evidence_id,
            relation="refutes",
            confidence=0.7,
            verification_reason="来源 B 给出相反方向",
        )
        self.assertEqual("conflicted", claim.status)
        self.assertEqual(1, len(ledger.pending_conflicts("task-1")))
        detail = ledger.claim_detail("claim-1")
        self.assertEqual(2, len(detail["evidence"]))
        self.assertEqual(2, len(detail["claim"]["evidence_links"]))

    def test_claim_and_evidence_are_task_scoped(self):
        ledger = EvidenceLedger()
        entry, _ = ledger.register(EvidenceCandidate("https://example.com", "A", "task-a"))
        ledger.create_claim(ClaimRecord("claim-a", "task-a", "A"))
        ledger.create_claim(ClaimRecord("claim-b", "task-b", "B"))
        with self.assertRaises(LedgerError):
            ledger.link_claim("claim-b", evidence_id=entry.evidence_id, relation="supports")

    def test_persistent_ledger_reloads_and_keeps_idempotency(self):
        candidate = EvidenceCandidate(
            "https://example.com/persisted-report",
            "Revenue was 100",
            "task-persisted",
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            first = PersistentEvidenceLedger(path)
            entry, created = first.register(candidate)
            first.create_claim(ClaimRecord("claim-persisted", "task-persisted", "Revenue was 100"))
            first.link_claim(
                "claim-persisted",
                evidence_id=entry.evidence_id,
                relation="supports",
                confidence=0.9,
                verification_reason="可由来源片段复算",
            )
            self.assertTrue(created)
            self.assertTrue(path.is_file())

            restored = PersistentEvidenceLedger(path)
            duplicate, duplicate_created = restored.register(candidate)
            self.assertEqual(entry.evidence_id, duplicate.evidence_id)
            self.assertFalse(duplicate_created)
            detail = restored.claim_detail("claim-persisted")
            self.assertEqual("supported", detail["claim"]["status"])
            self.assertEqual(1, len(detail["evidence"]))


if __name__ == "__main__":
    unittest.main()
