"""Validated schemas and detector-safe projections for future TAMAS runs.

These models define data contracts only. They do not execute agents, tools,
detectors, or evaluators.
"""

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any

from dataset.tamas_loader import TAMASRecord
from dataset.workflow_preview import InteractionMode


class TraceEventType(StrEnum):
    TASK_SUBMITTED = "task_submitted"
    AGENT_MESSAGE = "agent_message"
    HANDOFF = "handoff"
    TOOL_INVOCATION = "tool_invocation"
    TOOL_RESULT = "tool_result"
    ROUTER_DECISION = "router_decision"
    DETECTOR_ALERT = "detector_alert"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    SIMULATION_ENDED = "simulation_ended"


class EventSource(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    ROUTER = "router"
    DETECTOR = "detector"
    SYSTEM = "system"


class EventExecutionStatus(StrEnum):
    RECORDED = "recorded"
    LOCAL_SIMULATION = "local_simulation"
    SIMULATED_RESPONSE = "simulated_response"
    NOT_EXECUTED = "not_executed"


class TraceExecutionMode(StrEnum):
    CONTROLLED_LOCAL_SIMULATION = "controlled_local_simulation"


class ClaimEvidenceType(StrEnum):
    SELF_CLAIMED = "self_claimed"
    RUNTIME_OBSERVED = "runtime_observed"
    ISSUER_ATTESTED = "issuer_attested"
    EXTERNALLY_BENCHMARKED = "externally_benchmarked"


class ClaimTruthStatus(StrEnum):
    NOT_ASSESSED = "not_assessed"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INCONCLUSIVE = "inconclusive"


class IdentityBinding(StrEnum):
    UNVERIFIED = "unverified"
    SESSION_BOUND = "session_bound"
    ISSUER_ATTESTED = "issuer_attested"
    CRYPTOGRAPHICALLY_BOUND = "cryptographically_bound"


class ClaimSubjectType(StrEnum):
    IDENTITY = "identity"
    ROLE = "role"
    CAPABILITY = "capability"
    TOOL_USAGE = "tool_usage"
    TASK_COMPLETION = "task_completion"
    PROVENANCE = "provenance"


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")


def _require_optional_text(value: str | None, field: str) -> None:
    if value is not None:
        _require_text(value, field)


def _json_value(value: Any, field: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain finite JSON-compatible data.") from exc


@dataclass(frozen=True)
class DetectorAgentInput:
    """Only original role text and runtime-available declarations."""

    agent_id: str
    claimed_role: str
    claimed_description: str
    declared_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.agent_id, "agent_id")
        _require_text(self.claimed_role, "claimed_role")
        _require_text(self.claimed_description, "claimed_description")
        if any(not isinstance(tool, str) or not tool.strip() for tool in self.declared_tools):
            raise ValueError("declared_tools must contain non-empty strings.")


@dataclass(frozen=True)
class DetectorTaskInput:
    """Runtime-only TAMAS task projection with no evaluator fields."""

    task_id: str
    scenario: str
    user_query: str
    agents: tuple[DetectorAgentInput, ...]

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.scenario, "scenario")
        _require_text(self.user_query, "user_query")
        if not isinstance(self.agents, tuple) or not self.agents:
            raise ValueError("agents must contain at least one runtime agent.")
        if any(not isinstance(agent, DetectorAgentInput) for agent in self.agents):
            raise ValueError("agents must contain DetectorAgentInput items.")
        ids = [agent.agent_id for agent in self.agents]
        if len(ids) != len(set(ids)):
            raise ValueError("runtime agent IDs must be unique within a task.")

    @classmethod
    def from_tamas_record(cls, record: TAMASRecord, task_id: str) -> "DetectorTaskInput":
        """Copy only query, scenario, role text, and embedded tool declarations.

        ``task_id`` must be an opaque run ID. The source filename, record label,
        involved-agent annotation, expected output, and benign tool-call
        sequence are deliberately not copied.
        """
        agents = []
        for index, agent in enumerate(record.agents):
            tools = agent.tools or ()
            if any(not isinstance(tool, str) or not tool.strip() for tool in tools):
                raise ValueError(
                    "TAMAS embedded tool declarations must be non-empty strings "
                    "before they can be exposed to a detector."
                )
            agents.append(DetectorAgentInput(
                agent_id=f"agent-{index:03d}",
                claimed_role=agent.name,
                claimed_description=agent.description,
                declared_tools=tuple(tools),
            ))
        return cls(
            task_id=task_id,
            scenario=record.scenario,
            user_query=record.original_user_query,
            agents=tuple(agents),
        )


