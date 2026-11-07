from dataclasses import replace
from io import StringIO
import json

import pytest

from dataset.claim_bearing_agents import (
    AgentBehaviour,
    ScenarioName,
    claim_jsonl,
    evidence_jsonl,
    evaluator_jsonl,
    message_jsonl,
    run_claim_bearing_experiment,
    trace_jsonl,
    trust_policy_json,
)
from dataset.claim_message_schemas import (
    CapabilityState,
    ClaimMessageType,
    ProvenanceClaimType,
    claim_message_from_json,
    validate_messages_against_trace,
)
from dataset.experiment_schemas import EventExecutionStatus, TraceEventType
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
import run_claim_bearing_experiment as runner


def _run(*, scenarios=(ScenarioName.ARITHMETIC,), behaviours=None, seed=13):
    return run_claim_bearing_experiment(
        seed=seed,
        scenarios=scenarios,
        behaviours=behaviours,
    )


def _sidecar_by_assertion(run):
    return {
        assertion["assertion_id"]: assertion
        for task in run.evaluator_sidecar
        for assertion in task["assertions"]
    }


def test_same_seed_and_configuration_produce_identical_detector_and_evaluator_outputs():
    first = _run(scenarios=None)
    second = _run(scenarios=None)

    assert first.experiment_id == second.experiment_id
    assert trace_jsonl(first) == trace_jsonl(second)
    assert message_jsonl(first) == message_jsonl(second)
    assert claim_jsonl(first) == claim_jsonl(second)
    assert evidence_jsonl(first) == evidence_jsonl(second)
    assert evaluator_jsonl(first) == evaluator_jsonl(second)
    assert trust_policy_json(first) == trust_policy_json(second)


def test_different_seeds_produce_independent_deterministic_experiment_ids():
    first = _run(seed=13)
    second = _run(seed=14)

    assert first.experiment_id != second.experiment_id
    assert first.traces[0].trace_id != second.traces[0].trace_id


def test_all_honest_claims_match_observable_task_and_agent_facts():
    run = _run(
        scenarios=tuple(ScenarioName),
        behaviours=(AgentBehaviour.HONEST_AGENT,) * 5,
    )

    assert all(
        assertion["matches_expected"]
        for task in run.evaluator_sidecar
        for assertion in task["assertions"]
    )
    assert len(run.traces) == len(ScenarioName)
    assert run.simulated_tool_invocations == len(ScenarioName)
    assert any(
        event.event_type == TraceEventType.TASK_FAILED
        for trace in run.traces
        for event in trace.events
    )


def test_false_reporting_agent_makes_a_false_tool_execution_claim_without_altering_trace():
    behaviours = tuple(AgentBehaviour)
    run = _run(behaviours=behaviours)
    false_agent = next(
        agent["agent_id"]
        for agent in run.evaluator_sidecar[0]["agents"]
        if agent["behaviour_category"] == AgentBehaviour.FALSE_REPORTING_AGENT.value
    )
    false_assertion = next(
        assertion
        for task in run.evaluator_sidecar
        for assertion in task["assertions"]
        if assertion["agent_id"] == false_agent
        and assertion["claim_type"] == ProvenanceClaimType.TOOL_USAGE.value
    )

    assert false_assertion["asserted_value"]["execution_status"] == "RECORDED"
    assert false_assertion["matches_expected"] is False
    assert run.simulated_tool_invocations == 1
    invocation = next(
        event
        for event in run.traces[0].events
        if event.event_type == TraceEventType.TOOL_INVOCATION
    )
    assert invocation.actor_id != false_agent or (
        invocation.execution_status == EventExecutionStatus.LOCAL_SIMULATION
    )
    assert invocation.execution_status == EventExecutionStatus.LOCAL_SIMULATION


def test_identity_mismatch_does_not_change_registered_or_transport_identity():
    run = _run(behaviours=tuple(AgentBehaviour))
    sidecar = run.evaluator_sidecar[0]
    mismatch_agent = next(
        agent for agent in sidecar["agents"]
        if agent["behaviour_category"] == AgentBehaviour.IDENTITY_MISMATCH_AGENT.value
    )
    assertion = next(
        assertion for assertion in sidecar["assertions"]
        if assertion["agent_id"] == mismatch_agent["agent_id"]
        and assertion["claim_type"] == ProvenanceClaimType.IDENTITY.value
    )
    message = next(
        message for message in run.messages
        if message.message_id == assertion["message_id"]
    ) if "message_id" in assertion else next(
        message for message in run.messages
        if any(item.assertion_id == assertion["assertion_id"] for item in message.assertions)
    )
    evidence = {item.evidence_id: item for item in run.evidence}

    assert message.claimed_identity == mismatch_agent["ground_truth_identity"]
    assert assertion["asserted_value"] != mismatch_agent["ground_truth_identity"]
    assert any(
        evidence[reference].category == EvidenceCategory.IDENTITY
        for reference in assertion["evidence_refs"]
    )


