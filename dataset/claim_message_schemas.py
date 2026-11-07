"""Validated claim-bearing message contracts for future runtime integrations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import math
import re
from typing import Any, Iterable

from dataset.experiment_schemas import InteractionTrace, TraceEventType


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ClaimMessageType(StrEnum):
    TASK_ASSIGNMENT = "TASK_ASSIGNMENT"
    STATUS_UPDATE = "STATUS_UPDATE"
    CAPABILITY_REPORT = "CAPABILITY_REPORT"
    TOOL_REPORT = "TOOL_REPORT"
    TASK_COMPLETION = "TASK_COMPLETION"
    HANDOFF = "HANDOFF"
    EVIDENCE_REFERENCE = "EVIDENCE_REFERENCE"


class ProvenanceClaimType(StrEnum):
    IDENTITY = "IDENTITY"
    ROLE = "ROLE"
    CAPABILITY = "CAPABILITY"
    TOOL_USAGE = "TOOL_USAGE"
    TASK_COMPLETION = "TASK_COMPLETION"
    EVIDENCE_SOURCE = "EVIDENCE_SOURCE"


class CapabilityState(StrEnum):
    CONFIGURED_PERMISSION = "CONFIGURED_PERMISSION"
    DEMONSTRATED_CAPABILITY = "DEMONSTRATED_CAPABILITY"


def _identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a valid identifier.")


def _text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")


def _json_value(value: Any, field: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must contain finite JSON-compatible data.")
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, field)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{field} object keys must be strings.")
        for item in value.values():
            _json_value(item, field)
        return
    raise ValueError(f"{field} must contain JSON-compatible data.")


@dataclass(frozen=True)
class ClaimBearingAssertion:
    assertion_id: str
    provenance_claim_id: str
    agent_id: str
    claim_type: ProvenanceClaimType
    subject: str
    predicate: str
    asserted_value: Any
    scope: str
    trace_id: str
    message_id: str
    evidence_refs: tuple[str, ...] = ()
    capability_state: CapabilityState | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.assertion_id, "assertion_id"),
            (self.provenance_claim_id, "provenance_claim_id"),
            (self.agent_id, "agent_id"),
            (self.trace_id, "trace_id"),
            (self.message_id, "message_id"),
        ):
            _identifier(value, name)
        if not isinstance(self.claim_type, ProvenanceClaimType):
            raise ValueError("claim_type must be a ProvenanceClaimType.")
        for value, name in (
            (self.subject, "subject"),
            (self.predicate, "predicate"),
            (self.scope, "scope"),
        ):
            _text(value, name)
        _json_value(self.asserted_value, "asserted_value")
        if not isinstance(self.evidence_refs, tuple):
            raise ValueError("evidence_refs must be a tuple.")
        for evidence_id in self.evidence_refs:
            _identifier(evidence_id, "evidence reference")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("evidence_refs must not contain duplicates.")
        if self.claim_type == ProvenanceClaimType.CAPABILITY:
            if not isinstance(self.capability_state, CapabilityState):
                raise ValueError(
                    "capability assertions must identify configured permission "
                    "or demonstrated capability explicitly."
                )
        elif self.capability_state is not None:
            raise ValueError("capability_state is only valid for capability assertions.")


@dataclass(frozen=True)
class ClaimBearingMessage:
    message_id: str
    experiment_id: str
    trace_id: str
    task_id: str
    agent_id: str
    claimed_identity: str
    claimed_role: str
    subtask_id: str | None
    message_type: ClaimMessageType
    content: Any
    assertions: tuple[ClaimBearingAssertion, ...]
    evidence_ids: tuple[str, ...]
    handoff_to: str | None
    sequence_number: int
    timestamp: int | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.message_id, "message_id"),
            (self.experiment_id, "experiment_id"),
            (self.trace_id, "trace_id"),
            (self.task_id, "task_id"),
            (self.agent_id, "agent_id"),
        ):
            _identifier(value, name)
        _text(self.claimed_identity, "claimed_identity")
        _text(self.claimed_role, "claimed_role")
        if self.subtask_id is not None:
            _identifier(self.subtask_id, "subtask_id")
        if not isinstance(self.message_type, ClaimMessageType):
            raise ValueError("message_type must be a ClaimMessageType.")
        _json_value(self.content, "content")
        if not isinstance(self.assertions, tuple) or any(
            not isinstance(assertion, ClaimBearingAssertion)
            for assertion in self.assertions
        ):
            raise ValueError("assertions must be a tuple of ClaimBearingAssertion items.")
        if not isinstance(self.evidence_ids, tuple):
            raise ValueError("evidence_ids must be a tuple.")
        for evidence_id in self.evidence_ids:
            _identifier(evidence_id, "evidence_id")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must not contain duplicates.")
        if self.handoff_to is not None:
            _identifier(self.handoff_to, "handoff_to")
        if self.message_type == ClaimMessageType.HANDOFF:
            if self.handoff_to is None:
                raise ValueError("HANDOFF messages require handoff_to.")
        elif self.handoff_to is not None:
            raise ValueError("handoff_to is only valid for HANDOFF messages.")
        if (
            not isinstance(self.sequence_number, int)
            or isinstance(self.sequence_number, bool)
            or self.sequence_number < 0
        ):
            raise ValueError("sequence_number must be a non-negative integer.")
        if self.timestamp is not None and (
            not isinstance(self.timestamp, int)
            or isinstance(self.timestamp, bool)
            or self.timestamp < 0
        ):
            raise ValueError("timestamp must be a non-negative Unix timestamp.")


def validate_claim_messages(
    messages: Iterable[ClaimBearingMessage],
    *,
    experiment_id: str,
    trace_id: str,
    task_id: str,
    agent_ids: Iterable[str],
    subtask_ids: Iterable[str] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
) -> tuple[ClaimBearingMessage, ...]:
    """Validate message references and uniqueness in one experiment trace."""
    for value, field in (
        (experiment_id, "experiment_id"),
        (trace_id, "trace_id"),
        (task_id, "task_id"),
    ):
        _identifier(value, field)
    batch = tuple(messages)
    roster = set(agent_ids)
    if not roster:
        raise ValueError("agent_ids must contain at least one known agent.")
    for agent_id in roster:
        _identifier(agent_id, "agent_id")
    subtasks = None if subtask_ids is None else set(subtask_ids)
    if subtasks is not None:
        for subtask_id in subtasks:
            _identifier(subtask_id, "subtask_id")
    known_evidence = (
        None if known_evidence_ids is None else set(known_evidence_ids)
    )
    if known_evidence is not None:
        for evidence_id in known_evidence:
            _identifier(evidence_id, "evidence_id")

    message_ids: set[str] = set()
    assertion_ids: set[str] = set()
    sequences: set[int] = set()
    for message in batch:
        if not isinstance(message, ClaimBearingMessage):
            raise ValueError("messages must contain ClaimBearingMessage items.")
        if message.experiment_id != experiment_id:
            raise ValueError(f"message {message.message_id} has a mismatched experiment_id.")
        if message.trace_id != trace_id or message.task_id != task_id:
            raise ValueError(f"message {message.message_id} has a mismatched trace/task association.")
        if message.agent_id not in roster:
            raise ValueError(f"message {message.message_id} has an unknown agent_id.")
        if message.message_id in message_ids:
            raise ValueError(f"duplicate message_id: {message.message_id}")
        if message.sequence_number in sequences:
            raise ValueError(f"duplicate sequence_number: {message.sequence_number}")
        message_ids.add(message.message_id)
        sequences.add(message.sequence_number)
        if message.subtask_id is not None and subtasks is not None:
            if message.subtask_id not in subtasks:
                raise ValueError(f"message {message.message_id} references an unknown subtask.")
        if message.handoff_to is not None:
            if message.handoff_to not in roster:
                raise ValueError(f"message {message.message_id} hands off to an unknown agent.")
            if message.handoff_to == message.agent_id:
                raise ValueError(f"message {message.message_id} cannot hand off to itself.")
        if known_evidence is not None:
            missing = set(message.evidence_ids) - known_evidence
            if missing:
                raise ValueError(
                    f"message {message.message_id} references missing evidence: "
                    f"{', '.join(sorted(missing))}."
                )
        for assertion in message.assertions:
            if assertion.assertion_id in assertion_ids:
                raise ValueError(f"duplicate assertion_id: {assertion.assertion_id}")
            assertion_ids.add(assertion.assertion_id)
            if (
                assertion.agent_id != message.agent_id
                or assertion.message_id != message.message_id
                or assertion.trace_id != message.trace_id
            ):
                raise ValueError(
                    f"assertion {assertion.assertion_id} does not match its message context."
                )
            if not set(assertion.evidence_refs).issubset(message.evidence_ids):
                raise ValueError(
                    f"assertion {assertion.assertion_id} references evidence not declared "
                    "by its message."
                )
            if known_evidence is not None:
                missing = set(assertion.evidence_refs) - known_evidence
                if missing:
                    raise ValueError(
                        f"assertion {assertion.assertion_id} references missing evidence: "
                        f"{', '.join(sorted(missing))}."
                    )
    return batch


def validate_messages_against_trace(
    messages: Iterable[ClaimBearingMessage],
    trace: InteractionTrace,
    *,
    experiment_id: str,
    agent_ids: Iterable[str],
) -> tuple[ClaimBearingMessage, ...]:
    """Bind structured messages to their corresponding recorded trace events."""
    batch = validate_claim_messages(
        messages,
        experiment_id=experiment_id,
        trace_id=trace.trace_id,
        task_id=trace.task_id,
        agent_ids=agent_ids,
    )
    for message in batch:
        if message.sequence_number >= len(trace.events):
            raise ValueError(f"message {message.message_id} has no corresponding trace event.")
        event = trace.events[message.sequence_number]
        expected_type = (
            TraceEventType.HANDOFF
            if message.message_type == ClaimMessageType.HANDOFF
            else TraceEventType.AGENT_MESSAGE
        )
        if (
            event.event_type != expected_type
            or event.actor_id != message.agent_id
            or event.recipient_id != message.handoff_to
        ):
            raise ValueError(
                f"message {message.message_id} does not match trace event "
                f"{message.sequence_number}."
            )
    return batch


def claim_message_to_dict(message: ClaimBearingMessage) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "experiment_id": message.experiment_id,
        "trace_id": message.trace_id,
        "task_id": message.task_id,
        "agent_id": message.agent_id,
        "claimed_identity": message.claimed_identity,
        "claimed_role": message.claimed_role,
        "subtask_id": message.subtask_id,
        "message_type": message.message_type.value,
        "content": message.content,
        "assertions": [
            {
                "assertion_id": assertion.assertion_id,
                "provenance_claim_id": assertion.provenance_claim_id,
                "agent_id": assertion.agent_id,
                "claim_type": assertion.claim_type.value,
                "subject": assertion.subject,
                "predicate": assertion.predicate,
                "asserted_value": assertion.asserted_value,
                "scope": assertion.scope,
                "trace_id": assertion.trace_id,
                "message_id": assertion.message_id,
                "evidence_refs": list(assertion.evidence_refs),
                "capability_state": (
                    assertion.capability_state.value
                    if assertion.capability_state is not None
                    else None
                ),
            }
            for assertion in message.assertions
        ],
        "evidence_ids": list(message.evidence_ids),
        "handoff_to": message.handoff_to,
        "sequence_number": message.sequence_number,
        "timestamp": message.timestamp,
    }


def claim_message_to_json(message: ClaimBearingMessage) -> str:
    return json.dumps(
        claim_message_to_dict(message),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def claim_message_from_dict(data: dict[str, Any]) -> ClaimBearingMessage:
    if not isinstance(data, dict):
        raise ValueError("Claim-bearing message JSON must contain an object.")
    expected = {
        "message_id", "experiment_id", "trace_id", "task_id", "agent_id",
        "claimed_identity", "claimed_role", "subtask_id", "message_type",
        "content", "assertions", "evidence_ids", "handoff_to",
        "sequence_number", "timestamp",
    }
    if set(data) != expected:
        raise ValueError("Claim-bearing message contains missing or unknown fields.")
    if not isinstance(data["assertions"], list):
        raise ValueError("assertions must be an array.")
    assertions = []
    assertion_fields = {
        "assertion_id", "provenance_claim_id", "agent_id", "claim_type",
        "subject", "predicate", "asserted_value", "scope", "trace_id",
        "message_id", "evidence_refs", "capability_state",
    }
    for item in data["assertions"]:
        if not isinstance(item, dict) or set(item) != assertion_fields:
            raise ValueError("Assertion contains missing or unknown fields.")
        if not isinstance(item["evidence_refs"], list):
            raise ValueError("evidence_refs must be an array.")
        assertions.append(
            ClaimBearingAssertion(
                **{
                    **item,
                    "claim_type": ProvenanceClaimType(item["claim_type"]),
                    "evidence_refs": tuple(item["evidence_refs"]),
                    "capability_state": (
                        CapabilityState(item["capability_state"])
                        if item["capability_state"] is not None
                        else None
                    ),
                }
            )
        )
    if not isinstance(data["evidence_ids"], list):
        raise ValueError("evidence_ids must be an array.")
    return ClaimBearingMessage(
        **{
            **{key: value for key, value in data.items() if key != "assertions"},
            "message_type": ClaimMessageType(data["message_type"]),
            "assertions": tuple(assertions),
            "evidence_ids": tuple(data["evidence_ids"]),
        }
    )


def claim_message_from_json(serialized: str) -> ClaimBearingMessage:
    try:
        data = json.loads(serialized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid claim-bearing message JSON: {exc}") from exc
    return claim_message_from_dict(data)
