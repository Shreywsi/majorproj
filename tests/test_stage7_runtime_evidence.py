import base64
from dataclasses import replace
import json

import pytest
from nacl.signing import SigningKey

from crypto_ledger import EvidenceVerifier
from dataset.claim_message_schemas import (
    ClaimBearingAssertion,
    ClaimBearingMessage,
    ClaimMessageType,
    ProvenanceClaimType,
)
from dataset.experiment_schemas import EventExecutionStatus
from dataset.runtime_evidence import (
    AgentIdentityRegistry,
    AuthorityKind,
    EvidenceCategory,
    EvidenceVerificationStatus,
    RuntimeEvidence,
    RuntimeEvidenceIssuer,
    RuntimeEvidenceVerifier,
    TrustPolicy,
    validate_message_evidence_links,
)


def _issuer(
    issuer_id="runtime-authority",
    kind=AuthorityKind.RUNTIME_EXECUTION,
    categories=None,
):
    return RuntimeEvidenceIssuer(
        issuer_id,
        kind,
        categories=categories,
        seed=bytes([7]) * 32,
    )


def _policy(*issuers):
    return TrustPolicy(tuple(issuer.trusted_issuer() for issuer in issuers))


def _evidence(
    issuer=None,
    *,
    category=EvidenceCategory.CONFIGURED_PERMISSION,
    agent_id="agent-1",
    created_at=100,
    expires_at=200,
    scope="task:task-1",
    execution_status=None,
    evidence_id="evidence-1",
    nonce="nonce-1",
    message_id="message-1",
):
    issuer = issuer or _issuer()
    return issuer.issue_evidence(
        evidence_id=evidence_id,
        nonce=nonce,
        category=category,
        agent_id=agent_id,
        experiment_id="experiment-1",
        trace_id="trace-1",
        task_id="task-1",
        message_id=message_id,
        assertion_id="assertion-1",
        subject="capability",
        predicate="allowed",
        asserted_value=True,
        scope=scope,
        created_at=created_at,
        expires_at=expires_at,
        source_event_sequence=3,
        execution_status=execution_status,
    )


def _tool_issuer():
    return _issuer(
        "tool-gateway",
        AuthorityKind.TOOL_EXECUTION,
        (EvidenceCategory.TOOL_EXECUTION,),
    )


def test_valid_signature_and_public_policy_do_not_expose_private_key():
    issuer = _issuer()
    evidence = _evidence(issuer)
    policy = _policy(issuer)
    result = RuntimeEvidenceVerifier(policy).verify(
        evidence, now=150, expected_agent_id="agent-1"
    )

    assert result.status == EvidenceVerificationStatus.VALID
    public_json = json.dumps(policy.to_public_dict())
    assert issuer.public_key_b64 in public_json
    assert base64.b64encode(bytes([7]) * 32).decode("ascii") not in public_json
    assert "private_key" not in public_json


def test_invalid_signature_is_reported():
    issuer = _issuer()
    evidence = _evidence(issuer)
    tampered = replace(evidence, asserted_value=False)

    result = RuntimeEvidenceVerifier(_policy(issuer)).verify(tampered, now=150)

    assert result.status == EvidenceVerificationStatus.INVALID_SIGNATURE


def test_untrusted_issuer_and_unauthorized_category_are_reported():
    signing_issuer = _issuer()
    unrelated_trust = _issuer("other-authority")
    evidence = _evidence(signing_issuer)
    assert RuntimeEvidenceVerifier(_policy(unrelated_trust)).verify(
        evidence, now=150
    ).status == EvidenceVerificationStatus.UNTRUSTED_ISSUER

    permission_issuer = _issuer(
        "permission-only",
        AuthorityKind.RUNTIME_EXECUTION,
        (EvidenceCategory.CONFIGURED_PERMISSION,),
    )
    role_issuer = _issuer(
        "identity-authority",
        AuthorityKind.IDENTITY_REGISTRY,
        (EvidenceCategory.ROLE,),
    )
    role_evidence = role_issuer.issue_evidence(
        category=EvidenceCategory.ROLE,
        agent_id="agent-1",
        experiment_id="experiment-1",
        subject="role",
        predicate="assigned",
        asserted_value="analyst",
        scope="task:task-1",
        created_at=100,
        expires_at=200,
    )
    permission_only_policy = TrustPolicy((
        permission_issuer.trusted_issuer(),
        role_issuer.trusted_issuer(),
    ))
    # This authority can issue ROLE by its kind, but an explicit trust entry may
    # still narrow the issuer to a different evidence category.
    narrowed_role = replace(
        role_issuer.trusted_issuer(),
        categories=(EvidenceCategory.IDENTITY,),
    )
    narrowed_policy = TrustPolicy((
        permission_issuer.trusted_issuer(),
        narrowed_role,
    ))
    assert permission_only_policy.find("permission-only") is not None
    assert RuntimeEvidenceVerifier(narrowed_policy).verify(
        role_evidence, now=150
    ).status == EvidenceVerificationStatus.CATEGORY_NOT_AUTHORIZED


