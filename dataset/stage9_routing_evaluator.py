"""Independent post-routing evaluation for Stage 9 controlled experiments."""

from __future__ import annotations

from collections import defaultdict
from random import Random
from statistics import mean
from typing import Any, Iterable, Mapping


def _task_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if not count:
        return {
            "tasks": 0,
            "task_successes": 0,
            "task_failures": 0,
            "task_success_rate": None,
            "handoffs": 0,
            "simulated_tool_invocations": 0,
            "selected_agents_with_false_assertions": 0,
            "selected_false_assertions": 0,
            "selected_assertions": 0,
            "selected_task_local_simulated_capability_demonstrations": 0,
            "selected_predecision_verified_capability_demonstrations": 0,
            "selected_evidence_coverage": None,
            "selected_agents_with_prior_insufficient_evidence": 0,
            "selected_prior_insufficient_evidence_assertions": 0,
        }
    successes = sum(bool(row["task_success"]) for row in rows)
    selected_assertions = sum(row["selected_assertions"] for row in rows)
    coverage_values = [
        row["selected_evidence_coverage"]
        for row in rows
        if row["selected_evidence_coverage"] is not None
    ]
    return {
        "tasks": count,
        "task_successes": successes,
        "task_failures": count - successes,
        "task_success_rate": successes / count,
        "handoffs": sum(row["handoffs"] for row in rows),
        "simulated_tool_invocations": sum(
            row["simulated_tool_invocations"] for row in rows
        ),
        "selected_agents_with_false_assertions": sum(
            row["selected_false_assertions"] > 0 for row in rows
        ),
        "selected_false_assertions": sum(
            row["selected_false_assertions"] for row in rows
        ),
        "selected_assertions": selected_assertions,
        "selected_task_local_simulated_capability_demonstrations": sum(
            row["selected_task_local_simulated_capability_demonstrated"]
            for row in rows
        ),
        "selected_predecision_verified_capability_demonstrations": sum(
            row["selected_predecision_verified_capability_demonstrated"]
            for row in rows
        ),
        "selected_evidence_coverage": (
            mean(coverage_values) if coverage_values else None
        ),
        "selected_agents_with_prior_insufficient_evidence": sum(
            row["selected_prior_insufficient_evidence_assertions"] > 0
            for row in rows
        ),
        "selected_prior_insufficient_evidence_assertions": sum(
            row["selected_prior_insufficient_evidence_assertions"]
            for row in rows
        ),
    }


def _cluster_bootstrap_interval(
    rows: list[dict[str, Any]], *, seed: int, replicates: int = 2000
) -> dict[str, float] | None:
    by_seed: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_seed[row["seed"]].append(row)
    clusters = [
        mean(bool(row["task_success"]) for row in task_rows)
        for _, task_rows in sorted(by_seed.items())
    ]
    if len(clusters) < 2:
        return None
    rng = Random(seed)
    estimates = sorted(
        mean(rng.choice(clusters) for _ in clusters)
        for _ in range(replicates)
    )
    return {
        "lower_95": estimates[int(0.025 * (replicates - 1))],
        "upper_95": estimates[int(0.975 * (replicates - 1))],
        "seed_clusters": len(clusters),
        "replicates": replicates,
    }


