"""Evidence-bounded provenance claim checks for detector-safe runtime inputs."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Mapping

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from dataset.experiment_schemas import (
    ClaimAssertion,
    ClaimEvidence,
    ClaimEvidenceType,
    ClaimSubjectType,
    DetectorProvenanceClaim,
    DetectorTaskInput,
    EventExecutionStatus,
    EventSource,
    IdentityBinding,
    InteractionTrace,
    TraceEventType,
)
from dataset.trace_store import read_traces_jsonl


class ClaimOutcome(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DetectionRule(StrEnum):
    TRUSTED_ATTESTATION_MATCH = "trusted_attestation_match"
    TRUSTED_ATTESTATION_CONTRADICTION = "trusted_attestation_contradiction"
    SIGNATURE_INVALID = "signature_invalid"
    ISSUER_UNTRUSTED = "issuer_untrusted"
    EVIDENCE_IDENTITY_MISMATCH = "evidence_identity_mismatch"
    IDENTITY_BINDING_MISSING = "identity_binding_missing"
    EVIDENCE_EXPIRED_OR_STALE = "evidence_expired_or_stale"
    EVIDENCE_REPLAYED = "evidence_replayed"
    EVIDENCE_MISSING = "evidence_missing"
    SELF_CLAIM_ONLY = "self_claim_only"
    TRACE_INVOCATION_MATCH = "trace_invocation_match"
    TRACE_INVOCATION_CONTRADICTION = "trace_invocation_contradiction"
    TRACE_INVOCATION_SIMULATED = "trace_invocation_simulated"
    COMPLETION_EVENT_MATCH = "completion_event_match"
    COMPLETION_EVENT_CONTRADICTION = "completion_event_contradiction"
    OBSERVATION_MISSING = "observation_missing"
    TRACE_REFERENCE_INVALID = "trace_reference_invalid"
    NO_SUPPORTING_EVIDENCE = "no_supporting_evidence"


@dataclass(frozen=True)
class SignedEvidenceEnvelope:
    """Signed assertion-specific evidence; signatures attest bytes, not objective truth."""

    evidence_id: str
    assertion_id: str
    agent_id: str
    evidence_type: ClaimEvidenceType
    claim_type: ClaimSubjectType
    verified_value: str
    issuer: str
    issued_at: int
    expires_at: int | None
    signature: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.evidence_id, "evidence_id"),
            (self.assertion_id, "assertion_id"),
            (self.agent_id, "agent_id"),
            (self.verified_value, "verified_value"),
            (self.issuer, "issuer"),
            (self.signature, "signature"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string.")
        if self.evidence_type not in (
            ClaimEvidenceType.ISSUER_ATTESTED,
            ClaimEvidenceType.EXTERNALLY_BENCHMARKED,
        ):
            raise ValueError("Signed envelopes must be issuer-attested or externally benchmarked.")
        if not isinstance(self.claim_type, ClaimSubjectType):
            raise ValueError("claim_type must be a ClaimSubjectType.")
        if not isinstance(self.issued_at, int) or isinstance(self.issued_at, bool) or self.issued_at < 0:
            raise ValueError("issued_at must be a non-negative integer.")
        if self.expires_at is not None and (
            not isinstance(self.expires_at, int)
            or isinstance(self.expires_at, bool)
            or self.expires_at <= self.issued_at
        ):
            raise ValueError("expires_at must be later than issued_at.")
        try:
            base64.b64decode(self.signature, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("signature must be valid base64.") from exc

    def signing_payload(self) -> bytes:
        fields = {
            "agent_id": self.agent_id,
            "assertion_id": self.assertion_id,
            "claim_type": self.claim_type.value,
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type.value,
            "expires_at": self.expires_at,
            "issued_at": self.issued_at,
            "issuer": self.issuer,
            "verified_value": self.verified_value,
        }
        return json.dumps(
            fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    @classmethod
    def sign(
        cls,
        *,
        signing_key: SigningKey,
        evidence_id: str,
        assertion_id: str,
        agent_id: str,
        evidence_type: ClaimEvidenceType,
        claim_type: ClaimSubjectType,
        verified_value: str,
        issuer: str,
        issued_at: int,
        expires_at: int | None,
    ) -> "SignedEvidenceEnvelope":
        unsigned = cls(
            evidence_id=evidence_id,
            assertion_id=assertion_id,
            agent_id=agent_id,
            evidence_type=evidence_type,
            claim_type=claim_type,
            verified_value=verified_value,
            issuer=issuer,
            issued_at=issued_at,
            expires_at=expires_at,
            signature=base64.b64encode(bytes(64)).decode("ascii"),
        )
        signature = signing_key.sign(unsigned.signing_payload()).signature
        return cls(
            evidence_id=evidence_id,
            assertion_id=assertion_id,
            agent_id=agent_id,
            evidence_type=evidence_type,
            claim_type=claim_type,
            verified_value=verified_value,
            issuer=issuer,
            issued_at=issued_at,
            expires_at=expires_at,
            signature=base64.b64encode(signature).decode("ascii"),
        )


@dataclass(frozen=True)
class ClaimFinding:
    """Finding for one assertion; ``claim_id`` remains a deprecated alias."""

    # Deprecated compatibility alias: use assertion_id.
    claim_id: str
    assertion_id: str
    provenance_claim_id: str
    agent_id: str
    claim_type: ClaimSubjectType
    asserted_value: str
    predicted_outcome: ClaimOutcome
    evidence_examined: tuple[str, ...]
    detection_reason: str
    rule: DetectionRule


@dataclass(frozen=True)
class DetectionReport:
    trace_id: str
    task_id: str
    predicted_outcome: ClaimOutcome
    findings: tuple[ClaimFinding, ...]
    unassessed_agent_ids: tuple[str, ...]
    missing_information: tuple[str, ...]


_BOUND_IDENTITY = {
    IdentityBinding.ISSUER_ATTESTED,
    IdentityBinding.CRYPTOGRAPHICALLY_BOUND,
}


def _atomic_assertions(claim: DetectorProvenanceClaim) -> tuple[ClaimAssertion, ...]:
    assertions = list(claim.assertions)
    declared = {(item.claim_type, item.asserted_value) for item in assertions}
    defaults = [
        (ClaimSubjectType.IDENTITY, claim.agent_id, f"{claim.claim_id}:derived:identity"),
        (ClaimSubjectType.ROLE, claim.claimed_role, f"{claim.claim_id}:derived:role"),
    ]
    defaults.extend(
        (
            ClaimSubjectType.CAPABILITY,
            capability,
            f"{claim.claim_id}:derived:capability:{index}",
        )
        for index, capability in enumerate(claim.claimed_capabilities)
    )
    for claim_type, value, assertion_id in defaults:
        if (claim_type, value) not in declared:
            assertions.append(ClaimAssertion(assertion_id, claim_type, value))
    return tuple(assertions)


class ProvenanceDetector:
    """Deterministic checks over detector-safe claims, evidence and trace events."""

    def __init__(
        self,
        trusted_issuers: Mapping[str, VerifyKey | bytes],
        *,
        max_evidence_age_seconds: int | None = 86_400,
    ) -> None:
        if max_evidence_age_seconds is not None and (
            not isinstance(max_evidence_age_seconds, int)
            or isinstance(max_evidence_age_seconds, bool)
            or max_evidence_age_seconds < 0
        ):
            raise ValueError("max_evidence_age_seconds must be non-negative or None.")
        self.trusted_issuers = {
            issuer: key if isinstance(key, VerifyKey) else VerifyKey(key)
            for issuer, key in trusted_issuers.items()
        }
        self.max_evidence_age_seconds = max_evidence_age_seconds

    def detect(
        self,
        task: DetectorTaskInput,
        trace: InteractionTrace,
        claims: tuple[DetectorProvenanceClaim, ...] = (),
        *,
        signed_evidence: tuple[SignedEvidenceEnvelope, ...] = (),
        now: int,
        seen_evidence_ids: set[str] | None = None,
    ) -> DetectionReport:
        if not isinstance(task, DetectorTaskInput):
            raise TypeError("Detector requires a detector-safe DetectorTaskInput.")
        if not isinstance(trace, InteractionTrace):
            raise TypeError("Detector requires a validated InteractionTrace.")
        trace = self._validate_trace(trace)
        if trace.task_id != task.task_id:
            raise ValueError("trace task_id does not match detector task input.")
        if not isinstance(now, int) or isinstance(now, bool) or now < 0:
            raise ValueError("now must be a non-negative Unix timestamp.")
        if not isinstance(claims, tuple) or any(
            not isinstance(claim, DetectorProvenanceClaim) for claim in claims
        ):
            raise TypeError(
                "Detector accepts only DetectorProvenanceClaim values; use detector_view() "
                "to exclude evaluator-only assessments."
            )
        if not isinstance(signed_evidence, tuple) or any(
            not isinstance(item, SignedEvidenceEnvelope) for item in signed_evidence
        ):
            raise TypeError("signed_evidence must contain SignedEvidenceEnvelope items.")
        if len({item.evidence_id for item in signed_evidence}) != len(signed_evidence):
            raise ValueError("signed evidence IDs must be unique.")
        if seen_evidence_ids is not None and not isinstance(seen_evidence_ids, set):
            raise TypeError("seen_evidence_ids must be a mutable set of evidence IDs.")
        if seen_evidence_ids is not None and any(
            not isinstance(value, str) or not value.strip()
            for value in seen_evidence_ids
        ):
            raise ValueError("seen_evidence_ids must contain non-empty strings.")
        if len({claim.claim_id for claim in claims}) != len(claims):
            raise ValueError("claim IDs must be unique within a detector call.")
        known_agents = {agent.agent_id for agent in task.agents}
        if any(claim.agent_id not in known_agents for claim in claims):
            raise ValueError("every claim agent_id must exist in the detector task.")

        envelope_by_id = {item.evidence_id: item for item in signed_evidence}
        evidence_by_claim = {
            claim.claim_id: {item.evidence_id: item for item in claim.supporting_evidence}
            for claim in claims
        }
        all_evidence_ids = {
            evidence_id
            for items in evidence_by_claim.values()
            for evidence_id in items
        }
        if set(envelope_by_id) - all_evidence_ids:
            raise ValueError("signed evidence must be referenced by a detector claim.")

        seen = seen_evidence_ids if seen_evidence_ids is not None else set()
        used_in_report: set[str] = set()
        findings: list[ClaimFinding] = []
        for claim in claims:
            references = evidence_by_claim[claim.claim_id]
            for assertion in _atomic_assertions(claim):
                refs = assertion.supporting_evidence_ids or tuple(
                    evidence_id
                    for evidence_id, reference in references.items()
                    if (
                        evidence_id in envelope_by_id
                        and envelope_by_id[evidence_id].assertion_id == assertion.assertion_id
                    )
                    or (
                        assertion.claim_type == ClaimSubjectType.TOOL_USAGE
                        and reference.evidence_type == ClaimEvidenceType.RUNTIME_OBSERVED
                    )
                )
                refs = tuple(sorted(set(refs)))
                finding = self._evaluate(
                    claim,
                    assertion,
                    refs,
                    references,
                    envelope_by_id,
                    trace,
                    now,
                    seen,
                    used_in_report,
                )
                findings.append(finding)

        if not findings:
            outcome = ClaimOutcome.INSUFFICIENT_EVIDENCE
            missing = ("No structured provenance claims were present in the trace input.",)
        else:
            outcomes = {finding.predicted_outcome for finding in findings}
            if ClaimOutcome.CONTRADICTED in outcomes:
                outcome = ClaimOutcome.CONTRADICTED
            elif ClaimOutcome.UNSUPPORTED in outcomes:
                outcome = ClaimOutcome.UNSUPPORTED
            elif ClaimOutcome.INSUFFICIENT_EVIDENCE in outcomes:
                outcome = ClaimOutcome.INSUFFICIENT_EVIDENCE
            else:
                outcome = ClaimOutcome.SUPPORTED
            missing = tuple(
                f"{finding.agent_id}/{finding.assertion_id}: {finding.detection_reason}"
                for finding in findings
                if finding.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE
            )
        claim_agents = {claim.agent_id for claim in claims}
        return DetectionReport(
            trace_id=trace.trace_id,
            task_id=task.task_id,
            predicted_outcome=outcome,
            findings=tuple(findings),
            unassessed_agent_ids=tuple(
                agent.agent_id for agent in task.agents if agent.agent_id not in claim_agents
            ),
            missing_information=missing,
        )

    def detect_jsonl(
        self,
        trace_path: str,
        task: DetectorTaskInput,
        trace_id: str,
        claims: tuple[DetectorProvenanceClaim, ...] = (),
        *,
        signed_evidence: tuple[SignedEvidenceEnvelope, ...] = (),
        now: int,
        seen_evidence_ids: set[str] | None = None,
    ) -> DetectionReport:
        traces = read_traces_jsonl(trace_path)
        matches = [trace for trace in traces if trace.trace_id == trace_id]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one trace with trace_id {trace_id!r}.")
        return self.detect(
            task,
            matches[0],
            claims,
            signed_evidence=signed_evidence,
            now=now,
            seen_evidence_ids=seen_evidence_ids,
        )

    @staticmethod
    def _validate_trace(trace: InteractionTrace) -> InteractionTrace:
        try:
            return InteractionTrace(
                trace_id=trace.trace_id,
                task_id=trace.task_id,
                interaction_mode=trace.interaction_mode,
                events=trace.events,
                execution_mode=trace.execution_mode,
                model_id=trace.model_id,
                model_version=trace.model_version,
                prompt_version=trace.prompt_version,
                prompt_hash=trace.prompt_hash,
                tool_registry_version=trace.tool_registry_version,
                tool_registry_hash=trace.tool_registry_hash,
                config_hash=trace.config_hash,
                seed=trace.seed,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Trace failed structural validation.") from exc

    def _evaluate(
        self,
        claim: DetectorProvenanceClaim,
        assertion: ClaimAssertion,
        refs: tuple[str, ...],
        references: dict[str, ClaimEvidence],
        envelopes: dict[str, SignedEvidenceEnvelope],
        trace: InteractionTrace,
        now: int,
        seen: set[str],
        used_in_report: set[str],
    ) -> ClaimFinding:
        examined = set(refs)
        evidence_results: list[
            tuple[ClaimOutcome, DetectionRule, str]
        ] = []
        for evidence_id in refs:
            reference = references.get(evidence_id)
            if reference is None:
                evidence_results.append((
                    ClaimOutcome.INSUFFICIENT_EVIDENCE,
                    DetectionRule.EVIDENCE_MISSING,
                    f"Referenced evidence {evidence_id} is not present in the claim record.",
                ))
                continue
            envelope = envelopes.get(evidence_id)
            if reference.evidence_type == ClaimEvidenceType.SELF_CLAIMED:
                evidence_results.append((
                    ClaimOutcome.UNSUPPORTED,
                    DetectionRule.SELF_CLAIM_ONLY,
                    f"Evidence {evidence_id} is self-asserted and does not independently support the claim.",
                ))
                continue
            if reference.evidence_type == ClaimEvidenceType.RUNTIME_OBSERVED:
                runtime_finding = self._evaluate_runtime_reference(
                    claim, assertion, reference, trace
                )
                evidence_results.append(runtime_finding)
                if runtime_finding[3]:
                    examined.update(runtime_finding[3])
                continue
            if envelope is None:
                evidence_results.append((
                    ClaimOutcome.INSUFFICIENT_EVIDENCE,
                    DetectionRule.EVIDENCE_MISSING,
                    f"Signed evidence envelope {evidence_id} is unavailable.",
                ))
                continue
            binding_matches = (
                reference.evidence_type == envelope.evidence_type
                and reference.issuer == envelope.issuer
                and reference.source_ref == envelope.evidence_id
                and envelope.assertion_id == assertion.assertion_id
                and envelope.agent_id == claim.agent_id
                and envelope.claim_type == assertion.claim_type
            )
            if not binding_matches:
                evidence_results.append((
                    ClaimOutcome.UNSUPPORTED,
                    DetectionRule.EVIDENCE_IDENTITY_MISMATCH,
                    f"Evidence {evidence_id} is not bound to this reference, assertion, or agent.",
                ))
                continue
            if claim.identity_binding not in _BOUND_IDENTITY:
                evidence_results.append((
                    ClaimOutcome.UNSUPPORTED,
                    DetectionRule.IDENTITY_BINDING_MISSING,
                    f"Evidence {evidence_id} lacks the required claim identity binding.",
                ))
                continue
            key = self.trusted_issuers.get(envelope.issuer)
            if key is None:
                evidence_results.append((
                    ClaimOutcome.UNSUPPORTED,
                    DetectionRule.ISSUER_UNTRUSTED,
                    f"Evidence {evidence_id} was issued by an untrusted issuer.",
                ))
                continue
            try:
                key.verify(
                    envelope.signing_payload(),
                    base64.b64decode(envelope.signature, validate=True),
                )
            except (BadSignatureError, ValueError, TypeError):
                evidence_results.append((
                    ClaimOutcome.UNSUPPORTED,
                    DetectionRule.SIGNATURE_INVALID,
                    f"Evidence {evidence_id} has an invalid signature.",
                ))
                continue
            if (
                envelope.issued_at > now
                or (envelope.expires_at is not None and now >= envelope.expires_at)
                or (
                    self.max_evidence_age_seconds is not None
                    and now - envelope.issued_at > self.max_evidence_age_seconds
                )
            ):
                evidence_results.append((
                    ClaimOutcome.UNSUPPORTED,
                    DetectionRule.EVIDENCE_EXPIRED_OR_STALE,
                    f"Evidence {evidence_id} is expired, future-dated, or outside the freshness window.",
                ))
                continue
            if evidence_id in seen or evidence_id in used_in_report:
                evidence_results.append((
                    ClaimOutcome.UNSUPPORTED,
                    DetectionRule.EVIDENCE_REPLAYED,
                    f"Evidence ID {evidence_id} was already consumed.",
                ))
                continue

            # Consume only after all reference, assertion, identity, issuer,
            # signature, and freshness checks have succeeded.
            seen.add(evidence_id)
            used_in_report.add(evidence_id)
            if envelope.verified_value != assertion.asserted_value:
                evidence_results.append((
                    ClaimOutcome.CONTRADICTED,
                    DetectionRule.TRUSTED_ATTESTATION_CONTRADICTION,
                    f"Trusted fresh evidence {evidence_id} attests a different value.",
                ))
            else:
                evidence_results.append((
                    ClaimOutcome.SUPPORTED,
                    DetectionRule.TRUSTED_ATTESTATION_MATCH,
                    f"Trusted fresh evidence {evidence_id} matches the asserted value.",
                ))

        trace_finding = self._evaluate_trace_claim(claim, assertion, trace)
        if trace_finding is not None:
            trace_outcome, trace_reason, trace_rule, trace_refs = trace_finding
            evidence_results.append((trace_outcome, trace_rule, trace_reason))
            examined.update(trace_refs)

        examined_ids = tuple(sorted(examined))
        contradictions = [
            result for result in evidence_results
            if result[0] == ClaimOutcome.CONTRADICTED
        ]
        if contradictions:
            chosen = min(contradictions, key=lambda item: (
                _RULE_PRECEDENCE[item[1]], item[2]
            ))
            reason = "Contradictory evidence found; " + "; ".join(
                sorted({item[2] for item in contradictions})
            )
            return self._finding(
                claim, assertion, ClaimOutcome.CONTRADICTED,
                examined_ids, reason, chosen[1],
            )

        supports = [
            result for result in evidence_results
            if result[0] == ClaimOutcome.SUPPORTED
        ]
        if supports:
            chosen = min(supports, key=lambda item: (
                _RULE_PRECEDENCE[item[1]], item[2]
            ))
            reason = "Supporting evidence found; " + "; ".join(
                sorted({item[2] for item in supports})
            )
            return self._finding(
                claim, assertion, ClaimOutcome.SUPPORTED,
                examined_ids, reason, chosen[1],
            )

        unsupported = [
            result for result in evidence_results
            if result[0] == ClaimOutcome.UNSUPPORTED
        ]
        if unsupported:
            chosen = min(unsupported, key=lambda item: (
                _RULE_PRECEDENCE[item[1]], item[2]
            ))
            reason = "No valid supporting attestation was found; " + "; ".join(
                sorted({item[2] for item in unsupported})
            )
            return self._finding(
                claim, assertion, ClaimOutcome.UNSUPPORTED,
                examined_ids, reason, chosen[1],
            )

        insufficient = [
            result for result in evidence_results
            if result[0] == ClaimOutcome.INSUFFICIENT_EVIDENCE
        ]
        if insufficient:
            chosen = min(insufficient, key=lambda item: (
                _RULE_PRECEDENCE[item[1]], item[2]
            ))
            reason = "; ".join(sorted({item[2] for item in insufficient}))
            return self._finding(
                claim, assertion, ClaimOutcome.INSUFFICIENT_EVIDENCE,
                examined_ids, reason, chosen[1],
            )
        return self._finding(
            claim, assertion, ClaimOutcome.INSUFFICIENT_EVIDENCE, examined_ids,
            "No supporting evidence or relevant observed event was provided.",
            DetectionRule.OBSERVATION_MISSING,
        )

    @staticmethod
    def _evaluate_runtime_reference(
        claim: DetectorProvenanceClaim,
        assertion: ClaimAssertion,
        reference: ClaimEvidence,
        trace: InteractionTrace,
    ) -> tuple[ClaimOutcome, DetectionRule, str, tuple[str, ...]]:
        try:
            referenced_trace_id, raw_sequence = reference.source_ref.rsplit(":", 1)
            sequence = int(raw_sequence)
        except (ValueError, TypeError):
            return (
                ClaimOutcome.UNSUPPORTED,
                DetectionRule.TRACE_REFERENCE_INVALID,
                "Runtime evidence source_ref must have the form trace_id:sequence.",
                (),
            )
        event = next(
            (item for item in trace.events if item.sequence == sequence), None
        )
        if referenced_trace_id != trace.trace_id or event is None:
            return (
                ClaimOutcome.UNSUPPORTED,
                DetectionRule.TRACE_REFERENCE_INVALID,
                "Runtime evidence points to no event in the supplied trace.",
                (),
            )
        event_ref = (f"{trace.trace_id}:{event.sequence}",)
        if assertion.claim_type != ClaimSubjectType.TOOL_USAGE:
            return (
                ClaimOutcome.INSUFFICIENT_EVIDENCE,
                DetectionRule.NO_SUPPORTING_EVIDENCE,
                "This runtime event cannot independently verify an identity, role, capability, or other claim.",
                event_ref,
            )
        if (
            event.event_type != TraceEventType.TOOL_INVOCATION
            or event.tool_name != assertion.asserted_value
            or event.source != EventSource.AGENT
        ):
            return (
                ClaimOutcome.INSUFFICIENT_EVIDENCE,
                DetectionRule.NO_SUPPORTING_EVIDENCE,
                "The cited event is not a matching agent invocation of the claimed tool.",
                event_ref,
            )
        if event.execution_status != EventExecutionStatus.RECORDED:
            return (
                ClaimOutcome.INSUFFICIENT_EVIDENCE,
                DetectionRule.TRACE_INVOCATION_SIMULATED,
                "The cited invocation is simulated or not executed, not actual tool use.",
                event_ref,
            )
        if event.actor_id != claim.agent_id:
            if (
                ProvenanceDetector._trace_is_complete(trace)
                and not any(
                    observed.event_type == TraceEventType.TOOL_INVOCATION
                    and observed.source == EventSource.AGENT
                    and observed.actor_id == claim.agent_id
                    and observed.tool_name == assertion.asserted_value
                    and observed.execution_status == EventExecutionStatus.RECORDED
                    for observed in trace.events
                )
            ):
                return (
                    ClaimOutcome.CONTRADICTED,
                    DetectionRule.TRACE_INVOCATION_CONTRADICTION,
                    "The complete trace records the matching invocation for another agent, not this agent.",
                    event_ref,
                )
            return (
                ClaimOutcome.INSUFFICIENT_EVIDENCE,
                DetectionRule.NO_SUPPORTING_EVIDENCE,
                "A matching invocation is attributed to another agent, but the trace does not establish that this agent did not also invoke it.",
                event_ref,
            )
        return (
            ClaimOutcome.SUPPORTED,
            DetectionRule.TRACE_INVOCATION_MATCH,
            "A recorded trace event shows this agent invoked the claimed tool; this does not prove success.",
            event_ref,
        )

    @staticmethod
    def _trace_is_complete(trace: InteractionTrace) -> bool:
        if not trace.events:
            return False
        terminal = trace.events[-1]
        if terminal.source != EventSource.SYSTEM:
            return False
        if terminal.event_type == TraceEventType.SIMULATION_ENDED:
            return True
        return (
            terminal.event_type in (
                TraceEventType.TASK_COMPLETED,
                TraceEventType.TASK_FAILED,
            )
            and terminal.execution_status == EventExecutionStatus.RECORDED
        )

    @staticmethod
    def _evaluate_trace_claim(
        claim: DetectorProvenanceClaim,
        assertion: ClaimAssertion,
        trace: InteractionTrace,
    ) -> tuple[ClaimOutcome, str, DetectionRule, tuple[str, ...]] | None:
        if assertion.claim_type == ClaimSubjectType.TOOL_USAGE:
            invocations = [
                event for event in trace.events
                if event.event_type == TraceEventType.TOOL_INVOCATION
                and event.source == EventSource.AGENT
                and event.tool_name == assertion.asserted_value
            ]
            recorded_invocations = [
                event for event in invocations
                if event.execution_status == EventExecutionStatus.RECORDED
            ]
            agent_invocations = [
                event for event in recorded_invocations if event.actor_id == claim.agent_id
            ]
            if agent_invocations:
                event = agent_invocations[-1]
                return (
                    ClaimOutcome.SUPPORTED,
                    "A recorded trace event shows this agent invoked the claimed tool; this does not prove success.",
                    DetectionRule.TRACE_INVOCATION_MATCH,
                    (f"{trace.trace_id}:{event.sequence}",),
                )
            simulated_invocations = [
                event for event in invocations
                if event.execution_status != EventExecutionStatus.RECORDED
            ]
            if simulated_invocations:
                event = simulated_invocations[-1]
                return (
                    ClaimOutcome.INSUFFICIENT_EVIDENCE,
                    "Only simulated invocations are recorded; no actual tool use is established.",
                    DetectionRule.TRACE_INVOCATION_SIMULATED,
                    (f"{trace.trace_id}:{event.sequence}",),
                )
            if not ProvenanceDetector._trace_is_complete(trace):
                return (
                    ClaimOutcome.INSUFFICIENT_EVIDENCE,
                    "The trace has no valid terminal marker, so absence of an invocation is inconclusive.",
                    DetectionRule.OBSERVATION_MISSING,
                    (),
                )
            if recorded_invocations:
                refs = tuple(
                    f"{trace.trace_id}:{event.sequence}" for event in recorded_invocations
                )
                return (
                    ClaimOutcome.CONTRADICTED,
                    "The complete trace records the claimed tool invocation for another agent, not this agent.",
                    DetectionRule.TRACE_INVOCATION_CONTRADICTION,
                    refs,
                )
            return (
                ClaimOutcome.CONTRADICTED,
                "The complete trace contains no invocation of the claimed tool by this agent.",
                DetectionRule.TRACE_INVOCATION_CONTRADICTION,
                (),
            )

        if assertion.claim_type == ClaimSubjectType.TASK_COMPLETION:
            status_events = [
                event for event in trace.events
                if event.source == EventSource.SYSTEM
                and event.execution_status == EventExecutionStatus.RECORDED
                and event.event_type in (
                    TraceEventType.TASK_COMPLETED,
                    TraceEventType.TASK_FAILED,
                )
            ]
            if not status_events:
                return (
                    ClaimOutcome.INSUFFICIENT_EVIDENCE,
                    "No system-generated recorded task status event establishes completion or failure.",
                    DetectionRule.OBSERVATION_MISSING,
                    (),
                )
            latest = max(status_events, key=lambda event: event.sequence)
            event_ref = (f"{trace.trace_id}:{latest.sequence}",)
            if latest.event_type == TraceEventType.TASK_COMPLETED:
                return (
                    ClaimOutcome.SUPPORTED,
                    "The latest valid system task-status event records completion; answer quality is not evaluated.",
                    DetectionRule.COMPLETION_EVENT_MATCH,
                    event_ref,
                )
            return (
                ClaimOutcome.CONTRADICTED,
                "The latest valid system task-status event records failure.",
                DetectionRule.COMPLETION_EVENT_CONTRADICTION,
                event_ref,
            )
        return None

    @staticmethod
    def _finding(
        claim: DetectorProvenanceClaim,
        assertion: ClaimAssertion,
        outcome: ClaimOutcome,
        evidence_examined: tuple[str, ...],
        reason: str,
        rule: DetectionRule,
    ) -> ClaimFinding:
        return ClaimFinding(
            claim_id=assertion.assertion_id,
            assertion_id=assertion.assertion_id,
            provenance_claim_id=claim.claim_id,
            agent_id=claim.agent_id,
            claim_type=assertion.claim_type,
            asserted_value=assertion.asserted_value,
            predicted_outcome=outcome,
            evidence_examined=evidence_examined,
            detection_reason=reason,
            rule=rule,
        )


_RULE_PRECEDENCE = {
    DetectionRule.TRUSTED_ATTESTATION_CONTRADICTION: 0,
    DetectionRule.TRACE_INVOCATION_CONTRADICTION: 1,
    DetectionRule.COMPLETION_EVENT_CONTRADICTION: 2,
    DetectionRule.TRUSTED_ATTESTATION_MATCH: 10,
    DetectionRule.TRACE_INVOCATION_MATCH: 11,
    DetectionRule.COMPLETION_EVENT_MATCH: 12,
    DetectionRule.EVIDENCE_REPLAYED: 20,
    DetectionRule.SIGNATURE_INVALID: 21,
    DetectionRule.ISSUER_UNTRUSTED: 22,
    DetectionRule.EVIDENCE_IDENTITY_MISMATCH: 23,
    DetectionRule.IDENTITY_BINDING_MISSING: 24,
    DetectionRule.EVIDENCE_EXPIRED_OR_STALE: 25,
    DetectionRule.SELF_CLAIM_ONLY: 26,
    DetectionRule.TRACE_REFERENCE_INVALID: 27,
    DetectionRule.EVIDENCE_MISSING: 30,
    DetectionRule.TRACE_INVOCATION_SIMULATED: 31,
    DetectionRule.OBSERVATION_MISSING: 32,
    DetectionRule.NO_SUPPORTING_EVIDENCE: 33,
}


def evidence_reference(envelope: SignedEvidenceEnvelope) -> ClaimEvidence:
    """Create the Stage 2 reference matching a signed evidence envelope."""
    return ClaimEvidence(
        evidence_id=envelope.evidence_id,
        evidence_type=envelope.evidence_type,
        source_ref=envelope.evidence_id,
        issuer=envelope.issuer,
    )
