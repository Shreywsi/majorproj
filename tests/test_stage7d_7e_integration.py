import base64
from dataclasses import replace
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
    ClaimBearingAssertion,
    ClaimBearingMessage,
    ClaimMessageType,
    ProvenanceClaimType,
)
from dataset.independent_evaluator import (
    EvaluationInputError,
    evaluate_claim_bearing_experiments,
)
from dataset.runtime_evidence import (
    AuthorityKind,
    EvidenceCategory,
    RuntimeEvidenceIssuer,
    TrustPolicy,
)
from run_claim_bearing_detection import (
    DetectionInputError,
    _evidence_results,
    detect_claim_bearing_experiment,
)


def _run(seed=811):
    return run_claim_bearing_experiment(
        seed=seed,
        scenarios=(ScenarioName.ARITHMETIC,),
        behaviours=tuple(AgentBehaviour),
    )


def _write_inputs(run, directory):
    paths = {}
    for name, content in (
        ("traces", trace_jsonl(run)),
        ("messages", message_jsonl(run)),
        ("claims", claim_jsonl(run)),
        ("evidence", evidence_jsonl(run)),
        ("evaluator", evaluator_jsonl(run)),
        ("trust", trust_policy_json(run)),
    ):
        suffix = ".json" if name == "trust" else (
            ".evaluator.jsonl" if name == "evaluator" else f".{name}.jsonl"
        )
        path = directory / f"{run.experiment_id}{suffix}"
        path.write_text(content, encoding="utf-8")
        paths[name] = path
    return paths


def _detect(paths, *, now=None):
    return detect_claim_bearing_experiment(
        traces_path=paths["traces"],
        messages_path=paths["messages"],
        claims_path=paths["claims"],
        evidence_path=paths["evidence"],
        trust_policy_path=paths["trust"],
        now=now,
    )


def _prediction(assertion_id, outcome, *, experiment_id="experiment-test"):
    return {
        "experiment_id": experiment_id,
        "assertion_id": assertion_id,
        "trace_id": "trace-test",
        "task_id": "task-test",
        "agent_id": "agent-test",
        "claim_type": "TOOL_USAGE",
        "detector_outcome": outcome,
        "evidence_verification": [],
    }


def _sidecar(assertions, *, experiment_id="experiment-test", seed=1):
    return {
        "experiment_id": experiment_id,
        "trace_id": "trace-test",
        "task_id": "task-test",
        "scenario": "scenario-test",
        "seed": seed,
        "agents": [{
            "agent_id": "agent-test",
            "behaviour_category": "HONEST_AGENT",
        }],
        "assertions": assertions,
    }


def _assertion(assertion_id, matches=None, label=None):
    record = {
        "assertion_id": assertion_id,
        "agent_id": "agent-test",
        "claim_type": "TOOL_USAGE",
        "message_id": f"message-{assertion_id}",
    }
    if matches is not None:
        record["matches_expected"] = matches
    if label is not None:
        record["ground_truth_label"] = label
    return record


def _message(
    *,
    message_id,
    assertion_id,
    agent_id="agent-a",
    task_id="task-a",
    trace_id="trace-a",
    sequence=1,
    claim_type=ProvenanceClaimType.TOOL_USAGE,
    value=True,
    subject="tool-x",
    scope="task:task-a",
    capability_state=None,
):
    assertion = ClaimBearingAssertion(
        assertion_id=assertion_id,
        provenance_claim_id=f"claim-{assertion_id}",
        agent_id=agent_id,
        claim_type=claim_type,
        subject=subject,
        predicate=(
            "execution_status"
            if claim_type == ProvenanceClaimType.TOOL_USAGE
            else "status"
        ),
        asserted_value=value,
        scope=scope,
        trace_id=trace_id,
        message_id=message_id,
        capability_state=capability_state,
    )
    return ClaimBearingMessage(
        message_id=message_id,
        experiment_id="experiment-test",
        trace_id=trace_id,
        task_id=task_id,
        agent_id=agent_id,
        claimed_identity=agent_id,
        claimed_role="operator",
        subtask_id=None,
        message_type=ClaimMessageType.STATUS_UPDATE,
        content={"status": "reported"},
        assertions=(assertion,),
        evidence_ids=(),
        handoff_to=None,
        sequence_number=sequence,
    )