def test_expired_future_dated_and_stale_evidence_are_explicit():
    issuer = _issuer()
    verifier = RuntimeEvidenceVerifier(_policy(issuer), max_age_seconds=20)

    assert verifier.verify(
        _evidence(issuer, evidence_id="expired", expires_at=120),
        now=120,
    ).status == EvidenceVerificationStatus.EXPIRED
    assert RuntimeEvidenceVerifier(_policy(issuer)).verify(
        _evidence(issuer, evidence_id="future", created_at=151, expires_at=250),
        now=150,
    ).status == EvidenceVerificationStatus.FUTURE_DATED
    assert verifier.verify(
        _evidence(issuer, evidence_id="stale", created_at=100, expires_at=250),
        now=150,
    ).status == EvidenceVerificationStatus.STALE


def test_replay_is_rejected_after_first_success():
    issuer = _issuer()
    verifier = RuntimeEvidenceVerifier(_policy(issuer))
    evidence = _evidence(issuer)

    assert verifier.verify(evidence, now=150).status == EvidenceVerificationStatus.VALID
    assert verifier.verify(evidence, now=150).status == EvidenceVerificationStatus.REPLAYED


def test_agent_and_scope_context_mismatches_are_reported():
    issuer = _issuer()
    evidence = _evidence(issuer)
    verifier = RuntimeEvidenceVerifier(_policy(issuer))

    assert verifier.verify(
        evidence, now=150, expected_agent_id="agent-2"
    ).status == EvidenceVerificationStatus.IDENTITY_MISMATCH
    assert RuntimeEvidenceVerifier(_policy(issuer)).verify(
        evidence, now=150, expected_scope="other-scope"
    ).status == EvidenceVerificationStatus.SCOPE_MISMATCH


def test_identity_registry_binds_public_key_with_one_time_proof_of_possession():
    identity_issuer = _issuer(
        "identity-registry",
        AuthorityKind.IDENTITY_REGISTRY,
        (EvidenceCategory.IDENTITY,),
    )
    registry = AgentIdentityRegistry(identity_issuer)
    agent_key = SigningKey(bytes([9]) * 32)
    challenge = registry.create_challenge("agent-1")
    proof = agent_key.sign(
        registry.registration_payload("agent-1", challenge)
    ).signature
    evidence = registry.register_agent(
        agent_id="agent-1",
        public_key=bytes(agent_key.verify_key),
        challenge=challenge,
        proof_signature=proof,
        experiment_id="experiment-1",
        created_at=100,
        expires_at=200,
    )

    assert registry.is_registered("agent-1")
    assert registry.public_key_for("agent-1") == bytes(agent_key.verify_key)
    assert RuntimeEvidenceVerifier(
        _policy(identity_issuer), identities=registry
    ).verify(evidence, now=150, expected_agent_id="agent-1").status == (
        EvidenceVerificationStatus.VALID
    )
    with pytest.raises(ValueError, match="challenge"):
        registry.register_agent(
            agent_id="agent-1",
            public_key=bytes(agent_key.verify_key),
            challenge=challenge,
            proof_signature=proof,
            experiment_id="experiment-1",
            created_at=100,
            expires_at=200,
        )


def test_identity_evidence_does_not_authorize_an_unregistered_agent():
    issuer = _issuer(
        "identity-registry",
        AuthorityKind.IDENTITY_REGISTRY,
        (EvidenceCategory.IDENTITY,),
    )
    registry = AgentIdentityRegistry(issuer)
    evidence = issuer.issue_evidence(
        category=EvidenceCategory.IDENTITY,
        agent_id="agent-1",
        experiment_id="experiment-1",
        subject="agent-1",
        predicate="public_key_bound",
        asserted_value="not-registered",
        scope="agent_identity",
        created_at=100,
        expires_at=200,
    )

    assert RuntimeEvidenceVerifier(
        _policy(issuer), identities=registry
    ).verify(evidence, now=150).status == (
        EvidenceVerificationStatus.IDENTITY_UNREGISTERED
    )


