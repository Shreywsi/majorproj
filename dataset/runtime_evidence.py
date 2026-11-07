"""Typed, signed runtime evidence built on the existing crypto ledger keys."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import StrEnum
import json
import math
import secrets
import uuid
from typing import Any, Iterable, Mapping

from crypto_ledger import EvidenceAuthority, EvidenceVerifier
from dataset.experiment_schemas import EventExecutionStatus


_IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)


class EvidenceCategory(StrEnum):
    IDENTITY = "identity"
    ROLE = "role"
    CONFIGURED_PERMISSION = "configured_permission"
    DEMONSTRATED_CAPABILITY = "demonstrated_capability"
    TOOL_EXECUTION = "tool_execution"
    TASK_COMPLETION = "task_completion"
    EVIDENCE_SOURCE = "evidence_source"


class AuthorityKind(StrEnum):
    IDENTITY_REGISTRY = "identity_registry"
    RUNTIME_EXECUTION = "runtime_execution"
    TOOL_EXECUTION = "tool_execution"
    EXPERIMENT_EVALUATOR = "experiment_evaluator"


class EvidenceVerificationStatus(StrEnum):
    VALID = "valid"
    MISSING = "missing"
    INVALID_SIGNATURE = "invalid_signature"
    UNTRUSTED_ISSUER = "untrusted_issuer"
    CATEGORY_NOT_AUTHORIZED = "category_not_authorized"
    EXPIRED = "expired"
    FUTURE_DATED = "future_dated"
    STALE = "stale"
    REPLAYED = "replayed"
    IDENTITY_MISMATCH = "identity_mismatch"
    IDENTITY_UNREGISTERED = "identity_unregistered"
    SCOPE_MISMATCH = "scope_mismatch"
    SIMULATED_EXECUTION = "simulated_execution"


_KIND_CATEGORIES = {
    AuthorityKind.IDENTITY_REGISTRY: frozenset(
        {EvidenceCategory.IDENTITY, EvidenceCategory.ROLE}
    ),
    AuthorityKind.RUNTIME_EXECUTION: frozenset(
        {
            EvidenceCategory.CONFIGURED_PERMISSION,
            EvidenceCategory.EVIDENCE_SOURCE,
        }
    ),
    AuthorityKind.TOOL_EXECUTION: frozenset(
        {EvidenceCategory.TOOL_EXECUTION, EvidenceCategory.EVIDENCE_SOURCE}
    ),
    AuthorityKind.EXPERIMENT_EVALUATOR: frozenset(
        {
            EvidenceCategory.DEMONSTRATED_CAPABILITY,
            EvidenceCategory.TASK_COMPLETION,
        }
    ),
}


def _identifier(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or value[0] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        or any(character not in _IDENTIFIER_CHARS for character in value)
    ):
        raise ValueError(f"{field} must be a valid identifier.")


def _text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")


def _timestamp(value: int, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")


def _json_value(value: Any, field: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must contain finite JSON-compatible data.")
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, field)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{field} object keys must be strings.")
        for item in value.values():
            _json_value(item, field)
        return
    raise ValueError(f"{field} must contain JSON-compatible data.")


def _canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _decode_b64(value: str, field: str, expected_length: int | None = None) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field} must be valid base64.") from exc
    if expected_length is not None and len(decoded) != expected_length:
        raise ValueError(f"{field} must decode to {expected_length} bytes.")
    return decoded


@dataclass(frozen=True)
class RuntimeEvidence:
    evidence_id: str
    issuer_id: str
    category: EvidenceCategory
    agent_id: str
    experiment_id: str
    subject: str
    predicate: str
    asserted_value: Any
    scope: str
    created_at: int
    expires_at: int
    nonce: str
    signature: str
    trace_id: str | None = None
    task_id: str | None = None
    message_id: str | None = None
    assertion_id: str | None = None
    source_event_sequence: int | None = None
    execution_status: EventExecutionStatus | None = None
    source_ref: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.evidence_id, "evidence_id"),
            (self.issuer_id, "issuer_id"),
            (self.agent_id, "agent_id"),
            (self.experiment_id, "experiment_id"),
        ):
            _identifier(value, name)
        for value, name in (
            (self.trace_id, "trace_id"),
            (self.task_id, "task_id"),
            (self.message_id, "message_id"),
            (self.assertion_id, "assertion_id"),
        ):
            if value is not None:
                _identifier(value, name)
        if not isinstance(self.category, EvidenceCategory):
            raise ValueError("category must be an EvidenceCategory.")
        for value, name in (
            (self.subject, "subject"),
            (self.predicate, "predicate"),
            (self.scope, "scope"),
            (self.nonce, "nonce"),
        ):
            _text(value, name)
        _json_value(self.asserted_value, "asserted_value")
        _timestamp(self.created_at, "created_at")
        _timestamp(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at.")
        if self.source_event_sequence is not None:
            _timestamp(self.source_event_sequence, "source_event_sequence")
        if self.execution_status is not None and not isinstance(
            self.execution_status, EventExecutionStatus
        ):
            raise ValueError("execution_status must be an EventExecutionStatus.")
        if self.category == EvidenceCategory.TOOL_EXECUTION:
            if self.execution_status is None:
                raise ValueError("tool execution evidence requires execution_status.")
        if self.source_ref is not None:
            _text(self.source_ref, "source_ref")
        _decode_b64(self.signature, "signature", expected_length=64)

    def signing_payload(self) -> bytes:
        return _canonical_json(self.unsigned_dict())

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "issuer_id": self.issuer_id,
            "category": self.category.value,
            "agent_id": self.agent_id,
            "experiment_id": self.experiment_id,
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "message_id": self.message_id,
            "assertion_id": self.assertion_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "asserted_value": self.asserted_value,
            "scope": self.scope,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "source_event_sequence": self.source_event_sequence,
            "execution_status": (
                self.execution_status.value
                if self.execution_status is not None
                else None
            ),
            "source_ref": self.source_ref,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "signature": self.signature}

    def to_json(self) -> str:
        return _canonical_json(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeEvidence":
        expected = {
            "evidence_id", "issuer_id", "category", "agent_id", "experiment_id",
            "trace_id", "task_id", "message_id", "assertion_id", "subject",
            "predicate", "asserted_value", "scope", "created_at", "expires_at",
            "nonce", "source_event_sequence", "execution_status", "source_ref",
            "signature",
        }
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("Runtime evidence contains missing or unknown fields.")
        return cls(
            **{
                **data,
                "category": EvidenceCategory(data["category"]),
                "execution_status": (
                    EventExecutionStatus(data["execution_status"])
                    if data["execution_status"] is not None
                    else None
                ),
            }
        )

    @classmethod
    def from_json(cls, serialized: str) -> "RuntimeEvidence":
        try:
            data = json.loads(serialized)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid runtime evidence JSON: {exc}") from exc
        return cls.from_dict(data)


class RuntimeEvidenceIssuer:
    """Private-key holder for one configured authority identity.

    Instances must remain in trusted runtime components and must not be passed
    to agents or serialized. Public trust metadata is available separately.
    """

    def __init__(
        self,
        issuer_id: str,
        authority_kind: AuthorityKind,
        categories: Iterable[EvidenceCategory] | None = None,
        seed: bytes | None = None,
    ):
        _identifier(issuer_id, "issuer_id")
        if not isinstance(authority_kind, AuthorityKind):
            raise ValueError("authority_kind must be an AuthorityKind.")
        permitted = _KIND_CATEGORIES[authority_kind]
        selected = frozenset(permitted if categories is None else categories)
        if not selected or any(
            not isinstance(category, EvidenceCategory) or category not in permitted
            for category in selected
        ):
            raise ValueError("categories are not authorized for this authority kind.")
        if seed is not None and (not isinstance(seed, bytes) or len(seed) != 32):
            raise ValueError("seed must be exactly 32 bytes when provided.")
        self.issuer_id = issuer_id
        self.authority_kind = authority_kind
        self.categories = selected
        self._authority = EvidenceAuthority(seed)

    @property
    def public_key(self) -> bytes:
        return bytes(self._authority.vk)

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key).decode("ascii")

    def trusted_issuer(self) -> "TrustedIssuer":
        return TrustedIssuer(
            issuer_id=self.issuer_id,
            authority_kind=self.authority_kind,
            public_key=self.public_key_b64,
            categories=tuple(sorted(self.categories, key=lambda value: value.value)),
        )

    def issue_evidence(
        self,
        *,
        category: EvidenceCategory,
        agent_id: str,
        experiment_id: str,
        subject: str,
        predicate: str,
        asserted_value: Any,
        scope: str,
        created_at: int,
        expires_at: int,
        trace_id: str | None = None,
        task_id: str | None = None,
        message_id: str | None = None,
        assertion_id: str | None = None,
        source_event_sequence: int | None = None,
        execution_status: EventExecutionStatus | None = None,
        source_ref: str | None = None,
        evidence_id: str | None = None,
        nonce: str | None = None,
    ) -> RuntimeEvidence:
        if category not in self.categories:
            raise ValueError(
                f"issuer {self.issuer_id} is not authorized for {category!r}."
            )
        unsigned = RuntimeEvidence(
            evidence_id=evidence_id or f"evi-{uuid.uuid4().hex}",
            issuer_id=self.issuer_id,
            category=category,
            agent_id=agent_id,
            experiment_id=experiment_id,
            trace_id=trace_id,
            task_id=task_id,
            message_id=message_id,
            assertion_id=assertion_id,
            subject=subject,
            predicate=predicate,
            asserted_value=asserted_value,
            scope=scope,
            created_at=created_at,
            expires_at=expires_at,
            nonce=nonce or secrets.token_urlsafe(24),
            signature=base64.b64encode(bytes(64)).decode("ascii"),
            source_event_sequence=source_event_sequence,
            execution_status=execution_status,
            source_ref=source_ref,
        )
        signature = self._authority.sign_payload(unsigned.signing_payload())
        return RuntimeEvidence(
            **{
                **unsigned.to_dict(),
                "signature": base64.b64encode(signature).decode("ascii"),
                "category": unsigned.category,
                "execution_status": unsigned.execution_status,
            }
        )


@dataclass(frozen=True)
class TrustedIssuer:
    issuer_id: str
    authority_kind: AuthorityKind
    public_key: str
    categories: tuple[EvidenceCategory, ...]

    def __post_init__(self) -> None:
        _identifier(self.issuer_id, "issuer_id")
        if not isinstance(self.authority_kind, AuthorityKind):
            raise ValueError("authority_kind must be an AuthorityKind.")
        if not isinstance(self.categories, tuple) or not self.categories:
            raise ValueError("categories must be a non-empty tuple.")
        if any(
            not isinstance(category, EvidenceCategory)
            or category not in _KIND_CATEGORIES[self.authority_kind]
            for category in self.categories
        ):
            raise ValueError("issuer category is not permitted for its authority kind.")
        if len(self.categories) != len(set(self.categories)):
            raise ValueError("issuer categories must not contain duplicates.")
        _decode_b64(self.public_key, "public_key", expected_length=32)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "issuer_id": self.issuer_id,
            "authority_kind": self.authority_kind.value,
            "public_key": self.public_key,
            "categories": sorted(category.value for category in self.categories),
        }


class TrustPolicy:
    """Explicit allow-list of trusted authority public keys and evidence types."""

    def __init__(self, issuers: Iterable[TrustedIssuer]):
        issuer_list = tuple(issuers)
        if any(not isinstance(issuer, TrustedIssuer) for issuer in issuer_list):
            raise ValueError("issuers must contain TrustedIssuer items.")
        self._issuers = {issuer.issuer_id: issuer for issuer in issuer_list}
        if len(self._issuers) != len(issuer_list):
            raise ValueError("trusted issuer IDs must be unique.")

    def find(self, issuer_id: str) -> TrustedIssuer | None:
        return self._issuers.get(issuer_id)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "issuers": [
                self._issuers[key].to_public_dict()
                for key in sorted(self._issuers)
            ]
        }


class AgentIdentityRegistry:
    """Challenge-based proof-of-possession registry for task-local agent keys."""

    _CHALLENGE_PURPOSE = "provenanceguard-agent-key-binding-v1"

    def __init__(self, identity_issuer: RuntimeEvidenceIssuer):
        if (
            not isinstance(identity_issuer, RuntimeEvidenceIssuer)
            or identity_issuer.authority_kind != AuthorityKind.IDENTITY_REGISTRY
            or EvidenceCategory.IDENTITY not in identity_issuer.categories
        ):
            raise ValueError("identity_issuer must be authorized to issue identity evidence.")
        self._identity_issuer = identity_issuer
        self._pending: dict[str, str] = {}
        self._public_keys: dict[str, bytes] = {}

    @staticmethod
    def registration_payload(agent_id: str, challenge: str) -> bytes:
        _identifier(agent_id, "agent_id")
        _text(challenge, "challenge")
        return _canonical_json(
            {
                "agent_id": agent_id,
                "challenge": challenge,
                "purpose": AgentIdentityRegistry._CHALLENGE_PURPOSE,
            }
        )

    def create_challenge(self, agent_id: str, challenge: str | None = None) -> str:
        _identifier(agent_id, "agent_id")
        challenge = challenge or secrets.token_urlsafe(32)
        _text(challenge, "challenge")
        if challenge in self._pending:
            raise ValueError("registration challenge must be unique.")
        self._pending[challenge] = agent_id
        return challenge

    def register_agent(
        self,
        *,
        agent_id: str,
        public_key: bytes | str,
        challenge: str,
        proof_signature: bytes | str,
        experiment_id: str,
        created_at: int,
        expires_at: int,
        trace_id: str | None = None,
        task_id: str | None = None,
        evidence_id: str | None = None,
        nonce: str | None = None,
    ) -> RuntimeEvidence:
        if self._pending.pop(challenge, None) != agent_id:
            raise ValueError("registration challenge is missing, expired, or for another agent.")
        if isinstance(public_key, str):
            public_key_bytes = _decode_b64(public_key, "public_key", expected_length=32)
        elif isinstance(public_key, bytes) and len(public_key) == 32:
            public_key_bytes = public_key
        else:
            raise ValueError("public_key must be 32 raw bytes or base64-encoded bytes.")
        if isinstance(proof_signature, str):
            proof_signature_bytes = _decode_b64(
                proof_signature, "proof_signature", expected_length=64
            )
        elif isinstance(proof_signature, bytes) and len(proof_signature) == 64:
            proof_signature_bytes = proof_signature
        else:
            raise ValueError("proof_signature must be 64 raw bytes or base64-encoded bytes.")
        if not EvidenceVerifier(public_key_bytes).verify_payload(
            self.registration_payload(agent_id, challenge), proof_signature_bytes
        ):
            raise ValueError("agent key registration proof is invalid.")
        if agent_id in self._public_keys:
            raise ValueError(f"agent {agent_id} is already registered.")
        evidence = self._identity_issuer.issue_evidence(
            category=EvidenceCategory.IDENTITY,
            agent_id=agent_id,
            experiment_id=experiment_id,
            trace_id=trace_id,
            task_id=task_id,
            subject=agent_id,
            predicate="public_key_bound",
            asserted_value=base64.b64encode(public_key_bytes).decode("ascii"),
            scope="agent_identity",
            created_at=created_at,
            expires_at=expires_at,
            evidence_id=evidence_id,
            nonce=nonce,
            source_ref="identity_registry:proof_of_possession",
        )
        self._public_keys[agent_id] = public_key_bytes
        return evidence

    def is_registered(self, agent_id: str) -> bool:
        return agent_id in self._public_keys

    def public_key_for(self, agent_id: str) -> bytes | None:
        return self._public_keys.get(agent_id)

    def to_public_dict(self) -> dict[str, str]:
        return {
            agent_id: base64.b64encode(key).decode("ascii")
            for agent_id, key in sorted(self._public_keys.items())
        }


@dataclass(frozen=True)
class EvidenceVerificationResult:
    evidence_id: str | None
    status: EvidenceVerificationStatus
    reason: str

    @property
    def trusted(self) -> bool:
        return self.status == EvidenceVerificationStatus.VALID


class RuntimeEvidenceVerifier:
    """Verifies evidence integrity, trust policy, scope, freshness, and replay."""

    def __init__(
        self,
        policy: TrustPolicy,
        *,
        identities: AgentIdentityRegistry | None = None,
        max_age_seconds: int | None = None,
    ):
        if not isinstance(policy, TrustPolicy):
            raise ValueError("policy must be a TrustPolicy.")
        if max_age_seconds is not None:
            _timestamp(max_age_seconds, "max_age_seconds")
        self._policy = policy
        self._identities = identities
        self._max_age_seconds = max_age_seconds
        self._seen_evidence_ids: set[str] = set()
        self._seen_nonces: set[tuple[str, str]] = set()

    def verify(
        self,
        evidence: RuntimeEvidence | None,
        *,
        now: int,
        expected_agent_id: str | None = None,
        expected_experiment_id: str | None = None,
        expected_trace_id: str | None = None,
        expected_task_id: str | None = None,
        expected_message_id: str | None = None,
        expected_scope: str | None = None,
        require_actual_execution: bool = False,
        seen_evidence_ids: set[str] | None = None,
    ) -> EvidenceVerificationResult:
        _timestamp(now, "now")
        if evidence is None:
            return EvidenceVerificationResult(
                None, EvidenceVerificationStatus.MISSING, "Evidence record is missing."
            )
        if not isinstance(evidence, RuntimeEvidence):
            raise ValueError("evidence must be a RuntimeEvidence or None.")
        for value, field in (
            (expected_agent_id, "expected_agent_id"),
            (expected_experiment_id, "expected_experiment_id"),
            (expected_trace_id, "expected_trace_id"),
            (expected_task_id, "expected_task_id"),
            (expected_message_id, "expected_message_id"),
        ):
            if value is not None:
                _identifier(value, field)
        if expected_scope is not None:
            _text(expected_scope, "expected_scope")
        if seen_evidence_ids is not None and not isinstance(seen_evidence_ids, set):
            raise ValueError("seen_evidence_ids must be a set when provided.")

        def result(status: EvidenceVerificationStatus, reason: str):
            return EvidenceVerificationResult(evidence.evidence_id, status, reason)

        for actual, expected, name in (
            (evidence.agent_id, expected_agent_id, "agent"),
            (evidence.experiment_id, expected_experiment_id, "experiment"),
            (evidence.trace_id, expected_trace_id, "trace"),
            (evidence.task_id, expected_task_id, "task"),
            (evidence.message_id, expected_message_id, "message"),
        ):
            if expected is not None and actual != expected:
                return result(
                    EvidenceVerificationStatus.IDENTITY_MISMATCH,
                    f"Evidence {name} reference does not match the expected context.",
                )
        if expected_scope is not None and evidence.scope != expected_scope:
            return result(
                EvidenceVerificationStatus.SCOPE_MISMATCH,
                "Evidence scope does not match the expected scope.",
            )
        if (
            self._identities is not None
            and not self._identities.is_registered(evidence.agent_id)
        ):
            return result(
                EvidenceVerificationStatus.IDENTITY_UNREGISTERED,
                "Evidence agent has no registered public-key binding.",
            )
        if (
            self._identities is not None
            and evidence.category == EvidenceCategory.IDENTITY
            and evidence.predicate == "public_key_bound"
            and evidence.asserted_value
            != base64.b64encode(
                self._identities.public_key_for(evidence.agent_id) or b""
            ).decode("ascii")
        ):
            return result(
                EvidenceVerificationStatus.IDENTITY_MISMATCH,
                "Identity evidence does not match the registered public key.",
            )

        trusted = self._policy.find(evidence.issuer_id)
        if trusted is None:
            return result(
                EvidenceVerificationStatus.UNTRUSTED_ISSUER,
                "Issuer is not on the configured trusted-issuer allow-list.",
            )
        if evidence.category not in trusted.categories:
            return result(
                EvidenceVerificationStatus.CATEGORY_NOT_AUTHORIZED,
                "Issuer is not trusted for this evidence category.",
            )
        verify_key = _decode_b64(trusted.public_key, "trusted public_key", 32)
        signature = _decode_b64(evidence.signature, "signature", 64)
        if not EvidenceVerifier(verify_key).verify_payload(
            evidence.signing_payload(), signature
        ):
            return result(
                EvidenceVerificationStatus.INVALID_SIGNATURE,
                "Evidence signature is invalid for its canonical contents.",
            )
        if evidence.created_at > now:
            return result(
                EvidenceVerificationStatus.FUTURE_DATED,
                "Evidence creation time is in the future.",
            )
        if now >= evidence.expires_at:
            return result(
                EvidenceVerificationStatus.EXPIRED,
                "Evidence has passed its expiry time.",
            )
        if (
            self._max_age_seconds is not None
            and now - evidence.created_at > self._max_age_seconds
        ):
            return result(
                EvidenceVerificationStatus.STALE,
                "Evidence exceeds the configured maximum age.",
            )
        replay_key = (evidence.issuer_id, evidence.nonce)
        if (
            evidence.evidence_id in self._seen_evidence_ids
            or evidence.nonce in (seen_evidence_ids or set())
            or replay_key in self._seen_nonces
        ):
            return result(
                EvidenceVerificationStatus.REPLAYED,
                "Evidence ID or issuer nonce has already been accepted.",
            )
        if require_actual_execution and evidence.category == EvidenceCategory.TOOL_EXECUTION:
            if evidence.execution_status != EventExecutionStatus.RECORDED:
                return result(
                    EvidenceVerificationStatus.SIMULATED_EXECUTION,
                    "Tool evidence describes a simulation or non-execution, not actual execution.",
                )
        self._seen_evidence_ids.add(evidence.evidence_id)
        self._seen_nonces.add(replay_key)
        if seen_evidence_ids is not None:
            seen_evidence_ids.add(evidence.evidence_id)
        return result(
            EvidenceVerificationStatus.VALID,
            "Signature and configured trust, identity, scope, and freshness checks passed.",
        )

    def verify_reference(
        self,
        evidence_id: str,
        evidence_by_id: Mapping[str, RuntimeEvidence],
        **verify_options: Any,
    ) -> EvidenceVerificationResult:
        _identifier(evidence_id, "evidence_id")
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            return EvidenceVerificationResult(
                evidence_id,
                EvidenceVerificationStatus.MISSING,
                "Referenced evidence record is not available.",
            )
        if evidence.evidence_id != evidence_id:
            return EvidenceVerificationResult(
                evidence_id,
                EvidenceVerificationStatus.IDENTITY_MISMATCH,
                "Evidence store key does not match the record's evidence_id.",
            )
        return self.verify(evidence, **verify_options)


def validate_message_evidence_links(
    messages: Iterable[Any],
    evidence_by_id: Mapping[str, RuntimeEvidence],
) -> None:
    """Check that message evidence references resolve to the same runtime context."""
    for message in messages:
        for evidence_id in message.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                raise ValueError(f"message {message.message_id} references missing evidence.")
            if evidence.agent_id != message.agent_id:
                raise ValueError(
                    f"evidence {evidence_id} belongs to a different agent than its message."
                )
            if evidence.trace_id is not None and evidence.trace_id != message.trace_id:
                raise ValueError(f"evidence {evidence_id} references a different trace.")
            if evidence.task_id is not None and evidence.task_id != message.task_id:
                raise ValueError(f"evidence {evidence_id} references a different task.")
            if evidence.message_id is not None and evidence.message_id != message.message_id:
                raise ValueError(f"evidence {evidence_id} references a different message.")
            for assertion in message.assertions:
                if evidence_id in assertion.evidence_refs and (
                    evidence.assertion_id is not None
                    and evidence.assertion_id != assertion.assertion_id
                ):
                    raise ValueError(
                        f"evidence {evidence_id} references a different assertion."
                    )