def _trace(run, *, trace_id="trace-a", task_id="task-a"):
    return replace(run.traces[0], trace_id=trace_id, task_id=task_id)


def test_stage7c_inputs_load_and_produce_assertion_findings(tmp_path):
    run = _run()
    paths = _write_inputs(run, tmp_path)
    result = _detect(paths)

    assert result["experiment_id"] == run.experiment_id
    assert result["assertions_evaluated"] == len(run.assertions)
    assert {row["assertion_id"] for row in result["findings"]} == {
        item.assertion_id for item in run.assertions
    }
    assert result["evidence_verification_status_counts"]["valid"] == len(run.evidence)


def test_invalid_claim_message_association_is_rejected(tmp_path):
    run = _run()
    paths = _write_inputs(run, tmp_path)
    claims = [json.loads(line) for line in paths["claims"].read_text().splitlines()]
    claims[0]["agent_id"] = "agent-not-associated"
    paths["claims"].write_text(
        "".join(json.dumps(row) + "\n" for row in claims), encoding="utf-8"
    )

    with pytest.raises(DetectionInputError, match="disagrees with its structured message"):
        _detect(paths)


def test_stage7b_signature_verification_failure_is_explicit(tmp_path):
    run = _run()
    paths = _write_inputs(run, tmp_path)
    evidence = [json.loads(line) for line in paths["evidence"].read_text().splitlines()]
    evidence[0]["signature"] = base64.b64encode(bytes(64)).decode("ascii")
    paths["evidence"].write_text(
        "".join(json.dumps(row) + "\n" for row in evidence), encoding="utf-8"
    )

    result = _detect(paths)
    assert any(
        item["status"] == "invalid_signature"
        for item in result["evidence_verification_failures"]
    )


def test_expired_evidence_is_not_silently_discarded(tmp_path):
    run = _run()
    paths = _write_inputs(run, tmp_path)
    rows = [json.loads(line) for line in paths["evidence"].read_text().splitlines()]
    now = max(row["expires_at"] for row in rows)

    result = _detect(paths, now=now)

    assert result["evidence_verification_status_counts"]["expired"] == len(rows)


def test_replayed_nonce_is_reported_by_stage7b_verifier():
    issuer = RuntimeEvidenceIssuer(
        "test-runtime",
        AuthorityKind.RUNTIME_EXECUTION,
        (EvidenceCategory.CONFIGURED_PERMISSION,),
        seed=bytes([19]) * 32,
    )
    records = [
        issuer.issue_evidence(
            evidence_id=f"evidence-{index}",
            nonce="same-nonce",
            category=EvidenceCategory.CONFIGURED_PERMISSION,
            agent_id="agent-test",
            experiment_id="experiment-test",
            subject="capability",
            predicate="permission",
            asserted_value=True,
            scope="task:task-test",
            created_at=100,
            expires_at=200,
        )
        for index in range(2)
    ]
    results = _evidence_results(
        evidence_by_id={record.evidence_id: record for record in records},
        policy=TrustPolicy((issuer.trusted_issuer(),)),
        trace_by_id={},
        experiment_id="experiment-test",
        now=101,
    )

    assert sorted(result["status"] for result in results.values()) == [
        "replayed", "valid"
    ]


def test_supported_claims_use_existing_stage4_outcome(tmp_path):
    run = _run()
    paths = _write_inputs(run, tmp_path)
    result = _detect(paths)

    assert any(row["detector_outcome"] == "SUPPORTED" for row in result["findings"])


def test_simulated_false_tool_use_is_not_mislabeled_as_contradicted(tmp_path):
    run = _run()
    paths = _write_inputs(run, tmp_path)
    result = _detect(paths)
    truth = {
        assertion["assertion_id"]: assertion
        for record in run.evaluator_sidecar
        for assertion in record["assertions"]
    }
    false_tool_assertions = {
        assertion_id
        for assertion_id, assertion in truth.items()
        if not assertion["matches_expected"]
        and assertion["claim_type"] == ProvenanceClaimType.TOOL_USAGE.value
    }

    assert false_tool_assertions
    assert all(
        next(row for row in result["findings"] if row["assertion_id"] == assertion_id)[
            "detector_outcome"
        ] != "CONTRADICTED"
        for assertion_id in false_tool_assertions
    )


