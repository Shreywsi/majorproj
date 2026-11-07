import pytest

from dataset.stage8_analysis import evaluate_cross_message_findings


def _experiment(*, emitted_findings=None):
    assertions = [
        {
            "assertion_id": "identity-claim",
            "agent_id": "agent-a",
            "claim_type": "IDENTITY",
            "message_id": "message-1",
            "matches_expected": False,
            "asserted_value": "agent-forged",
            "ground_truth_label": "identity-mismatch",
        },
        {
            "assertion_id": "truthful-claim",
            "agent_id": "agent-a",
            "claim_type": "ROLE",
            "message_id": "message-2",
            "matches_expected": True,
            "asserted_value": "operator",
        },
    ]
    messages = [
        {
            "message_id": "message-1",
            "experiment_id": "experiment-a",
            "trace_id": "trace-a",
            "task_id": "task-a",
            "agent_id": "agent-a",
            "claimed_identity": "agent-forged",
            "sequence_number": 1,
            "timestamp": 100,
            "assertions": [{
                "assertion_id": "identity-claim",
                "claim_type": "IDENTITY",
                "predicate": "identity",
                "asserted_value": "agent-forged",
                "scope": "task:task-a",
                "subject": "identity",
            }],
        },
        {
            "message_id": "message-2",
            "experiment_id": "experiment-a",
            "trace_id": "trace-a",
            "task_id": "task-a",
            "agent_id": "agent-a",
            "claimed_identity": "agent-a",
            "sequence_number": 2,
            "timestamp": 101,
            "assertions": [{
                "assertion_id": "truthful-claim",
                "claim_type": "ROLE",
                "predicate": "role",
                "asserted_value": "operator",
                "scope": "task:task-a",
                "subject": "role",
            }],
        },
    ]
    finding = {
        "contradiction_kind": "identity_claim_conflicts_with_sender",
        "experiment_id": "experiment-a",
        "trace_id": "trace-a",
        "task_id": "task-a",
        "agent_id": "agent-a",
        "assertion_ids": ["identity-claim"],
        "message_refs": [
            {"message_id": "message-1"},
            {"message_id": "message-2"},
        ],
    }
    return {
        "experiment_id": "experiment-a",
        "ground_truth_records": [{
            "experiment_id": "experiment-a",
            "trace_id": "trace-a",
            "task_id": "task-a",
            "scenario": "identity",
            "seed": 1,
            "agents": [{
                "agent_id": "agent-a",
                "ground_truth_identity": "agent-a",
            }],
            "assertions": assertions,
        }],
        "messages": messages,
        "traces": {
            "trace-a": {
                "trace_id": "trace-a",
                "task_id": "task-a",
                "events": [],
            }
        },
        "cross_message_findings": (
            [finding] if emitted_findings is None else emitted_findings
        ),
    }


def test_cross_message_evaluation_uses_sidecar_truth_and_exact_associations():
    result = evaluate_cross_message_findings(experiments=[_experiment()])

    assert result["metrics"]["true_positives"] == 1
    assert result["metrics"]["false_positives"] == 0
    assert result["metrics"]["false_negatives"] == 0
    assert result["metrics"]["true_negatives"] is None
    assert result["results_by_finding_type"][
        "identity_claim_conflicts_with_sender"
    ]["f1_score"] == 1.0


def test_cross_message_evaluation_counts_eligible_but_unemitted_finding_as_fn():
    result = evaluate_cross_message_findings(
        experiments=[_experiment(emitted_findings=[])]
    )

    assert result["metrics"]["true_positives"] == 0
    assert result["metrics"]["false_negatives"] == 1


def test_cross_message_evaluation_counts_emitted_ineligible_finding_as_fp():
    finding = {
        "contradiction_kind": "unsupported-pattern",
        "experiment_id": "experiment-a",
        "trace_id": "trace-a",
        "task_id": "task-a",
        "agent_id": "agent-a",
        "assertion_ids": ["identity-claim"],
        "message_refs": [
            {"message_id": "message-1"},
            {"message_id": "message-2"},
        ],
    }
    result = evaluate_cross_message_findings(
        experiments=[_experiment(emitted_findings=[finding])]
    )

    assert result["metrics"]["false_positives"] == 1
    assert result["metrics"]["false_negatives"] == 1


def test_cross_message_evaluation_rejects_mismatched_message_association():
    finding = {
        "contradiction_kind": "identity_claim_conflicts_with_sender",
        "experiment_id": "experiment-a",
        "trace_id": "trace-a",
        "task_id": "task-a",
        "agent_id": "agent-a",
        "assertion_ids": ["identity-claim"],
        "message_refs": [
            {"message_id": "message-unknown"},
            {"message_id": "message-2"},
        ],
    }

    with pytest.raises(ValueError, match="unknown/mismatched message"):
        evaluate_cross_message_findings(
            experiments=[_experiment(emitted_findings=[finding])]
        )


def test_cross_message_evaluation_rejects_duplicate_sidecar_assertion_ids():
    experiment = _experiment()
    duplicate = dict(experiment["ground_truth_records"][0]["assertions"][0])
    experiment["ground_truth_records"][0]["assertions"].append(duplicate)

    with pytest.raises(ValueError, match="Duplicate sidecar assertion"):
        evaluate_cross_message_findings(experiments=[experiment])


def test_cross_message_evaluation_excludes_inconclusive_truth_explicitly():
    experiment = _experiment()
    experiment["ground_truth_records"][0]["assertions"][0][
        "matches_expected"
    ] = None

    result = evaluate_cross_message_findings(experiments=[experiment])

    assert result["inconclusive_assertion_count"] == 1
    assert result["eligible_contradiction_count"] == 0
    assert result["metrics"]["false_positives"] == 1


def test_cross_message_evaluation_counts_false_but_ineligible_patterns():
    experiment = _experiment()
    experiment["messages"][0]["assertions"][0]["predicate"] = "not-identity"

    result = evaluate_cross_message_findings(experiments=[experiment])

    assert result["eligible_contradiction_count"] == 0
    assert result["excluded_eligibility_counts"][
        "false_assertion_not_eligible_for_supported_cross_rule"
    ] == 1
    assert result["metrics"]["false_positives"] == 1
