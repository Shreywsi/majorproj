"""Independent assertion-level evaluation against evaluator-only sidecars."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from provenance_detector import ClaimOutcome


_DETECTOR_OUTCOMES = tuple(outcome.value for outcome in ClaimOutcome)
_INCONCLUSIVE_LABELS = {"inconclusive", "unsupported", "not_assessed", "unknown"}
_FALSE_LABELS = {"false", "false_assertion", "contradicted"}
_TRUE_LABELS = {"true", "truthful", "supported"}


class EvaluationInputError(ValueError):
    """Malformed or ambiguous evaluator input."""


def _ground_truth_label(assertion: dict[str, Any]) -> str:
    if "ground_truth_label" in assertion:
        raw = assertion["ground_truth_label"]
        if not isinstance(raw, str):
            raise EvaluationInputError(
                f"ground_truth_label for {assertion.get('assertion_id')} must be text."
            )
        label = raw.strip().lower()
        if label in _FALSE_LABELS:
            return "false"
        if label in _TRUE_LABELS:
            return "truthful"
        if label in _INCONCLUSIVE_LABELS:
            return "inconclusive"
        raise EvaluationInputError(
            f"Unknown ground-truth label {raw!r} for {assertion.get('assertion_id')}."
        )
    if "matches_expected" not in assertion:
        return "inconclusive"
    if not isinstance(assertion["matches_expected"], bool):
        raise EvaluationInputError(
            f"matches_expected for {assertion.get('assertion_id')} must be boolean."
        )
    return "truthful" if assertion["matches_expected"] else "false"


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _binary_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = tn = fn = inconclusive = 0
    outcomes = Counter({outcome: 0 for outcome in _DETECTOR_OUTCOMES})
    for row in rows:
        outcome = row["detector_outcome"]
        outcomes[outcome] += 1
        label = row["ground_truth_label"]
        if label == "inconclusive":
            inconclusive += 1
            continue
        predicted_false = outcome == ClaimOutcome.CONTRADICTED.value
        if label == "false" and predicted_false:
            tp += 1
        elif label == "truthful" and predicted_false:
            fp += 1
        elif label == "truthful":
            tn += 1
        else:
            fn += 1
    denominator = tp + fp + tn + fn
    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    return {
        "assertions_total": len(rows),
        "inconclusive_ground_truth": inconclusive,
        "binary_metric_denominator": denominator,
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "accuracy": _safe_ratio(tp + tn, denominator),
        "precision": precision,
        "recall": recall,
        "f1_score": (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        ),
        "detector_outcome_counts": {
            outcome: outcomes[outcome] for outcome in _DETECTOR_OUTCOMES
        },
    }


def _group_metrics(
    rows: list[dict[str, Any]],
    dimensions: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for dimension in dimensions:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            value = row.get(dimension)
            groups[str(value if value is not None else "unknown")].append(row)
        output[dimension] = {
            key: _binary_metrics(groups[key])
            for key in sorted(groups)
        }
    return output


def _outcome_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = ("truthful", "false", "inconclusive")
    matrix = {
        label: {outcome: 0 for outcome in _DETECTOR_OUTCOMES}
        for label in labels
    }
    for row in rows:
        matrix[row["ground_truth_label"]][row["detector_outcome"]] += 1
    return {
        "ground_truth_to_detector_outcomes": matrix,
        "mapping": {
            "ground_truth_label": (
                "truthful/false are derived from matches_expected when present; "
                "explicit ground_truth_label takes precedence; absent/ambiguous labels "
                "are inconclusive."
            ),
            "binary_positive_prediction": "CONTRADICTED only",
            "binary_negative_prediction": (
                "SUPPORTED, UNSUPPORTED, and INSUFFICIENT_EVIDENCE; a false claim "
                "with either latter outcome is a false negative, not proven truthful."
            ),
            "inconclusive_ground_truth": (
                "Reported separately and excluded from TP/FP/TN/FN denominators."
            ),
        },
    }


def _evidence_failure_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    failures: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        for evidence in row.get("evidence_verification", []):
            if not isinstance(evidence, dict):
                raise EvaluationInputError("evidence_verification entries must be objects.")
            evidence_id = evidence.get("evidence_id")
            status = evidence.get("status")
            if not isinstance(evidence_id, str) or not isinstance(status, str):
                raise EvaluationInputError(
                    "evidence_verification requires evidence_id and status strings."
                )
            if status != "valid":
                failures[status].add((row["experiment_id"], evidence_id))
    return {status: len(ids) for status, ids in sorted(failures.items())}


def _evidence_association_failure_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    failures: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in rows:
        for evidence in row.get("evidence_association", []):
            if not isinstance(evidence, dict):
                raise EvaluationInputError(
                    "evidence_association entries must be objects."
                )
            evidence_id = evidence.get("evidence_id")
            status = evidence.get("status")
            if not isinstance(evidence_id, str) or not isinstance(status, str):
                raise EvaluationInputError(
                    "evidence_association requires evidence_id and status strings."
                )
            if status != "valid":
                failures[status].add(
                    (row["experiment_id"], row["assertion_id"], evidence_id)
                )
    return {status: len(ids) for status, ids in sorted(failures.items())}


def evaluate_claim_bearing_experiments(
    detector_findings: Iterable[dict[str, Any]],
    cross_message_findings: Iterable[dict[str, Any]],
    ground_truth_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Join predictions to unique sidecar assertions and calculate metrics."""
    detector_rows = tuple(detector_findings)
    cross_rows = tuple(cross_message_findings)
    sidecar_rows = tuple(ground_truth_records)
    if not sidecar_rows:
        raise EvaluationInputError("Ground-truth sidecar contains no records.")

    ground_truth: dict[tuple[str, str], dict[str, Any]] = {}
    ground_truth_by_message: dict[
        tuple[str, str], set[tuple[str, str, str, str]]
    ] = defaultdict(set)
    experiments: set[str] = set()
    seeds: set[int] = set()
    for record_index, record in enumerate(sidecar_rows, start=1):
        if not isinstance(record, dict):
            raise EvaluationInputError(f"Ground-truth record {record_index} must be an object.")
        experiment_id = record.get("experiment_id")
        assertions = record.get("assertions")
        agents = record.get("agents")
        trace_id = record.get("trace_id")
        task_id = record.get("task_id")
        scenario = record.get("scenario")
        seed = record.get("seed")
        if (
            not isinstance(experiment_id, str)
            or not isinstance(trace_id, str)
            or not isinstance(task_id, str)
            or not isinstance(scenario, str)
            or not isinstance(seed, int)
            or isinstance(seed, bool)
            or not isinstance(assertions, list)
            or not isinstance(agents, list)
        ):
            raise EvaluationInputError(
                f"Ground-truth record {record_index} is missing required associations."
            )
        experiments.add(experiment_id)
        seeds.add(seed)
        behavior_by_agent: dict[str, str] = {}
        for agent in agents:
            if not isinstance(agent, dict) or not isinstance(agent.get("agent_id"), str):
                raise EvaluationInputError(
                    f"Ground-truth record {record_index} has malformed agent metadata."
                )
            behavior = agent.get("behaviour_category", "unknown")
            if not isinstance(behavior, str):
                raise EvaluationInputError("behaviour_category must be text.")
            if agent["agent_id"] in behavior_by_agent:
                raise EvaluationInputError(
                    f"Duplicate agent_id {agent['agent_id']} in evaluator metadata."
                )
            behavior_by_agent[agent["agent_id"]] = behavior
        for assertion in assertions:
            if not isinstance(assertion, dict):
                raise EvaluationInputError("Ground-truth assertions must be objects.")
            assertion_id = assertion.get("assertion_id")
            agent_id = assertion.get("agent_id")
            claim_type = assertion.get("claim_type")
            if not all(isinstance(value, str) and value for value in (
                assertion_id, agent_id, claim_type
            )):
                raise EvaluationInputError(
                    f"Ground-truth record {record_index} has an assertion without IDs."
                )
            assertion_key = (experiment_id, assertion_id)
            if assertion_key in ground_truth:
                raise EvaluationInputError(f"Duplicate assertion_id: {assertion_id}.")
            if agent_id not in behavior_by_agent:
                raise EvaluationInputError(
                    f"Assertion {assertion_id} references unknown evaluator agent."
                )
            if assertion.get("message_id") is not None and not isinstance(
                assertion["message_id"], str
            ):
                raise EvaluationInputError(f"Invalid message association for {assertion_id}.")
            ground_truth[assertion_key] = {
                "assertion_id": assertion_id,
                "experiment_id": experiment_id,
                "trace_id": trace_id,
                "task_id": task_id,
                "scenario": scenario,
                "seed": seed,
                "agent_id": agent_id,
                "agent_behaviour": behavior_by_agent[agent_id],
                "claim_type": claim_type,
                "ground_truth_label": _ground_truth_label(assertion),
            }
            message_id = assertion.get("message_id")
            if message_id is not None:
                ground_truth_by_message[(experiment_id, message_id)].add(
                    (trace_id, task_id, agent_id, message_id)
                )

    prediction_by_id: dict[tuple[str, str], dict[str, Any]] = {}
    for prediction in detector_rows:
        if not isinstance(prediction, dict):
            raise EvaluationInputError("Detector findings must contain objects.")
        assertion_id = prediction.get("assertion_id")
        if not isinstance(assertion_id, str) or not assertion_id:
            raise EvaluationInputError("Detector finding has no assertion_id.")
        experiment_id = prediction.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            raise EvaluationInputError(
                f"Detector finding {assertion_id} has no experiment_id."
            )
        assertion_key = (experiment_id, assertion_id)
        if assertion_key in prediction_by_id:
            raise EvaluationInputError(f"Duplicate detector prediction for {assertion_id}.")
        if assertion_key not in ground_truth:
            raise EvaluationInputError(
                f"Detector prediction references unknown assertion_id {assertion_id}."
            )
        outcome = prediction.get("detector_outcome")
        if outcome not in _DETECTOR_OUTCOMES:
            raise EvaluationInputError(
                f"Detector prediction {assertion_id} has an unknown outcome."
            )
        for key in ("trace_id", "task_id", "agent_id", "claim_type"):
            if prediction.get(key) != ground_truth[assertion_key][key]:
                raise EvaluationInputError(
                    f"Detector prediction {assertion_id} has a mismatched {key}."
                )
        prediction_by_id[assertion_key] = prediction

    for item in cross_rows:
        if not isinstance(item, dict):
            raise EvaluationInputError("Cross-message findings must contain objects.")
        experiment_id = item.get("experiment_id")
        trace_id = item.get("trace_id")
        task_id = item.get("task_id")
        agent_id = item.get("agent_id")
        assertion_ids = item.get("assertion_ids")
        message_refs = item.get("message_refs")
        if (
            not isinstance(experiment_id, str)
            or experiment_id not in experiments
            or not isinstance(trace_id, str)
            or not isinstance(task_id, str)
            or not isinstance(agent_id, str)
            or not isinstance(assertion_ids, list)
            or not assertion_ids
            or any(not isinstance(value, str) or not value for value in assertion_ids)
            or len(assertion_ids) != len(set(assertion_ids))
            or not isinstance(message_refs, list)
            or not message_refs
        ):
            raise EvaluationInputError(
                "Cross-message finding has missing or invalid association metadata."
            )
        for assertion_id in assertion_ids:
            assertion = ground_truth.get((experiment_id, assertion_id))
            if assertion is None:
                raise EvaluationInputError(
                    f"Cross-message finding references unknown assertion_id {assertion_id}."
                )
            if (
                assertion["trace_id"] != trace_id
                or assertion["task_id"] != task_id
                or assertion["agent_id"] != agent_id
            ):
                raise EvaluationInputError(
                    f"Cross-message finding assertion {assertion_id} has a "
                    "mismatched trace, task, or agent association."
                )
        for message_ref in message_refs:
            if not isinstance(message_ref, dict):
                raise EvaluationInputError(
                    "Cross-message message_refs must contain objects."
                )
            message_id = message_ref.get("message_id")
            if not isinstance(message_id, str) or not message_id:
                raise EvaluationInputError(
                    "Cross-message message reference has no message_id."
                )
            associations = ground_truth_by_message.get((experiment_id, message_id))
            if not associations or any(
                association[:3] != (trace_id, task_id, agent_id)
                for association in associations
            ):
                raise EvaluationInputError(
                    f"Cross-message finding references an unknown or mismatched "
                    f"message_id {message_id}."
                )

    if experiments != {row.get("experiment_id") for row in detector_rows}:
        if detector_rows:
            raise EvaluationInputError(
                "Detector findings and sidecars do not cover the same experiment IDs."
            )
    missing_predictions = set(ground_truth) - set(prediction_by_id)
    if missing_predictions:
        raise EvaluationInputError(
            "Detector output is missing predictions for assertion IDs: "
            + ", ".join(
                f"{experiment}:{assertion}"
                for experiment, assertion in sorted(missing_predictions)[:10]
            )
        )

    evaluation_rows = [
        {
            **ground_truth[assertion_key],
            "detector_outcome": prediction_by_id[assertion_key]["detector_outcome"],
            "evidence_verification": prediction_by_id[assertion_key].get(
                "evidence_verification", []
            ),
            "evidence_association": prediction_by_id[assertion_key].get(
                "evidence_association", []
            ),
        }
        for assertion_key in sorted(ground_truth)
    ]
    overall = _binary_metrics(evaluation_rows)
    grouping_dimensions = (
        "claim_type", "agent_behaviour", "scenario", "seed"
    )
    groupings = _group_metrics(evaluation_rows, grouping_dimensions)
    agent_summaries: dict[str, Any] = {}
    agent_keys = sorted({
        (row["experiment_id"], row["agent_id"]) for row in evaluation_rows
    })
    for experiment_id, agent_id in agent_keys:
        rows = [
            row for row in evaluation_rows
            if row["experiment_id"] == experiment_id and row["agent_id"] == agent_id
        ]
        false_count = sum(row["ground_truth_label"] == "false" for row in rows)
        agent_summaries[f"{experiment_id}:{agent_id}"] = {
            "assertions": len(rows),
            "false_assertion_count": false_count,
            "false_assertion_proportion": _safe_ratio(false_count, len(rows)),
        }
    evidence_failures = _evidence_failure_counts(evaluation_rows)
    evidence_association_failures = _evidence_association_failure_counts(
        evaluation_rows
    )
    cross_assertion_count = sum(
        len(set(item.get("assertion_ids", []))) for item in cross_rows
    )
    summary = {
        "experiment_ids": sorted(experiments),
        "random_seeds": sorted(seeds),
        "assertion_metrics": overall,
        "results_by": groupings,
        "agent_assertion_summaries": agent_summaries,
        "evidence_verification_failure_counts": evidence_failures,
        "evidence_association_failure_counts": evidence_association_failures,
        "cross_message_contradiction_count": len(cross_rows),
        "cross_message_assertion_reference_count": cross_assertion_count,
        "ground_truth_label_counts": dict(sorted(Counter(
            row["ground_truth_label"] for row in evaluation_rows
        ).items())),
        "outcome_comparison": _outcome_comparison(evaluation_rows),
        "limitations": [
            "A CONTRADICTED outcome is the only binary positive; UNSUPPORTED and "
            "INSUFFICIENT_EVIDENCE do not establish falsehood.",
            "Binary false negatives include false assertions not contradicted by the "
            "available detector evidence.",
            "Inconclusive ground-truth assertions are excluded from binary metrics.",
            "Agent summaries count false assertions and do not classify agents as malicious.",
            "Controlled local simulation results are not TAMAS record-level results.",
        ],
    }
    confusion = {
        "experiment_ids": sorted(experiments),
        "binary": {
            "positive_ground_truth": "false",
            "negative_ground_truth": "truthful",
            "positive_prediction": "CONTRADICTED",
            "negative_prediction": (
                "SUPPORTED, UNSUPPORTED, or INSUFFICIENT_EVIDENCE"
            ),
            "inconclusive_ground_truth_excluded": True,
            "matrix": {
                "actual_false": {
                    "predicted_false": overall["true_positives"],
                    "predicted_not_false": overall["false_negatives"],
                },
                "actual_truthful": {
                    "predicted_false": overall["false_positives"],
                    "predicted_not_false": overall["true_negatives"],
                },
            },
        },
        "outcome_comparison": summary["outcome_comparison"],
    }
    csv_rows = []
    for group in ("overall", *groupings):
        if group == "overall":
            groups = {"all": overall}
            dimension = "overall"
        else:
            groups = groupings[group]
            dimension = group
        for group_value, metrics in groups.items():
            csv_rows.append({
                "dimension": dimension,
                "group": group_value,
                **metrics,
            })
    return {
        "summary": summary,
        "confusion_matrix": confusion,
        "assertion_metrics_rows": csv_rows,
        "evaluated_assertions": evaluation_rows,
    }
