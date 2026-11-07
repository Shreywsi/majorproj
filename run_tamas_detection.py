"""Run the provenance detector over Stage 3 traces and explicit claim inputs."""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from typing import Any

from nacl.signing import VerifyKey

from dataset.experiment_schemas import (
    ClaimAssessment,
    ClaimAssertion,
    ClaimEvidence,
    ClaimEvidenceType,
    ClaimSubjectType,
    DetectorAgentInput,
    DetectorProvenanceClaim,
    DetectorTaskInput,
    IdentityBinding,
    ObservedBehavior,
    ProvenanceClaim,
    TraceExecutionMode,
)
from dataset.trace_store import read_traces_jsonl
from provenance_detector import (
    ClaimFinding,
    ClaimOutcome,
    ProvenanceDetector,
    SignedEvidenceEnvelope,
)


_TASK_KEYS = {"task_id", "scenario", "user_query", "agents"}
_TASK_AGENT_KEYS = {"agent_id", "claimed_role", "claimed_description", "declared_tools"}
_CLAIM_RECORD_KEYS = {"trace_id", "task_input", "claims"}
_CLAIM_ENTRY_KEYS = {"provenance_claim", "signed_evidence"}
_PROVENANCE_CLAIM_KEYS = {
    "claim_id",
    "agent_id",
    "identity_binding",
    "claimed_role",
    "claimed_capabilities",
    "supporting_evidence",
    "observed_behavior",
    "assertions",
}
_EVIDENCE_KEYS = {
    "evidence_id", "evidence_type", "source_ref", "issuer", "integrity_verified"
}
_OBSERVED_BEHAVIOR_KEYS = {"trace_id", "event_sequences", "summary"}
_ASSERTION_KEYS = {
    "assertion_id", "claim_type", "asserted_value", "supporting_evidence_ids"
}
_SIGNED_EVIDENCE_KEYS = {
    "evidence_id",
    "assertion_id",
    "agent_id",
    "evidence_type",
    "claim_type",
    "verified_value",
    "issuer",
    "issued_at",
    "expires_at",
    "signature",
}
_EVALUATOR_ONLY_KEYS = {
    "assessment",
    "ground_truth_label",
    "tamas_record_label",
    "attack_type",
    "source_file",
    "agents_involved",
    "expected_output",
    "sequence_of_tool_calls",
    "evaluator",
    "ground_truth",
    "label",
}


class ClaimsInputError(ValueError):
    """Raised when an explicit claims JSONL file violates the runtime format."""


