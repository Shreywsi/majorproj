import pytest

from dataset.claim_message_schemas import (
    CapabilityState,
    ClaimBearingAssertion,
    ClaimBearingMessage,
    ClaimMessageType,
    ProvenanceClaimType,
    claim_message_from_json,
    claim_message_to_json,
    validate_claim_messages,
    validate_messages_against_trace,
)
from dataset.experiment_schemas import (
    EventSource,
    InteractionTrace,
    TraceEvent,
    TraceEventType,
)
from dataset.workflow_preview import InteractionMode


def _assertion(
    assertion_id="assertion-1",
    *,
    message_id="message-1",
    value="agent-1",
    claim_type=ProvenanceClaimType.IDENTITY,
    capability_state=None,
    evidence_refs=(),
):
    return ClaimBearingAssertion(
        assertion_id=assertion_id,
        provenance_claim_id="claim-1",
        agent_id="agent-1",
        claim_type=claim_type,
        subject="agent identity",
        predicate="is",
        asserted_value=value,
        scope="task",
        trace_id="trace-1",
        message_id=message_id,
        evidence_refs=evidence_refs,
        capability_state=capability_state,
    )


def _message(
    message_id="message-1",
    *,
    sequence=1,
    message_type=ClaimMessageType.STATUS_UPDATE,
    agent_id="agent-1",
    handoff_to=None,
    assertions=None,
    evidence_ids=(),
    experiment_id="experiment-1",
    trace_id="trace-1",
    task_id="task-1",
):
    return ClaimBearingMessage(
        message_id=message_id,
        experiment_id=experiment_id,
        trace_id=trace_id,
        task_id=task_id,
        agent_id=agent_id,
        claimed_identity=agent_id,
        claimed_role="analyst",
        subtask_id="subtask-1",
        message_type=message_type,
        content={"status": "working"},
        assertions=tuple(assertions or ()),
        evidence_ids=tuple(evidence_ids),
        handoff_to=handoff_to,
        sequence_number=sequence,
    )


def test_valid_structured_message_and_multiple_assertions():
    assertions = (
        _assertion("assertion-1"),
        _assertion(
            "assertion-2",
            message_id="message-1",
            claim_type=ProvenanceClaimType.ROLE,
            value="analyst",
        ),
    )
    message = _message(assertions=assertions)

    validated = validate_claim_messages(
        (message,),
        experiment_id="experiment-1",
        trace_id="trace-1",
        task_id="task-1",
        agent_ids=("agent-1", "agent-2"),
        subtask_ids=("subtask-1",),
    )

    assert validated == (message,)
    assert len(validated[0].assertions) == 2


def test_invalid_identifiers_and_mismatched_message_references_are_rejected():
    with pytest.raises(ValueError, match="message_id"):
        _message(message_id="bad id")
    invalid_assertion = _assertion(message_id="another-message")
    with pytest.raises(ValueError, match="does not match"):
        validate_claim_messages(
            (_message(assertions=(invalid_assertion,)),),
            experiment_id="experiment-1",
            trace_id="trace-1",
            task_id="task-1",
            agent_ids=("agent-1",),
        )


def test_duplicate_assertion_ids_are_rejected_across_messages():
    first = _message(
        "message-1",
        sequence=1,
        assertions=(_assertion("duplicate", message_id="message-1"),),
    )
    second = _message(
        "message-2",
        sequence=2,
        assertions=(_assertion("duplicate", message_id="message-2"),),
    )
    with pytest.raises(ValueError, match="duplicate assertion_id"):
        validate_claim_messages(
            (first, second),
            experiment_id="experiment-1",
            trace_id="trace-1",
            task_id="task-1",
            agent_ids=("agent-1",),
        )


def test_valid_handoff_and_invalid_recipient():
    handoff = _message(
        message_type=ClaimMessageType.HANDOFF,
        handoff_to="agent-2",
        assertions=(),
    )
    validate_claim_messages(
        (handoff,),
        experiment_id="experiment-1",
        trace_id="trace-1",
        task_id="task-1",
        agent_ids=("agent-1", "agent-2"),
    )
    with pytest.raises(ValueError, match="unknown agent"):
        validate_claim_messages(
            (_message(
                message_type=ClaimMessageType.HANDOFF,
                handoff_to="unknown-agent",
                assertions=(),
            ),),
            experiment_id="experiment-1",
            trace_id="trace-1",
            task_id="task-1",
            agent_ids=("agent-1",),
        )


