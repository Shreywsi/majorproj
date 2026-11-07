from dataclasses import asdict

import pytest

from dataset.experiment_schemas import (
    ClaimAssessment,
    ClaimEvidence,
    ClaimEvidenceType,
    ClaimTruthStatus,
    DetectorAgentInput,
    DetectorProvenanceClaim,
    DetectorTaskInput,
    EventSource,
    IdentityBinding,
    InteractionTrace,
    ObservedBehavior,
    ProvenanceClaim,
    TraceEvent,
    TraceEventType,
)
from dataset.tamas_loader import TAMASAttack, TAMASAgent, TAMASRecord
from dataset.workflow_preview import InteractionMode


def _record():
    return TAMASRecord(
        record_id="RAS_finance_DPI.json:0000",
        scenario="finance",
        original_user_query="Review the transaction and manage exposure.",
        agents=(
            TAMASAgent(
                agent_id="RAS_finance_DPI.json:0000:agent:0",
                name="Risk Management Agent",
                description="Monitors exposure and stop-loss limits.",
                backstory=None,
                tools=None,
                original_fields={},
            ),
        ),
        attack_type=TAMASAttack.DIRECT_PROMPT_INJECTION,
        ground_truth_label="direct_prompt_injection",
        source_file="RAS_finance_DPI.json",
        agents_involved=("Risk Management Agent",),
        expected_output=None,
        sequence_of_tool_calls=None,
        original_fields={"private_test_marker": "evaluator-only"},
    )


def test_detector_task_projection_is_allowlisted_and_uses_opaque_agent_ids():
    detector_input = DetectorTaskInput.from_tamas_record(_record(), "opaque-task-01")
    serialized = asdict(detector_input)

    assert detector_input.task_id == "opaque-task-01"
    assert detector_input.user_query == "Review the transaction and manage exposure."
    assert detector_input.agents[0].agent_id == "agent-000"
    assert detector_input.agents[0].claimed_role == "Risk Management Agent"
    assert detector_input.agents[0].declared_tools == ()
    assert "attack_type" not in serialized
    assert "ground_truth_label" not in serialized
    assert "agents_involved" not in serialized
    assert "expected_output" not in serialized
    assert "sequence_of_tool_calls" not in serialized
    assert "source_file" not in serialized
    assert "private_test_marker" not in repr(serialized)


def test_detector_inputs_reject_empty_or_duplicated_agent_identifiers():
    agent = DetectorAgentInput("agent-0", "role", "description")
    with pytest.raises(ValueError, match="at least one"):
        DetectorTaskInput("task-0", "finance", "query", ())
    with pytest.raises(ValueError, match="unique"):
        DetectorTaskInput("task-0", "finance", "query", (agent, agent))


def test_trace_schema_accepts_observed_events_and_requires_contiguous_sequence():
    events = (
        TraceEvent(
            sequence=0,
            event_type=TraceEventType.TASK_SUBMITTED,
            source=EventSource.USER,
            content="Original query",
        ),
        TraceEvent(
            sequence=1,
            event_type=TraceEventType.AGENT_MESSAGE,
            source=EventSource.AGENT,
            actor_id="agent-000",
            content="Observed response",
            elapsed_ms=120,
        ),
        TraceEvent(
            sequence=2,
            event_type=TraceEventType.TOOL_INVOCATION,
            source=EventSource.AGENT,
            actor_id="agent-000",
            tool_name="lookup",
            tool_arguments={"symbol": "AAPL"},
        ),
        TraceEvent(
            sequence=3,
            event_type=TraceEventType.TOOL_RESULT,
            source=EventSource.TOOL,
            tool_name="lookup",
            tool_result={"status": "simulated"},
        ),
    )
    trace = InteractionTrace(
        trace_id="trace-01",
        task_id="opaque-task-01",
        interaction_mode=InteractionMode.ROUND_ROBIN,
        events=events,
        model_id="test-model",
        model_version="v1",
        prompt_version="prompt-v1",
        prompt_hash="sha256:prompt",
        tool_registry_version="registry-v1",
        tool_registry_hash="sha256:registry",
        config_hash="sha256:config",
        seed=17,
    )
    assert len(trace.events) == 4
    assert trace.prompt_version == "prompt-v1"
    assert trace.tool_registry_version == "registry-v1"
    with pytest.raises(ValueError, match="contiguous"):
        InteractionTrace(
            trace_id="trace-02",
            task_id="opaque-task-01",
            interaction_mode=InteractionMode.SWARM,
            events=(events[0], events[2]),
        )