def evaluate_routing_decisions(
    decisions: Iterable[Mapping[str, Any]],
    evaluator_sidecars: Iterable[Mapping[str, Any]],
    *,
    bootstrap_seed: int = 9,
) -> dict[str, Any]:
    """Join decisions to sidecars only after routing has completed.

    Sidecar labels and expected values are consumed here for evaluation, never
    accepted by the router or the online feedback adapter.
    """
    decision_rows = [dict(row) for row in decisions]
    sidecar_rows = [dict(row) for row in evaluator_sidecars]
    sidecars: dict[tuple[str, str], dict[str, Any]] = {}
    for sidecar in sidecar_rows:
        key = (sidecar.get("experiment_id"), sidecar.get("task_id"))
        if not all(isinstance(value, str) and value for value in key):
            raise ValueError("Evaluator sidecar is missing experiment/task association.")
        if key in sidecars:
            raise ValueError(f"Duplicate evaluator sidecar association: {key}.")
        sidecars[key] = sidecar

    joined: list[dict[str, Any]] = []
    seen_decisions: set[tuple[str, str]] = set()
    for decision in decision_rows:
        experiment_id = decision.get("experiment_id")
        task_id = decision.get("task_id")
        key = (experiment_id, task_id)
        if not all(isinstance(value, str) and value for value in key):
            raise ValueError("Routing decision is missing experiment/task association.")
        if key in seen_decisions:
            raise ValueError(f"Duplicate routing decision association: {key}.")
        seen_decisions.add(key)
        sidecar = sidecars.get(key)
        if sidecar is None:
            raise ValueError(f"No evaluator sidecar matches routing decision {key}.")
        selected_agent = decision.get("selected_agent")
        agents = sidecar.get("agents")
        if not isinstance(agents, list):
            raise ValueError(f"Evaluator sidecar {key} has no agent list.")
        selected = next(
            (
                agent for agent in agents
                if isinstance(agent, dict) and agent.get("agent_id") == selected_agent
            ),
            None,
        )
        if selected is None:
            raise ValueError(f"Selected agent is not associated with evaluator sidecar {key}.")
        expected = sidecar.get("expected_execution_outcome")
        if not isinstance(expected, dict) or not isinstance(
            expected.get("completed"), bool
        ):
            raise ValueError(f"Evaluator sidecar {key} has no task completion value.")
        false_assertions = [
            assertion
            for assertion in sidecar.get("assertions", [])
            if isinstance(assertion, dict)
            and assertion.get("agent_id") == selected_agent
            and assertion.get("matches_expected") is False
        ]
        capabilities = selected.get("ground_truth_capabilities", {})
        required_capability = sidecar.get("scenario_configuration", {}).get(
            "required_capability"
        )
        capability_status = (
            capabilities.get(required_capability, {})
            if isinstance(capabilities, dict)
            else {}
        )
        trust_inputs = decision.get(
            "trust_scores",
            decision.get("trust_scores_diagnostic_only", {}),
        )
        selected_history = (
            trust_inputs.get(selected_agent, {})
            if isinstance(trust_inputs, dict)
            else {}
        )
        prior_outcomes = (
            selected_history.get("historical_detector_outcomes", {})
            if isinstance(selected_history, dict)
            else {}
        )
        prior_insufficient = (
            prior_outcomes.get("INSUFFICIENT_EVIDENCE", 0)
            if isinstance(prior_outcomes, dict)
            else 0
        )
        if not isinstance(prior_insufficient, int) or prior_insufficient < 0:
            raise ValueError("Selected agent history has invalid outcome counts.")
        compatibility = decision.get("capability_compatibility", {})
        selected_compatibility = (
            compatibility.get(selected_agent)
            if isinstance(compatibility, dict)
            else None
        )
        joined.append(
            {
                **decision,
                "selected_behavior": selected.get("behaviour_category"),
                "task_success": expected["completed"],
                "selected_false_assertions": len(false_assertions),
                "selected_assertions": sum(
                    isinstance(assertion, dict)
                    and assertion.get("agent_id") == selected_agent
                    for assertion in sidecar.get("assertions", [])
                ),
                "selected_task_local_simulated_capability_demonstrated": bool(
                    isinstance(capability_status, dict)
                    and capability_status.get("demonstrated") is True
                ),
                "selected_predecision_verified_capability_demonstrated": (
                    selected_compatibility
                    in {
                        "verified_simulated_or_actual_demonstration",
                        "verified_actual_demonstration",
                    }
                ),
                "selected_prior_insufficient_evidence_assertions": prior_insufficient,
            }
        )
    missing_decisions = set(sidecars) - seen_decisions
    if missing_decisions:
        raise ValueError(
            f"Evaluator sidecars have no matching routing decisions: "
            f"{sorted(missing_decisions)[:3]}"
        )

    by_dimension: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for dimension, key in (
        ("strategy", "strategy"),
        ("scenario", "scenario"),
        ("selected_behavior", "selected_behavior"),
        ("seed", "seed"),
    ):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in joined:
            grouped[str(row.get(key, "unknown"))].append(row)
        by_dimension[dimension] = {
            group: _task_metrics(group_rows)
            for group, group_rows in sorted(grouped.items())
        }

    strategy_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    behavior_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for row in joined:
        strategy_rows[str(row["strategy"])].append(row)
        behavior_counts[str(row["strategy"])][str(row["selected_behavior"])] += 1
    strategy_metrics = {
        strategy: {
            **_task_metrics(rows),
            "task_success_95ci_cluster_bootstrap": _cluster_bootstrap_interval(
                rows, seed=bootstrap_seed
            ),
            "selection_count_by_behavior": dict(
                sorted(behavior_counts[strategy].items())
            ),
            "selection_rate_by_behavior": {
                behavior: count / len(rows)
                for behavior, count in sorted(behavior_counts[strategy].items())
            },
        }
        for strategy, rows in sorted(strategy_rows.items())
    }
    results_by_strategy: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for strategy, rows in sorted(strategy_rows.items()):
        strategy_groups: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for dimension, key in (
            ("scenario", "scenario"),
            ("selected_behavior", "selected_behavior"),
            ("seed", "seed"),
        ):
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[str(row.get(key, "unknown"))].append(row)
            strategy_groups[dimension] = grouped
        results_by_strategy[strategy] = {
            dimension: {
                group: _task_metrics(group_rows)
                for group, group_rows in sorted(groups.items())
            }
            for dimension, groups in strategy_groups.items()
        }
    return {
        "evaluation_unit": "task/experiment run",
        "sidecar_join_count": len(joined),
        "strategy_metrics": strategy_metrics,
        "results_by": by_dimension,
        "results_by_strategy": results_by_strategy,
        "task_results": sorted(
            joined,
            key=lambda row: (
                str(row["strategy"]),
                int(row["seed"]),
                str(row["scenario"]),
            ),
        ),
        "limitations": [
            "All tasks use deterministic scripted agents and local simulations.",
            "Task-level bootstrap resamples seed/configuration clusters; five clusters "
            "are too few for reliable inferential confidence intervals.",
            "Selected capability demonstrations are evaluator-confirmed local "
            "simulations, not external execution.",
        ],
    }