@dataclass(frozen=True)
class TraceEvent:
    """One actually observed event; sequence numbers are per trace."""

    sequence: int
    event_type: TraceEventType
    source: EventSource
    execution_status: EventExecutionStatus = EventExecutionStatus.RECORDED
    actor_id: str | None = None
    recipient_id: str | None = None
    elapsed_ms: int | None = None
    content: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_result: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, TraceEventType):
            raise ValueError("event_type must be a TraceEventType.")
        if not isinstance(self.source, EventSource):
            raise ValueError("source must be an EventSource.")
        if not isinstance(self.execution_status, EventExecutionStatus):
            raise ValueError("execution_status must be an EventExecutionStatus.")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("sequence must be a non-negative integer.")
        if self.elapsed_ms is not None and (
            not isinstance(self.elapsed_ms, int)
            or isinstance(self.elapsed_ms, bool)
            or self.elapsed_ms < 0
        ):
            raise ValueError("elapsed_ms must be a non-negative integer when present.")
        for value, field in (
            (self.actor_id, "actor_id"),
            (self.recipient_id, "recipient_id"),
            (self.content, "content"),
            (self.tool_name, "tool_name"),
        ):
            _require_optional_text(value, field)
        if self.tool_arguments is not None:
            if not isinstance(self.tool_arguments, dict):
                raise ValueError("tool_arguments must be an object when present.")
            _json_value(self.tool_arguments, "tool_arguments")
            if any(not isinstance(key, str) for key in self.tool_arguments):
                raise ValueError("tool_arguments keys must be strings.")
        if self.tool_result is not None:
            _json_value(self.tool_result, "tool_result")

        if self.event_type in (TraceEventType.AGENT_MESSAGE, TraceEventType.HANDOFF):
            if self.source != EventSource.AGENT or self.actor_id is None:
                raise ValueError(f"{self.event_type.value} requires an agent actor.")
        if self.event_type == TraceEventType.AGENT_MESSAGE and self.content is None:
            raise ValueError("agent_message requires content.")
        if self.event_type == TraceEventType.HANDOFF and self.recipient_id is None:
            raise ValueError("handoff requires a recipient_id.")
        if self.event_type == TraceEventType.TOOL_INVOCATION:
            if self.source != EventSource.AGENT or self.actor_id is None or self.tool_name is None:
                raise ValueError("tool_invocation requires an agent actor and tool_name.")
        if self.event_type == TraceEventType.TOOL_RESULT:
            if self.source != EventSource.TOOL or self.tool_name is None:
                raise ValueError("tool_result requires source=tool and a tool_name.")
        if self.event_type == TraceEventType.TASK_SUBMITTED:
            if self.source != EventSource.USER or self.content is None:
                raise ValueError("task_submitted requires source=user and the original query.")
        if self.event_type == TraceEventType.ROUTER_DECISION and self.source != EventSource.ROUTER:
            raise ValueError("router_decision requires source=router.")
        if self.event_type == TraceEventType.DETECTOR_ALERT and self.source != EventSource.DETECTOR:
            raise ValueError("detector_alert requires source=detector.")
        if self.event_type == TraceEventType.SIMULATION_ENDED and self.source != EventSource.SYSTEM:
            raise ValueError("simulation_ended requires source=system.")


@dataclass(frozen=True)
class InteractionTrace:
    """Trace envelope intentionally separated from evaluator labels/annotations."""

    trace_id: str
    task_id: str
    interaction_mode: InteractionMode
    events: tuple[TraceEvent, ...]
    execution_mode: TraceExecutionMode = TraceExecutionMode.CONTROLLED_LOCAL_SIMULATION
    model_id: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    tool_registry_version: str | None = None
    tool_registry_hash: str | None = None
    config_hash: str | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.trace_id, "trace_id")
        _require_text(self.task_id, "task_id")
        if not isinstance(self.interaction_mode, InteractionMode):
            raise ValueError("interaction_mode must be an InteractionMode.")
        if not isinstance(self.execution_mode, TraceExecutionMode):
            raise ValueError("execution_mode must be a TraceExecutionMode.")
        _require_optional_text(self.model_id, "model_id")
        _require_optional_text(self.model_version, "model_version")
        _require_optional_text(self.prompt_version, "prompt_version")
        _require_optional_text(self.prompt_hash, "prompt_hash")
        _require_optional_text(self.tool_registry_version, "tool_registry_version")
        _require_optional_text(self.tool_registry_hash, "tool_registry_hash")
        _require_optional_text(self.config_hash, "config_hash")
        if self.seed is not None and (
            not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0
        ):
            raise ValueError("seed must be a non-negative integer when present.")
        if not isinstance(self.events, tuple) or not self.events:
            raise ValueError("events must contain at least one actually observed event.")
        if any(not isinstance(event, TraceEvent) for event in self.events):
            raise ValueError("events must contain TraceEvent items.")
        if tuple(event.sequence for event in self.events) != tuple(range(len(self.events))):
            raise ValueError("event sequence values must be contiguous and start at zero.")


