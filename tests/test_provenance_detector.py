from dataclasses import replace

import pytest
from nacl.signing import SigningKey

from dataset.experiment_schemas import (
    ClaimAssessment,
    ClaimAssertion,
    ClaimEvidence,
    ClaimEvidenceType,
    ClaimSubjectType,
    ClaimTruthStatus,
    DetectorProvenanceClaim,
    EventExecutionStatus,
    EventSource,
    IdentityBinding,
    ProvenanceClaim,
    TraceEvent,
    TraceEventType,
)
from dataset.download_tamas import DATASET_ROOT
from dataset.execution_engine import SafeTAMASExecutionEngine
from dataset.experiment_schemas import DetectorTaskInput
from dataset.tamas_loader import TAMASAttack, load_tamas_dataset
from dataset.trace_store import write_trace_jsonl
from dataset.workflow_preview import InteractionMode
from provenance_detector import (
    ClaimOutcome,
    DetectionRule,
    ProvenanceDetector,
    SignedEvidenceEnvelope,
)
from tests.provenance_detector_fixtures import (
    ISSUER,
    SIGNING_KEY,
    TASK,
    claim,
    invalid_signature,
    reference,
    signed,
    tool_invocation,
    trace,
    trusted_key,
)


NOW = 200
DETECTOR = ProvenanceDetector({ISSUER: trusted_key()})


def _assertion(
    assertion_id: str = "claim-001:capability:0",
    claim_type: ClaimSubjectType = ClaimSubjectType.CAPABILITY,
    asserted_value: str = "read_market_data",
    evidence_ids: tuple[str, ...] = (),
) -> ClaimAssertion:
    return ClaimAssertion(assertion_id, claim_type, asserted_value, evidence_ids)


def _finding(report, claim_id: str):
    return next(finding for finding in report.findings if finding.assertion_id == claim_id)


def _task_status(event_type, *, source=EventSource.SYSTEM, sequence=1):
    return TraceEvent(
        sequence=sequence,
        event_type=event_type,
        source=source,
        execution_status=EventExecutionStatus.RECORDED,
        content="Synthetic task status event.",
    )


def test_synthetic_honest_agent_has_fresh_bound_supporting_evidence():
    assertions = (
        ClaimAssertion(
            "honest-identity", ClaimSubjectType.IDENTITY, "agent-001",
            ("evidence-identity",),
        ),
        ClaimAssertion(
            "honest-role", ClaimSubjectType.ROLE, "analyst", ("evidence-role",)
        ),
        ClaimAssertion(
            "honest-capability", ClaimSubjectType.CAPABILITY, "read_market_data",
            ("evidence-capability",),
        ),
    )
    envelopes = tuple(
        signed(assertion, evidence_id=assertion.supporting_evidence_ids[0])
        for assertion in assertions
    )
    honest_claim = DetectorProvenanceClaim(
        claim_id="honest-claim",
        agent_id="agent-001",
        identity_binding=IdentityBinding.CRYPTOGRAPHICALLY_BOUND,
        claimed_role="analyst",
        claimed_capabilities=("read_market_data",),
        supporting_evidence=tuple(reference(item) for item in envelopes),
        observed_behavior=(),
        assertions=assertions,
    )
    report = DETECTOR.detect(
        TASK, trace(), (honest_claim,), signed_evidence=envelopes, now=NOW,
    )

    assert report.predicted_outcome == ClaimOutcome.SUPPORTED
    assert len(report.findings) == 3
    assert all(finding.predicted_outcome == ClaimOutcome.SUPPORTED for finding in report.findings)
    assert all(finding.rule == DetectionRule.TRUSTED_ATTESTATION_MATCH
               for finding in report.findings)
    assert all(len(finding.evidence_examined) == 1 for finding in report.findings)
    capability_finding = _finding(report, "honest-capability")
    assert capability_finding.provenance_claim_id == "honest-claim"
    assert capability_finding.assertion_id == "honest-capability"
    assert capability_finding.claim_id == capability_finding.assertion_id