def test_permission_and_demonstrated_capability_have_separate_authorities():
    permission_issuer = _issuer(
        "runtime",
        AuthorityKind.RUNTIME_EXECUTION,
        (EvidenceCategory.CONFIGURED_PERMISSION,),
    )
    evaluator = _issuer(
        "evaluator",
        AuthorityKind.EXPERIMENT_EVALUATOR,
        (EvidenceCategory.DEMONSTRATED_CAPABILITY,),
    )
    permission = _evidence(permission_issuer)
    demonstrated = evaluator.issue_evidence(
        category=EvidenceCategory.DEMONSTRATED_CAPABILITY,
        agent_id="agent-1",
        experiment_id="experiment-1",
        subject="capability",
        predicate="demonstrated",
        asserted_value=True,
        scope="task:task-1",
        created_at=100,
        expires_at=200,
    )

    assert permission.category == EvidenceCategory.CONFIGURED_PERMISSION
    assert demonstrated.category == EvidenceCategory.DEMONSTRATED_CAPABILITY
    with pytest.raises(ValueError, match="not authorized"):
        permission_issuer.issue_evidence(
            category=EvidenceCategory.DEMONSTRATED_CAPABILITY,
            agent_id="agent-1",
            experiment_id="experiment-1",
            subject="capability",
            predicate="demonstrated",
            asserted_value=True,
            scope="task:task-1",
            created_at=100,
            expires_at=200,
        )


def test_simulated_tool_evidence_is_not_actual_execution_evidence():
    issuer = _tool_issuer()
    simulated = _evidence(
        issuer,
        category=EvidenceCategory.TOOL_EXECUTION,
        execution_status=EventExecutionStatus.LOCAL_SIMULATION,
    )
    simulated_result = RuntimeEvidenceVerifier(_policy(issuer)).verify(
        simulated, now=150
    )
    actual_required = RuntimeEvidenceVerifier(_policy(issuer)).verify(
        simulated, now=150, require_actual_execution=True
    )
    actual = _evidence(
        issuer,
        category=EvidenceCategory.TOOL_EXECUTION,
        execution_status=EventExecutionStatus.RECORDED,
        evidence_id="actual-tool",
        nonce="actual-nonce",
    )
    actual_result = RuntimeEvidenceVerifier(_policy(issuer)).verify(
        actual, now=150, require_actual_execution=True
    )

    assert simulated_result.status == EvidenceVerificationStatus.VALID
    assert actual_required.status == EvidenceVerificationStatus.SIMULATED_EXECUTION
    assert actual_result.status == EvidenceVerificationStatus.VALID


def test_evidence_json_round_trip_and_missing_reference_status():
    issuer = _issuer()
    evidence = _evidence(issuer)

    assert RuntimeEvidence.from_json(evidence.to_json()) == evidence
    assert RuntimeEvidenceVerifier(_policy(issuer)).verify_reference(
        "absent-evidence", {}, now=150
    ).status == EvidenceVerificationStatus.MISSING


def test_message_evidence_link_validation_checks_referenced_context():
    issuer = _issuer()
    evidence = _evidence(issuer)
    assertion = ClaimBearingAssertion(
        assertion_id="assertion-1",
        provenance_claim_id="claim-1",
        agent_id="agent-1",
        claim_type=ProvenanceClaimType.IDENTITY,
        subject="identity",
        predicate="is",
        asserted_value="agent-1",
        scope="task",
        trace_id="trace-1",
        message_id="message-1",
        evidence_refs=("evidence-1",),
    )
    message = ClaimBearingMessage(
        message_id="message-1",
        experiment_id="experiment-1",
        trace_id="trace-1",
        task_id="task-1",
        agent_id="agent-1",
        claimed_identity="agent-1",
        claimed_role="analyst",
        subtask_id=None,
        message_type=ClaimMessageType.EVIDENCE_REFERENCE,
        content="identity evidence",
        assertions=(assertion,),
        evidence_ids=("evidence-1",),
        handoff_to=None,
        sequence_number=3,
    )

    validate_message_evidence_links((message,), {"evidence-1": evidence})
    mismatched = replace(evidence, agent_id="agent-2")
    with pytest.raises(ValueError, match="different agent"):
        validate_message_evidence_links((message,), {"evidence-1": mismatched})


def test_invalid_registration_signature_does_not_bind_identity():
    issuer = _issuer(
        "identity-registry",
        AuthorityKind.IDENTITY_REGISTRY,
        (EvidenceCategory.IDENTITY,),
    )
    registry = AgentIdentityRegistry(issuer)
    agent_key = SigningKey(bytes([10]) * 32)
    attacker_key = SigningKey(bytes([11]) * 32)
    challenge = registry.create_challenge("agent-1")
    bad_signature = attacker_key.sign(
        registry.registration_payload("agent-1", challenge)
    ).signature

    with pytest.raises(ValueError, match="proof is invalid"):
        registry.register_agent(
            agent_id="agent-1",
            public_key=bytes(agent_key.verify_key),
            challenge=challenge,
            proof_signature=bad_signature,
            experiment_id="experiment-1",
            created_at=100,
            expires_at=200,
        )
    assert not registry.is_registered("agent-1")


def test_legacy_ledger_signature_api_remains_compatible():
    issuer = _issuer()
    evidence = _evidence(issuer)
    assert EvidenceVerifier(issuer.public_key).verify_payload(
        evidence.signing_payload(),
        base64.b64decode(evidence.signature),
    )