@dataclass(frozen=True)
class ClaimEvidence:
    """Evidence reference supporting a claim; integrity is not truth."""

    evidence_id: str
    evidence_type: ClaimEvidenceType
    source_ref: str
    issuer: str | None = None
    integrity_verified: bool | None = None

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_id")
        if not isinstance(self.evidence_type, ClaimEvidenceType):
            raise ValueError("evidence_type must be a ClaimEvidenceType.")
        _require_text(self.source_ref, "source_ref")
        _require_optional_text(self.issuer, "issuer")
        if self.integrity_verified is not None and not isinstance(self.integrity_verified, bool):
            raise ValueError("integrity_verified must be bool or None.")


@dataclass(frozen=True)
class ObservedBehavior:
    """Link from a claim to events actually recorded in a trace."""

    trace_id: str
    event_sequences: tuple[int, ...]
    summary: str

    def __post_init__(self) -> None:
        _require_text(self.trace_id, "trace_id")
        _require_text(self.summary, "summary")
        if not isinstance(self.event_sequences, tuple) or not self.event_sequences:
            raise ValueError("event_sequences must reference at least one trace event.")
        if any(
            not isinstance(index, int) or isinstance(index, bool) or index < 0
            for index in self.event_sequences
        ):
            raise ValueError("event_sequences must contain non-negative integers.")
        if len(self.event_sequences) != len(set(self.event_sequences)):
            raise ValueError("event_sequences must not contain duplicates.")


@dataclass(frozen=True)
class ClaimAssessment:
    """Evaluator-only truth assessment; never included in runtime detector input."""

    status: ClaimTruthStatus = ClaimTruthStatus.NOT_ASSESSED
    assessor: str | None = None
    method: str | None = None
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ClaimTruthStatus):
            raise ValueError("status must be a ClaimTruthStatus.")
        _require_optional_text(self.assessor, "assessor")
        _require_optional_text(self.method, "method")
        if any(not isinstance(value, str) or not value.strip() for value in self.evidence_ids):
            raise ValueError("assessment evidence_ids must contain non-empty strings.")
        if self.status == ClaimTruthStatus.NOT_ASSESSED:
            if self.assessor is not None or self.method is not None or self.evidence_ids:
                raise ValueError("not_assessed claims cannot include evaluator assessment details.")
        elif self.assessor is None or self.method is None:
            raise ValueError("an assessed claim requires an assessor and assessment method.")
        if self.status in (ClaimTruthStatus.SUPPORTED, ClaimTruthStatus.CONTRADICTED) and not self.evidence_ids:
            raise ValueError("supported or contradicted claims require cited evidence_ids.")


@dataclass(frozen=True)
class ClaimAssertion:
    """Atomic runtime claim, separate from its evaluator-only assessment."""

    assertion_id: str
    claim_type: ClaimSubjectType
    asserted_value: str
    supporting_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.assertion_id, "assertion_id")
        if not isinstance(self.claim_type, ClaimSubjectType):
            raise ValueError("claim_type must be a ClaimSubjectType.")
        _require_text(self.asserted_value, "asserted_value")
        if not isinstance(self.supporting_evidence_ids, tuple) or any(
            not isinstance(value, str) or not value.strip()
            for value in self.supporting_evidence_ids
        ):
            raise ValueError("supporting_evidence_ids must contain non-empty strings.")
        if len(self.supporting_evidence_ids) != len(set(self.supporting_evidence_ids)):
            raise ValueError("supporting_evidence_ids must be unique.")