def test_synthetic_self_claimed_nonexistent_capability_is_unsupported_not_proven_false():
    assertion = _assertion(asserted_value="drain_other_agents_accounts", evidence_ids=("self-001",))
    self_claim = ClaimEvidence(
        "self-001", ClaimEvidenceType.SELF_CLAIMED, "agent-message-1"
    )
    report = DETECTOR.detect(
        TASK, trace(), (claim(assertion=assertion, references=(self_claim,)),), now=NOW
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.UNSUPPORTED
    assert finding.rule == DetectionRule.SELF_CLAIM_ONLY


def test_synthetic_tool_claim_without_any_invocation_is_contradicted_by_complete_trace():
    assertion = _assertion(
        assertion_id="claim-001:tool:1",
        claim_type=ClaimSubjectType.TOOL_USAGE,
        asserted_value="place_order",
    )
    report = DETECTOR.detect(TASK, trace(), (claim(assertion=assertion),), now=NOW)

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.CONTRADICTED
    assert finding.rule == DetectionRule.TRACE_INVOCATION_CONTRADICTION


def test_recorded_tool_invocation_supports_only_the_invocation_claim():
    assertion = _assertion(
        assertion_id="claim-001:tool:recorded",
        claim_type=ClaimSubjectType.TOOL_USAGE,
        asserted_value="place_order",
    )
    report = DETECTOR.detect(
        TASK, trace(tool_invocation("agent-001", "place_order")),
        (claim(assertion=assertion),), now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.SUPPORTED
    assert finding.rule == DetectionRule.TRACE_INVOCATION_MATCH
    assert finding.evidence_examined == ("synthetic-trace:1",)


@pytest.mark.parametrize(
    "claim_type",
    [ClaimSubjectType.CAPABILITY, ClaimSubjectType.ROLE],
)
def test_unrelated_other_agent_invocation_cannot_contradict_role_or_capability(claim_type):
    asserted_value = "analyst" if claim_type == ClaimSubjectType.ROLE else "read_market_data"
    assertion = _assertion(
        assertion_id=f"unrelated:{claim_type.value}",
        claim_type=claim_type,
        asserted_value=asserted_value,
        evidence_ids=("runtime-unrelated",),
    )
    unrelated = ClaimEvidence(
        "runtime-unrelated",
        ClaimEvidenceType.RUNTIME_OBSERVED,
        "synthetic-trace:1",
    )
    report = DETECTOR.detect(
        TASK,
        trace(tool_invocation("agent-002", "place_order")),
        (claim(assertion=assertion, references=(unrelated,)),),
        now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE
    assert finding.predicted_outcome != ClaimOutcome.CONTRADICTED


def test_runtime_reference_matching_other_agent_tool_call_contradicts_complete_trace():
    assertion = _assertion(
        assertion_id="runtime:tool:other",
        claim_type=ClaimSubjectType.TOOL_USAGE,
        asserted_value="place_order",
        evidence_ids=("runtime-other",),
    )
    runtime_reference = ClaimEvidence(
        "runtime-other",
        ClaimEvidenceType.RUNTIME_OBSERVED,
        "synthetic-trace:1",
    )
    report = DETECTOR.detect(
        TASK,
        trace(tool_invocation("agent-002", "place_order")),
        (claim(assertion=assertion, references=(runtime_reference,)),),
        now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.CONTRADICTED
    assert finding.rule == DetectionRule.TRACE_INVOCATION_CONTRADICTION


def test_simulated_invocation_by_another_agent_does_not_contradict_tool_claim():
    assertion = _assertion(
        assertion_id="runtime:tool:other-simulated",
        claim_type=ClaimSubjectType.TOOL_USAGE,
        asserted_value="place_order",
        evidence_ids=("runtime-other-simulated",),
    )
    runtime_reference = ClaimEvidence(
        "runtime-other-simulated",
        ClaimEvidenceType.RUNTIME_OBSERVED,
        "synthetic-trace:1",
    )
    simulated_event = replace(
        tool_invocation("agent-002", "place_order"),
        execution_status=EventExecutionStatus.LOCAL_SIMULATION,
    )
    report = DETECTOR.detect(
        TASK,
        trace(simulated_event),
        (claim(assertion=assertion, references=(runtime_reference,)),),
        now=NOW,
    )
    finding = _finding(report, assertion.assertion_id)

    assert finding.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE
    assert finding.rule == DetectionRule.TRACE_INVOCATION_SIMULATED


def test_runtime_reference_matching_claimed_agent_invocation_supports_tool_use():
    assertion = _assertion(
        assertion_id="runtime:tool:own",
        claim_type=ClaimSubjectType.TOOL_USAGE,
        asserted_value="place_order",
        evidence_ids=("runtime-own",),
    )
    runtime_reference = ClaimEvidence(
        "runtime-own",
        ClaimEvidenceType.RUNTIME_OBSERVED,
        "synthetic-trace:1",
    )
    report = DETECTOR.detect(
        TASK,
        trace(tool_invocation("agent-001", "place_order")),
        (claim(assertion=assertion, references=(runtime_reference,)),),
        now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.SUPPORTED
    assert finding.rule == DetectionRule.TRACE_INVOCATION_MATCH


def test_simulated_invocation_does_not_support_actual_tool_use():
    assertion = _assertion(
        assertion_id="claim-001:tool:simulated",
        claim_type=ClaimSubjectType.TOOL_USAGE,
        asserted_value="place_order",
    )
    simulated_event = replace(
        tool_invocation("agent-001", "place_order"),
        execution_status=EventExecutionStatus.LOCAL_SIMULATION,
    )
    report = DETECTOR.detect(
        TASK, trace(simulated_event), (claim(assertion=assertion),), now=NOW
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE
    assert finding.rule == DetectionRule.TRACE_INVOCATION_SIMULATED


def test_synthetic_task_completion_claim_without_completion_event_is_insufficient():
    assertion = _assertion(
        assertion_id="claim-001:complete:1",
        claim_type=ClaimSubjectType.TASK_COMPLETION,
        asserted_value="completed",
    )
    report = DETECTOR.detect(TASK, trace(), (claim(assertion=assertion),), now=NOW)

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE
    assert finding.rule == DetectionRule.OBSERVATION_MISSING


@pytest.mark.parametrize(
    ("events", "expected_outcome", "expected_rule"),
    [
        (
            (
                _task_status(TraceEventType.TASK_FAILED, sequence=1),
                _task_status(TraceEventType.TASK_COMPLETED, sequence=2),
            ),
            ClaimOutcome.SUPPORTED,
            DetectionRule.COMPLETION_EVENT_MATCH,
        ),
        (
            (
                _task_status(TraceEventType.TASK_COMPLETED, sequence=1),
                _task_status(TraceEventType.TASK_FAILED, sequence=2),
            ),
            ClaimOutcome.CONTRADICTED,
            DetectionRule.COMPLETION_EVENT_CONTRADICTION,
        ),
        (
            (),
            ClaimOutcome.INSUFFICIENT_EVIDENCE,
            DetectionRule.OBSERVATION_MISSING,
        ),
        (
            (_task_status(TraceEventType.TASK_COMPLETED, source=EventSource.AGENT),),
            ClaimOutcome.INSUFFICIENT_EVIDENCE,
            DetectionRule.OBSERVATION_MISSING,
        ),
    ],
)
def test_task_completion_uses_latest_valid_system_status_event(
    events, expected_outcome, expected_rule
):
    assertion = _assertion(
        assertion_id="claim-001:completion-order",
        claim_type=ClaimSubjectType.TASK_COMPLETION,
        asserted_value="completed",
    )
    report = DETECTOR.detect(
        TASK,
        trace(*events),
        (claim(assertion=assertion),),
        now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == expected_outcome
    assert finding.rule == expected_rule


def test_simulation_ended_without_task_status_is_insufficient():
    assertion = _assertion(
        assertion_id="claim-001:completion-simulation-end",
        claim_type=ClaimSubjectType.TASK_COMPLETION,
        asserted_value="completed",
    )
    finding = _finding(
        DETECTOR.detect(TASK, trace(), (claim(assertion=assertion),), now=NOW),
        assertion.assertion_id,
    )

    assert finding.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE


def test_absence_from_incomplete_trace_does_not_contradict_tool_claim():
    assertion = _assertion(
        assertion_id="claim-001:tool:incomplete",
        claim_type=ClaimSubjectType.TOOL_USAGE,
        asserted_value="place_order",
    )
    incomplete_trace = replace(
        trace(),
        events=(trace().events[0],),
    )
    finding = _finding(
        DETECTOR.detect(TASK, incomplete_trace, (claim(assertion=assertion),), now=NOW),
        assertion.assertion_id,
    )

    assert finding.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE
    assert finding.rule == DetectionRule.OBSERVATION_MISSING


def test_incomplete_trace_can_still_support_a_recorded_positive_invocation():
    assertion = _assertion(
        assertion_id="claim-001:tool:incomplete-positive",
        claim_type=ClaimSubjectType.TOOL_USAGE,
        asserted_value="place_order",
    )
    invocation = tool_invocation("agent-001", "place_order")
    incomplete_trace = replace(
        trace(invocation),
        events=trace(invocation).events[:-1],
    )
    finding = _finding(
        DETECTOR.detect(TASK, incomplete_trace, (claim(assertion=assertion),), now=NOW),
        assertion.assertion_id,
    )

    assert finding.predicted_outcome == ClaimOutcome.SUPPORTED
    assert finding.rule == DetectionRule.TRACE_INVOCATION_MATCH


def test_detector_revalidates_trace_structure():
    malformed_trace = trace()
    object.__setattr__(malformed_trace, "events", ())

    with pytest.raises(ValueError, match="structural validation"):
        DETECTOR.detect(TASK, malformed_trace, now=NOW)


def test_synthetic_evidence_bound_to_another_agent_is_unsupported():
    assertion = _assertion(evidence_ids=("evidence-other",))
    envelope = signed(assertion, evidence_id="evidence-other", agent_id="agent-002")
    report = DETECTOR.detect(
        TASK, trace(), (claim(assertion=assertion, references=(reference(envelope),)),),
        signed_evidence=(envelope,), now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.UNSUPPORTED
    assert finding.rule == DetectionRule.EVIDENCE_IDENTITY_MISMATCH


def test_synthetic_modified_signed_evidence_is_rejected():
    assertion = _assertion(evidence_ids=("evidence-tampered",))
    envelope = signed(assertion, evidence_id="evidence-tampered")
    tampered = invalid_signature(envelope)
    report = DETECTOR.detect(
        TASK, trace(), (claim(assertion=assertion, references=(reference(tampered),)),),
        signed_evidence=(tampered,), now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.UNSUPPORTED
    assert finding.rule == DetectionRule.SIGNATURE_INVALID


def test_synthetic_expired_evidence_is_rejected():
    assertion = _assertion(evidence_ids=("evidence-expired",))
    envelope = signed(
        assertion, evidence_id="evidence-expired", issued_at=50, expires_at=150
    )
    report = DETECTOR.detect(
        TASK, trace(), (claim(assertion=assertion, references=(reference(envelope),)),),
        signed_evidence=(envelope,), now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.UNSUPPORTED
    assert finding.rule == DetectionRule.EVIDENCE_EXPIRED_OR_STALE


def test_stale_evidence_is_rejected_by_maximum_age_policy():
    assertion = _assertion(evidence_ids=("evidence-stale",))
    envelope = signed(
        assertion,
        evidence_id="evidence-stale",
        issued_at=100,
        expires_at=None,
    )
    detector = ProvenanceDetector(
        {ISSUER: trusted_key()}, max_evidence_age_seconds=10
    )
    finding = _finding(
        detector.detect(
            TASK,
            trace(),
            (claim(assertion=assertion, references=(reference(envelope),)),),
            signed_evidence=(envelope,),
            now=NOW,
        ),
        assertion.assertion_id,
    )

    assert finding.predicted_outcome == ClaimOutcome.UNSUPPORTED
    assert finding.rule == DetectionRule.EVIDENCE_EXPIRED_OR_STALE


def test_synthetic_previously_used_evidence_is_replay_rejected():
    assertion = _assertion(evidence_ids=("evidence-replayed",))
    envelope = signed(assertion, evidence_id="evidence-replayed")
    references = (reference(envelope),)
    claim_input = claim(assertion=assertion, references=references)
    seen = set()
    first = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(envelope,), now=NOW,
        seen_evidence_ids=seen,
    )
    second = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(envelope,), now=NOW,
        seen_evidence_ids=seen,
    )

    assert _finding(first, assertion.assertion_id).predicted_outcome == ClaimOutcome.SUPPORTED
    assert "evidence-replayed" in seen
    replay = _finding(second, assertion.assertion_id)
    assert replay.predicted_outcome == ClaimOutcome.UNSUPPORTED
    assert replay.rule == DetectionRule.EVIDENCE_REPLAYED


def test_invalid_evidence_does_not_consume_its_id():
    assertion = _assertion(evidence_ids=("evidence-invalid-not-consumed",))
    valid_envelope = signed(assertion, evidence_id="evidence-invalid-not-consumed")
    invalid_envelope = invalid_signature(valid_envelope)
    claim_input = claim(
        assertion=assertion,
        references=(reference(invalid_envelope),),
    )
    seen = set()
    invalid_report = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(invalid_envelope,),
        now=NOW, seen_evidence_ids=seen,
    )
    valid_report = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(valid_envelope,),
        now=NOW, seen_evidence_ids=seen,
    )

    assert _finding(invalid_report, assertion.assertion_id).rule == DetectionRule.SIGNATURE_INVALID
    assert seen == {"evidence-invalid-not-consumed"}
    assert _finding(valid_report, assertion.assertion_id).predicted_outcome == ClaimOutcome.SUPPORTED


def test_untrusted_evidence_does_not_consume_its_id():
    assertion = _assertion(evidence_ids=("evidence-untrusted",))
    envelope = SignedEvidenceEnvelope.sign(
        signing_key=SIGNING_KEY,
        evidence_id="evidence-untrusted",
        assertion_id=assertion.assertion_id,
        agent_id="agent-001",
        evidence_type=ClaimEvidenceType.ISSUER_ATTESTED,
        claim_type=assertion.claim_type,
        verified_value=assertion.asserted_value,
        issuer="unconfigured-issuer",
        issued_at=100,
        expires_at=500,
    )
    claim_input = claim(assertion=assertion, references=(reference(envelope),))
    seen = set()
    report = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(envelope,),
        now=NOW, seen_evidence_ids=seen,
    )

    assert _finding(report, assertion.assertion_id).rule == DetectionRule.ISSUER_UNTRUSTED
    assert seen == set()


def test_evidence_reference_order_does_not_change_aggregated_result():
    assertion = _assertion(
        assertion_id="claim-001:capability:aggregation",
        evidence_ids=("evidence-a-match", "evidence-b-conflict"),
    )
    matching = signed(
        assertion, evidence_id="evidence-a-match",
    )
    conflicting = signed(
        assertion,
        evidence_id="evidence-b-conflict",
        verified_value="capability_not_granted",
    )
    refs = (reference(matching), reference(conflicting))
    first_claim = claim(assertion=assertion, references=refs)
    reversed_assertion = replace(
        assertion,
        supporting_evidence_ids=tuple(reversed(assertion.supporting_evidence_ids)),
    )
    reversed_claim = claim(
        claim_id="claim-reordered",
        assertion=reversed_assertion,
        references=tuple(reversed(refs)),
    )
    first = DETECTOR.detect(
        TASK, trace(), (first_claim,),
        signed_evidence=(matching, conflicting), now=NOW,
    )
    second = DETECTOR.detect(
        TASK, trace(), (reversed_claim,),
        signed_evidence=(conflicting, matching), now=NOW,
    )
    first_finding = _finding(first, assertion.assertion_id)
    second_finding = _finding(second, assertion.assertion_id)

    assert first_finding.predicted_outcome == ClaimOutcome.CONTRADICTED
    assert second_finding.predicted_outcome == ClaimOutcome.CONTRADICTED
    assert first_finding.rule == second_finding.rule == DetectionRule.TRUSTED_ATTESTATION_CONTRADICTION
    assert first_finding.evidence_examined == second_finding.evidence_examined == (
        "evidence-a-match",
        "evidence-b-conflict",
    )
    assert first_finding.detection_reason == second_finding.detection_reason


def test_valid_contradiction_is_not_hidden_by_invalid_evidence():
    assertion = _assertion(
        assertion_id="claim-001:capability:invalid-and-conflict",
        evidence_ids=("evidence-invalid", "evidence-conflict"),
    )
    invalid = invalid_signature(signed(assertion, evidence_id="evidence-invalid"))
    conflict = signed(
        assertion,
        evidence_id="evidence-conflict",
        verified_value="capability_not_granted",
    )
    claim_input = claim(
        assertion=assertion,
        references=(reference(invalid), reference(conflict)),
    )
    report = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(invalid, conflict), now=NOW
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.CONTRADICTED
    assert finding.evidence_examined == ("evidence-conflict", "evidence-invalid")


def test_matching_and_contradictory_valid_attestations_resolve_to_contradicted():
    assertion = _assertion(
        assertion_id="claim-001:capability:conflicting-attestations",
        evidence_ids=("evidence-match", "evidence-conflict"),
    )
    matching = signed(assertion, evidence_id="evidence-match")
    conflicting = signed(
        assertion, evidence_id="evidence-conflict",
        verified_value="capability_not_granted",
    )
    claim_input = claim(
        assertion=assertion,
        references=(reference(matching), reference(conflicting)),
    )
    finding = _finding(
        DETECTOR.detect(
            TASK, trace(), (claim_input,), signed_evidence=(matching, conflicting), now=NOW
        ),
        assertion.assertion_id,
    )

    assert finding.predicted_outcome == ClaimOutcome.CONTRADICTED
    assert finding.rule == DetectionRule.TRUSTED_ATTESTATION_CONTRADICTION


def test_valid_evidence_id_cannot_be_used_twice_in_one_report():
    assertion = _assertion(
        assertion_id="shared-assertion",
        evidence_ids=("evidence-shared",),
    )
    envelope = signed(assertion, evidence_id="evidence-shared")
    references = (reference(envelope),)
    claims = (
        claim(claim_id="claim-first-use", assertion=assertion, references=references),
        claim(claim_id="claim-second-use", assertion=assertion, references=references),
    )
    report = DETECTOR.detect(
        TASK, trace(), claims, signed_evidence=(envelope,), now=NOW
    )
    first = next(
        finding for finding in report.findings
        if finding.provenance_claim_id == "claim-first-use"
        and finding.assertion_id == assertion.assertion_id
    )
    second = next(
        finding for finding in report.findings
        if finding.provenance_claim_id == "claim-second-use"
        and finding.assertion_id == assertion.assertion_id
    )

    assert first.provenance_claim_id == "claim-first-use"
    assert first.predicted_outcome == ClaimOutcome.SUPPORTED
    assert second.provenance_claim_id == "claim-second-use"
    assert second.predicted_outcome == ClaimOutcome.UNSUPPORTED
    assert second.rule == DetectionRule.EVIDENCE_REPLAYED


def test_calls_without_shared_replay_state_do_not_claim_global_replay_protection():
    assertion = _assertion(evidence_ids=("evidence-call-local",))
    envelope = signed(assertion, evidence_id="evidence-call-local")
    claim_input = claim(assertion=assertion, references=(reference(envelope),))
    first = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(envelope,), now=NOW
    )
    second = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(envelope,), now=NOW
    )

    assert _finding(first, assertion.assertion_id).predicted_outcome == ClaimOutcome.SUPPORTED
    assert _finding(second, assertion.assertion_id).predicted_outcome == ClaimOutcome.SUPPORTED


def test_synthetic_claim_without_any_evidence_is_insufficient():
    assertion = _assertion(
        assertion_id="claim-001:provenance:1",
        claim_type=ClaimSubjectType.PROVENANCE,
        asserted_value="from_verified_source",
    )
    report = DETECTOR.detect(TASK, trace(), (claim(assertion=assertion),), now=NOW)

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE
    assert finding.rule == DetectionRule.OBSERVATION_MISSING


def test_synthetic_observation_attributing_tool_call_to_different_agent_contradicts_claim():
    assertion = _assertion(
        assertion_id="claim-001:tool:2",
        claim_type=ClaimSubjectType.TOOL_USAGE,
        asserted_value="place_order",
    )
    runtime_trace = trace(tool_invocation("agent-002", "place_order"))
    report = DETECTOR.detect(
        TASK, runtime_trace, (claim(assertion=assertion),), now=NOW
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.CONTRADICTED
    assert finding.rule == DetectionRule.TRACE_INVOCATION_CONTRADICTION
    assert finding.evidence_examined == ("synthetic-trace:1",)


def test_valid_signature_does_not_override_identity_binding_policy():
    assertion = _assertion(evidence_ids=("evidence-unbound",))
    envelope = signed(assertion, evidence_id="evidence-unbound")
    unbound_claim = claim(
        assertion=assertion, references=(reference(envelope),)
    )
    unbound_claim = DetectorProvenanceClaim(
        **{
            **unbound_claim.__dict__,
            "identity_binding": IdentityBinding.UNVERIFIED,
        }
    )
    report = DETECTOR.detect(
        TASK, trace(), (unbound_claim,), signed_evidence=(envelope,), now=NOW
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.UNSUPPORTED
    assert finding.rule == DetectionRule.IDENTITY_BINDING_MISSING


def test_valid_signature_over_a_different_value_is_a_contradiction():
    assertion = _assertion(evidence_ids=("evidence-conflict",))
    envelope = signed(
        assertion, evidence_id="evidence-conflict", verified_value="no_market_access"
    )
    report = DETECTOR.detect(
        TASK, trace(), (claim(assertion=assertion, references=(reference(envelope),)),),
        signed_evidence=(envelope,), now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.CONTRADICTED
    assert finding.rule == DetectionRule.TRUSTED_ATTESTATION_CONTRADICTION


def test_missing_signed_envelope_is_insufficient_and_invalid_claim_is_rejected():
    assertion = _assertion(evidence_ids=("missing-envelope",))
    reference_only = ClaimEvidence(
        "missing-envelope", ClaimEvidenceType.ISSUER_ATTESTED,
        "missing-envelope", issuer=ISSUER,
    )
    report = DETECTOR.detect(
        TASK, trace(), (claim(assertion=assertion, references=(reference_only,)),), now=NOW
    )
    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE
    assert finding.rule == DetectionRule.EVIDENCE_MISSING

    with pytest.raises(ValueError, match="claim_type"):
        ClaimAssertion("bad", "capability", "value")


def test_evaluator_assessment_cannot_be_passed_to_detector():
    source_claim = ProvenanceClaim(
        claim_id="claim-with-evaluator-data",
        agent_id="agent-001",
        identity_binding=IdentityBinding.CRYPTOGRAPHICALLY_BOUND,
        claimed_role="analyst",
        claimed_capabilities=("read_market_data",),
        assessment=ClaimAssessment(
            status=ClaimTruthStatus.SUPPORTED,
            assessor="synthetic evaluator",
            method="controlled fixture",
            evidence_ids=("hidden-ground-truth-id",),
        ),
    )
    assert not hasattr(source_claim.detector_view(), "assessment")
    with pytest.raises(TypeError, match="DetectorProvenanceClaim"):
        DETECTOR.detect(TASK, trace(), (source_claim,), now=NOW)


def test_detector_is_deterministic_for_identical_inputs():
    assertion = _assertion(evidence_ids=("evidence-deterministic",))
    envelope = signed(assertion, evidence_id="evidence-deterministic")
    claim_input = claim(assertion=assertion, references=(reference(envelope),))
    first = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(envelope,), now=NOW
    )
    second = DETECTOR.detect(
        TASK, trace(), (claim_input,), signed_evidence=(envelope,), now=NOW
    )

    assert first == second


def test_wrong_trusted_issuer_key_does_not_validate_evidence():
    assertion = _assertion(evidence_ids=("evidence-wrong-key",))
    envelope = signed(assertion, evidence_id="evidence-wrong-key")
    wrong_key = SigningKey(bytes(reversed(range(32)))).verify_key
    detector = ProvenanceDetector({ISSUER: wrong_key})
    report = detector.detect(
        TASK, trace(), (claim(assertion=assertion, references=(reference(envelope),)),),
        signed_evidence=(envelope,), now=NOW,
    )

    finding = _finding(report, assertion.assertion_id)
    assert finding.predicted_outcome == ClaimOutcome.UNSUPPORTED
    assert finding.rule == DetectionRule.SIGNATURE_INVALID


def test_stage3_jsonl_trace_without_claims_is_insufficient_not_malicious(tmp_path):
    dataset = load_tamas_dataset(DATASET_ROOT)
    benign = next(record for record in dataset.records if record.is_benign)
    adversarial = next(
        record for record in dataset.records
        if record.attack_type != TAMASAttack.BENIGN
    )
    engine = SafeTAMASExecutionEngine(
        dataset.tool_definitions, InteractionMode.ROUND_ROBIN
    )
    tasks_and_traces = tuple(
        (
            DetectorTaskInput.from_tamas_record(record, task_id=f"opaque-{index}"),
            engine.run(
                DetectorTaskInput.from_tamas_record(record, task_id=f"opaque-{index}"),
                seed=19,
            ).trace,
        )
        for index, record in enumerate((benign, adversarial))
    )
    path = tmp_path / "trace.jsonl"
    for _, actual_trace in tasks_and_traces:
        write_trace_jsonl(actual_trace, path)

    reports = tuple(
        DETECTOR.detect_jsonl(
            str(path), task, actual_trace.trace_id, now=NOW
        )
        for task, actual_trace in tasks_and_traces
    )

    assert all(report.predicted_outcome == ClaimOutcome.INSUFFICIENT_EVIDENCE for report in reports)
    assert all(report.findings == () for report in reports)
    assert all("No structured provenance claims" in report.missing_information[0]
               for report in reports)
    assert all(report.unassessed_agent_ids for report in reports)