def test_handoff_requires_a_recipient_and_cannot_self_handoff():
    with pytest.raises(ValueError, match="require handoff_to"):
        _message(message_type=ClaimMessageType.HANDOFF)
    with pytest.raises(ValueError, match="cannot hand off to itself"):
        validate_claim_messages(
            (_message(
                message_type=ClaimMessageType.HANDOFF,
                handoff_to="agent-1",
                assertions=(),
            ),),
            experiment_id="experiment-1",
            trace_id="trace-1",
            task_id="task-1",
            agent_ids=("agent-1",),
        )


def test_json_serialization_round_trip():
    message = _message(
        assertions=(
            _assertion(
                claim_type=ProvenanceClaimType.CAPABILITY,
                value={"tool": "lookup"},
                capability_state=CapabilityState.CONFIGURED_PERMISSION,
                evidence_refs=("evidence-1",),
            ),
        ),
        evidence_ids=("evidence-1",),
    )

    serialized = claim_message_to_json(message)
    restored = claim_message_from_json(serialized)

    assert restored == message
    assert claim_message_to_json(restored) == serialized


def test_conflicting_assertions_are_preserved_without_conflict_detection():
    earlier = _message(
        "message-1",
        sequence=1,
        assertions=(_assertion("assertion-1", message_id="message-1", value="yes"),),
    )
    later = _message(
        "message-2",
        sequence=2,
        assertions=(_assertion("assertion-2", message_id="message-2", value="no"),),
    )

    assert validate_claim_messages(
        (earlier, later),
        experiment_id="experiment-1",
        trace_id="trace-1",
        task_id="task-1",
        agent_ids=("agent-1",),
    ) == (earlier, later)


def test_permission_and_demonstrated_capability_are_explicit_separate_states():
    configured = _assertion(
        "permission",
        claim_type=ProvenanceClaimType.CAPABILITY,
        capability_state=CapabilityState.CONFIGURED_PERMISSION,
    )
    demonstrated = _assertion(
        "demonstrated",
        claim_type=ProvenanceClaimType.CAPABILITY,
        capability_state=CapabilityState.DEMONSTRATED_CAPABILITY,
    )

    assert configured.capability_state != demonstrated.capability_state
    with pytest.raises(ValueError, match="identify configured permission"):
        _assertion(
            claim_type=ProvenanceClaimType.CAPABILITY,
            capability_state=None,
        )
    with pytest.raises(ValueError, match="only valid"):
        _assertion(capability_state=CapabilityState.CONFIGURED_PERMISSION)


def test_evidence_reference_validation_and_task_trace_associations():
    message = _message(
        assertions=(
            _assertion(evidence_refs=("known-evidence",)),
        ),
        evidence_ids=("known-evidence",),
    )
    with pytest.raises(ValueError, match="mismatched trace/task"):
        validate_claim_messages(
            (message,),
            experiment_id="experiment-1",
            trace_id="another-trace",
            task_id="task-1",
            agent_ids=("agent-1",),
        )
    with pytest.raises(ValueError, match="missing evidence"):
        validate_claim_messages(
            (message,),
            experiment_id="experiment-1",
            trace_id="trace-1",
            task_id="task-1",
            agent_ids=("agent-1",),
            known_evidence_ids=(),
        )


def test_messages_bind_to_trace_event_sequence_and_handoff_recipient():
    trace = InteractionTrace(
        trace_id="trace-1",
        task_id="task-1",
        interaction_mode=InteractionMode.ROUND_ROBIN,
        events=(
            TraceEvent(0, TraceEventType.TASK_SUBMITTED, EventSource.USER, content="task"),
            TraceEvent(
                1,
                TraceEventType.HANDOFF,
                EventSource.AGENT,
                actor_id="agent-1",
                recipient_id="agent-2",
            ),
        ),
    )
    handoff = _message(
        message_type=ClaimMessageType.HANDOFF,
        handoff_to="agent-2",
        assertions=(),
    )

    assert validate_messages_against_trace(
        (handoff,),
        trace,
        experiment_id="experiment-1",
        agent_ids=("agent-1", "agent-2"),
    ) == (handoff,)
    with pytest.raises(ValueError, match="does not match trace event"):
        validate_messages_against_trace(
            (_message(assertions=()),),
            trace,
            experiment_id="experiment-1",
            agent_ids=("agent-1", "agent-2"),
        )