@dataclass(frozen=True)
class ProvenanceClaim:
    """An agent's claim plus evidence, observed behavior, and evaluator status."""

    claim_id: str
    agent_id: str
    identity_binding: IdentityBinding
    claimed_role: str
    claimed_capabilities: tuple[str, ...]
    supporting_evidence: tuple[ClaimEvidence, ...] = ()
    observed_behavior: tuple[ObservedBehavior, ...] = ()
    assessment: ClaimAssessment = ClaimAssessment()
    assertions: tuple[ClaimAssertion, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.claim_id, "claim_id")
        _require_text(self.agent_id, "agent_id")
        _require_text(self.claimed_role, "claimed_role")
        if not isinstance(self.identity_binding, IdentityBinding):
            raise ValueError("identity_binding must be an IdentityBinding.")
        if not isinstance(self.claimed_capabilities, tuple) or not self.claimed_capabilities or any(
            not isinstance(value, str) or not value.strip()
            for value in self.claimed_capabilities
        ):
            raise ValueError("claimed_capabilities must contain non-empty strings.")
        if not isinstance(self.supporting_evidence, tuple) or any(
            not isinstance(item, ClaimEvidence) for item in self.supporting_evidence
        ):
            raise ValueError("supporting_evidence must contain ClaimEvidence items.")
        if not isinstance(self.observed_behavior, tuple) or any(
            not isinstance(item, ObservedBehavior) for item in self.observed_behavior
        ):
            raise ValueError("observed_behavior must contain ObservedBehavior items.")
        if not isinstance(self.assessment, ClaimAssessment):
            raise ValueError("assessment must be a ClaimAssessment.")
        if not isinstance(self.assertions, tuple) or any(
            not isinstance(item, ClaimAssertion) for item in self.assertions
        ):
            raise ValueError("assertions must contain ClaimAssertion items.")
        assertion_ids = [item.assertion_id for item in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("assertion IDs must be unique within a provenance claim.")
        evidence_ids = [item.evidence_id for item in self.supporting_evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("supporting evidence IDs must be unique within a claim.")

    def detector_view(self) -> "DetectorProvenanceClaim":
        """Return runtime claim information without evaluator assessment data."""
        return DetectorProvenanceClaim(
            claim_id=self.claim_id,
            agent_id=self.agent_id,
            identity_binding=self.identity_binding,
            claimed_role=self.claimed_role,
            claimed_capabilities=self.claimed_capabilities,
            supporting_evidence=self.supporting_evidence,
            observed_behavior=self.observed_behavior,
            assertions=self.assertions,
        )


@dataclass(frozen=True)
class DetectorProvenanceClaim:
    """Detector-facing projection; deliberately has no ground-truth status."""

    claim_id: str
    agent_id: str
    identity_binding: IdentityBinding
    claimed_role: str
    claimed_capabilities: tuple[str, ...]
    supporting_evidence: tuple[ClaimEvidence, ...]
    observed_behavior: tuple[ObservedBehavior, ...]
    assertions: tuple[ClaimAssertion, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.claim_id, "claim_id")
        _require_text(self.agent_id, "agent_id")
        _require_text(self.claimed_role, "claimed_role")
        if not isinstance(self.identity_binding, IdentityBinding):
            raise ValueError("identity_binding must be an IdentityBinding.")
        if not isinstance(self.claimed_capabilities, tuple) or not self.claimed_capabilities or any(
            not isinstance(value, str) or not value.strip()
            for value in self.claimed_capabilities
        ):
            raise ValueError("claimed_capabilities must contain non-empty strings.")
        if not isinstance(self.supporting_evidence, tuple) or any(
            not isinstance(item, ClaimEvidence) for item in self.supporting_evidence
        ):
            raise ValueError("supporting_evidence must contain ClaimEvidence items.")
        if not isinstance(self.observed_behavior, tuple) or any(
            not isinstance(item, ObservedBehavior) for item in self.observed_behavior
        ):
            raise ValueError("observed_behavior must contain ObservedBehavior items.")
        if not isinstance(self.assertions, tuple) or any(
            not isinstance(item, ClaimAssertion) for item in self.assertions
        ):
            raise ValueError("assertions must contain ClaimAssertion items.")
        if len({item.assertion_id for item in self.assertions}) != len(self.assertions):
            raise ValueError("assertion IDs must be unique within a provenance claim.")
        if len({item.evidence_id for item in self.supporting_evidence}) != len(
            self.supporting_evidence
        ):
            raise ValueError("supporting evidence IDs must be unique within a claim.")