def _object(value: Any, field: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ClaimsInputError(f"{field} must be a JSON object.")
    forbidden = _EVALUATOR_ONLY_KEYS.intersection(value)
    if forbidden:
        raise ClaimsInputError(
            f"{field} contains evaluator-only field(s): {', '.join(sorted(forbidden))}."
        )
    unknown = set(value) - allowed
    if unknown:
        raise ClaimsInputError(
            f"{field} contains unsupported field(s): {', '.join(sorted(unknown))}."
        )
    return value


def _required(value: dict[str, Any], key: str, field: str) -> Any:
    if key not in value:
        raise ClaimsInputError(f"{field} is missing required field {key!r}.")
    return value[key]


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ClaimsInputError(f"{field} must be an array of non-empty strings.")
    return tuple(value)


def _parse_task_input(raw: Any) -> DetectorTaskInput:
    data = _object(raw, "task_input", _TASK_KEYS)
    agents_raw = _required(data, "agents", "task_input")
    if not isinstance(agents_raw, list):
        raise ClaimsInputError("task_input.agents must be an array.")
    agents = []
    for index, agent_raw in enumerate(agents_raw):
        field = f"task_input.agents[{index}]"
        agent = _object(agent_raw, field, _TASK_AGENT_KEYS)
        agents.append(DetectorAgentInput(
            agent_id=_required(agent, "agent_id", field),
            claimed_role=_required(agent, "claimed_role", field),
            claimed_description=_required(agent, "claimed_description", field),
            declared_tools=_string_tuple(agent.get("declared_tools", []), f"{field}.declared_tools"),
        ))
    try:
        return DetectorTaskInput(
            task_id=_required(data, "task_id", "task_input"),
            scenario=_required(data, "scenario", "task_input"),
            user_query=_required(data, "user_query", "task_input"),
            agents=tuple(agents),
        )
    except (TypeError, ValueError) as exc:
        raise ClaimsInputError(f"Invalid task_input schema: {exc}") from exc


def _parse_provenance_claim(raw: Any) -> ProvenanceClaim:
    data = _object(raw, "provenance_claim", _PROVENANCE_CLAIM_KEYS)
    evidence_raw = data.get("supporting_evidence", [])
    behavior_raw = data.get("observed_behavior", [])
    assertions_raw = data.get("assertions", [])
    if not isinstance(evidence_raw, list):
        raise ClaimsInputError("provenance_claim.supporting_evidence must be an array.")
    if not isinstance(behavior_raw, list):
        raise ClaimsInputError("provenance_claim.observed_behavior must be an array.")
    if not isinstance(assertions_raw, list):
        raise ClaimsInputError("provenance_claim.assertions must be an array.")
    try:
        evidence = []
        for index, raw_evidence in enumerate(evidence_raw):
            field = f"provenance_claim.supporting_evidence[{index}]"
            item = _object(raw_evidence, field, _EVIDENCE_KEYS)
            integrity = item.get("integrity_verified")
            if integrity is not None and not isinstance(integrity, bool):
                raise ClaimsInputError(f"{field}.integrity_verified must be boolean or null.")
            evidence.append(ClaimEvidence(
                evidence_id=_required(item, "evidence_id", field),
                evidence_type=ClaimEvidenceType(_required(item, "evidence_type", field)),
                source_ref=_required(item, "source_ref", field),
                issuer=item.get("issuer"),
                integrity_verified=integrity,
            ))

        behaviors = []
        for index, raw_behavior in enumerate(behavior_raw):
            field = f"provenance_claim.observed_behavior[{index}]"
            item = _object(raw_behavior, field, _OBSERVED_BEHAVIOR_KEYS)
            sequences = item.get("event_sequences")
            if not isinstance(sequences, list):
                raise ClaimsInputError(f"{field}.event_sequences must be an array.")
            behaviors.append(ObservedBehavior(
                trace_id=_required(item, "trace_id", field),
                event_sequences=tuple(sequences),
                summary=_required(item, "summary", field),
            ))

        assertions = []
        for index, raw_assertion in enumerate(assertions_raw):
            field = f"provenance_claim.assertions[{index}]"
            item = _object(raw_assertion, field, _ASSERTION_KEYS)
            assertions.append(ClaimAssertion(
                assertion_id=_required(item, "assertion_id", field),
                claim_type=ClaimSubjectType(_required(item, "claim_type", field)),
                asserted_value=_required(item, "asserted_value", field),
                supporting_evidence_ids=_string_tuple(
                    item.get("supporting_evidence_ids", []),
                    f"{field}.supporting_evidence_ids",
                ),
            ))

        claim = ProvenanceClaim(
            claim_id=_required(data, "claim_id", "provenance_claim"),
            agent_id=_required(data, "agent_id", "provenance_claim"),
            identity_binding=IdentityBinding(
                _required(data, "identity_binding", "provenance_claim")
            ),
            claimed_role=_required(data, "claimed_role", "provenance_claim"),
            claimed_capabilities=_string_tuple(
                _required(data, "claimed_capabilities", "provenance_claim"),
                "provenance_claim.claimed_capabilities",
            ),
            supporting_evidence=tuple(evidence),
            observed_behavior=tuple(behaviors),
            assessment=ClaimAssessment(),
            assertions=tuple(assertions),
        )
    except ClaimsInputError:
        raise
    except (TypeError, ValueError) as exc:
        raise ClaimsInputError(f"Invalid provenance_claim schema: {exc}") from exc
    return claim


def _parse_signed_evidence(raw: Any, index: int) -> SignedEvidenceEnvelope:
    field = f"signed_evidence[{index}]"
    data = _object(raw, field, _SIGNED_EVIDENCE_KEYS)
    try:
        return SignedEvidenceEnvelope(
            evidence_id=_required(data, "evidence_id", field),
            assertion_id=_required(data, "assertion_id", field),
            agent_id=_required(data, "agent_id", field),
            evidence_type=ClaimEvidenceType(_required(data, "evidence_type", field)),
            claim_type=ClaimSubjectType(_required(data, "claim_type", field)),
            verified_value=_required(data, "verified_value", field),
            issuer=_required(data, "issuer", field),
            issued_at=_required(data, "issued_at", field),
            expires_at=data.get("expires_at"),
            signature=_required(data, "signature", field),
        )
    except (TypeError, ValueError) as exc:
        raise ClaimsInputError(f"Invalid {field} schema: {exc}") from exc


def _read_claims_jsonl(
    path: str | Path,
) -> dict[str, tuple[DetectorTaskInput, tuple[DetectorProvenanceClaim, ...], tuple[SignedEvidenceEnvelope, ...]]]:
    claims_path = Path(path)
    if not claims_path.is_file():
        raise FileNotFoundError(f"Claims JSONL file does not exist: {claims_path}")
    per_trace = {}
    with claims_path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                data = _object(record, f"line {line_number}", _CLAIM_RECORD_KEYS)
                trace_id = _required(data, "trace_id", f"line {line_number}")
                if not isinstance(trace_id, str) or not trace_id.strip():
                    raise ClaimsInputError(f"line {line_number}.trace_id must be non-empty text.")
                if trace_id in per_trace:
                    raise ClaimsInputError(f"line {line_number}: duplicate trace_id {trace_id!r}.")
                task = _parse_task_input(_required(data, "task_input", f"line {line_number}"))
                claims_raw = _required(data, "claims", f"line {line_number}")
                if not isinstance(claims_raw, list):
                    raise ClaimsInputError(f"line {line_number}.claims must be an array.")
                parsed_claims = []
                signed = []
                for index, claim_entry_raw in enumerate(claims_raw):
                    field = f"line {line_number}.claims[{index}]"
                    entry = _object(claim_entry_raw, field, _CLAIM_ENTRY_KEYS)
                    parsed_claims.append(
                        _parse_provenance_claim(
                            _required(entry, "provenance_claim", field)
                        ).detector_view()
                    )
                    signed_raw = entry.get("signed_evidence", [])
                    if not isinstance(signed_raw, list):
                        raise ClaimsInputError(f"{field}.signed_evidence must be an array.")
                    signed.extend(
                        _parse_signed_evidence(item, signed_index)
                        for signed_index, item in enumerate(signed_raw)
                    )
                per_trace[trace_id] = (task, tuple(parsed_claims), tuple(signed))
            except (json.JSONDecodeError, ClaimsInputError) as exc:
                raise ClaimsInputError(
                    f"Invalid claims JSONL at line {line_number}: {exc}"
                ) from exc
    return per_trace


def _read_trust_policy(path: str | Path | None) -> dict[str, VerifyKey]:
    if path is None:
        return {}
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read trust policy JSON: {exc}") from exc
    if not isinstance(policy, dict):
        raise ValueError("Trust policy must be a JSON object mapping issuer IDs to base64 public keys.")
    keys = {}
    for issuer, encoded in policy.items():
        if not isinstance(issuer, str) or not issuer.strip() or not isinstance(encoded, str):
            raise ValueError("Trust policy issuer IDs and public keys must be non-empty strings.")
        try:
            keys[issuer] = VerifyKey(base64.b64decode(encoded, validate=True))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid Ed25519 public key for issuer {issuer!r}.") from exc
    return keys


def _finding_json(finding: ClaimFinding) -> dict[str, Any]:
    return {
        "provenance_claim_id": finding.provenance_claim_id,
        "assertion_id": finding.assertion_id,
        "agent_id": finding.agent_id,
        "claim_type": finding.claim_type.value,
        "asserted_value": finding.asserted_value,
        "predicted_outcome": finding.predicted_outcome.value,
        "evidence_examined": list(finding.evidence_examined),
        "detection_reason": finding.detection_reason,
        "rule": finding.rule.value,
    }


def _trace_metadata(trace) -> dict[str, Any]:
    events = trace.events
    terminal = events[-1] if events else None
    closed = bool(
        terminal
        and terminal.source.value == "system"
        and (
            terminal.event_type.value == "simulation_ended"
            or (
                terminal.event_type.value in ("task_completed", "task_failed")
                and terminal.execution_status.value == "recorded"
            )
        )
    )
    invocation_events = [
        event for event in events if event.event_type.value == "tool_invocation"
    ]
    invocation_status_counts = Counter(
        event.execution_status.value for event in invocation_events
    )
    controlled_simulation_only = (
        trace.execution_mode == TraceExecutionMode.CONTROLLED_LOCAL_SIMULATION
        and invocation_status_counts.get("recorded", 0) == 0
    )
    external_calls = 0 if controlled_simulation_only else None
    return {
        "trace_id": trace.trace_id,
        "task_id": trace.task_id,
        "execution_mode": trace.execution_mode.value,
        "trace_completeness_status": (
            "CLOSED_BY_TERMINAL_MARKER" if closed else "INCOMPLETE_OR_UNCONFIRMED"
        ),
        "tool_invocations_by_status": dict(sorted(invocation_status_counts.items())),
        "execution_classification": (
            "controlled_local_simulation_only"
            if controlled_simulation_only
            else "mixed_or_unverified_event_statuses"
        ),
        "simulated_tool_invocations": sum(
            count
            for status, count in invocation_status_counts.items()
            if status in ("local_simulation", "simulated_response")
        ),
        "recorded_tool_invocations": invocation_status_counts.get("recorded", 0),
        "external_actions_executed": external_calls,
    }


def build_detection_report(
    trace_path: str | Path,
    claims_path: str | Path | None = None,
    *,
    trust_policy_path: str | Path | None = None,
    now: int = 0,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """Load runtime traces and explicit claims, then return a deterministic report."""
    traces = read_traces_jsonl(trace_path)
    trace_by_id = {trace.trace_id: trace for trace in traces}
    if len(trace_by_id) != len(traces):
        raise ValueError("Trace JSONL contains duplicate trace IDs; refusing ambiguous association.")
    claims_file = Path(claims_path) if claims_path is not None else None
    missing_claims_file = claims_file is not None and not claims_file.exists()
    if claims_file is not None and claims_file.exists() and not claims_file.is_file():
        raise ValueError(f"Claims path is not a file: {claims_path}")
    claim_inputs = (
        _read_claims_jsonl(claims_file)
        if claims_file is not None and not missing_claims_file
        else {}
    )
    unknown_trace_ids = set(claim_inputs) - set(trace_by_id)
    if unknown_trace_ids:
        raise ClaimsInputError(
            "Claims reference trace IDs not present in the trace file: "
            + ", ".join(sorted(unknown_trace_ids))
        )
    for trace_id, (task, claims, _) in claim_inputs.items():
        if task.task_id != trace_by_id[trace_id].task_id:
            raise ClaimsInputError(
                f"Claims task_id does not match trace {trace_id!r}."
            )

    trust_policy = _read_trust_policy(trust_policy_path)
    detector = ProvenanceDetector(trust_policy)
    if not isinstance(now, int) or isinstance(now, bool) or now < 0:
        raise ValueError("now must be a non-negative Unix timestamp.")

    trace_reports = []
    all_findings = []
    claims_supplied = 0
    seen_evidence_ids: set[str] = set()
    for trace in traces:
        trace_meta = _trace_metadata(trace)
        trace_input = claim_inputs.get(trace.trace_id)
        if trace_input is None:
            trace_reports.append({
                **trace_meta,
                "claims_supplied": 0,
                "assertions_evaluated": 0,
                "findings": [],
                "outcome_counts": {outcome.value: 0 for outcome in ClaimOutcome},
            })
            continue
        task, claims, signed_evidence = trace_input
        claims_supplied += len(claims)
        detection = detector.detect(
            task,
            trace,
            claims,
            signed_evidence=signed_evidence,
            now=now,
            seen_evidence_ids=seen_evidence_ids,
        )
        findings = [_finding_json(finding) for finding in detection.findings]
        all_findings.extend(findings)
        outcome_counts = Counter(
            finding.predicted_outcome.value for finding in detection.findings
        )
        trace_reports.append({
            **trace_meta,
            "claims_supplied": len(claims),
            "assertions_evaluated": len(detection.findings),
            "findings": findings,
            "outcome_counts": {
                outcome.value: outcome_counts.get(outcome.value, 0)
                for outcome in ClaimOutcome
            },
            "unassessed_agent_ids": list(detection.unassessed_agent_ids),
            "missing_information": list(detection.missing_information),
        })

    no_claims = claims_supplied == 0
    counts = Counter(item["predicted_outcome"] for item in all_findings)
    trace_bytes = Path(trace_path).read_bytes()
    claims_bytes = (
        claims_file.read_bytes()
        if claims_file is not None and not missing_claims_file
        else b"<no-claims-file>"
    )
    trust_bytes = (
        Path(trust_policy_path).read_bytes()
        if trust_policy_path is not None else b"<empty-trust-policy>"
    )
    identity_payload = b"\0".join((
        trace_bytes,
        claims_bytes,
        trust_bytes,
        str(now).encode(),
    ))
    deterministic_id = f"stage5-{hashlib.sha256(identity_payload).hexdigest()[:20]}"
    warnings = [
        "No agent-level ground truth is inferred from TAMAS record labels.",
        "Stage 3 local-simulation tool events are not actual external tool executions.",
        "Detector outcomes are evidence-bounded findings, not honesty labels or benchmark metrics.",
    ]
    if missing_claims_file:
        warnings.append(
            "The requested claims file was not found; no claims were loaded."
        )
    status = "NO_CLAIMS_AVAILABLE" if no_claims else "COMPLETED"
    return {
        "experiment_id": experiment_id or deterministic_id,
        "status": status,
        "execution_modes": sorted({
            trace.execution_mode.value for trace in traces
        }),
        "trace_ids_processed": [trace.trace_id for trace in traces],
        "traces_loaded": len(traces),
        "claims_supplied": claims_supplied,
        "assertions_evaluated": len(all_findings),
        "findings_count": len(all_findings),
        "outcome_counts": {
            outcome.value: counts.get(outcome.value, 0)
            for outcome in ClaimOutcome
        },
        "now_unix_seconds": now,
        "trace_reports": trace_reports,
        "warnings": warnings,
        "limitations": [
            "Trace completeness is a terminal-marker convention, not proof of complete real-world instrumentation.",
            "Replay protection is not persistent across pipeline runs unless managed by a separate durable store.",
            "A valid signature establishes integrity and signer authenticity under configured trust policy, not objective truth.",
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument(
        "--claims",
        type=Path,
        help="Optional explicit per-trace claims JSONL. Omit for a no-claims report.",
    )
    parser.add_argument("--trust-policy", type=Path)
    parser.add_argument("--now", type=int, default=0)
    parser.add_argument("--experiment-id")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_detection_report(
        args.traces,
        args.claims,
        trust_policy_path=args.trust_policy,
        now=args.now,
        experiment_id=args.experiment_id,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "experiment_id": report["experiment_id"],
        "traces_loaded": report["traces_loaded"],
        "claims_supplied": report["claims_supplied"],
        "assertions_evaluated": report["assertions_evaluated"],
        "findings_count": report["findings_count"],
        "out": str(args.out),
    }, indent=2))


if __name__ == "__main__":
    main()
