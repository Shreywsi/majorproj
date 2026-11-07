"""Verify Stage 7C inputs and run the existing Stage 4 provenance detector."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable

from dataset.claim_message_schemas import (
    ClaimBearingAssertion,
    ClaimBearingMessage,
    CapabilityState,
    ProvenanceClaimType,
    claim_message_from_dict,
    validate_claim_messages,
    validate_messages_against_trace,
)
from dataset.cross_message_analysis import analyze_cross_message_assertions
from dataset.experiment_schemas import (
    ClaimAssertion,
    ClaimEvidence,
    ClaimEvidenceType,
    ClaimSubjectType,
    DetectorAgentInput,
    DetectorProvenanceClaim,
    DetectorTaskInput,
    IdentityBinding,
    ObservedBehavior,
    TraceEventType,
)
from dataset.runtime_evidence import (
    AuthorityKind,
    EvidenceCategory,
    EvidenceVerificationStatus,
    RuntimeEvidence,
    RuntimeEvidenceVerifier,
    TrustPolicy,
    TrustedIssuer,
)
from dataset.trace_store import read_traces_jsonl
from provenance_detector import ClaimOutcome, ProvenanceDetector


ROOT = Path(__file__).resolve().parent
DETECTION_ROOT = ROOT / "data" / "controlled" / "detection"
_CLAIM_FIELDS = {
    "assertion_id", "provenance_claim_id", "experiment_id", "task_id",
    "trace_id", "message_id", "agent_id", "claim_type", "subject",
    "predicate", "asserted_value", "scope", "evidence_refs", "capability_state",
}
_OUTCOME_ORDER = tuple(outcome.value for outcome in ClaimOutcome)


class DetectionInputError(ValueError):
    """Malformed or ambiguously associated Stage 7C detector input."""


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} file does not exist: {path}")
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DetectionInputError(
                    f"Invalid {label} JSONL at line {line_number}: {exc}"
                ) from exc
            if not isinstance(record, dict):
                raise DetectionInputError(
                    f"{label} JSONL line {line_number} must contain an object."
                )
            records.append(record)
    return records


def _read_trust_policy(path: Path) -> TrustPolicy:
    if not path.is_file():
        raise FileNotFoundError(f"Trust policy file does not exist: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DetectionInputError(f"Unable to read trust policy: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"issuers"} or not isinstance(
        raw["issuers"], list
    ):
        raise DetectionInputError("Trust policy must contain an issuers array.")
    issuers = []
    for index, item in enumerate(raw["issuers"]):
        expected = {"issuer_id", "authority_kind", "public_key", "categories"}
        if not isinstance(item, dict) or set(item) != expected:
            raise DetectionInputError(
                f"Trust policy issuers[{index}] has missing or unknown fields."
            )
        try:
            issuers.append(TrustedIssuer(
                issuer_id=item["issuer_id"],
                authority_kind=AuthorityKind(item["authority_kind"]),
                public_key=item["public_key"],
                categories=tuple(EvidenceCategory(value) for value in item["categories"]),
            ))
        except (TypeError, ValueError, KeyError) as exc:
            raise DetectionInputError(
                f"Invalid trust policy issuer at index {index}: {exc}"
            ) from exc
    try:
        return TrustPolicy(issuers)
    except ValueError as exc:
        raise DetectionInputError(f"Invalid trust policy: {exc}") from exc


def _parse_inputs(
    traces_path: Path,
    messages_path: Path,
    claims_path: Path,
    evidence_path: Path,
    trust_path: Path,
) -> tuple[
    str,
    int | None,
    dict[str, Any],
    dict[str, Any],
    dict[str, RuntimeEvidence],
    TrustPolicy,
    dict[str, list[ClaimBearingMessage]],
]:
    traces = read_traces_jsonl(traces_path)
    trace_by_id = {trace.trace_id: trace for trace in traces}
    if not traces or len(trace_by_id) != len(traces):
        raise DetectionInputError("Trace input is empty or contains duplicate trace IDs.")

    raw_messages = _read_jsonl(messages_path, "messages")
    messages: list[ClaimBearingMessage] = []
    for index, raw in enumerate(raw_messages, start=1):
        try:
            messages.append(claim_message_from_dict(raw))
        except (TypeError, ValueError, KeyError) as exc:
            raise DetectionInputError(f"Invalid message record {index}: {exc}") from exc
    if not messages:
        raise DetectionInputError("Messages input contains no messages.")

    experiment_ids = {message.experiment_id for message in messages}
    seeds = {trace.seed for trace in traces}
    if len(experiment_ids) != 1:
        raise DetectionInputError("Messages must identify exactly one experiment.")
    experiment_id = next(iter(experiment_ids))
    message_ids = [message.message_id for message in messages]
    if len(message_ids) != len(set(message_ids)):
        raise DetectionInputError("Messages contain duplicate message IDs.")
    if any(message.trace_id not in trace_by_id for message in messages):
        unknown = sorted({message.trace_id for message in messages} - set(trace_by_id))
        raise DetectionInputError(f"Messages reference unknown traces: {', '.join(unknown)}.")
    by_trace: dict[str, list[ClaimBearingMessage]] = {}
    for trace_id, trace in trace_by_id.items():
        local = [message for message in messages if message.trace_id == trace_id]
        if not local:
            raise DetectionInputError(f"Trace {trace_id} has no associated messages.")
        if any(message.task_id != trace.task_id for message in local):
            raise DetectionInputError(f"Messages for trace {trace_id} have mismatched task IDs.")
        agents = {message.agent_id for message in local}
        try:
            validate_claim_messages(
                local,
                experiment_id=experiment_id,
                trace_id=trace_id,
                task_id=trace.task_id,
                agent_ids=agents,
            )
            validate_messages_against_trace(
                local,
                trace,
                experiment_id=experiment_id,
                agent_ids=agents,
            )
        except ValueError as exc:
            raise DetectionInputError(
                f"Invalid message associations for trace {trace_id}: {exc}"
            ) from exc
        by_trace[trace_id] = local

    raw_claims = _read_jsonl(claims_path, "claims")
    claim_by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_claims, start=1):
        if set(raw) != _CLAIM_FIELDS:
            raise DetectionInputError(
                f"Claim record {index} has missing or unsupported fields."
            )
        assertion_id = raw.get("assertion_id")
        if not isinstance(assertion_id, str) or not assertion_id:
            raise DetectionInputError(f"Claim record {index} has no assertion_id.")
        if assertion_id in claim_by_id:
            raise DetectionInputError(f"Duplicate assertion_id in claims: {assertion_id}.")
        claim_by_id[assertion_id] = raw
    message_assertions = {
        assertion.assertion_id: (message, assertion)
        for message in messages
        for assertion in message.assertions
    }
    if len(message_assertions) != sum(
        len(message.assertions) for message in messages
    ):
        raise DetectionInputError("Messages contain duplicate assertion IDs.")
    if set(claim_by_id) != set(message_assertions):
        missing = sorted(set(message_assertions) - set(claim_by_id))
        extra = sorted(set(claim_by_id) - set(message_assertions))
        raise DetectionInputError(
            f"Claims/messages assertion IDs differ; missing={missing}, extra={extra}."
        )
    for assertion_id, raw in claim_by_id.items():
        message, assertion = message_assertions[assertion_id]
        expected = {
            "provenance_claim_id": assertion.provenance_claim_id,
            "experiment_id": message.experiment_id,
            "task_id": message.task_id,
            "trace_id": message.trace_id,
            "message_id": message.message_id,
            "agent_id": assertion.agent_id,
            "claim_type": assertion.claim_type.value,
            "subject": assertion.subject,
            "predicate": assertion.predicate,
            "asserted_value": assertion.asserted_value,
            "scope": assertion.scope,
            "evidence_refs": list(assertion.evidence_refs),
            "capability_state": (
                assertion.capability_state.value
                if assertion.capability_state is not None
                else None
            ),
        }
        if any(raw.get(key) != value for key, value in expected.items()):
            raise DetectionInputError(
                f"Claim {assertion_id} disagrees with its structured message."
            )
        if raw["experiment_id"] != experiment_id:
            raise DetectionInputError(f"Claim {assertion_id} has a mismatched experiment ID.")
    provenance_claim_owners: dict[str, tuple[str, str, str]] = {}
    for assertion_id, raw in claim_by_id.items():
        owner = (raw["agent_id"], raw["trace_id"], raw["task_id"])
        previous = provenance_claim_owners.setdefault(raw["provenance_claim_id"], owner)
        if previous != owner:
            raise DetectionInputError(
                f"Provenance claim {raw['provenance_claim_id']} is associated with "
                "multiple agents, traces, or tasks."
            )

    raw_evidence = _read_jsonl(evidence_path, "evidence")
    evidence_by_id: dict[str, RuntimeEvidence] = {}
    for index, raw in enumerate(raw_evidence, start=1):
        try:
            evidence = RuntimeEvidence.from_dict(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise DetectionInputError(f"Invalid evidence record {index}: {exc}") from exc
        if evidence.evidence_id in evidence_by_id:
            raise DetectionInputError(
                f"Duplicate evidence_id: {evidence.evidence_id}."
            )
        if evidence.experiment_id != experiment_id:
            raise DetectionInputError(
                f"Evidence {evidence.evidence_id} has a mismatched experiment ID."
            )
        if evidence.trace_id is not None and evidence.trace_id not in trace_by_id:
            raise DetectionInputError(
                f"Evidence {evidence.evidence_id} references unknown trace {evidence.trace_id}."
            )
        if (
            evidence.trace_id is not None
            and evidence.task_id is not None
            and trace_by_id[evidence.trace_id].task_id != evidence.task_id
        ):
            raise DetectionInputError(
                f"Evidence {evidence.evidence_id} has a trace/task mismatch."
            )
        evidence_by_id[evidence.evidence_id] = evidence
    for message in messages:
        for assertion in message.assertions:
            if not set(assertion.evidence_refs).issubset(message.evidence_ids):
                raise DetectionInputError(
                    f"Assertion {assertion.assertion_id} references evidence not declared "
                    "by its message."
                )

    policy = _read_trust_policy(trust_path)
    return (
        experiment_id,
        next(iter(seeds)) if len(seeds) == 1 else None,
        trace_by_id,
        claim_by_id,
        evidence_by_id,
        policy,
        by_trace,
    )


def _inferred_now(evidence: Iterable[RuntimeEvidence]) -> int:
    timestamps = [item.created_at for item in evidence]
    return max(timestamps, default=0) + 1


def _evidence_results(
    *,
    evidence_by_id: dict[str, RuntimeEvidence],
    policy: TrustPolicy,
    trace_by_id: dict[str, Any],
    experiment_id: str,
    now: int,
) -> dict[str, dict[str, Any]]:
    verifier = RuntimeEvidenceVerifier(policy)
    results: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for evidence_id, evidence in sorted(evidence_by_id.items()):
        result = verifier.verify(
            evidence,
            now=now,
            expected_agent_id=evidence.agent_id,
            expected_experiment_id=experiment_id,
            expected_trace_id=evidence.trace_id,
            expected_task_id=evidence.task_id,
            require_actual_execution=False,
            seen_evidence_ids=seen,
        )
        status = result.status
        reason = result.reason
        trace = trace_by_id.get(evidence.trace_id)
        if (
            status == EvidenceVerificationStatus.VALID
            and evidence.source_event_sequence is not None
            and (
                trace is None
                or evidence.source_event_sequence >= len(trace.events)
            )
        ):
            status = EvidenceVerificationStatus.SCOPE_MISMATCH
            reason = "Evidence source event does not resolve in its associated trace."
        elif (
            status == EvidenceVerificationStatus.VALID
            and trace is not None
            and evidence.task_id != trace.task_id
        ):
            status = EvidenceVerificationStatus.SCOPE_MISMATCH
            reason = "Evidence task ID does not match its associated trace."
        elif (
            status == EvidenceVerificationStatus.VALID
            and evidence.category == EvidenceCategory.TOOL_EXECUTION
            and (
                evidence.source_event_sequence is None
                or trace.events[evidence.source_event_sequence].event_type
                != TraceEventType.TOOL_INVOCATION
            )
        ):
            status = EvidenceVerificationStatus.SCOPE_MISMATCH
            reason = "Tool evidence does not reference a tool invocation event."
        results[evidence_id] = {
            "evidence_id": evidence_id,
            "status": status.value,
            "reason": reason,
        }
    return results


def _assertion_evidence_result(
    assertion: ClaimBearingAssertion,
    message: ClaimBearingMessage,
    evidence: RuntimeEvidence | None,
    verification: dict[str, Any] | None,
) -> dict[str, Any]:
    if evidence is None:
        return {
            "status": EvidenceVerificationStatus.MISSING.value,
            "reason": "Referenced evidence record is missing.",
        }
    if evidence.agent_id != assertion.agent_id:
        return {
            "status": EvidenceVerificationStatus.IDENTITY_MISMATCH.value,
            "reason": "Evidence agent does not match the assertion agent.",
        }
    if (
        evidence.experiment_id != message.experiment_id
        or evidence.trace_id != message.trace_id
        or evidence.task_id != message.task_id
        or (evidence.message_id is not None and evidence.message_id != message.message_id)
        or (evidence.assertion_id is not None and evidence.assertion_id != assertion.assertion_id)
    ):
        return {
            "status": EvidenceVerificationStatus.SCOPE_MISMATCH.value,
            "reason": "Evidence experiment, trace, task, message, or assertion association mismatches.",
        }
    if evidence.scope != assertion.scope:
        return {
            "status": EvidenceVerificationStatus.SCOPE_MISMATCH.value,
            "reason": "Evidence scope does not match the assertion scope.",
        }
    if verification is None or verification["status"] != EvidenceVerificationStatus.VALID.value:
        return verification or {
            "status": EvidenceVerificationStatus.MISSING.value,
            "reason": "Evidence verification result is unavailable.",
        }

    relevant_categories = {
        ProvenanceClaimType.IDENTITY: {EvidenceCategory.IDENTITY},
        ProvenanceClaimType.ROLE: {EvidenceCategory.ROLE},
        ProvenanceClaimType.CAPABILITY: (
            {EvidenceCategory.CONFIGURED_PERMISSION}
            if assertion.capability_state == CapabilityState.CONFIGURED_PERMISSION
            else {
                EvidenceCategory.DEMONSTRATED_CAPABILITY,
                EvidenceCategory.TOOL_EXECUTION,
            }
        ),
        ProvenanceClaimType.TOOL_USAGE: {EvidenceCategory.TOOL_EXECUTION},
        ProvenanceClaimType.TASK_COMPLETION: {EvidenceCategory.TASK_COMPLETION},
        ProvenanceClaimType.EVIDENCE_SOURCE: set(EvidenceCategory),
    }
    if evidence.category not in relevant_categories[assertion.claim_type]:
        return {
            "status": "evidence_not_relevant",
            "reason": (
                f"{evidence.category.value} evidence does not establish "
                f"{assertion.claim_type.value}."
            ),
        }
    if assertion.claim_type == ProvenanceClaimType.EVIDENCE_SOURCE:
        value = assertion.asserted_value
        if not isinstance(value, dict) or (
            value.get("evidence_id") != evidence.evidence_id
            or value.get("source_ref") != evidence.source_ref
        ):
            return {
                "status": "evidence_relationship_mismatch",
                "reason": "The assertion misrepresents the referenced evidence source.",
            }
    return verification


def _adapt_value(assertion: ClaimBearingAssertion) -> tuple[ClaimSubjectType, str]:
    mapping = {
        ProvenanceClaimType.IDENTITY: ClaimSubjectType.IDENTITY,
        ProvenanceClaimType.ROLE: ClaimSubjectType.ROLE,
        ProvenanceClaimType.CAPABILITY: ClaimSubjectType.CAPABILITY,
        ProvenanceClaimType.TOOL_USAGE: ClaimSubjectType.TOOL_USAGE,
        ProvenanceClaimType.TASK_COMPLETION: ClaimSubjectType.TASK_COMPLETION,
        ProvenanceClaimType.EVIDENCE_SOURCE: ClaimSubjectType.PROVENANCE,
    }
    claim_type = mapping[assertion.claim_type]
    value = assertion.asserted_value
    if assertion.claim_type == ProvenanceClaimType.TOOL_USAGE:
        value = assertion.subject
    elif assertion.claim_type == ProvenanceClaimType.CAPABILITY:
        value = assertion.subject
    elif assertion.claim_type == ProvenanceClaimType.TASK_COMPLETION:
        if value is False:
            # Stage 4 interprets TASK_COMPLETION as a positive completion claim.
            claim_type = ClaimSubjectType.PROVENANCE
            value = f"{assertion.subject}:completed=false"
        else:
            value = str(value).lower()
    elif isinstance(value, (dict, list, bool)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    elif not isinstance(value, str):
        value = str(value)
    return claim_type, value


def _adapt_evidence(
    evidence_id: str,
    evidence_by_id: dict[str, RuntimeEvidence],
    evidence_results: dict[str, dict[str, Any]],
    contextual_result: dict[str, Any],
    trace_id: str,
) -> ClaimEvidence:
    evidence = evidence_by_id.get(evidence_id)
    if evidence is None:
        return ClaimEvidence(
            evidence_id=evidence_id,
            evidence_type=ClaimEvidenceType.ISSUER_ATTESTED,
            source_ref=evidence_id,
            integrity_verified=False,
        )
    result = contextual_result
    valid = result["status"] == EvidenceVerificationStatus.VALID.value
    if not valid:
        return ClaimEvidence(
            evidence_id=evidence_id,
            evidence_type=ClaimEvidenceType.SELF_CLAIMED,
            source_ref=evidence_id,
            issuer=evidence.issuer_id,
            integrity_verified=False,
        )
    if (
        evidence.category == EvidenceCategory.TOOL_EXECUTION
        and evidence.trace_id == trace_id
        and evidence.source_event_sequence is not None
    ):
        return ClaimEvidence(
            evidence_id=evidence_id,
            evidence_type=ClaimEvidenceType.RUNTIME_OBSERVED,
            source_ref=f"{trace_id}:{evidence.source_event_sequence}",
            issuer=evidence.issuer_id,
            integrity_verified=True,
        )
    return ClaimEvidence(
        evidence_id=evidence_id,
        evidence_type=ClaimEvidenceType.ISSUER_ATTESTED,
        source_ref=evidence_id,
        issuer=evidence.issuer_id,
        integrity_verified=True,
    )


def _build_detector_inputs(
    messages: list[ClaimBearingMessage],
    trace: Any,
    claim_records: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, RuntimeEvidence],
    evidence_results: dict[str, dict[str, Any]],
    contextual_results: dict[tuple[str, str], dict[str, Any]],
) -> tuple[DetectorTaskInput, tuple[DetectorProvenanceClaim, ...]]:
    agents: dict[str, DetectorAgentInput] = {}
    assertions_by_claim: dict[str, list[ClaimAssertion]] = {}
    refs_by_claim: dict[str, dict[str, ClaimEvidence]] = {}
    behavior_by_claim: dict[str, list[ObservedBehavior]] = {}
    identity_by_claim: dict[str, IdentityBinding] = {}
    role_by_claim: dict[str, str] = {}
    capabilities_by_claim: dict[str, set[str]] = {}

    for message in messages:
        agents.setdefault(
            message.agent_id,
            DetectorAgentInput(
                agent_id=message.agent_id,
                claimed_role=message.claimed_role,
                claimed_description="Structured claim-bearing agent.",
            ),
        )
        for assertion in message.assertions:
            record = claim_records[assertion.assertion_id]
            claim_id = assertion.provenance_claim_id
            claim_type, value = _adapt_value(assertion)
            assertions_by_claim.setdefault(claim_id, []).append(ClaimAssertion(
                assertion_id=assertion.assertion_id,
                claim_type=claim_type,
                asserted_value=value,
                supporting_evidence_ids=assertion.evidence_refs,
            ))
            refs = refs_by_claim.setdefault(claim_id, {})
            for evidence_id in assertion.evidence_refs:
                refs.setdefault(
                    evidence_id,
                    _adapt_evidence(
                        evidence_id,
                        evidence_by_id,
                        evidence_results,
                        contextual_results[(assertion.assertion_id, evidence_id)],
                        message.trace_id,
                    ),
                )
            behavior_by_claim.setdefault(claim_id, []).append(ObservedBehavior(
                trace_id=message.trace_id,
                event_sequences=(message.sequence_number,),
                summary=f"Claim-bearing message {message.message_id}.",
            ))
            role_by_claim[claim_id] = message.claimed_role
            capabilities_by_claim.setdefault(claim_id, set())
            if assertion.claim_type == ProvenanceClaimType.CAPABILITY:
                capabilities_by_claim[claim_id].add(assertion.subject)
            if (
                assertion.claim_type == ProvenanceClaimType.IDENTITY
                and assertion.evidence_refs
                and any(
                    evidence_by_id.get(evidence_id) is not None
                    and evidence_by_id[evidence_id].category == EvidenceCategory.IDENTITY
                    and evidence_results[evidence_id]["status"]
                    == EvidenceVerificationStatus.VALID.value
                    for evidence_id in assertion.evidence_refs
                )
            ):
                identity_by_claim[claim_id] = IdentityBinding.ISSUER_ATTESTED
            else:
                identity_by_claim.setdefault(claim_id, IdentityBinding.UNVERIFIED)

    task_submitted = next(
        (
            event.content for event in trace.events
            if event.event_type == TraceEventType.TASK_SUBMITTED
            and event.content is not None
        ),
        "Controlled claim-bearing task.",
    )
    task = DetectorTaskInput(
        task_id=trace.task_id,
        scenario="controlled_claim_bearing",
        user_query=task_submitted,
        agents=tuple(agents[key] for key in sorted(agents)),
    )
    claims = tuple(
        DetectorProvenanceClaim(
            claim_id=claim_id,
            agent_id=claim_records[assertions_by_claim[claim_id][0].assertion_id]["agent_id"],
            identity_binding=identity_by_claim[claim_id],
            claimed_role=role_by_claim[claim_id],
            claimed_capabilities=tuple(sorted(capabilities_by_claim[claim_id] or {"unspecified"})),
            supporting_evidence=tuple(
                refs_by_claim[claim_id][key] for key in sorted(refs_by_claim[claim_id])
            ),
            observed_behavior=tuple(behavior_by_claim[claim_id]),
            assertions=(
                *assertions_by_claim[claim_id],
                ClaimAssertion(
                    assertion_id=f"{claim_id}:derived:identity",
                    claim_type=ClaimSubjectType.IDENTITY,
                    asserted_value=claim_records[
                        assertions_by_claim[claim_id][0].assertion_id
                    ]["agent_id"],
                ),
                ClaimAssertion(
                    assertion_id=f"{claim_id}:derived:role",
                    claim_type=ClaimSubjectType.ROLE,
                    asserted_value=role_by_claim[claim_id],
                ),
                *(
                    ClaimAssertion(
                        assertion_id=f"{claim_id}:derived:capability:{index}",
                        claim_type=ClaimSubjectType.CAPABILITY,
                        asserted_value=capability,
                    )
                    for index, capability in enumerate(
                        sorted(capabilities_by_claim[claim_id])
                    )
                ),
            ),
        )
        for claim_id in sorted(assertions_by_claim)
    )
    return task, claims


def detect_claim_bearing_experiment(
    *,
    traces_path: str | Path,
    messages_path: str | Path,
    claims_path: str | Path,
    evidence_path: str | Path,
    trust_policy_path: str | Path,
    now: int | None = None,
) -> dict[str, Any]:
    """Run Stage 4 against validated Stage 7C runtime inputs only."""
    paths = [Path(path) for path in (
        traces_path, messages_path, claims_path, evidence_path, trust_policy_path
    )]
    (
        experiment_id,
        seed,
        trace_by_id,
        claim_records,
        evidence_by_id,
        policy,
        messages_by_trace,
    ) = _parse_inputs(*paths)
    effective_now = _inferred_now(evidence_by_id.values()) if now is None else now
    if not isinstance(effective_now, int) or isinstance(effective_now, bool) or effective_now < 0:
        raise ValueError("now must be a non-negative Unix timestamp.")
    evidence_results = _evidence_results(
        evidence_by_id=evidence_by_id,
        policy=policy,
        trace_by_id=trace_by_id,
        experiment_id=experiment_id,
        now=effective_now,
    )
    contextual_results: dict[tuple[str, str], dict[str, Any]] = {}
    for messages in messages_by_trace.values():
        for message in messages:
            for assertion in message.assertions:
                for evidence_id in assertion.evidence_refs:
                    contextual_results[(assertion.assertion_id, evidence_id)] = (
                        _assertion_evidence_result(
                            assertion,
                            message,
                            evidence_by_id.get(evidence_id),
                            evidence_results.get(evidence_id),
                        )
                    )
    detector = ProvenanceDetector({})
    # Stage 7B evidence has a different signed payload from Stage 4 envelopes.
    # Its trust/freshness result gates evidence states above; Stage 4 still owns
    # all primary outcome decisions and is never given evaluator-only data.
    findings: list[dict[str, Any]] = []
    seen_evidence_ids: set[str] = set()
    for trace_id in sorted(trace_by_id):
        trace = trace_by_id[trace_id]
        local_messages = messages_by_trace[trace_id]
        task, claims = _build_detector_inputs(
            local_messages,
            trace,
            claim_records,
            evidence_by_id,
            evidence_results,
            contextual_results,
        )
        report = detector.detect(
            task,
            trace,
            claims,
            now=effective_now,
            seen_evidence_ids=seen_evidence_ids,
        )
        assertion_by_id = {
            assertion.assertion_id: assertion
            for message in local_messages
            for assertion in message.assertions
        }
        source_assertion_ids = set(assertion_by_id)
        claim_rows = {record["assertion_id"]: record for record in claim_records.values()}
        for finding in report.findings:
            if finding.assertion_id not in source_assertion_ids:
                continue
            assertion = assertion_by_id[finding.assertion_id]
            record = claim_rows[finding.assertion_id]
            evidence_statuses = [
                {
                    "evidence_id": evidence_id,
                    "status": evidence_results[evidence_id]["status"],
                    "reason": evidence_results[evidence_id]["reason"],
                }
                if evidence_id in evidence_results
                else {
                    "evidence_id": evidence_id,
                    "status": EvidenceVerificationStatus.MISSING.value,
                    "reason": "Referenced evidence record is missing.",
                }
                for evidence_id in assertion.evidence_refs
            ]
            association_statuses = [
                {
                    "evidence_id": evidence_id,
                    **contextual_results[(assertion.assertion_id, evidence_id)],
                }
                for evidence_id in assertion.evidence_refs
            ]
            event_refs = sorted(
                value for value in finding.evidence_examined
                if value.startswith(f"{trace_id}:")
            )
            findings.append({
                "finding_type": "stage4_provenance",
                "experiment_id": experiment_id,
                "seed": seed,
                "assertion_id": finding.assertion_id,
                "provenance_claim_id": finding.provenance_claim_id,
                "agent_id": finding.agent_id,
                "claim_type": record["claim_type"],
                "trace_id": trace_id,
                "task_id": trace.task_id,
                "detector_outcome": finding.predicted_outcome.value,
                "explanation": finding.detection_reason,
                "detector_rule": finding.rule.value,
                "evidence_ids": list(assertion.evidence_refs),
                "evidence_verification": evidence_statuses,
                "evidence_association": association_statuses,
                "relevant_event_references": event_refs,
            })

    messages = [
        message for group in messages_by_trace.values() for message in group
    ]
    cross_findings = analyze_cross_message_assertions(messages, trace_by_id)
    cross_records = [
        {**record, "seed": seed}
        for record in cross_findings
    ]
    status_counts: dict[str, int] = {}
    for item in evidence_results.values():
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    association_status_counts: dict[str, int] = {}
    association_failures: list[dict[str, Any]] = []
    for finding in findings:
        for item in finding["evidence_association"]:
            status = item["status"]
            association_status_counts[status] = (
                association_status_counts.get(status, 0) + 1
            )
            if status != EvidenceVerificationStatus.VALID.value:
                association_failures.append({
                    "assertion_id": finding["assertion_id"],
                    **item,
                })
    return {
        "experiment_id": experiment_id,
        "seed": seed,
        "now_unix_seconds": effective_now,
        "traces_loaded": len(trace_by_id),
        "assertions_evaluated": len(findings),
        "detector_outcome_counts": {
            outcome: sum(row["detector_outcome"] == outcome for row in findings)
            for outcome in _OUTCOME_ORDER
        },
        "evidence_records_verified": len(evidence_results),
        "evidence_verification_status_counts": dict(sorted(status_counts.items())),
        "evidence_verification_failures": [
            item for _, item in sorted(evidence_results.items())
            if item["status"] != EvidenceVerificationStatus.VALID.value
        ],
        "evidence_association_status_counts": dict(
            sorted(association_status_counts.items())
        ),
        "evidence_association_failures": association_failures,
        "cross_message_finding_count": len(cross_records),
        "findings": findings,
        "cross_message_findings": cross_records,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--messages", type=Path, required=True)
    parser.add_argument("--claims", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--trust-policy", type=Path, required=True)
    parser.add_argument("--now", type=int)
    parser.add_argument("--out-dir", type=Path, default=DETECTION_ROOT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = detect_claim_bearing_experiment(
            traces_path=args.traces,
            messages_path=args.messages,
            claims_path=args.claims,
            evidence_path=args.evidence,
            trust_policy_path=args.trust_policy,
            now=args.now,
        )
        output_dir = args.out_dir / result["experiment_id"]
        output_dir.mkdir(parents=True, exist_ok=True)
        targets = {
            "findings": output_dir / "detector_findings.jsonl",
            "cross": output_dir / "cross_message_findings.jsonl",
            "summary": output_dir / "detection_summary.json",
        }
        existing = [path for path in targets.values() if path.exists()]
        if existing and not args.overwrite:
            raise FileExistsError(
                "Detection outputs already exist; pass --overwrite: "
                + ", ".join(str(path) for path in existing)
            )
        _write_jsonl(targets["findings"], result["findings"])
        _write_jsonl(targets["cross"], result["cross_message_findings"])
        summary = {key: value for key, value in result.items() if key not in {
            "findings", "cross_message_findings"
        }}
        targets["summary"].write_text(
            json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, TypeError) as exc:
        print(f"Detection failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "experiment_id": result["experiment_id"],
        "seed": result["seed"],
        "assertions_evaluated": result["assertions_evaluated"],
        "cross_message_finding_count": result["cross_message_finding_count"],
        "out_dir": str(output_dir),
    }, indent=2, sort_keys=True))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