def test_capability_exaggeration_keeps_permission_distinct_from_demonstration():
    run = _run(behaviours=tuple(AgentBehaviour))
    sidecar = run.evaluator_sidecar[0]
    exaggerated_agent = next(
        agent["agent_id"] for agent in sidecar["agents"]
        if agent["behaviour_category"]
        == AgentBehaviour.CAPABILITY_EXAGGERATION_AGENT.value
    )
    assertions = [
        assertion for assertion in sidecar["assertions"]
        if assertion["agent_id"] == exaggerated_agent
        and assertion["claim_type"] == ProvenanceClaimType.CAPABILITY.value
    ]
    evidence = {item.evidence_id: item for item in run.evidence}
    permission = next(
        assertion for assertion in assertions
        if assertion["capability_state"] == CapabilityState.CONFIGURED_PERMISSION.value
        and assertion["subject"] == "local.reverse_text"
    )
    demonstration = next(
        assertion for assertion in assertions
        if assertion["capability_state"] == CapabilityState.DEMONSTRATED_CAPABILITY.value
    )

    assert permission["asserted_value"]["granted"] is True
    assert demonstration["asserted_value"]["demonstrated"] is True
    assert demonstration["expected_asserted_value"]["demonstrated"] is False
    assert evidence[demonstration["evidence_refs"][0]].category == (
        EvidenceCategory.CONFIGURED_PERMISSION
    )


def test_evidence_misrepresentation_uses_existing_but_non_supporting_evidence():
    run = _run(behaviours=tuple(AgentBehaviour))
    evidence = {item.evidence_id: item for item in run.evidence}
    assertion = next(
        assertion for task in run.evaluator_sidecar
        for assertion in task["assertions"]
        if assertion["claim_type"] == ProvenanceClaimType.EVIDENCE_SOURCE.value
        and assertion["asserted_value"] != assertion["expected_asserted_value"]
    )
    referenced = evidence[assertion["evidence_refs"][0]]

    assert referenced.agent_id == assertion["agent_id"]
    assert referenced.source_ref != assertion["asserted_value"]["source_ref"]
    assert assertion["asserted_value"]["evidence_id"] == referenced.evidence_id


def test_all_references_resolve_and_all_runtime_evidence_signatures_verify():
    run = _run(scenarios=None)
    evidence = {item.evidence_id: item for item in run.evidence}

    for message in run.messages:
        assert set(message.evidence_ids).issubset(evidence)
        for assertion in message.assertions:
            assert set(assertion.evidence_refs).issubset(evidence)
    verifier = RuntimeEvidenceVerifier(run.trust_policy)
    for record in run.evidence:
        assert verifier.verify(
            record,
            now=record.created_at + 1,
            expected_agent_id=record.agent_id,
            expected_experiment_id=run.experiment_id,
            expected_trace_id=record.trace_id,
            expected_task_id=record.task_id,
        ).status == EvidenceVerificationStatus.VALID


def test_tampered_runtime_evidence_is_rejected():
    run = _run()
    original = run.evidence[0]
    tampered = replace(original, asserted_value="tampered")
    result = RuntimeEvidenceVerifier(run.trust_policy).verify(
        tampered, now=tampered.created_at + 1
    )

    assert result.status == EvidenceVerificationStatus.INVALID_SIGNATURE


def test_evaluator_attestations_verify_only_with_sidecar_trust_metadata():
    run = _run()
    sidecar = run.evaluator_sidecar[0]
    attestations = sidecar["attestations"]
    public = attestations["trusted_issuer"]
    trusted_issuer = TrustedIssuer(
        issuer_id=public["issuer_id"],
        authority_kind=AuthorityKind(public["authority_kind"]),
        public_key=public["public_key"],
        categories=tuple(EvidenceCategory(value) for value in public["categories"]),
    )
    verifier = RuntimeEvidenceVerifier(TrustPolicy((trusted_issuer,)))
    records = (
        RuntimeEvidence.from_dict(attestations["completion_attestation"]),
        RuntimeEvidence.from_dict(
            attestations["demonstrated_capability_attestation"]
        ),
    )

    assert all(
        verifier.verify(record, now=record.created_at + 1).trusted
        for record in records
    )
    assert "experiment-evaluator" not in trust_policy_json(run)