def test_missing_evidence_has_explicit_insufficient_state(tmp_path):
    run = _run()
    paths = _write_inputs(run, tmp_path)
    rows = [json.loads(line) for line in paths["evidence"].read_text().splitlines()]
    removed_id = rows[0]["evidence_id"]
    paths["evidence"].write_text(
        "".join(json.dumps(row) + "\n" for row in rows[1:]), encoding="utf-8"
    )

    result = _detect(paths)
    finding = next(
        row for row in result["findings"] if removed_id in row["evidence_ids"]
    )
    assert finding["detector_outcome"] == "INSUFFICIENT_EVIDENCE"
    assert finding["evidence_verification"][0]["status"] == "missing"
    assert result["evidence_association_status_counts"]["missing"] == 1


def test_cross_message_capability_exaggeration_is_detected(tmp_path):
    run = _run()
    result = _detect(_write_inputs(run, tmp_path))
    # Detection integration includes the separate cross-message findings.
    assert any(
        row["contradiction_kind"] == "permission_misrepresented_as_demonstration"
        for row in result["cross_message_findings"]
    )
    assert not any(
        row["contradiction_kind"] == "permission_misrepresented_as_demonstration"
        and row["subject"] == "local.add"
        for row in result["cross_message_findings"]
    )


def test_cross_message_tool_conflict_is_detected_for_same_agent_task_and_scope():
    run = _run()
    first = _message(
        message_id="message-a", assertion_id="assertion-a", value=False
    )
    second = _message(
        message_id="message-b", assertion_id="assertion-b", sequence=2, value=True
    )
    from dataset.cross_message_analysis import analyze_cross_message_assertions

    findings = analyze_cross_message_assertions(
        (first, second), {"trace-a": _trace(run)}
    )

    assert len(findings) == 1
    assert findings[0]["contradiction_kind"] == "tool_invocation_status_conflict"
    assert findings[0]["assertion_ids"] == ["assertion-a", "assertion-b"]


def test_cross_message_analysis_does_not_compare_different_agents():
    run = _run()
    messages = (
        _message(message_id="message-a", assertion_id="assertion-a", value=False),
        _message(
            message_id="message-b", assertion_id="assertion-b",
            agent_id="agent-b", sequence=2, value=True,
        ),
    )
    from dataset.cross_message_analysis import analyze_cross_message_assertions

    assert analyze_cross_message_assertions(messages, {"trace-a": _trace(run)}) == ()


def test_cross_message_analysis_does_not_compare_different_tasks():
    run = _run()
    messages = (
        _message(message_id="message-a", assertion_id="assertion-a", value=False),
        _message(
            message_id="message-b", assertion_id="assertion-b",
            task_id="task-b", trace_id="trace-b", sequence=2, value=True,
            scope="task:task-b",
        ),
    )
    from dataset.cross_message_analysis import analyze_cross_message_assertions

    findings = analyze_cross_message_assertions(
        messages,
        {
            "trace-a": _trace(run, trace_id="trace-a", task_id="task-a"),
            "trace-b": _trace(run, trace_id="trace-b", task_id="task-b"),
        },
    )
    assert findings == ()


def test_cross_message_analysis_does_not_compare_different_scopes():
    run = _run()
    messages = (
        _message(message_id="message-a", assertion_id="assertion-a", value=False),
        _message(
            message_id="message-b", assertion_id="assertion-b", sequence=2,
            value=True, scope="subtask:other",
        ),
    )
    from dataset.cross_message_analysis import analyze_cross_message_assertions

    assert analyze_cross_message_assertions(messages, {"trace-a": _trace(run)}) == ()


def test_evaluator_sidecar_labels_never_appear_in_detector_outputs(tmp_path):
    run = _run()
    paths = _write_inputs(run, tmp_path)
    result = _detect(paths)
    serialized = json.dumps({
        "findings": result["findings"],
        "cross": result["cross_message_findings"],
        "summary": {
            key: value for key, value in result.items()
            if key not in {"findings", "cross_message_findings"}
        },
    })

    assert "matches_expected" not in serialized
    assert "expected_asserted_value" not in serialized
    assert "behaviour_category" not in serialized
    assert "ground_truth_label" not in serialized


