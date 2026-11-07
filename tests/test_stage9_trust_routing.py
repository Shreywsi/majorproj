from __future__ import annotations

from dataclasses import replace
import json

import pytest

from dataset.agent_trust_scoring import (
    AgentTrustHistory,
    CapabilityObservation,
    TrustWeights,
    score_agent_trust,
)
from dataset.trust_aware_router import (
    AgentCandidate,
    RoutingTask,
    TrustAwareRouter,
)
from provenance_detector import ClaimOutcome
from run_stage9_routing_experiment import (
    STRATEGIES,
    _rotated_conditions,
    _strategy_result,
    run_stage9_experiment,
)


def _task(capability: str = "local.add") -> RoutingTask:
    return RoutingTask(
        task_id="task-route-test",
        decision_sequence=0,
        required_role="operator",
        required_capability=capability,
        execution_mode="local_simulation",
    )


def _candidate(
    agent_id: str,
    capability: str = "local.add",
    *,
    identity: str = "verified",
) -> AgentCandidate:
    return AgentCandidate(
        agent_id=agent_id,
        eligible_roles=("operator",),
        declared_capabilities=(capability,),
        identity_status=identity,
    )


def test_outcome_classes_stay_distinct_and_insufficient_is_not_dishonesty():
    base = AgentTrustHistory(
        "agent-a",
        outcome_counts=(
            (ClaimOutcome.SUPPORTED.value, 2),
            (ClaimOutcome.CONTRADICTED.value, 1),
        ),
        assertions_evaluated=3,
    )
    with_non_binary_outcomes = replace(
        base,
        outcome_counts=base.outcome_counts
        + (
            (ClaimOutcome.UNSUPPORTED.value, 5),
            (ClaimOutcome.INSUFFICIENT_EVIDENCE.value, 7),
        ),
        assertions_evaluated=15,
    )

    score_without = score_agent_trust(base)
    score_with = score_agent_trust(with_non_binary_outcomes)

    assert score_without.components["assertion_reliability"] == pytest.approx(
        score_with.components["assertion_reliability"]
    )
    assert score_with.score == pytest.approx(
        0.5 + score_with.confidence * (score_with.raw_score - 0.5)
    )
    assert score_with.evaluated_assertions == 15
    assert score_with.cold_start is False


def test_cold_start_and_smoothing_are_neutral_and_documented():
    history = AgentTrustHistory("agent-a")

    score = score_agent_trust(history)

    assert score.score == 0.5
    assert score.raw_score == 0.5
    assert score.cold_start is True
    assert score.confidence == 0


def test_evidence_failures_reduce_evidence_component_not_assertion_truth():
    history = AgentTrustHistory(
        "agent-a",
        outcome_counts=((ClaimOutcome.UNSUPPORTED.value, 1),),
        evidence_status_counts=(
            ("invalid_signature", 1),
            ("expired", 1),
            ("untrusted_issuer", 1),
        ),
        assertions_evaluated=1,
    )

    score = score_agent_trust(history)

    assert score.components["assertion_reliability"] == 0.5
    assert score.components["evidence_integrity"] < 0.5
    assert score.score < 0.5


def test_weights_require_finite_nonnegative_values_summing_to_one():
    with pytest.raises(ValueError):
        TrustWeights(assertion_reliability=0.5)
    with pytest.raises(ValueError):
        TrustWeights(assertion_reliability=-0.1)


def test_router_uses_capability_before_trust_and_records_full_decision():
    router = TrustAwareRouter()
    high_trust = AgentTrustHistory(
        "agent-a",
        outcome_counts=((ClaimOutcome.SUPPORTED.value, 20),),
        assertions_evaluated=20,
        capabilities=(
            CapabilityObservation("local.add", "configured_permission", True),
        ),
    )
    demonstrated = AgentTrustHistory(
        "agent-b",
        outcome_counts=((ClaimOutcome.CONTRADICTED.value, 3),),
        assertions_evaluated=3,
        capabilities=(
            CapabilityObservation("local.add", "simulated_demonstration", True),
        ),
    )

    decision = router.route(
        _task(),
        (_candidate("agent-a"), _candidate("agent-b")),
        {"agent-a": high_trust, "agent-b": demonstrated},
    )

    assert decision.selected_agent == "agent-b"
    assert decision.eligible_agents == ("agent-a", "agent-b")
    assert decision.excluded_agents == ()
    assert (
        decision.capability_compatibility["agent-a"]
        == "configured_permission_cold_start_fallback"
    )
    assert (
        decision.capability_compatibility["agent-b"]
        == "verified_simulated_or_actual_demonstration"
    )
    assert "components" in decision.trust_scores["agent-a"]
    assert decision.evidence_coverage["agent-a"] == 0
    assert "not evidence of external execution" in decision.selection_rationale


def test_router_cold_start_fallback_does_not_reject_permission_only_agents():
    decision = TrustAwareRouter().route(
        _task(),
        (_candidate("agent-b", identity="unverified"), _candidate("agent-a")),
        {},
    )

    assert decision.eligible_agents == ("agent-a", "agent-b")
    assert decision.selected_agent == "agent-a"
    assert decision.trust_scores["agent-a"]["cold_start"] is True
    assert decision.capability_compatibility["agent-a"] == (
        "configured_permission_cold_start_fallback"
    )