def test_detector_facing_serializations_exclude_evaluator_only_fields_and_private_keys():
    run = _run(scenarios=None)
    detector_text = "\n".join(
        (
            trace_jsonl(run),
            message_jsonl(run),
            claim_jsonl(run),
            evidence_jsonl(run),
            trust_policy_json(run),
        )
    ).casefold()

    for forbidden in (
        "is_malicious",
        "is_false",
        "expected_asserted_value",
        "ground_truth",
        "behavior_category",
        "behaviour_category",
        "matches_expected",
        "completion_attestation",
        "private_key",
        "secret_key",
    ):
        assert forbidden not in detector_text
    assert "experiment-evaluator" not in detector_text
    assert "experiment-evaluator" in evaluator_jsonl(run)


def test_evaluator_sidecar_is_written_separately_from_runtime_outputs(tmp_path, monkeypatch):
    root = tmp_path / "controlled" / "runs"
    output = root / "demo"
    output.mkdir(parents=True)
    monkeypatch.setattr(runner, "CONTROLLED_RUNS_DIR", root)
    run = _run()

    detector_files = runner.write_detector_outputs(run, output)
    sidecar_path = runner.write_evaluator_sidecar(
        run, output / f"{run.experiment_id}.evaluator.jsonl"
    )

    assert sidecar_path not in detector_files.values()
    assert sidecar_path.exists()
    assert "expected_asserted_value" not in detector_files["claims"].read_text()
    assert "behavior_category" not in detector_files["messages"].read_text()
    assert "expected_asserted_value" in sidecar_path.read_text()
    public_policy = json.loads(detector_files["trust"].read_text())
    assert all(
        issuer["issuer_id"] != "experiment-evaluator"
        for issuer in public_policy["issuers"]
    )


def test_trace_message_and_claim_jsonl_round_trip_and_associations():
    run = _run(scenarios=(ScenarioName.RELAY,))
    restored_traces = read_traces_jsonl(
        StringIO(trace_jsonl(run))
    )
    restored_messages = tuple(
        claim_message_from_json(line)
        for line in message_jsonl(run).splitlines()
    )
    assert len(restored_traces) == 1
    assert len(restored_messages) == len(run.messages)
    for message in restored_messages:
        trace = restored_traces[0]
        validate_messages_against_trace(
            (message,),
            trace,
            experiment_id=run.experiment_id,
            agent_ids=tuple(
                item["agent_id"]
                for item in run.evaluator_sidecar[0]["agents"]
            ),
        )
    assert all(json.loads(line)["assertion_id"] for line in claim_jsonl(run).splitlines())
    assert all(
        RuntimeEvidence.from_json(line).evidence_id
        for line in evidence_jsonl(run).splitlines()
    )
    message_types = {message.message_type for message in restored_messages}
    assert message_types == set(ClaimMessageType)


def test_runner_restricts_output_directory_to_controlled_runs_tree(tmp_path):
    root = tmp_path / "controlled" / "runs"
    assert runner._output_directory(root / "nested", root=root) == root / "nested"
    assert runner._output_directory("nested", root=root) == root / "nested"
    with pytest.raises(ValueError, match="controlled runs directory"):
        runner._output_directory(tmp_path / "outside", root=root)


def test_behavior_does_not_change_actual_local_invocation_status():
    honest = _run(behaviours=(AgentBehaviour.HONEST_AGENT,) * 5)
    deceptive = _run(behaviours=tuple(AgentBehaviour))
    honest_invocation = next(
        event for event in honest.traces[0].events
        if event.event_type == TraceEventType.TOOL_INVOCATION
    )
    deceptive_invocation = next(
        event for event in deceptive.traces[0].events
        if event.event_type == TraceEventType.TOOL_INVOCATION
    )

    assert honest_invocation.execution_status == deceptive_invocation.execution_status
    assert honest_invocation.tool_name == deceptive_invocation.tool_name
    assert honest_invocation.actor_id.endswith("-02")
    assert deceptive_invocation.actor_id.endswith("-02")


def test_agent_counts_above_default_behavior_roster_remain_valid():
    run = run_claim_bearing_experiment(
        seed=15,
        scenarios=(ScenarioName.ARITHMETIC,),
        agents_per_task=7,
    )

    assert run.participating_agents == 7
    assert len(run.messages) > 0
