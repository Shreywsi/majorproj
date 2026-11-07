"""Ground-truth-bounded analysis helpers for Stage 8 reports."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from typing import Any, Iterable

from dataset.experiment_schemas import EventExecutionStatus, TraceEventType


_IDENTITY_PREDICATES = {"is", "identity", "claimed_identity"}
_TOOL_PREDICATES = {
    "execution_status", "invoked", "tool_invocation", "tool_used",
}


def _positive(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        for key in ("completed", "invoked", "demonstrated", "granted"):
            if isinstance(value.get(key), bool):
                return value[key]
        status = value.get("execution_status")
        if isinstance(status, str):
            if status.lower() in {"recorded", "invoked", "success", "succeeded"}:
                return True
            if status.lower() in {"not_executed", "not_invoked", "failed"}:
                return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "completed", "complete", "invoked"}:
            return True
        if normalized in {"false", "no", "incomplete", "not_completed", "not_invoked"}:
            return False
    return None


def _time_compatible(
    first: dict[str, Any],
    second: dict[str, Any],
    max_time_window_seconds: int | None,
) -> bool:
    first_time, second_time = first.get("timestamp"), second.get("timestamp")
    if first_time is None or second_time is None:
        return True
    delta = second_time - first_time
    return delta >= 0 and (
        max_time_window_seconds is None or delta <= max_time_window_seconds
    )


def _association_key(
    experiment_id: str,
    assertion_id: str,
    ground_truth: dict[tuple[str, str], dict[str, Any]],
) -> tuple[str, str, str, str, str, str] | None:
    row = ground_truth.get((experiment_id, assertion_id))
    if row is None:
        return None
    return (
        experiment_id,
        row["trace_id"],
        row["task_id"],
        row["agent_id"],
        assertion_id,
        row["message_id"],
    )


def analyze_false_negatives(
    experiments: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Join false assertions to predictions and preserve their evidence context."""
    false_negatives: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []
    for experiment in experiments:
        experiment_id = experiment["experiment_id"]
        seed = experiment.get("seed")
        sidecar_rows = tuple(experiment["ground_truth_records"])
        finding_by_id = {
            row["assertion_id"]: row for row in experiment["detector_findings"]
        }
        cross_ids = {
            assertion_id
            for finding in experiment["cross_message_findings"]
            for assertion_id in finding.get("assertion_ids", [])
        }
        behavior_by_agent: dict[str, str] = {}
        for record in sidecar_rows:
            for agent in record["agents"]:
                behavior_by_agent[agent["agent_id"]] = agent.get(
                    "behaviour_category", "unknown"
                )
        for record in sidecar_rows:
            for assertion in record["assertions"]:
                finding = finding_by_id[assertion["assertion_id"]]
                details = {
                    "experiment_id": experiment_id,
                    "seed": seed,
                    "trace_id": record["trace_id"],
                    "task_id": record["task_id"],
                    "scenario": record["scenario"],
                    "assertion_id": assertion["assertion_id"],
                    "claim_type": assertion["claim_type"],
                    "agent_id": assertion["agent_id"],
                    "agent_behaviour": behavior_by_agent[assertion["agent_id"]],
                    "asserted_value": assertion.get("asserted_value"),
                    "expected_asserted_value": assertion.get(
                        "expected_asserted_value"
                    ),
                    "evidence_refs": assertion.get("evidence_refs", []),
                    "detector_outcome": finding["detector_outcome"],
                    "detector_rule": finding.get("detector_rule"),
                    "detector_reason": finding.get("explanation"),
                    "evidence_verification": finding.get(
                        "evidence_verification", []
                    ),
                    "evidence_association": finding.get(
                        "evidence_association", []
                    ),
                    "relevant_trace_event_references": finding.get(
                        "relevant_event_references", []
                    ),
                    "cross_message_finding": assertion["assertion_id"] in cross_ids,
                }
                if assertion.get("matches_expected") is False:
                    associations = {
                        item["status"]
                        for item in details["evidence_association"]
                    }
                    verification = {
                        item["status"]
                        for item in details["evidence_verification"]
                    }
                    if "missing" in associations or "missing" in verification:
                        reason = "missing_evidence"
                    elif associations.intersection({
                        "scope_mismatch", "identity_mismatch",
                        "evidence_relationship_mismatch",
                    }):
                        reason = "evidence_association_mismatch"
                    elif "evidence_not_relevant" in associations:
                        reason = "irrelevant_evidence"
                    elif any(status != "valid" for status in verification):
                        reason = "evidence_verification_failure"
                    elif finding["detector_outcome"] == "UNSUPPORTED":
                        reason = "unsupported_evidence_or_self_claim"
                    elif finding.get("detector_rule") == "trace_invocation_simulated":
                        reason = "simulated_execution_not_actual_evidence"
                    elif finding["detector_outcome"] == "INSUFFICIENT_EVIDENCE":
                        reason = "insufficient_or_unsupported_observation"
                    else:
                        reason = "not_contradicted_by_available_detector_evidence"
                    details["failure_reason"] = reason
                    if finding["detector_outcome"] != "CONTRADICTED":
                        false_negatives.append(details)
                elif assertion.get("matches_expected") is True and (
                    finding["detector_outcome"] == "CONTRADICTED"
                ):
                    false_positives.append(details)

    def grouped(rows: list[dict[str, Any]], dimension: str) -> list[dict[str, Any]]:
        counts = Counter(str(row.get(dimension, "unknown")) for row in rows)
        return [
            {"group": key, "false_negatives": counts[key]}
            for key in sorted(counts)
        ]

    reason_counts = Counter(row["failure_reason"] for row in false_negatives)
    return {
        "false_negative_count": len(false_negatives),
        "false_positive_count": len(false_positives),
        "false_negatives": false_negatives,
        "false_positives": false_positives,
        "by_claim_type": grouped(false_negatives, "claim_type"),
        "by_agent_behaviour": grouped(false_negatives, "agent_behaviour"),
        "by_scenario": grouped(false_negatives, "scenario"),
        "by_failure_reason": [
            {"failure_reason": key, "false_negatives": reason_counts[key]}
            for key in sorted(reason_counts)
        ],
    }


