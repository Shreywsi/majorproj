"""Assertion-level contradiction analysis over runtime messages and traces."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from dataset.claim_message_schemas import (
    CapabilityState,
    ClaimBearingAssertion,
    ClaimBearingMessage,
    ProvenanceClaimType,
)
from dataset.experiment_schemas import (
    EventExecutionStatus,
    InteractionTrace,
    TraceEventType,
)


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


def _identity_value(assertion: ClaimBearingAssertion) -> str | None:
    if (
        assertion.claim_type != ProvenanceClaimType.IDENTITY
        or assertion.predicate.strip().lower() not in {"is", "identity", "claimed_identity"}
    ):
        return None
    return assertion.asserted_value if isinstance(assertion.asserted_value, str) else None


def _compatible_window(
    first: ClaimBearingMessage,
    second: ClaimBearingMessage,
    max_time_window_seconds: int | None,
) -> bool:
    if first.timestamp is not None and second.timestamp is not None:
        delta = second.timestamp - first.timestamp
        return delta >= 0 and (
            max_time_window_seconds is None or delta <= max_time_window_seconds
        )
    return True


def _task_status_supports(
    trace: InteractionTrace,
    assertion: ClaimBearingAssertion,
) -> bool:
    expected_type = (
        TraceEventType.TASK_COMPLETED
        if _positive(assertion.asserted_value)
        else TraceEventType.TASK_FAILED
    )
    return any(
        event.event_type == expected_type
        and event.execution_status == EventExecutionStatus.RECORDED
        for event in trace.events
    )


def _capability_was_demonstrated(
    trace: InteractionTrace,
    agent_id: str,
    capability: str,
) -> bool:
    tool_names = {
        capability,
        capability.replace(".", "_"),
        capability.split(".", 1)[-1],
    }
    if capability == "local.lookup":
        tool_names.add("fixture_lookup")
    return any(
        event.event_type == TraceEventType.TOOL_INVOCATION
        and event.actor_id == agent_id
        and event.tool_name in tool_names
        for event in trace.events
    )


def analyze_cross_message_assertions(
    messages: Iterable[ClaimBearingMessage],
    traces: Mapping[str, InteractionTrace],
    *,
    max_time_window_seconds: int | None = 3600,
) -> tuple[dict[str, Any], ...]:
    """Return conflicts only within one agent, task, trace, scope, and time window."""
    if max_time_window_seconds is not None and (
        not isinstance(max_time_window_seconds, int)
        or isinstance(max_time_window_seconds, bool)
        or max_time_window_seconds < 0
    ):
        raise ValueError("max_time_window_seconds must be non-negative or None.")

    message_batch = tuple(messages)
    if any(not isinstance(message, ClaimBearingMessage) for message in message_batch):
        raise ValueError("messages must contain ClaimBearingMessage values.")
    by_id: dict[str, ClaimBearingMessage] = {}
    assertion_rows: list[
        tuple[ClaimBearingMessage, ClaimBearingAssertion]
    ] = []
    seen_assertions: set[str] = set()
    for message in message_batch:
        if message.message_id in by_id:
            raise ValueError(f"duplicate message_id: {message.message_id}")
        if message.trace_id not in traces:
            raise ValueError(f"message {message.message_id} references an unknown trace.")
        trace = traces[message.trace_id]
        if message.task_id != trace.task_id:
            raise ValueError(f"message {message.message_id} references a mismatched task.")
        by_id[message.message_id] = message
        for assertion in message.assertions:
            if assertion.assertion_id in seen_assertions:
                raise ValueError(f"duplicate assertion_id: {assertion.assertion_id}")
            seen_assertions.add(assertion.assertion_id)
            assertion_rows.append((message, assertion))

    grouped: dict[tuple[str, str, str, str], list[
        tuple[ClaimBearingMessage, ClaimBearingAssertion]
    ]] = defaultdict(list)
    for message, assertion in assertion_rows:
        grouped[
            (message.agent_id, message.task_id, message.trace_id, assertion.scope)
        ].append((message, assertion))

    findings: list[dict[str, Any]] = []

    def append_finding(
        first: tuple[ClaimBearingMessage, ClaimBearingAssertion],
        second: tuple[ClaimBearingMessage, ClaimBearingAssertion],
        kind: str,
        explanation: str,
        event_refs: tuple[str, ...] = (),
    ) -> None:
        first_message, first_assertion = first
        second_message, second_assertion = second
        findings.append({
            "finding_type": "cross_message_contradiction",
            "contradiction_kind": kind,
            "experiment_id": first_message.experiment_id,
            "trace_id": first_message.trace_id,
            "task_id": first_message.task_id,
            "agent_id": first_message.agent_id,
            "subject": first_assertion.subject,
            "scope": first_assertion.scope,
            "assertion_ids": [
                first_assertion.assertion_id,
                second_assertion.assertion_id,
            ],
            "provenance_claim_ids": [
                first_assertion.provenance_claim_id,
                second_assertion.provenance_claim_id,
            ],
            "message_refs": [
                {
                    "message_id": first_message.message_id,
                    "sequence_number": first_message.sequence_number,
                },
                {
                    "message_id": second_message.message_id,
                    "sequence_number": second_message.sequence_number,
                },
            ],
            "relevant_event_references": list(event_refs),
            "explanation": explanation,
        })

    for (agent_id, _task_id, trace_id, _scope), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: (row[0].sequence_number, row[1].assertion_id))
        trace = traces[trace_id]

        for index, first in enumerate(rows):
            first_message, first_assertion = first
            for second in rows[index + 1:]:
                second_message, second_assertion = second
                if first_message.message_id == second_message.message_id:
                    continue
                if first_message.experiment_id != second_message.experiment_id:
                    continue
                if not _compatible_window(
                    first_message, second_message, max_time_window_seconds
                ):
                    continue
                if first_assertion.subject != second_assertion.subject:
                    continue

                if (
                    first_assertion.claim_type == ProvenanceClaimType.TASK_COMPLETION
                    and second_assertion.claim_type == ProvenanceClaimType.TASK_COMPLETION
                    and first_assertion.predicate == second_assertion.predicate
                    and _positive(first_assertion.asserted_value) is not None
                    and _positive(second_assertion.asserted_value) is not None
                    and _positive(first_assertion.asserted_value)
                    != _positive(second_assertion.asserted_value)
                    and not _task_status_supports(trace, second_assertion)
                ):
                    append_finding(
                        first,
                        second,
                        "completion_status_conflict",
                        "The later completion assertion conflicts with an earlier status, "
                        "and the trace has no matching recorded terminal task status.",
                    )
                    continue

                if (
                    first_assertion.claim_type == ProvenanceClaimType.TOOL_USAGE
                    and second_assertion.claim_type == ProvenanceClaimType.TOOL_USAGE
                    and first_assertion.subject == second_assertion.subject
                    and first_assertion.predicate.strip().lower()
                    in {"execution_status", "invoked", "tool_invocation", "tool_used"}
                    and second_assertion.predicate.strip().lower()
                    in {"execution_status", "invoked", "tool_invocation", "tool_used"}
                ):
                    first_value = _positive(first_assertion.asserted_value)
                    second_value = _positive(second_assertion.asserted_value)
                    if first_value is not None and second_value is not None and first_value != second_value:
                        matching_events = tuple(
                            f"{trace.trace_id}:{event.sequence}"
                            for event in trace.events
                            if event.event_type == TraceEventType.TOOL_INVOCATION
                            and event.actor_id == agent_id
                            and event.tool_name == first_assertion.subject
                        )
                        if not matching_events:
                            append_finding(
                                first,
                                second,
                                "tool_invocation_status_conflict",
                                "The same agent made incompatible tool-use assertions for "
                                "the same tool, with no matching invocation in its trace.",
                            )
                    continue

                configured_assertion = next(
                    (
                        item for item in (first, second)
                        if item[1].claim_type == ProvenanceClaimType.CAPABILITY
                        and item[1].capability_state == CapabilityState.CONFIGURED_PERMISSION
                    ),
                    None,
                )
                demonstrated_assertion = next(
                    (
                        item for item in (first, second)
                        if item[1].claim_type == ProvenanceClaimType.CAPABILITY
                        and item[1].capability_state == CapabilityState.DEMONSTRATED_CAPABILITY
                    ),
                    None,
                )
                if configured_assertion and demonstrated_assertion:
                    demo_message, demo = demonstrated_assertion
                    if (
                        demo.subject == configured_assertion[1].subject
                        and _positive(demo.asserted_value) is True
                        and not _capability_was_demonstrated(
                            trace, agent_id, demo.subject
                        )
                    ):
                        event_refs = tuple(
                            f"{trace.trace_id}:{event.sequence}"
                            for event in trace.events
                            if event.actor_id == agent_id
                            and event.event_type in (
                                TraceEventType.TOOL_INVOCATION,
                                TraceEventType.TOOL_RESULT,
                            )
                        )
                        append_finding(
                            configured_assertion,
                            demonstrated_assertion,
                            "permission_misrepresented_as_demonstration",
                            "Configured permission is not execution evidence; no matching "
                            "tool invocation by this agent demonstrates the claimed capability.",
                            event_refs,
                        )

        identity_rows = [
            row for row in rows
            if _identity_value(row[1]) is not None
            and _identity_value(row[1]) != agent_id
        ]
        if identity_rows:
            identity_message, identity_assertion = identity_rows[0]
            corroborating_message = next(
                (
                    row[0] for row in rows
                    if row[0].message_id != identity_message.message_id
                    and row[0].claimed_identity == agent_id
                    and _compatible_window(
                        identity_message,
                        row[0],
                        max_time_window_seconds,
                    )
                ),
                None,
            )
            if corroborating_message is not None:
                findings.append({
                    "finding_type": "cross_message_contradiction",
                    "contradiction_kind": "identity_claim_conflicts_with_sender",
                    "experiment_id": identity_message.experiment_id,
                    "trace_id": identity_message.trace_id,
                    "task_id": identity_message.task_id,
                    "agent_id": agent_id,
                    "subject": identity_assertion.subject,
                    "scope": identity_assertion.scope,
                    "assertion_ids": [identity_assertion.assertion_id],
                    "provenance_claim_ids": [identity_assertion.provenance_claim_id],
                    "message_refs": [
                        {
                            "message_id": identity_message.message_id,
                            "sequence_number": identity_message.sequence_number,
                        },
                        {
                            "message_id": corroborating_message.message_id,
                            "sequence_number": corroborating_message.sequence_number,
                        },
                    ],
                    "relevant_event_references": [],
                    "explanation": (
                        "The identity assertion differs from the runtime-associated sender "
                        "identity, which is repeated in another message from the same agent."
                    ),
                })

    unique_findings = {
        (
            item["contradiction_kind"],
            item["experiment_id"],
            item["trace_id"],
            item["task_id"],
            item["agent_id"],
            tuple(item["assertion_ids"]),
            tuple(reference["message_id"] for reference in item["message_refs"]),
        ): item
        for item in findings
    }
    findings = list(unique_findings.values())
    findings.sort(key=lambda item: (
        item["experiment_id"],
        item["trace_id"],
        item["agent_id"],
        tuple(item["assertion_ids"]),
        item["contradiction_kind"],
    ))
    return tuple(findings)
