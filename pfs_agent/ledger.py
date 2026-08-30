"""Evidence Ledger and Claim--Evidence contracts owned by PFS.

The ledger is intentionally dependency-free and in-memory for this migration
slice.  It provides the stable identity, idempotent batch registration, claim
linking, and conflict detection rules that a persistent adapter can implement
later without changing the business contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit


class LedgerError(ValueError):
    """Raised when an evidence or claim violates the PFS ledger contract."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELATIONS = frozenset({"supports", "refutes"})
_CLAIM_STATUSES = frozenset({"unverified", "supported", "refuted", "conflicted", "needs_review"})


def normalize_http_url(value: str) -> str:
    """Normalize only non-substantive HTTP(S) URL differences.

    Fragments, default ports, and trailing path slashes are removed.  Query
    strings and non-default ports are retained because they may identify a
    different resource.
    """
    raw = str(value or "").strip()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise LedgerError("evidence URL must use http or https")
    if not parts.netloc or parts.username or parts.password:
        raise LedgerError("evidence URL must contain a host without credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise LedgerError("evidence URL has an invalid port") from exc
    hostname = (parts.hostname or "").lower().rstrip(".")
    if not hostname:
        raise LedgerError("evidence URL must contain a host")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = 80 if scheme == "http" else 443
    netloc = hostname if port in {None, default_port} else f"{hostname}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def clean_snippet(value: str) -> str:
    """Trim boundary whitespace while preserving the source's inner text."""
    return str(value or "").strip()


def evidence_identity(url: str, snippet: str) -> str:
    """Return the project-scoped SHA-256 identity for an evidence candidate."""
    normalized_url = normalize_http_url(url)
    cleaned = clean_snippet(snippet)
    if not cleaned:
        raise LedgerError("evidence snippet must not be empty")
    payload = json.dumps([normalized_url, cleaned], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceCandidate:
    """Explicit evidence supplied by a collector, user, or analysis run."""

    source_url: str
    snippet: str
    task_id: str
    title: str = ""
    publisher: str = ""
    published_at: str = ""
    captured_at: str = ""
    source_type: str = "unknown"
    trust_level: str = "unknown"
    content_sha256: str = ""

    def to_entry(self) -> "EvidenceEntry":
        normalized_url = normalize_http_url(self.source_url)
        cleaned = clean_snippet(self.snippet)
        task_id = str(self.task_id or "").strip()
        if not task_id:
            raise LedgerError("evidence task_id must not be empty")
        if not cleaned:
            raise LedgerError("evidence snippet must not be empty")
        content_hash = str(self.content_sha256 or "").strip().lower() or _content_hash(cleaned)
        if not _SHA256.fullmatch(content_hash):
            raise LedgerError("content_sha256 must be a 64-character lowercase SHA-256")
        identity_sha256 = evidence_identity(normalized_url, cleaned)
        return EvidenceEntry(
            evidence_id=f"ev_{identity_sha256[:16]}",
            identity_sha256=identity_sha256,
            source_url=normalized_url,
            title=str(self.title or "").strip(),
            publisher=str(self.publisher or "").strip(),
            published_at=str(self.published_at or "").strip(),
            captured_at=str(self.captured_at or "").strip() or _utc_now(),
            snippet=cleaned,
            source_type=str(self.source_type or "unknown").strip() or "unknown",
            trust_level=str(self.trust_level or "unknown").strip() or "unknown",
            task_id=task_id,
            content_sha256=content_hash,
        )


@dataclass(frozen=True)
class EvidenceEntry:
    evidence_id: str
    identity_sha256: str
    source_url: str
    title: str
    publisher: str
    published_at: str
    captured_at: str
    snippet: str
    source_type: str
    trust_level: str
    task_id: str
    content_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "identity_sha256": self.identity_sha256,
            "source_url": self.source_url,
            "title": self.title,
            "publisher": self.publisher,
            "published_at": self.published_at,
            "captured_at": self.captured_at,
            "snippet": self.snippet,
            "source_type": self.source_type,
            "trust_level": self.trust_level,
            "task_id": self.task_id,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class BatchRegistration:
    entries: tuple[EvidenceEntry, ...]
    created: int
    deduplicated: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "deduplicated": self.deduplicated,
            "entries": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class ClaimEvidenceLink:
    evidence_id: str
    relation: str
    confidence: float = 0.0
    verification_reason: str = ""

    def __post_init__(self) -> None:
        if not str(self.evidence_id or "").strip():
            raise LedgerError("claim evidence_id must not be empty")
        if self.relation not in _RELATIONS:
            raise LedgerError("claim relation must be supports or refutes")
        if not 0 <= float(self.confidence) <= 1:
            raise LedgerError("claim confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "relation": self.relation,
            "confidence": self.confidence,
            "verification_reason": self.verification_reason,
        }


@dataclass(frozen=True)
class ClaimRecord:
    claim_id: str
    task_id: str
    text: str
    status: str = "unverified"
    confidence: float = 0.0
    verification_reason: str = ""
    human_decision: str = ""
    evidence_links: tuple[ClaimEvidenceLink, ...] = ()

    def __post_init__(self) -> None:
        if not self.claim_id.strip() or not self.task_id.strip() or not self.text.strip():
            raise LedgerError("claim_id, task_id, and text must not be empty")
        if self.status not in _CLAIM_STATUSES:
            raise LedgerError(f"unsupported claim status: {self.status}")
        if not 0 <= float(self.confidence) <= 1:
            raise LedgerError("claim confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "task_id": self.task_id,
            "text": self.text,
            "status": self.status,
            "confidence": self.confidence,
            "verification_reason": self.verification_reason,
            "human_decision": self.human_decision,
            "evidence_links": [link.to_dict() for link in self.evidence_links],
        }


@dataclass(frozen=True)
class ConflictItem:
    conflict_id: str
    claim_id: str
    task_id: str
    evidence_ids: tuple[str, ...]
    status: str = "pending_review"
    reason: str = "同一 Claim 同时存在支持和反驳证据，不能静默选取其一。"

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "claim_id": self.claim_id,
            "task_id": self.task_id,
            "evidence_ids": list(self.evidence_ids),
            "status": self.status,
            "reason": self.reason,
        }


class EvidenceLedger:
    """An idempotent ledger and claim index for one process or test run."""

    def __init__(self) -> None:
        self._entries: dict[str, EvidenceEntry] = {}
        self._claims: dict[str, ClaimRecord] = {}

    def register(self, candidate: EvidenceCandidate) -> tuple[EvidenceEntry, bool]:
        batch = self.register_batch((candidate,))
        return batch.entries[0], batch.created == 1

    def register_batch(self, candidates: Iterable[EvidenceCandidate]) -> BatchRegistration:
        """Register candidates atomically and report created/deduplicated counts."""
        prepared = tuple(candidate.to_entry() for candidate in candidates)
        entries: list[EvidenceEntry] = []
        created = 0
        deduplicated = 0
        staged: dict[str, EvidenceEntry] = {}
        for entry in prepared:
            existing = self._entries.get(entry.identity_sha256) or staged.get(entry.identity_sha256)
            if existing is not None:
                entries.append(existing)
                deduplicated += 1
                continue
            staged[entry.identity_sha256] = entry
            entries.append(entry)
            created += 1
        self._entries.update(staged)
        return BatchRegistration(tuple(entries), created, deduplicated)

    def get_evidence(self, evidence_id: str) -> EvidenceEntry | None:
        return next((entry for entry in self._entries.values() if entry.evidence_id == evidence_id), None)

    def list_evidence(self, task_id: str = "") -> tuple[EvidenceEntry, ...]:
        task = str(task_id or "").strip()
        entries = (
            self._entries.values()
            if not task
            else (entry for entry in self._entries.values() if entry.task_id == task)
        )
        return tuple(entries)

    def create_claim(self, claim: ClaimRecord) -> ClaimRecord:
        existing = self._claims.get(claim.claim_id)
        if existing is not None and existing != claim:
            raise LedgerError(f"claim already exists with different content: {claim.claim_id}")
        self._claims[claim.claim_id] = claim
        return claim

    def link_claim(
        self,
        claim_id: str,
        *,
        evidence_id: str,
        relation: str,
        confidence: float = 0.0,
        verification_reason: str = "",
    ) -> ClaimRecord:
        claim = self._claims.get(claim_id)
        if claim is None:
            raise LedgerError(f"claim does not exist: {claim_id}")
        evidence = self.get_evidence(evidence_id)
        if evidence is None:
            raise LedgerError(f"evidence does not exist: {evidence_id}")
        if evidence.task_id != claim.task_id:
            raise LedgerError("claim and evidence must belong to the same task")
        link = ClaimEvidenceLink(
            evidence_id=evidence_id,
            relation=relation,
            confidence=confidence,
            verification_reason=str(verification_reason or "").strip(),
        )
        links = [item for item in claim.evidence_links if item.evidence_id != evidence_id]
        links.append(link)
        relations = {item.relation for item in links}
        status = (
            "conflicted"
            if relations == _RELATIONS
            else (
                "supported"
                if "supports" in relations
                else "refuted"
                if "refutes" in relations
                else "unverified"
            )
        )
        confidence_value = max((float(item.confidence) for item in links), default=0.0)
        reasons = []
        for item in links:
            reason = item.verification_reason.strip()
            if reason and reason not in reasons:
                reasons.append(reason)
        updated = replace(
            claim,
            status=status,
            confidence=confidence_value,
            verification_reason="；".join(reasons),
            evidence_links=tuple(links),
        )
        self._claims[claim_id] = updated
        return updated

    def decide_claim(self, claim_id: str, decision: str, *, reason: str = "") -> ClaimRecord:
        claim = self._claims.get(claim_id)
        if claim is None:
            raise LedgerError(f"claim does not exist: {claim_id}")
        decision = str(decision or "").strip()
        if not decision:
            raise LedgerError("human decision must not be empty")
        updated = replace(
            claim,
            human_decision=decision,
            verification_reason=(
                claim.verification_reason
                + ("；" if claim.verification_reason else "")
                + str(reason or "").strip()
            ).strip("；"),
        )
        self._claims[claim_id] = updated
        return updated

    def get_claim(self, claim_id: str) -> ClaimRecord | None:
        return self._claims.get(claim_id)

    def list_claims(self, task_id: str = "") -> tuple[ClaimRecord, ...]:
        """Return claims, optionally narrowed to one task namespace."""
        task = str(task_id or "").strip()
        claims = (
            self._claims.values()
            if not task
            else (claim for claim in self._claims.values() if claim.task_id == task)
        )
        return tuple(claims)

    def claim_detail(self, claim_id: str) -> dict[str, Any]:
        claim = self._claims.get(claim_id)
        if claim is None:
            raise LedgerError(f"claim does not exist: {claim_id}")
        return {
            "claim": claim.to_dict(),
            "evidence": [
                self.get_evidence(link.evidence_id).to_dict()
                for link in claim.evidence_links
                if self.get_evidence(link.evidence_id) is not None
            ],
        }

    def pending_conflicts(self, task_id: str = "") -> tuple[ConflictItem, ...]:
        conflicts = []
        for claim in self._claims.values():
            if task_id and claim.task_id != task_id:
                continue
            relations = {link.relation for link in claim.evidence_links}
            if relations != _RELATIONS:
                continue
            evidence_ids = tuple(sorted({link.evidence_id for link in claim.evidence_links}))
            conflict_key = f"{claim.claim_id}|{'|'.join(evidence_ids)}"
            conflict_id = "conf_" + hashlib.sha256(conflict_key.encode("utf-8")).hexdigest()[:16]
            conflicts.append(ConflictItem(conflict_id, claim.claim_id, claim.task_id, evidence_ids))
        return tuple(conflicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": [entry.to_dict() for entry in self._entries.values()],
            "claims": [claim.to_dict() for claim in self._claims.values()],
            "pending_conflicts": [item.to_dict() for item in self.pending_conflicts()],
        }


class PersistentEvidenceLedger(EvidenceLedger):
    """A small atomic JSON-backed ledger for local or single-user runtimes.

    The storage adapter deliberately sits below the business contract: callers
    still use the same idempotent registration and claim-linking methods.  A
    database adapter can replace this class later without changing the API
    payloads or identity rules.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        super().__init__()
        self.path = Path(path).resolve()
        self._persist_lock = threading.RLock()
        self._load()

    @staticmethod
    def _entry_from_dict(raw: Mapping[str, Any]) -> EvidenceEntry:
        fields = (
            "evidence_id",
            "identity_sha256",
            "source_url",
            "title",
            "publisher",
            "published_at",
            "captured_at",
            "snippet",
            "source_type",
            "trust_level",
            "task_id",
            "content_sha256",
        )
        try:
            entry = EvidenceEntry(**{field: str(raw.get(field) or "") for field in fields})
        except TypeError as exc:
            raise LedgerError("stored evidence entry is malformed") from exc
        if evidence_identity(entry.source_url, entry.snippet) != entry.identity_sha256:
            raise LedgerError("stored evidence identity does not match its content")
        return entry

    @classmethod
    def _claim_from_dict(cls, raw: Mapping[str, Any]) -> ClaimRecord:
        try:
            links = tuple(
                ClaimEvidenceLink(
                    evidence_id=str(item.get("evidence_id") or ""),
                    relation=str(item.get("relation") or ""),
                    confidence=float(item.get("confidence") or 0),
                    verification_reason=str(item.get("verification_reason") or ""),
                )
                for item in (raw.get("evidence_links") or ())
            )
            return ClaimRecord(
                claim_id=str(raw.get("claim_id") or ""),
                task_id=str(raw.get("task_id") or ""),
                text=str(raw.get("text") or ""),
                status=str(raw.get("status") or "unverified"),
                confidence=float(raw.get("confidence") or 0),
                verification_reason=str(raw.get("verification_reason") or ""),
                human_decision=str(raw.get("human_decision") or ""),
                evidence_links=links,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise LedgerError("stored claim is malformed") from exc

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError(f"cannot read evidence ledger: {self.path}") from exc
        if not isinstance(payload, Mapping):
            raise LedgerError("stored evidence ledger must be an object")

        entries: dict[str, EvidenceEntry] = {}
        for raw in payload.get("evidence") or ():
            if not isinstance(raw, Mapping):
                raise LedgerError("stored evidence entry must be an object")
            entry = self._entry_from_dict(raw)
            if entry.identity_sha256 in entries:
                raise LedgerError("stored evidence ledger contains duplicate identities")
            entries[entry.identity_sha256] = entry

        claims: dict[str, ClaimRecord] = {}
        for raw in payload.get("claims") or ():
            if not isinstance(raw, Mapping):
                raise LedgerError("stored claim must be an object")
            claim = self._claim_from_dict(raw)
            if claim.claim_id in claims:
                raise LedgerError("stored evidence ledger contains duplicate claims")
            for link in claim.evidence_links:
                evidence = next(
                    (item for item in entries.values() if item.evidence_id == link.evidence_id),
                    None,
                )
                if evidence is None or evidence.task_id != claim.task_id:
                    raise LedgerError("stored claim link violates task scope")
            claims[claim.claim_id] = claim
        self._entries = entries
        self._claims = claims

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def register_batch(self, candidates: Iterable[EvidenceCandidate]) -> BatchRegistration:
        with self._persist_lock:
            result = super().register_batch(candidates)
            if result.created:
                self._persist()
            return result

    def create_claim(self, claim: ClaimRecord) -> ClaimRecord:
        with self._persist_lock:
            result = super().create_claim(claim)
            self._persist()
            return result

    def link_claim(self, claim_id: str, **kwargs: Any) -> ClaimRecord:
        with self._persist_lock:
            result = super().link_claim(claim_id, **kwargs)
            self._persist()
            return result

    def decide_claim(self, claim_id: str, decision: str, *, reason: str = "") -> ClaimRecord:
        with self._persist_lock:
            result = super().decide_claim(claim_id, decision, reason=reason)
            self._persist()
            return result