def test_evaluator_counts_true_positive_false_positive_and_false_negative():
    sidecar = _sidecar([
        _assertion("tp", matches=False),
        _assertion("fp", matches=True),
        _assertion("fn", matches=False),
        _assertion("tn", matches=True),
        _assertion("unknown", label="inconclusive"),
    ])
    findings = [
        _prediction("tp", "CONTRADICTED"),
        _prediction("fp", "CONTRADICTED"),
        _prediction("fn", "INSUFFICIENT_EVIDENCE"),
        _prediction("tn", "SUPPORTED"),
        _prediction("unknown", "CONTRADICTED"),
    ]
    result = evaluate_claim_bearing_experiments(findings, [], [sidecar])
    metrics = result["summary"]["assertion_metrics"]

    assert metrics["true_positives"] == 1
    assert metrics["false_positives"] == 1
    assert metrics["false_negatives"] == 1
    assert metrics["true_negatives"] == 1
    assert metrics["accuracy"] == 0.5


def test_inconclusive_ground_truth_is_excluded_from_binary_denominator():
    sidecar = _sidecar([
        _assertion("truth", matches=True),
        _assertion("unknown", label="inconclusive"),
    ])
    findings = [
        _prediction("truth", "SUPPORTED"),
        _prediction("unknown", "CONTRADICTED"),
    ]
    metrics = evaluate_claim_bearing_experiments(
        findings, [], [sidecar]
    )["summary"]["assertion_metrics"]

    assert metrics["inconclusive_ground_truth"] == 1
    assert metrics["assertions_total"] == 2
    assert metrics["binary_metric_denominator"] == 1


def test_duplicate_ground_truth_assertion_ids_are_rejected():
    sidecar = _sidecar([_assertion("duplicate", matches=True)])
    second = {**sidecar, "trace_id": "trace-second", "task_id": "task-second"}
    with pytest.raises(EvaluationInputError, match="Duplicate assertion_id"):
        evaluate_claim_bearing_experiments(
            [
                _prediction("duplicate", "SUPPORTED"),
            ],
            [],
            [sidecar, second],
        )


def test_unknown_prediction_assertion_ids_are_rejected():
    sidecar = _sidecar([_assertion("known", matches=True)])
    with pytest.raises(EvaluationInputError, match="unknown assertion_id"):
        evaluate_claim_bearing_experiments(
            [_prediction("unknown", "SUPPORTED")], [], [sidecar]
        )


def test_duplicate_detector_assertion_predictions_are_rejected():
    sidecar = _sidecar([_assertion("known", matches=True)])
    with pytest.raises(EvaluationInputError, match="Duplicate detector prediction"):
        evaluate_claim_bearing_experiments(
            [
                _prediction("known", "SUPPORTED"),
                _prediction("known", "SUPPORTED"),
            ],
            [],
            [sidecar],
        )


def test_missing_prediction_is_not_silently_ignored():
    sidecar = _sidecar([_assertion("known", matches=True)])
    with pytest.raises(EvaluationInputError, match="missing predictions"):
        evaluate_claim_bearing_experiments([], [], [sidecar])


def test_repeated_detection_and_evaluation_are_deterministic(tmp_path):
    run = _run(seed=812)
    paths = _write_inputs(run, tmp_path)
    first = _detect(paths)
    second = _detect(paths)
    assert first == second

    sidecar = [json.loads(line) for line in paths["evaluator"].read_text().splitlines()]
    first_evaluation = evaluate_claim_bearing_experiments(
        first["findings"], first["cross_message_findings"], sidecar
    )
    second_evaluation = evaluate_claim_bearing_experiments(
        second["findings"], second["cross_message_findings"], sidecar
    )
    assert first_evaluation["summary"] == second_evaluation["summary"]


def test_cross_message_capability_claim_needs_matching_execution_to_avoid_finding():
    run = _run()
    from dataset.cross_message_analysis import analyze_cross_message_assertions
    from dataset.claim_message_schemas import CapabilityState

    capability = "unobserved.capability"
    first = _message(
        message_id="permission",
        assertion_id="permission-assertion",
        claim_type=ProvenanceClaimType.CAPABILITY,
        value={"capability": capability, "granted": True},
        subject=capability,
        capability_state=CapabilityState.CONFIGURED_PERMISSION,
    )
    second = _message(
        message_id="demonstration",
        assertion_id="demonstration-assertion",
        sequence=2,
        claim_type=ProvenanceClaimType.CAPABILITY,
        value={"capability": capability, "demonstrated": True},
        subject=capability,
        capability_state=CapabilityState.DEMONSTRATED_CAPABILITY,
    )
    findings = analyze_cross_message_assertions(
        (first, second), {"trace-a": _trace(run)}
    )
    assert [row["contradiction_kind"] for row in findings] == [
        "permission_misrepresented_as_demonstration"
    ]