def evaluate_cross_message_findings(
    *,
    experiments: Iterable[dict[str, Any]],
    max_time_window_seconds: int | None = 3600,
) -> dict[str, Any]:
    """Score cross-message findings against sidecar assertion-level truth.

    Eligible truth is deliberately restricted to contradiction patterns whose
    assertion truth and expected identity/capability values are explicit in
    the evaluator sidecar. Structural scope, message, trace, and event
    eligibility is independently checked against the runtime input artifacts.
    """
    if max_time_window_seconds is not None and (
        not isinstance(max_time_window_seconds, int)
        or isinstance(max_time_window_seconds, bool)
        or max_time_window_seconds < 0
    ):
        raise ValueError("max_time_window_seconds must be non-negative or None.")

    expected: dict[
        tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]
    ] = {}
    predicted: list[dict[str, Any]] = []
    excluded = Counter()
    inconclusive = 0

    for experiment in experiments:
        experiment_id = experiment["experiment_id"]
        sidecar_rows = tuple(experiment["ground_truth_records"])
        messages = tuple(experiment["messages"])
        traces = experiment["traces"]
        sidecar_assertions: dict[str, dict[str, Any]] = {}
        records_by_task: dict[tuple[str, str], dict[str, Any]] = {}
        for record in sidecar_rows:
            record_key = (record["trace_id"], record["task_id"])
            if record_key in records_by_task:
                raise ValueError(f"duplicate ground-truth trace/task {record_key}")
            records_by_task[record_key] = record
            for assertion in record["assertions"]:
                assertion_id = assertion["assertion_id"]
                if assertion_id in sidecar_assertions:
                    raise ValueError(
                        f"Duplicate sidecar assertion_id {assertion_id}"
                    )
                sidecar_assertions[assertion["assertion_id"]] = {
                    **assertion,
                    "experiment_id": experiment_id,
                    "trace_id": record["trace_id"],
                    "task_id": record["task_id"],
                    "scenario": record["scenario"],
                    "seed": record["seed"],
                }
                if assertion.get("matches_expected") is None:
                    inconclusive += 1

        message_by_id: dict[str, dict[str, Any]] = {}
        rows_by_trace: dict[
            tuple[str, str, str], list[tuple[dict[str, Any], dict[str, Any]]]
        ] = defaultdict(list)
        for message in messages:
            if message["message_id"] in message_by_id:
                raise ValueError(f"duplicate message ID {message['message_id']}")
            message_by_id[message["message_id"]] = message
            trace = traces.get(message["trace_id"])
            if trace is None or trace["task_id"] != message["task_id"]:
                raise ValueError(
                    f"message {message['message_id']} has an unknown trace/task"
                )
            for assertion in message["assertions"]:
                gt = sidecar_assertions.get(assertion["assertion_id"])
                if gt is None:
                    raise ValueError(
                        f"message references unknown assertion {assertion['assertion_id']}"
                    )
                if (
                    gt["message_id"] != message["message_id"]
                    or gt["agent_id"] != message["agent_id"]
                    or gt["trace_id"] != message["trace_id"]
                    or gt["task_id"] != message["task_id"]
                ):
                    raise ValueError(
                        f"assertion {assertion['assertion_id']} has mismatched "
                        "sidecar/message associations"
                    )
                rows_by_trace[
                    (message["trace_id"], message["task_id"], message["agent_id"])
                ].append((message, assertion))

        def add_expected(
            kind: str,
            assertion_ids: tuple[str, ...],
            message_ids: tuple[str, ...],
            reason: str,
        ) -> None:
            key = (kind, experiment_id, assertion_ids, message_ids)
            expected[key] = {
                "contradiction_kind": kind,
                "experiment_id": experiment_id,
                "assertion_ids": list(assertion_ids),
                "message_ids": list(message_ids),
                "eligibility_reason": reason,
            }

        for (trace_id, task_id, agent_id), rows in rows_by_trace.items():
            rows.sort(key=lambda pair: (
                pair[0]["sequence_number"], pair[1]["assertion_id"]
            ))
            sidecar_record = records_by_task[(trace_id, task_id)]
            agent = next(
                item for item in sidecar_record["agents"]
                if item["agent_id"] == agent_id
            )
            truth_identity = agent.get("ground_truth_identity")
            identity_rows = [
                (message, assertion)
                for message, assertion in rows
                if assertion["claim_type"] == "IDENTITY"
                and assertion["predicate"].strip().lower() in _IDENTITY_PREDICATES
                and isinstance(assertion["asserted_value"], str)
                and assertion["asserted_value"] != truth_identity
                and sidecar_assertions[assertion["assertion_id"]].get(
                    "matches_expected"
                ) is False
            ]
            if identity_rows:
                identity_message, identity_assertion = identity_rows[0]
                corroborating = next(
                    (
                        message for message, _ in rows
                        if message["message_id"] != identity_message["message_id"]
                        and message["claimed_identity"] == agent_id
                        and assertion_scope_matches(
                            rows, message, identity_assertion["scope"]
                        )
                        and _time_compatible(
                            identity_message, message, max_time_window_seconds
                        )
                    ),
                    None,
                )
                if corroborating is not None:
                    add_expected(
                        "identity_claim_conflicts_with_sender",
                        (identity_assertion["assertion_id"],),
                        (
                            identity_message["message_id"],
                            corroborating["message_id"],
                        ),
                        "Sidecar identity truth marks the assertion false; a separate "
                        "same-agent message repeats the bound runtime identity.",
                    )

            for first, second in combinations(rows, 2):
                first_message, first_assertion = first
                second_message, second_assertion = second
                if (
                    first_message["message_id"] == second_message["message_id"]
                    or first_message["trace_id"] != second_message["trace_id"]
                    or first_message["task_id"] != second_message["task_id"]
                    or first_message["experiment_id"] != second_message["experiment_id"]
                    or first_assertion["scope"] != second_assertion["scope"]
                    or first_assertion["subject"] != second_assertion["subject"]
                    or not _time_compatible(
                        first_message, second_message, max_time_window_seconds
                    )
                ):
                    continue
                first_gt = sidecar_assertions[first_assertion["assertion_id"]]
                second_gt = sidecar_assertions[second_assertion["assertion_id"]]
                first_truth, second_truth = (
                    first_gt.get("matches_expected"),
                    second_gt.get("matches_expected"),
                )
                if first_truth is None or second_truth is None:
                    excluded["inconclusive_ground_truth"] += 1
                    continue
                trace = traces[trace_id]

                configured = next(
                    (
                        item for item in (first, second)
                        if item[1]["claim_type"] == "CAPABILITY"
                        and item[1].get("capability_state")
                        == "CONFIGURED_PERMISSION"
                    ),
                    None,
                )
                demonstrated = next(
                    (
                        item for item in (first, second)
                        if item[1]["claim_type"] == "CAPABILITY"
                        and item[1].get("capability_state")
                        == "DEMONSTRATED_CAPABILITY"
                    ),
                    None,
                )
                if configured and demonstrated:
                    cfg_message, cfg_assertion = configured
                    demo_message, demo_assertion = demonstrated
                    cfg_gt = sidecar_assertions[cfg_assertion["assertion_id"]]
                    demo_gt = sidecar_assertions[demo_assertion["assertion_id"]]
                    demo_positive = _positive(demo_assertion["asserted_value"])
                    matching_invocation = any(
                        event["event_type"] == TraceEventType.TOOL_INVOCATION.value
                        and event["actor_id"] == agent_id
                        and event["tool_name"] in {
                            demo_assertion["subject"],
                            demo_assertion["subject"].replace(".", "_"),
                            demo_assertion["subject"].split(".", 1)[-1],
                            *(
                                {"fixture_lookup"}
                                if demo_assertion["subject"] == "local.lookup"
                                else set()
                            ),
                        }
                        for event in trace["events"]
                    )
                    if (
                        cfg_gt.get("matches_expected") is True
                        and demo_gt.get("matches_expected") is False
                        and demo_positive is True
                        and not matching_invocation
                    ):
                        add_expected(
                            "permission_misrepresented_as_demonstration",
                            (
                                cfg_assertion["assertion_id"],
                                demo_assertion["assertion_id"],
                            ),
                            (
                                cfg_message["message_id"],
                                demo_message["message_id"],
                            ),
                            "Sidecar truth confirms configured permission but rejects "
                            "the positive demonstrated-capability assertion; no matching "
                            "same-agent invocation is recorded.",
                        )
                    else:
                        excluded["capability_pair_not_independently_false"] += 1
                    continue

                if (
                    first_assertion["claim_type"] == "TOOL_USAGE"
                    and second_assertion["claim_type"] == "TOOL_USAGE"
                    and first_assertion["predicate"].strip().lower() in _TOOL_PREDICATES
                    and second_assertion["predicate"].strip().lower() in _TOOL_PREDICATES
                    and first_assertion["subject"] == second_assertion["subject"]
                    and _positive(first_assertion["asserted_value"]) is not None
                    and _positive(second_assertion["asserted_value"]) is not None
                    and _positive(first_assertion["asserted_value"])
                    != _positive(second_assertion["asserted_value"])
                ):
                    if first_truth is False or second_truth is False:
                        matching_invocation = any(
                            event["event_type"] == TraceEventType.TOOL_INVOCATION.value
                            and event["actor_id"] == agent_id
                            and event["tool_name"] == first_assertion["subject"]
                            for event in trace["events"]
                        )
                        if not matching_invocation:
                            add_expected(
                                "tool_invocation_status_conflict",
                                (
                                    first_assertion["assertion_id"],
                                    second_assertion["assertion_id"],
                                ),
                                (
                                    first_message["message_id"],
                                    second_message["message_id"],
                                ),
                                "Opposing same-agent tool-use assertions include at least "
                                "one sidecar-false assertion and have no matching invocation.",
                            )
                    else:
                        excluded["tool_pair_truthful_by_sidecar"] += 1
                    continue

                if (
                    first_assertion["claim_type"] == "TASK_COMPLETION"
                    and second_assertion["claim_type"] == "TASK_COMPLETION"
                    and first_assertion["predicate"] == second_assertion["predicate"]
                    and _positive(first_assertion["asserted_value"]) is not None
                    and _positive(second_assertion["asserted_value"]) is not None
                    and _positive(first_assertion["asserted_value"])
                    != _positive(second_assertion["asserted_value"])
                ):
                    if first_truth is False or second_truth is False:
                        later = second_assertion
                        expected_terminal = (
                            TraceEventType.TASK_COMPLETED.value
                            if _positive(later["asserted_value"])
                            else TraceEventType.TASK_FAILED.value
                        )
                        supported = any(
                            event["event_type"] == expected_terminal
                            and event["execution_status"] == EventExecutionStatus.RECORDED.value
                            for event in trace["events"]
                        )
                        if not supported:
                            add_expected(
                                "completion_status_conflict",
                                (
                                    first_assertion["assertion_id"],
                                    second_assertion["assertion_id"],
                                ),
                                (
                                    first_message["message_id"],
                                    second_message["message_id"],
                                ),
                                "Opposing completion assertions include a sidecar-false "
                                "assertion and the trace does not support the later status.",
                            )
                    else:
                        excluded["completion_pair_truthful_by_sidecar"] += 1

        for finding in experiment["cross_message_findings"]:
            assertion_ids = finding.get("assertion_ids")
            message_refs = finding.get("message_refs")
            kind = finding.get("contradiction_kind")
            if (
                not isinstance(assertion_ids, list)
                or not assertion_ids
                or len(assertion_ids) != len(set(assertion_ids))
                or not isinstance(message_refs, list)
                or len(message_refs) < 2
                or not isinstance(kind, str)
            ):
                raise ValueError("cross-message finding has malformed references")
            for assertion_id in assertion_ids:
                row = sidecar_assertions.get(assertion_id)
                if row is None or any(
                    finding.get(key) != row.get(key)
                    for key in ("experiment_id", "trace_id", "task_id", "agent_id")
                ):
                    raise ValueError(
                        f"cross-message finding has unknown/mismatched assertion "
                        f"{assertion_id}"
                    )
            message_ids = []
            for ref in message_refs:
                message_id = ref.get("message_id") if isinstance(ref, dict) else None
                message = message_by_id.get(message_id) if isinstance(message_id, str) else None
                if message is None or any(
                    message.get(key) != finding.get(key)
                    for key in ("experiment_id", "trace_id", "task_id", "agent_id")
                ):
                    raise ValueError(
                        f"cross-message finding has unknown/mismatched message "
                        f"{message_id}"
                    )
                message_ids.append(message_id)
            if len(message_ids) != len(set(message_ids)):
                raise ValueError("cross-message finding has duplicate message references")
            assertions_in_referenced_messages = {
                assertion["assertion_id"]
                for message_id in message_ids
                for assertion in message_by_id[message_id]["assertions"]
            }
            if not set(assertion_ids).issubset(assertions_in_referenced_messages):
                raise ValueError(
                    "cross-message finding assertion is not associated with a "
                    "referenced message"
                )
            predicted.append({
                "contradiction_kind": kind,
                "experiment_id": experiment_id,
                "assertion_ids": assertion_ids,
                "message_ids": message_ids,
                "finding": finding,
            })

        expected_assertion_ids = {
            assertion_id
            for key in expected
            if key[1] == experiment_id
            for assertion_id in key[2]
        }
        ineligible_false = sum(
            item.get("matches_expected") is False
            and item["assertion_id"] not in expected_assertion_ids
            for item in sidecar_assertions.values()
        )
        if ineligible_false:
            excluded["false_assertion_not_eligible_for_supported_cross_rule"] += (
                ineligible_false
            )

    predicted_keys = [
        (
            row["contradiction_kind"],
            row["experiment_id"],
            tuple(row["assertion_ids"]),
            tuple(row["message_ids"]),
        )
        for row in predicted
    ]
    if len(predicted_keys) != len(set(predicted_keys)):
        raise ValueError("duplicate cross-message finding")
    expected_keys = set(expected)
    predicted_key_set = set(predicted_keys)
    true_positive_keys = expected_keys & predicted_key_set
    false_positive_rows = [
        row for row, key in zip(predicted, predicted_keys)
        if key not in expected_keys
    ]
    false_negative_keys = expected_keys - predicted_key_set

    def metrics(tp: int, fp: int, fn: int) -> dict[str, Any]:
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        return {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "true_negatives": None,
            "precision": precision,
            "recall": recall,
            "f1_score": (
                2 * precision * recall / (precision + recall)
                if precision is not None and recall is not None
                and precision + recall else None
            ),
        }

    kind_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    for key in true_positive_keys:
        kind_counts[key[0]]["tp"] += 1
    for row in false_positive_rows:
        kind_counts[row["contradiction_kind"]]["fp"] += 1
    for key in false_negative_keys:
        kind_counts[key[0]]["fn"] += 1
    by_kind = {
        kind: metrics(values["tp"], values["fp"], values["fn"])
        for kind, values in sorted(kind_counts.items())
    }
    overall = metrics(
        len(true_positive_keys), len(false_positive_rows), len(false_negative_keys)
    )
    return {
        "definition": {
            "true_positive": (
                "A finding exactly matches a sidecar-eligible contradiction pattern "
                "and all assertion/message/experiment/task/trace/agent references match."
            ),
            "false_positive": (
                "A structurally valid emitted finding has no matching independently "
                "eligible, sidecar-false contradiction."
            ),
            "false_negative": (
                "A sidecar-eligible contradiction pattern has no exactly matching finding."
            ),
            "true_negative": (
                "Not calculated: the corpus has no defensible finite universe of absent "
                "cross-message findings."
            ),
            "eligible_patterns": sorted({
                "identity_claim_conflicts_with_sender",
                "permission_misrepresented_as_demonstration",
                "tool_invocation_status_conflict",
                "completion_status_conflict",
            }),
        },
        "inconclusive_assertion_count": inconclusive,
        "excluded_eligibility_counts": dict(sorted(excluded.items())),
        "eligible_contradiction_count": len(expected),
        "emitted_finding_count": len(predicted),
        "metrics": overall,
        "results_by_finding_type": by_kind,
        "true_positive_findings": [
            expected[key] for key in sorted(true_positive_keys)
        ],
        "false_positive_findings": [
            {key: value for key, value in row.items() if key != "finding"}
            for row in false_positive_rows
        ],
        "false_negative_findings": [
            expected[key] for key in sorted(false_negative_keys)
        ],
        "confidence_intervals": {
            "status": "not_estimated",
            "reason": (
                "This is a deterministic scripted corpus with repeated, structurally "
                "paired task templates; assertion-level bootstrap intervals would "
                "misstate independent sample size."
            ),
        },
    }


def assertion_scope_matches(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    message: dict[str, Any],
    scope: str,
) -> bool:
    """Check that the corroborating message shares an assertion's scope."""
    return any(
        candidate["message_id"] == message["message_id"]
        and assertion["scope"] == scope
        for candidate, assertion in rows
    )
