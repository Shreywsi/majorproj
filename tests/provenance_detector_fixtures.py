"""Synthetic detector-only fixtures; not TAMAS records or benchmark annotations."""

from dataclasses import replace

from nacl.signing import SigningKey

from dataset.experiment_schemas import (
    ClaimAssertion,
    ClaimEvidence,
    ClaimEvidenceType,
    ClaimSubjectType,
    DetectorAgentInput,
    DetectorProvenanceClaim,
    DetectorTaskInput,
    EventExecutionStatus,
    EventSource,
    IdentityBinding,
    InteractionTrace,
    TraceEvent,
    TraceEventType,
)
from dataset.workflow_preview import InteractionMode
from provenance_detector import SignedEvidenceEnvelope, evidence_reference


ISSUER = "synthetic-test-issuer"
SIGNING_KEY = SigningKey(bytes(range(32)))
TASK = DetectorTaskInput(
    task_id="synthetic-task",
    scenario="synthetic_detector_test",
    user_query="Synthetic detector fixture only.",
    agents=(
        DetectorAgentInput("agent-001", "analyst", "Synthetic test agent."),
        DetectorAgentInput("agent-002", "analyst", "Synthetic second test agent."),
    ),
)


def trace(*events: TraceEvent, trace_id: str = "synthetic-trace") -> InteractionTrace:
    sequence = [
        TraceEvent(0, TraceEventType.TASK_SUBMITTED, EventSource.USER, content=TASK.user_query),
        *events,
    ]
    sequence.append(TraceEvent(
        len(sequence),
        TraceEventType.SIMULATION_ENDED,
        EventSource.SYSTEM,
        EventExecutionStatus.NOT_EXECUTED,
        content="Synthetic detector fixture trace ended.",
    ))
    return InteractionTrace(
        trace_id=trace_id,
        task_id=TASK.task_id,
        interaction_mode=InteractionMode.ROUND_ROBIN,
        events=tuple(sequence),
    )


def claim(
    *,
    agent_id: str = "agent-001",
    claim_id: str = "claim-001",
    assertion: ClaimAssertion | None = None,
    references: tuple[ClaimEvidence, ...] = (),
) -> DetectorProvenanceClaim:
    assertions = (assertion,) if assertion is not None else ()
    return DetectorProvenanceClaim(
        claim_id=claim_id,
        agent_id=agent_id,
        identity_binding=IdentityBinding.CRYPTOGRAPHICALLY_BOUND,
        claimed_role="analyst",
        claimed_capabilities=("read_market_data",),
        supporting_evidence=references,
        observed_behavior=(),
        assertions=assertions,
    )


def signed(
    assertion: ClaimAssertion,
    *,
    evidence_id: str = "evidence-001",
    agent_id: str = "agent-001",
    verified_value: str | None = None,
    issued_at: int = 100,
    expires_at: int | None = 500,
) -> SignedEvidenceEnvelope:
    return SignedEvidenceEnvelope.sign(
        signing_key=SIGNING_KEY,
        evidence_id=evidence_id,
        assertion_id=assertion.assertion_id,
        agent_id=agent_id,
        evidence_type=ClaimEvidenceType.ISSUER_ATTESTED,
        claim_type=assertion.claim_type,
        verified_value=verified_value or assertion.asserted_value,
        issuer=ISSUER,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def reference(envelope: SignedEvidenceEnvelope) -> ClaimEvidence:
    return evidence_reference(envelope)


def tool_invocation(agent_id: str, tool_name: str, sequence: int = 1) -> TraceEvent:
    return TraceEvent(
        sequence=sequence,
        event_type=TraceEventType.TOOL_INVOCATION,
        source=EventSource.AGENT,
        execution_status=EventExecutionStatus.RECORDED,
        actor_id=agent_id,
        tool_name=tool_name,
        tool_arguments={},
    )


def invalid_signature(envelope: SignedEvidenceEnvelope) -> SignedEvidenceEnvelope:
    return replace(envelope, signature="A" * 88)


def trusted_key():
    return SIGNING_KEY.verify_key