@pytest.mark.parametrize(
    "event_factory, message",
    [
        (
            lambda: TraceEvent(
                sequence=0,
                event_type=TraceEventType.HANDOFF,
                source=EventSource.AGENT,
                actor_id="agent-000",
            ),
            "recipient_id",
        ),
        (
            lambda: TraceEvent(
                sequence=0,
                event_type=TraceEventType.TOOL_INVOCATION,
                source=EventSource.AGENT,
                actor_id="agent-000",
            ),
            "tool_name",
        ),
        (
            lambda: TraceEvent(
                sequence=0,
                event_type=TraceEventType.TASK_SUBMITTED,
                source=EventSource.SYSTEM,
                content="query",
            ),
            "source=user",
        ),
        (
            lambda: TraceEvent(
                sequence=0,
                event_type=TraceEventType.TOOL_RESULT,
                source=EventSource.TOOL,
                tool_name="lookup",
                tool_result=object(),
            ),
            "JSON-compatible",
        ),
    ],
)
def test_trace_event_rejects_malformed_or_non_json_events(event_factory, message):
    with pytest.raises(ValueError, match=message):
        event_factory()


def test_trace_rejects_negative_seed_and_event_sequence():
    submitted = TraceEvent(
        sequence=0,
        event_type=TraceEventType.TASK_SUBMITTED,
        source=EventSource.USER,
        content="query",
    )
    with pytest.raises(ValueError, match="seed"):
        InteractionTrace(
            trace_id="trace",
            task_id="task",
            interaction_mode=InteractionMode.SWARM,
            events=(submitted,),
            seed=-1,
        )
    with pytest.raises(ValueError, match="sequence"):
        TraceEvent(
            sequence=-1,
            event_type=TraceEventType.TASK_SUBMITTED,
            source=EventSource.USER,
            content="query",
        )


def test_provenance_claim_keeps_evaluator_assessment_out_of_detector_view():
    claim = ProvenanceClaim(
        claim_id="claim-1",
        agent_id="agent-000",
        identity_binding=IdentityBinding.UNVERIFIED,
        claimed_role="Market Analysis Agent",
        claimed_capabilities=("analyze market sentiment",),
        supporting_evidence=(
            ClaimEvidence(
                evidence_id="evidence-1",
                evidence_type=ClaimEvidenceType.SELF_CLAIMED,
                source_ref="recorded-profile",
                integrity_verified=True,
            ),
        ),
        observed_behavior=(
            ObservedBehavior("trace-1", (1, 2), "Observed a market-analysis tool call."),
        ),
        assessment=ClaimAssessment(
            status=ClaimTruthStatus.CONTRADICTED,
            assessor="evaluator-1",
            method="independent_replay",
            evidence_ids=("evidence-1",),
        ),
    )

    detector_view = claim.detector_view()
    assert isinstance(detector_view, DetectorProvenanceClaim)
    assert detector_view.claimed_capabilities == ("analyze market sentiment",)
    assert detector_view.supporting_evidence[0].integrity_verified is True
    assert "assessment" not in asdict(detector_view)
    assert "CONTRADICTED" not in repr(detector_view)
    assert claim.assessment.status == ClaimTruthStatus.CONTRADICTED


def test_claim_assessment_requires_evaluator_method_and_not_assessed_has_no_metadata():
    with pytest.raises(ValueError, match="assessor and assessment method"):
        ClaimAssessment(status=ClaimTruthStatus.SUPPORTED)
    with pytest.raises(ValueError, match="cannot include"):
        ClaimAssessment(assessor="reviewer", status=ClaimTruthStatus.NOT_ASSESSED)
    with pytest.raises(ValueError, match="cited evidence_ids"):
        ClaimAssessment(
            status=ClaimTruthStatus.SUPPORTED,
            assessor="reviewer",
            method="independent_replay",
        )


def test_claim_evidence_integrity_is_not_claim_truth_and_claims_need_capabilities():
    evidence = ClaimEvidence(
        evidence_id="sig-1",
        evidence_type=ClaimEvidenceType.ISSUER_ATTESTED,
        source_ref="issuer-record-1",
        issuer="issuer-1",
        integrity_verified=True,
    )
    assert evidence.integrity_verified is True
    with pytest.raises(ValueError, match="claimed_capabilities"):
        ProvenanceClaim(
            claim_id="claim-1",
            agent_id="agent-000",
            identity_binding=IdentityBinding.ISSUER_ATTESTED,
            claimed_role="role",
            claimed_capabilities=(),
        )