def test_router_excludes_missing_capability_and_identity_mismatch():
    candidates = (
        _candidate("agent-a", "local.lookup"),
        _candidate("agent-b", identity="mismatch"),
        _candidate("agent-c"),
    )

    decision = TrustAwareRouter().route(_task(), candidates, {})

    assert decision.selected_agent == "agent-c"
    assert decision.capability_compatibility["agent-a"] == "incompatible"
    assert decision.capability_compatibility["agent-b"] == "incompatible"
    assert len(decision.excluded_agents) == 2


def test_router_tie_breaks_by_stable_id_and_does_not_read_future_history():
    router = TrustAwareRouter()
    candidates = (_candidate("agent-z"), _candidate("agent-a"))
    prior = {
        candidate.agent_id: AgentTrustHistory(candidate.agent_id)
        for candidate in candidates
    }

    first = router.route(_task(), candidates, prior)
    later = {
        "agent-z": replace(
            prior["agent-z"],
            outcome_counts=((ClaimOutcome.CONTRADICTED.value, 10),),
            assertions_evaluated=10,
        ),
        "agent-a": prior["agent-a"],
    }
    replay_prior = router.route(_task(), candidates, prior)
    next_decision = router.route(
        replace(_task(), task_id="task-next", decision_sequence=1),
        candidates,
        later,
    )

    assert first.selected_agent == "agent-a"
    assert replay_prior.to_dict() == first.to_dict()
    assert next_decision.selected_agent == "agent-a"


def test_router_has_no_evaluator_label_or_behavior_input_fields():
    assert "ground_truth_label" not in AgentCandidate.__dataclass_fields__
    assert "behavior_category" not in AgentCandidate.__dataclass_fields__
    with pytest.raises(TypeError):
        AgentCandidate(
            "agent-a",
            ("operator",),
            ("local.add",),
            ground_truth_label="false",
        )
    with pytest.raises(TypeError):
        RoutingTask(
            "task-a",
            0,
            "operator",
            "local.add",
            "local_simulation",
            expected_output=42,
        )


def test_router_rejects_empty_or_incompatible_pools_and_malformed_history():
    router = TrustAwareRouter()
    with pytest.raises(ValueError, match="non-empty"):
        router.route(_task(), (), {})
    with pytest.raises(ValueError, match="No eligible"):
        router.route(_task(), (_candidate("agent-a", "local.lookup"),), {})
    with pytest.raises(ValueError, match="history keys"):
        router.route(
            _task(),
            (_candidate("agent-a"),),
            {"agent-b": AgentTrustHistory("agent-a")},
        )


def test_seed_rotations_change_behavior_assignment_and_scenario_order():
    first = _rotated_conditions(9100)
    second = _rotated_conditions(9101)

    assert first[0] != second[0]
    assert first[1] != second[1]


@pytest.mark.parametrize("strategy", ["R0", "R8", "trust_aware"])
def test_seeded_routing_decisions_reproduce(strategy, tmp_path):
    behaviours, scenarios = _rotated_conditions(9102)
    first = _strategy_result(
        strategy=strategy,
        seed=9102,
        behaviours=behaviours,
        scenarios=scenarios,
        namespace="stage9-roster-9102",
        output_root=tmp_path / "first",
    )[0]
    second = _strategy_result(
        strategy=strategy,
        seed=9102,
        behaviours=behaviours,
        scenarios=scenarios,
        namespace="stage9-roster-9102",
        output_root=tmp_path / "second",
    )[0]

    assert first == second


def test_stage9_end_to_end_produces_all_strategy_outputs_without_label_leakage(tmp_path):
    output = run_stage9_experiment(
        seeds=(9100,),
        output_root=tmp_path / "stage9",
    )

    manifest = output["manifest"]
    decisions = [
        json.loads(line)
        for line in (tmp_path / "stage9" / "routing_decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert manifest["strategies"] == list(STRATEGIES)
    assert manifest["tasks_per_strategy"] == 5
    assert len(decisions) == 5 * len(STRATEGIES)
    assert set(output["routing_evaluation"]["strategy_metrics"]) == set(STRATEGIES)
    assert (
        output["routing_evaluation"]["strategy_metrics"]["trust_aware"][
            "selected_predecision_verified_capability_demonstrations"
        ]
        >= 1
    )
    assert all(
        "ground_truth_label" not in decision
        and "expected_output" not in decision
        and "selected_behavior" not in decision
        for decision in decisions
    )
    for strategy in STRATEGIES:
        strategy_dir = tmp_path / "stage9" / "evaluation" / strategy
        assert (strategy_dir / "evaluation_summary.json").is_file()
        assert (strategy_dir / "assertion_metrics.csv").is_file()
    assert (tmp_path / "stage9" / "strategy_comparison.csv").is_file()
    assert (tmp_path / "stage9" / "routing_group_metrics.csv").is_file()
    assert (
        tmp_path / "stage9" / "routing_strategy_group_metrics.csv"
    ).is_file()
    assert set(
        output["routing_evaluation"]["results_by_strategy"]["trust_aware"]
    ) == {"scenario", "selected_behavior", "seed"}
    tamas_status = json.loads(
        (tmp_path / "stage9" / "tamas_integration_status.json")
        .read_text(encoding="utf-8")
    )
    assert tamas_status["records"] == 70
    assert tamas_status["claims_available"] == 0
    assert tamas_status["trust_aware_tamas_routing_performed"] is False
    detector_cross = json.loads(
        (tmp_path / "stage9" / "detector_and_cross_message_evaluation.json")
        .read_text(encoding="utf-8")
    )
    for strategy in STRATEGIES:
        result = detector_cross["by_strategy"][strategy]
        assert "detector" in result
        assert "metrics" in result["cross_message"]
        assert "results_by_finding_type" in result["cross_message"]