def test_cross_message_time_window_respected():
    run = _run()
    first = _message(
        message_id="message-a", assertion_id="assertion-a", value=False
    )
    second = replace(
        _message(
            message_id="message-b",
            assertion_id="assertion-b",
            sequence=2,
            value=True,
        ),
        timestamp=100_000,
    )
    first = replace(first, timestamp=1)
    from dataset.cross_message_analysis import analyze_cross_message_assertions

    assert analyze_cross_message_assertions(
        (first, second), {"trace-a": _trace(run)}, max_time_window_seconds=5
    ) == ()


def test_cross_message_identity_finding_respects_time_window():
    run = _run()
    identity_message = _message(
        message_id="identity-message",
        assertion_id="identity-assertion",
        claim_type=ProvenanceClaimType.IDENTITY,
        value="agent-b",
    )
    identity_assertion = replace(
        identity_message.assertions[0],
        predicate="claimed_identity",
        subject="identity",
    )
    identity_message = replace(
        identity_message,
        assertions=(identity_assertion,),
        timestamp=1,
    )
    corroborating_message = replace(
        _message(
            message_id="corroborating-message",
            assertion_id="corroborating-assertion",
            sequence=2,
            subject="identity",
        ),
        timestamp=100,
    )
    from dataset.cross_message_analysis import analyze_cross_message_assertions

    assert analyze_cross_message_assertions(
        (identity_message, corroborating_message),
        {"trace-a": _trace(run)},
        max_time_window_seconds=5,
    ) == ()
    assert analyze_cross_message_assertions(
        (identity_message, replace(corroborating_message, timestamp=2)),
        {"trace-a": _trace(run)},
        max_time_window_seconds=5,
    )[0]["contradiction_kind"] == "identity_claim_conflicts_with_sender"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_id", "trace-other"),
        ("task_id", "task-other"),
        ("agent_id", "agent-other"),
    ],
)
def test_evaluator_rejects_mismatched_cross_message_associations(field, value):
    sidecar = _sidecar([_assertion("known", matches=True)])
    prediction = [_prediction("known", "SUPPORTED")]
    cross_finding = {
        "experiment_id": "experiment-test",
        "trace_id": "trace-test",
        "task_id": "task-test",
        "agent_id": "agent-test",
        "assertion_ids": ["known"],
        "message_refs": [{"message_id": "message-known", "sequence_number": 1}],
    }
    cross_finding[field] = value

    with pytest.raises(EvaluationInputError, match="mismatched"):
        evaluate_claim_bearing_experiments(
            prediction, [cross_finding], [sidecar]
        )


def test_evaluator_rejects_unknown_cross_message_reference():
    sidecar = _sidecar([_assertion("known", matches=True)])
    cross_finding = {
        "experiment_id": "experiment-test",
        "trace_id": "trace-test",
        "task_id": "task-test",
        "agent_id": "agent-test",
        "assertion_ids": ["known"],
        "message_refs": [{"message_id": "unknown-message", "sequence_number": 1}],
    }

    with pytest.raises(EvaluationInputError, match="unknown or mismatched message_id"):
        evaluate_claim_bearing_experiments(
            [_prediction("known", "SUPPORTED")], [cross_finding], [sidecar]
        )


def test_evaluator_counts_evidence_association_failures_separately():
    sidecar = _sidecar([_assertion("known", matches=True)])
    prediction = _prediction("known", "SUPPORTED")
    prediction["evidence_association"] = [
        {"evidence_id": "evidence-a", "status": "scope_mismatch"},
        {"evidence_id": "evidence-b", "status": "evidence_not_relevant"},
    ]

    summary = evaluate_claim_bearing_experiments(
        [prediction], [], [sidecar]
    )["summary"]

    assert summary["evidence_verification_failure_counts"] == {}
    assert summary["evidence_association_failure_counts"] == {
        "evidence_not_relevant": 1,
        "scope_mismatch": 1,
    }
