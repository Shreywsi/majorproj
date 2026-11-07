"""Deterministic claim-bearing agents and safe controlled task scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Iterable

from nacl.signing import SigningKey

from dataset.claim_message_schemas import (
    CapabilityState,
    ClaimBearingAssertion,
    ClaimBearingMessage,
    ClaimMessageType,
    ProvenanceClaimType,
    validate_claim_messages,
    validate_messages_against_trace,
)
from dataset.experiment_schemas import (
    EventExecutionStatus,
    EventSource,
    InteractionTrace,
    TraceEvent,
    TraceEventType,
)
from dataset.runtime_evidence import (
    AgentIdentityRegistry,
    AuthorityKind,
    EvidenceCategory,
    RuntimeEvidence,
    RuntimeEvidenceIssuer,
    RuntimeEvidenceVerifier,
    TrustPolicy,
    validate_message_evidence_links,
)
from dataset.trace_store import trace_to_json
from dataset.workflow_preview import InteractionMode


class AgentBehaviour(StrEnum):
    HONEST_AGENT = "HONEST_AGENT"
    FALSE_REPORTING_AGENT = "FALSE_REPORTING_AGENT"
    IDENTITY_MISMATCH_AGENT = "IDENTITY_MISMATCH_AGENT"
    CAPABILITY_EXAGGERATION_AGENT = "CAPABILITY_EXAGGERATION_AGENT"
    EVIDENCE_MISREPRESENTATION_AGENT = "EVIDENCE_MISREPRESENTATION_AGENT"


class ScenarioName(StrEnum):
    ARITHMETIC = "arithmetic"
    LOCAL_LOOKUP = "local_lookup"
    AUTHORIZED_TOOL = "authorized_tool"
    RELAY = "relay"
    MISSING_LOOKUP = "missing_lookup"


_DEFAULT_BEHAVIOURS = tuple(AgentBehaviour)
_ROLES = ("coordinator", "reporter", "operator", "reviewer", "observer")
_UNDEMONSTRATED_CAPABILITY = "local.reverse_text"
_FIXTURE = {
    "sample-a": {"name": "North", "score": 17},
    "sample-b": {"name": "South", "score": 23},
}
_BASE_TIMESTAMP = 1_700_000_000
_EVIDENCE_TTL_SECONDS = 86_400


@dataclass(frozen=True)
class ControlledTaskScenario:
    name: ScenarioName
    description: str
    required_role: str
    required_capability: str
    operation: str
    tool_name: str
    inputs: dict[str, Any]
    procedure: str


@dataclass(frozen=True)
class ExperimentRun:
    experiment_id: str
    seed: int
    scenarios: tuple[ScenarioName, ...]
    agents_per_task: int
    traces: tuple[InteractionTrace, ...]
    messages: tuple[ClaimBearingMessage, ...]
    evidence: tuple[RuntimeEvidence, ...]
    evaluator_sidecar: tuple[dict[str, Any], ...]
    trust_policy: TrustPolicy

    @property
    def assertions(self) -> tuple[ClaimBearingAssertion, ...]:
        return tuple(
            assertion
            for message in self.messages
            for assertion in message.assertions
        )

    @property
    def simulated_tool_invocations(self) -> int:
        return sum(
            event.event_type == TraceEventType.TOOL_INVOCATION
            and event.execution_status == EventExecutionStatus.LOCAL_SIMULATION
            for trace in self.traces
            for event in trace.events
        )

    @property
    def participating_agents(self) -> int:
        return self.agents_per_task * len(self.traces)


@dataclass(frozen=True)
class _Agent:
    agent_id: str
    role: str
    behaviour: AgentBehaviour
    signing_key: SigningKey


@dataclass(frozen=True)
class _EvidenceRequest:
    issuer: RuntimeEvidenceIssuer
    category: EvidenceCategory
    subject: str
    predicate: str
    asserted_value: Any
    scope: str
    source_ref: str
    source_event_sequence: int | None = None
    execution_status: EventExecutionStatus | None = None
    evidence_id: str | None = None
    bind_to_message: bool = True


def available_scenarios() -> tuple[ScenarioName, ...]:
    return tuple(ScenarioName)


def _scenario(name: ScenarioName) -> ControlledTaskScenario:
    if name == ScenarioName.ARITHMETIC:
        return ControlledTaskScenario(
            name,
            "Add two small integers using a deterministic local operation.",
            "operator",
            "local.add",
            "add",
            "local_add",
            {"left": 14, "right": 28},
            "Read the two integer inputs and invoke local_add once.",
        )
    if name == ScenarioName.LOCAL_LOOKUP:
        return ControlledTaskScenario(
            name,
            "Look up a named record in a fixed, in-memory fixture.",
            "operator",
            "local.lookup",
            "lookup",
            "local_lookup",
            {"fixture": _FIXTURE, "key": "sample-b"},
            "Read the fixture key and return only its local record.",
        )
    if name == ScenarioName.AUTHORIZED_TOOL:
        return ControlledTaskScenario(
            name,
            "Normalize a fixed text value through an authorized local operation.",
            "operator",
            "local.normalize_text",
            "normalize_text",
            "local_normalize_text",
            {"text": "  ProvenanceGuard Local Demo  "},
            "Trim surrounding whitespace and case-fold the local text.",
        )
    if name == ScenarioName.RELAY:
        return ControlledTaskScenario(
            name,
            "Compute a local result and have a second agent report the handoff.",
            "operator",
            "local.add",
            "add",
            "local_add",
            {"left": 9, "right": 6},
            "The operator adds the inputs and hands the observed result to the reporter.",
        )
    if name == ScenarioName.MISSING_LOOKUP:
        return ControlledTaskScenario(
            name,
            "Check a missing key in a fixed local fixture and report the unavailable result.",
            "operator",
            "local.lookup",
            "lookup",
            "local_lookup",
            {"fixture": _FIXTURE, "key": "missing-record"},
            "Look up the requested key; the operation returns found=false.",
        )
    raise ValueError(f"Unknown controlled scenario: {name!r}.")


def scenario_details(name: ScenarioName | str) -> ControlledTaskScenario:
    """Return public task requirements for a controlled routing decision."""
    try:
        selected = name if isinstance(name, ScenarioName) else ScenarioName(name)
    except ValueError as exc:
        raise ValueError(f"Unknown controlled scenario: {name!r}.") from exc
    return _scenario(selected)


def _execute_local_operation(
    scenario: ControlledTaskScenario,
) -> tuple[Any, bool]:
    """Run one allow-listed in-memory operation; never dispatch arbitrary code."""
    if scenario.operation == "add":
        result = scenario.inputs["left"] + scenario.inputs["right"]
        return result, True
    if scenario.operation == "lookup":
        record = scenario.inputs["fixture"].get(scenario.inputs["key"])
        if record is None:
            return {"found": False, "key": scenario.inputs["key"]}, False
        return {"found": True, "key": scenario.inputs["key"], "record": record}, True
    if scenario.operation == "normalize_text":
        return scenario.inputs["text"].strip().casefold(), True
    raise ValueError(f"Unsupported local operation: {scenario.operation!r}.")


def _stable_seed(seed: int, experiment_id: str, authority_id: str) -> bytes:
    return hashlib.sha256(
        f"stage7c|{seed}|{experiment_id}|{authority_id}".encode("utf-8")
    ).digest()


def _issuer(
    issuer_id: str,
    kind: AuthorityKind,
    categories: tuple[EvidenceCategory, ...],
    seed: int,
    experiment_id: str,
) -> RuntimeEvidenceIssuer:
    return RuntimeEvidenceIssuer(
        issuer_id,
        kind,
        categories=categories,
        seed=_stable_seed(seed, experiment_id, issuer_id),
    )


def _canonical_json(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _short_hash(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _agent_behaviours(
    behaviours: Iterable[AgentBehaviour | str] | None,
    agents_per_task: int,
) -> tuple[AgentBehaviour, ...]:
    if (
        not isinstance(agents_per_task, int)
        or isinstance(agents_per_task, bool)
        or agents_per_task < 3
    ):
        raise ValueError("agents_per_task must be an integer of at least three.")
    if behaviours is None:
        selected = _DEFAULT_BEHAVIOURS[:agents_per_task]
        selected += (AgentBehaviour.HONEST_AGENT,) * (
            agents_per_task - len(selected)
        )
    else:
        try:
            selected = tuple(AgentBehaviour(value) for value in behaviours)
        except ValueError as exc:
            raise ValueError(f"Unsupported agent behaviour: {exc}") from exc
        if not selected:
            raise ValueError("behaviours must contain at least one behaviour.")
        if len(selected) > agents_per_task:
            raise ValueError("behaviours cannot contain more entries than agents_per_task.")
        selected = selected + (AgentBehaviour.HONEST_AGENT,) * (
            agents_per_task - len(selected)
        )
    return selected


def _scenario_selection(
    scenarios: Iterable[ScenarioName | str] | None,
) -> tuple[ScenarioName, ...]:
    if scenarios is None:
        return available_scenarios()
    try:
        selected = tuple(ScenarioName(value) for value in scenarios)
    except ValueError as exc:
        raise ValueError(f"Unsupported scenario: {exc}") from exc
    if not selected:
        raise ValueError("At least one scenario must be selected.")
    if len(selected) != len(set(selected)):
        raise ValueError("Scenario selection must not contain duplicates.")
    return selected


def _make_message_id(task_index: int, message_index: int) -> str:
    return f"msg-{task_index:02d}-{message_index:03d}"


def _make_assertion_id(task_index: int, assertion_index: int) -> str:
    return f"assert-{task_index:02d}-{assertion_index:04d}"


def _make_evidence_id(task_index: int, evidence_index: int) -> str:
    return f"evi-{task_index:02d}-{evidence_index:04d}"


def _issue(
    request: _EvidenceRequest,
    *,
    task_index: int,
    evidence_index: int,
    agent_id: str,
    experiment_id: str,
    trace_id: str,
    task_id: str,
    created_at: int,
    message_id: str | None = None,
    assertion_id: str | None = None,
) -> RuntimeEvidence:
    evidence_id = request.evidence_id or _make_evidence_id(task_index, evidence_index)
    return request.issuer.issue_evidence(
        evidence_id=evidence_id,
        nonce=f"nonce-{evidence_id}",
        category=request.category,
        agent_id=agent_id,
        experiment_id=experiment_id,
        trace_id=trace_id,
        task_id=task_id,
        message_id=message_id,
        assertion_id=assertion_id,
        subject=request.subject,
        predicate=request.predicate,
        asserted_value=request.asserted_value,
        scope=request.scope,
        created_at=created_at,
        expires_at=created_at + _EVIDENCE_TTL_SECONDS,
        source_event_sequence=request.source_event_sequence,
        execution_status=request.execution_status,
        source_ref=request.source_ref,
    )


def _build_task(
    *,
    experiment_id: str,
    task_index: int,
    scenario_name: ScenarioName,
    seed: int,
    agents_per_task: int,
    behaviours: tuple[AgentBehaviour, ...],
    selected_operator_index: int = 2,
    stable_agent_namespace: str | None = None,
    identity_issuer: RuntimeEvidenceIssuer,
    runtime_issuer: RuntimeEvidenceIssuer,
    tool_issuer: RuntimeEvidenceIssuer,
    identity_registry: AgentIdentityRegistry,
    evidence_verifier: RuntimeEvidenceVerifier,
) -> tuple[
    InteractionTrace,
    tuple[ClaimBearingMessage, ...],
    tuple[RuntimeEvidence, ...],
    dict[str, Any],
]:
    task_id = f"task-{_short_hash(experiment_id)}-{task_index:02d}"
    trace_id = f"trace-{_short_hash(experiment_id + '|' + task_id)}"
    task = _scenario(scenario_name)
    now = _BASE_TIMESTAMP + task_index * 1000
    task_scope = f"task:{task_id}"
    role_assignments = [
        _ROLES[index] if index < len(_ROLES) else f"observer-{index:02d}"
        for index in range(agents_per_task)
    ]
    if selected_operator_index != 2:
        role_assignments[2], role_assignments[selected_operator_index] = (
            role_assignments[selected_operator_index],
            role_assignments[2],
        )
    agents = tuple(
        _Agent(
            agent_id=(
                f"agent-{_short_hash(stable_agent_namespace)}-{index:02d}"
                if stable_agent_namespace is not None
                else f"agent-{_short_hash(task_id)}-{index:02d}"
            ),
            role=role_assignments[index],
            behaviour=behaviours[index],
            signing_key=SigningKey(
                _stable_seed(
                    seed,
                    stable_agent_namespace or experiment_id,
                    (
                        f"agent-{index}"
                        if stable_agent_namespace is not None
                        else f"{task_id}|agent-{index}"
                    ),
                )
            ),
        )
        for index in range(agents_per_task)
    )
    coordinator = next(agent for agent in agents if agent.role == "coordinator")
    reporter = next(agent for agent in agents if agent.role == "reporter")
    operator = next(agent for agent in agents if agent.role == "operator")
    events: list[TraceEvent] = []
    messages: list[ClaimBearingMessage] = []
    evidence_records: list[RuntimeEvidence] = []
    permission_evidence: dict[tuple[str, str], RuntimeEvidence] = {}
    observed_tool_evidence: RuntimeEvidence | None = None
    assertion_expectations: list[dict[str, Any]] = []
    message_index = 0
    assertion_index = 0
    evidence_index = 0

    def append_event(
        event_type: TraceEventType,
        source: EventSource,
        *,
        status: EventExecutionStatus = EventExecutionStatus.RECORDED,
        **fields: Any,
    ) -> int:
        sequence = len(events)
        events.append(
            TraceEvent(
                sequence=sequence,
                event_type=event_type,
                source=source,
                execution_status=status,
                elapsed_ms=sequence,
                **fields,
            )
        )
        return sequence

    def emit_message(
        agent: _Agent,
        message_type: ClaimMessageType,
        content: Any,
        *,
        claims: tuple[dict[str, Any], ...] = (),
        handoff_to: str | None = None,
    ) -> ClaimBearingMessage:
        nonlocal message_index, assertion_index, evidence_index
        message_id = _make_message_id(task_index, message_index)
        sequence = len(events)
        assertions: list[ClaimBearingAssertion] = []
        message_evidence: dict[str, RuntimeEvidence] = {}
        for claim in claims:
            claim_assertion_id = _make_assertion_id(task_index, assertion_index)
            assertion_index += 1
            claim_evidence = list(claim.get("evidence_refs", ()))
            for request in claim.get("evidence_requests", ()):
                record = _issue(
                    request,
                    task_index=task_index,
                    evidence_index=evidence_index,
                    agent_id=agent.agent_id,
                    experiment_id=experiment_id,
                    trace_id=trace_id,
                    task_id=task_id,
                    created_at=now + sequence,
                    message_id=message_id if request.bind_to_message else None,
                    assertion_id=claim_assertion_id if request.bind_to_message else None,
                )
                evidence_index += 1
                evidence_records.append(record)
                claim_evidence.append(record)
            for record in claim_evidence:
                message_evidence[record.evidence_id] = record
            assertions.append(
                ClaimBearingAssertion(
                    assertion_id=claim_assertion_id,
                    provenance_claim_id=f"claim-{task_index:02d}-{agent.agent_id[-2:]}",
                    agent_id=agent.agent_id,
                    claim_type=claim["claim_type"],
                    subject=claim["subject"],
                    predicate=claim["predicate"],
                    asserted_value=claim["asserted_value"],
                    scope=claim.get("scope", task_scope),
                    trace_id=trace_id,
                    message_id=message_id,
                    evidence_refs=tuple(
                        dict.fromkeys(record.evidence_id for record in claim_evidence)
                    ),
                    capability_state=claim.get("capability_state"),
                )
            )
            assertion_expectations.append(
                {
                    "assertion_id": claim_assertion_id,
                    "expected_asserted_value": claim["expected_asserted_value"],
                }
            )
        event_type = (
            TraceEventType.HANDOFF
            if message_type == ClaimMessageType.HANDOFF
            else TraceEventType.AGENT_MESSAGE
        )
        append_event(
            event_type,
            EventSource.AGENT,
            actor_id=agent.agent_id,
            recipient_id=handoff_to,
            content=_canonical_json(content),
        )
        message = ClaimBearingMessage(
            message_id=message_id,
            experiment_id=experiment_id,
            trace_id=trace_id,
            task_id=task_id,
            agent_id=agent.agent_id,
            claimed_identity=agent.agent_id,
            claimed_role=agent.role,
            subtask_id=f"subtask-{task_index:02d}",
            message_type=message_type,
            content=content,
            assertions=tuple(assertions),
            evidence_ids=tuple(sorted(message_evidence)),
            handoff_to=handoff_to,
            sequence_number=sequence,
            timestamp=now + sequence,
        )
        messages.append(message)
        message_index += 1
        return message

    append_event(
        TraceEventType.TASK_SUBMITTED,
        EventSource.USER,
        content=_canonical_json(
            {
                "description": task.description,
                "inputs": task.inputs,
                "task_id": task_id,
            }
        ),
    )
    assignment_event_sequence = len(events)
    emit_message(
        coordinator,
        ClaimMessageType.TASK_ASSIGNMENT,
        {
            "task_id": task_id,
            "assignments": [
                {"agent_id": agent.agent_id, "role": agent.role}
                for agent in agents
            ],
            "required_role": task.required_role,
        },
    )

    registration_evidence: dict[str, RuntimeEvidence] = {}
    for agent in agents:
        challenge = f"challenge-{task_id}-{agent.agent_id}"
        registry_challenge = identity_registry.create_challenge(
            agent.agent_id, challenge=challenge
        )
        registration_proof = agent.signing_key.sign(
            identity_registry.registration_payload(agent.agent_id, registry_challenge)
        ).signature
        record = identity_registry.register_agent(
            agent_id=agent.agent_id,
            public_key=bytes(agent.signing_key.verify_key),
            challenge=registry_challenge,
            proof_signature=registration_proof,
            experiment_id=experiment_id,
            created_at=now,
            expires_at=now + _EVIDENCE_TTL_SECONDS,
            trace_id=trace_id,
            task_id=task_id,
            evidence_id=f"identity-{task_index:02d}-{agent.agent_id[-2:]}",
            nonce=f"nonce-identity-{task_index:02d}-{agent.agent_id[-2:]}",
        )
        registration_evidence[agent.agent_id] = record
        evidence_records.append(record)

    # Identity and role statements remain agent claims, separate from the
    # registry's authenticated task-local identity binding.
    for index, agent in enumerate(agents):
        identity_value = (
            agents[(index + 1) % len(agents)].agent_id
            if agent.behaviour == AgentBehaviour.IDENTITY_MISMATCH_AGENT
            else agent.agent_id
        )
        role_value = agent.role
        role_request = _EvidenceRequest(
            issuer=identity_issuer,
            category=EvidenceCategory.ROLE,
            subject=agent.agent_id,
            predicate="assigned_role",
            asserted_value=role_value,
            scope=task_scope,
            source_ref="runtime:task_assignment",
            source_event_sequence=assignment_event_sequence,
        )
        identity_claim = {
            "claim_type": ProvenanceClaimType.IDENTITY,
            "subject": "task_local_identity",
            "predicate": "is",
            "asserted_value": identity_value,
            "expected_asserted_value": agent.agent_id,
            "evidence_refs": (registration_evidence[agent.agent_id],),
        }
        role_claim = {
            "claim_type": ProvenanceClaimType.ROLE,
            "subject": agent.agent_id,
            "predicate": "assigned_role",
            "asserted_value": role_value,
            "expected_asserted_value": role_value,
            "evidence_requests": (role_request,),
        }
        emit_message(
            agent,
            ClaimMessageType.STATUS_UPDATE,
            {"status": "assigned", "task_id": task_id},
            claims=(identity_claim, role_claim),
        )

    emit_message(
        coordinator,
        ClaimMessageType.HANDOFF,
        {"reason": "deterministic task assignment", "recipient": operator.agent_id},
        handoff_to=operator.agent_id,
    )

    invocation_sequence = len(events)
    append_event(
        TraceEventType.TOOL_INVOCATION,
        EventSource.AGENT,
        status=EventExecutionStatus.LOCAL_SIMULATION,
        actor_id=operator.agent_id,
        tool_name=task.tool_name,
        tool_arguments=task.inputs,
        content="Authorized deterministic local operation; no external action was performed.",
    )
    actual_result, task_completed = _execute_local_operation(task)
    tool_result_sequence = append_event(
        TraceEventType.TOOL_RESULT,
        EventSource.TOOL,
        status=EventExecutionStatus.SIMULATED_RESPONSE,
        actor_id="local-runtime",
        tool_name=task.tool_name,
        tool_result={
            "status": "simulated_response",
            "result": actual_result,
            "task_completion": task_completed,
        },
        content="Local deterministic result; no external service was called.",
    )
    observed_tool_evidence = _issue(
        _EvidenceRequest(
            issuer=tool_issuer,
            category=EvidenceCategory.TOOL_EXECUTION,
            subject=task.tool_name,
            predicate="invoked",
            asserted_value=True,
            scope=task_scope,
            source_ref=f"trace:{trace_id}:event:{invocation_sequence}",
            source_event_sequence=invocation_sequence,
            execution_status=EventExecutionStatus.LOCAL_SIMULATION,
        ),
        task_index=task_index,
        evidence_index=evidence_index,
        agent_id=operator.agent_id,
        experiment_id=experiment_id,
        trace_id=trace_id,
        task_id=task_id,
        created_at=now + tool_result_sequence,
    )
    evidence_index += 1
    evidence_records.append(observed_tool_evidence)

    for agent in agents:
        claims: list[dict[str, Any]] = []
        for capability in (task.required_capability, _UNDEMONSTRATED_CAPABILITY):
            request = _EvidenceRequest(
                issuer=runtime_issuer,
                category=EvidenceCategory.CONFIGURED_PERMISSION,
                subject=capability,
                predicate="configured_permission",
                asserted_value=True,
                scope=task_scope,
                source_ref=f"runtime:permission-manifest:{agent.role}",
                bind_to_message=False,
            )
            claims.append(
                {
                    "claim_type": ProvenanceClaimType.CAPABILITY,
                    "subject": capability,
                    "predicate": "configured_permission",
                    "asserted_value": {"capability": capability, "granted": True},
                    "expected_asserted_value": {"capability": capability, "granted": True},
                    "capability_state": CapabilityState.CONFIGURED_PERMISSION,
                    "evidence_requests": (request,),
                }
            )
        first_permission_record = len(evidence_records)
        message = emit_message(
            agent,
            ClaimMessageType.CAPABILITY_REPORT,
            {"capabilities_reported": [claim["subject"] for claim in claims]},
            claims=tuple(claims),
        )
        emitted_permissions = evidence_records[first_permission_record:]
        for assertion, record in zip(message.assertions, emitted_permissions):
            permission_evidence[(agent.agent_id, assertion.subject)] = record

    for agent in agents:
        demonstration_claims: tuple[dict[str, Any], ...] = ()
        if agent.behaviour == AgentBehaviour.CAPABILITY_EXAGGERATION_AGENT:
            permission_record = permission_evidence[
                (agent.agent_id, _UNDEMONSTRATED_CAPABILITY)
            ]
            demonstration_claims = (
                {
                    "claim_type": ProvenanceClaimType.CAPABILITY,
                    "subject": _UNDEMONSTRATED_CAPABILITY,
                    "predicate": "demonstrated_capability",
                    "asserted_value": {
                        "capability": _UNDEMONSTRATED_CAPABILITY,
                        "demonstrated": True,
                    },
                    "expected_asserted_value": {
                        "capability": _UNDEMONSTRATED_CAPABILITY,
                        "demonstrated": False,
                    },
                    "capability_state": CapabilityState.DEMONSTRATED_CAPABILITY,
                    "evidence_refs": (permission_record,),
                },
            )
        elif agent.agent_id == operator.agent_id:
            demonstration_claims = (
                {
                    "claim_type": ProvenanceClaimType.CAPABILITY,
                    "subject": task.required_capability,
                    "predicate": "demonstrated_capability",
                    "asserted_value": {
                        "capability": task.required_capability,
                        "demonstrated": True,
                    },
                    "expected_asserted_value": {
                        "capability": task.required_capability,
                        "demonstrated": True,
                    },
                    "capability_state": CapabilityState.DEMONSTRATED_CAPABILITY,
                    "evidence_refs": (observed_tool_evidence,),
                },
            )
        if demonstration_claims:
            emit_message(
                agent,
                ClaimMessageType.CAPABILITY_REPORT,
                {
                    "capabilities_reported": [
                        claim["subject"] for claim in demonstration_claims
                    ]
                },
                claims=demonstration_claims,
            )

    for agent in agents:
        if agent.behaviour == AgentBehaviour.FALSE_REPORTING_AGENT:
            tool_value = {
                "agent_id": agent.agent_id,
                "tool_name": task.tool_name,
                "execution_status": "RECORDED",
            }
        elif agent.agent_id == operator.agent_id:
            tool_value = {
                "agent_id": agent.agent_id,
                "tool_name": task.tool_name,
                "execution_status": EventExecutionStatus.LOCAL_SIMULATION.value,
            }
        else:
            tool_value = {
                "agent_id": agent.agent_id,
                "tool_name": task.tool_name,
                "execution_status": EventExecutionStatus.NOT_EXECUTED.value,
            }
        expected_tool_value = {
            "agent_id": operator.agent_id if agent.agent_id == operator.agent_id else agent.agent_id,
            "tool_name": task.tool_name,
            "execution_status": (
                EventExecutionStatus.LOCAL_SIMULATION.value
                if agent.agent_id == operator.agent_id
                else EventExecutionStatus.NOT_EXECUTED.value
            ),
        }
        tool_claim: dict[str, Any] = {
            "claim_type": ProvenanceClaimType.TOOL_USAGE,
            "subject": task.tool_name,
            "predicate": "execution_status",
            "asserted_value": tool_value,
            "expected_asserted_value": expected_tool_value,
        }
        if agent.agent_id == operator.agent_id:
            tool_claim["evidence_refs"] = (observed_tool_evidence,)
        elif agent.behaviour == AgentBehaviour.FALSE_REPORTING_AGENT:
            tool_claim["evidence_refs"] = ()
        elif agent.behaviour == AgentBehaviour.EVIDENCE_MISREPRESENTATION_AGENT:
            tool_claim["evidence_refs"] = (
                permission_evidence[(agent.agent_id, _UNDEMONSTRATED_CAPABILITY)],
            )
        emit_message(
            agent,
            ClaimMessageType.TOOL_REPORT,
            {"tool_name": task.tool_name, "reported_execution": tool_value},
            claims=(tool_claim,),
        )

    relay_payload = {
        "operation": task.operation,
        "result": actual_result,
        "execution_status": EventExecutionStatus.SIMULATED_RESPONSE.value,
    }
    emit_message(
        operator,
        ClaimMessageType.HANDOFF,
        {"recipient": reporter.agent_id, "payload": relay_payload},
        handoff_to=reporter.agent_id,
    )

    for agent in agents:
        evidence = permission_evidence[(agent.agent_id, _UNDEMONSTRATED_CAPABILITY)]
        if agent.behaviour == AgentBehaviour.EVIDENCE_MISREPRESENTATION_AGENT:
            source_value = {
                "evidence_id": evidence.evidence_id,
                "source_ref": f"trace:{trace_id}:event:0",
            }
        else:
            source_value = {
                "evidence_id": evidence.evidence_id,
                "source_ref": evidence.source_ref,
            }
        emit_message(
            agent,
            ClaimMessageType.EVIDENCE_REFERENCE,
            {"evidence_id": evidence.evidence_id},
            claims=(
                {
                    "claim_type": ProvenanceClaimType.EVIDENCE_SOURCE,
                    "subject": evidence.evidence_id,
                    "predicate": "source_ref",
                    "asserted_value": source_value,
                    "expected_asserted_value": {
                        "evidence_id": evidence.evidence_id,
                        "source_ref": evidence.source_ref,
                    },
                    "evidence_refs": (evidence,),
                },
            ),
        )

    reported_completion = (
        True
        if reporter.behaviour == AgentBehaviour.FALSE_REPORTING_AGENT
        else task_completed
    )
    completion_claim = {
        "claim_type": ProvenanceClaimType.TASK_COMPLETION,
        "subject": task_id,
        "predicate": "completed",
        "asserted_value": reported_completion,
        "expected_asserted_value": task_completed,
    }
    emit_message(
        reporter,
        ClaimMessageType.TASK_COMPLETION,
        {
            "completed": reported_completion,
            "result": actual_result,
            "task_id": task_id,
        },
        claims=(completion_claim,),
    )
    append_event(
        TraceEventType.TASK_COMPLETED if task_completed else TraceEventType.TASK_FAILED,
        EventSource.SYSTEM,
        status=EventExecutionStatus.RECORDED,
        content="Local task predicate evaluated by the controlled runtime.",
        tool_result={"completed": task_completed, "result": actual_result},
    )
    append_event(
        TraceEventType.SIMULATION_ENDED,
        EventSource.SYSTEM,
        status=EventExecutionStatus.NOT_EXECUTED,
        content="Controlled local claim-bearing experiment ended; no external actions occurred.",
    )

    trace = InteractionTrace(
        trace_id=trace_id,
        task_id=task_id,
        interaction_mode=InteractionMode.ROUND_ROBIN,
        events=tuple(events),
        seed=seed,
        tool_registry_version="controlled-local-operations-v1",
        tool_registry_hash=hashlib.sha256(
            b"arithmetic|fixture_lookup|text_normalization|safe_local_only"
        ).hexdigest(),
        config_hash=hashlib.sha256(
            f"{experiment_id}|{scenario_name.value}|{agents_per_task}".encode()
        ).hexdigest(),
    )
    validate_claim_messages(
        messages,
        experiment_id=experiment_id,
        trace_id=trace_id,
        task_id=task_id,
        agent_ids=(agent.agent_id for agent in agents),
        subtask_ids=(f"subtask-{task_index:02d}",),
        known_evidence_ids=(record.evidence_id for record in evidence_records),
    )
    validate_messages_against_trace(
        messages,
        trace,
        experiment_id=experiment_id,
        agent_ids=(agent.agent_id for agent in agents),
    )
    validate_message_evidence_links(
        messages,
        {record.evidence_id: record for record in evidence_records},
    )
    for record in evidence_records:
        result = evidence_verifier.verify(
            record,
            now=record.created_at + 1,
            expected_agent_id=record.agent_id,
            expected_experiment_id=experiment_id,
            expected_trace_id=trace_id,
            expected_task_id=task_id,
        )
        if not result.trusted:
            raise ValueError(
                f"Generated evidence {record.evidence_id} failed verification: "
                f"{result.status.value}."
            )
        if record.source_event_sequence is not None:
            if record.source_event_sequence >= len(trace.events):
                raise ValueError(
                    f"Evidence {record.evidence_id} points outside its trace."
                )
            if (
                record.category == EvidenceCategory.TOOL_EXECUTION
                and trace.events[record.source_event_sequence].event_type
                != TraceEventType.TOOL_INVOCATION
            ):
                raise ValueError(
                    f"Tool evidence {record.evidence_id} does not reference an invocation."
                )

    expected_by_assertion = {
        record["assertion_id"]: record["expected_asserted_value"]
        for record in assertion_expectations
    }
    sidecar = {
        "experiment_id": experiment_id,
        "trace_id": trace_id,
        "task_id": task_id,
        "scenario": scenario_name.value,
        "seed": seed,
        "scenario_configuration": {
            "description": task.description,
            "required_role": task.required_role,
            "required_capability": task.required_capability,
            "operation": task.operation,
            "tool_name": task.tool_name,
            "inputs": task.inputs,
            "expected_execution_procedure": task.procedure,
        },
        "expected_execution_outcome": {
            "completed": task_completed,
            "result": actual_result,
        },
        "agents": [
            {
                "agent_id": agent.agent_id,
                "ground_truth_identity": agent.agent_id,
                "ground_truth_role": agent.role,
                "behaviour_category": agent.behaviour.value,
                "ground_truth_capabilities": {
                    task.required_capability: {
                        "configured_permission": True,
                        "demonstrated": agent.agent_id == operator.agent_id,
                    },
                    _UNDEMONSTRATED_CAPABILITY: {
                        "configured_permission": True,
                        "demonstrated": False,
                    },
                },
            }
            for agent in agents
        ],
        "assertions": [
            {
                "assertion_id": assertion.assertion_id,
                "message_id": assertion.message_id,
                "agent_id": assertion.agent_id,
                "claim_type": assertion.claim_type.value,
                "subject": assertion.subject,
                "predicate": assertion.predicate,
                "capability_state": (
                    assertion.capability_state.value
                    if assertion.capability_state is not None
                    else None
                ),
                "asserted_value": assertion.asserted_value,
                "expected_asserted_value": expected_by_assertion[assertion.assertion_id],
                "matches_expected": (
                    assertion.asserted_value
                    == expected_by_assertion[assertion.assertion_id]
                ),
                "evidence_refs": list(assertion.evidence_refs),
            }
            for message in messages
            for assertion in message.assertions
        ],
        "expected_evidence_relationships": [
            {
                "evidence_id": record.evidence_id,
                "agent_id": record.agent_id,
                "category": record.category.value,
                "source_event_sequence": record.source_event_sequence,
                "source_ref": record.source_ref,
            }
            for record in evidence_records
        ],
    }
    return trace, tuple(messages), tuple(evidence_records), sidecar


def _evaluator_attestations(
    sidecar: dict[str, Any],
    *,
    task_index: int,
    evaluator_issuer: RuntimeEvidenceIssuer,
    seed: int,
    evidence_verifier: RuntimeEvidenceVerifier,
) -> dict[str, Any]:
    outcome = sidecar["expected_execution_outcome"]
    reporter_id = next(
        agent["agent_id"]
        for agent in sidecar["agents"]
        if agent["ground_truth_role"] == "reporter"
    )
    created_at = _BASE_TIMESTAMP + task_index * 1000
    completion_assertion = next(
        assertion
        for assertion in sidecar["assertions"]
        if assertion["claim_type"] == ProvenanceClaimType.TASK_COMPLETION.value
    )
    attestation = evaluator_issuer.issue_evidence(
        evidence_id=f"eval-{task_index:02d}-completion",
        nonce=f"nonce-eval-{task_index:02d}-completion",
        category=EvidenceCategory.TASK_COMPLETION,
        agent_id=reporter_id,
        experiment_id=sidecar["experiment_id"],
        trace_id=sidecar["trace_id"],
        task_id=sidecar["task_id"],
        assertion_id=completion_assertion["assertion_id"],
        subject=sidecar["task_id"],
        predicate="completed",
        asserted_value=outcome["completed"],
        scope=f"evaluator:task:{sidecar['task_id']}",
        created_at=created_at,
        expires_at=created_at + _EVIDENCE_TTL_SECONDS,
        source_ref="controlled_runtime:task_predicate",
    )
    operator = next(
        agent
        for agent in sidecar["agents"]
        if agent["ground_truth_role"] == "operator"
    )
    capability = next(
        name
        for name, state in operator["ground_truth_capabilities"].items()
        if state["demonstrated"]
    )
    demonstration_attestation = evaluator_issuer.issue_evidence(
        evidence_id=f"eval-{task_index:02d}-capability",
        nonce=f"nonce-eval-{task_index:02d}-capability",
        category=EvidenceCategory.DEMONSTRATED_CAPABILITY,
        agent_id=operator["agent_id"],
        experiment_id=sidecar["experiment_id"],
        trace_id=sidecar["trace_id"],
        task_id=sidecar["task_id"],
        subject=capability,
        predicate="demonstrated",
        asserted_value=True,
        scope=f"evaluator:task:{sidecar['task_id']}",
        created_at=created_at,
        expires_at=created_at + _EVIDENCE_TTL_SECONDS,
        source_ref="controlled_runtime:observed_local_invocation",
    )
    for record in (attestation, demonstration_attestation):
        verification = evidence_verifier.verify(
            record,
            now=record.created_at + 1,
            expected_agent_id=record.agent_id,
            expected_experiment_id=record.experiment_id,
            expected_trace_id=record.trace_id,
            expected_task_id=record.task_id,
        )
        if not verification.trusted:
            raise ValueError(
                f"Evaluator attestation {record.evidence_id} failed verification."
            )
    return {
        "evaluator_issuer": evaluator_issuer.issuer_id,
        "trusted_issuer": evaluator_issuer.trusted_issuer().to_public_dict(),
        "completion_attestation": attestation.to_dict(),
        "demonstrated_capability_attestation": demonstration_attestation.to_dict(),
        "seed_used_for_reproducibility": seed,
    }


def run_claim_bearing_experiment(
    *,
    seed: int = 0,
    scenarios: Iterable[ScenarioName | str] | None = None,
    behaviours: Iterable[AgentBehaviour | str] | None = None,
    agents_per_task: int = 5,
    selected_operator_index: int = 2,
    stable_agent_namespace: str | None = None,
    run_label: str | None = None,
) -> ExperimentRun:
    """Run deterministic, local-only tasks and keep evaluator data separate."""
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("seed must be a non-negative integer.")
    selected_scenarios = _scenario_selection(scenarios)
    selected_behaviours = _agent_behaviours(behaviours, agents_per_task)
    if (
        not isinstance(selected_operator_index, int)
        or isinstance(selected_operator_index, bool)
        or not 0 <= selected_operator_index < agents_per_task
    ):
        raise ValueError("selected_operator_index must identify a participating agent.")
    if stable_agent_namespace is not None:
        if not isinstance(stable_agent_namespace, str) or not stable_agent_namespace.strip():
            raise ValueError("stable_agent_namespace must be non-empty when supplied.")
        if len(selected_scenarios) != 1:
            raise ValueError(
                "stable_agent_namespace currently supports one task per experiment "
                "because identity registration is task-scoped."
            )
    if run_label is not None and (
        not isinstance(run_label, str) or not run_label.strip()
    ):
        raise ValueError("run_label must be non-empty when supplied.")
    config = {
        "seed": seed,
        "scenarios": [scenario.value for scenario in selected_scenarios],
        "behaviours": [behaviour.value for behaviour in selected_behaviours],
        "agents_per_task": agents_per_task,
        "version": "stage7c-v1",
    }
    if selected_operator_index != 2 or stable_agent_namespace is not None:
        config["stage9_routing"] = {
            "selected_operator_index": selected_operator_index,
            "stable_agent_namespace": stable_agent_namespace,
            "run_label": run_label,
        }
    elif run_label is not None:
        config["run_label"] = run_label
    experiment_id = f"experiment-{hashlib.sha256(_canonical_json(config).encode()).hexdigest()[:16]}"
    identity_issuer = _issuer(
        "identity-registry",
        AuthorityKind.IDENTITY_REGISTRY,
        (EvidenceCategory.IDENTITY, EvidenceCategory.ROLE),
        seed,
        experiment_id,
    )
    runtime_issuer = _issuer(
        "runtime-authority",
        AuthorityKind.RUNTIME_EXECUTION,
        (EvidenceCategory.CONFIGURED_PERMISSION, EvidenceCategory.EVIDENCE_SOURCE),
        seed,
        experiment_id,
    )
    tool_issuer = _issuer(
        "tool-gateway",
        AuthorityKind.TOOL_EXECUTION,
        (EvidenceCategory.TOOL_EXECUTION, EvidenceCategory.EVIDENCE_SOURCE),
        seed,
        experiment_id,
    )
    evaluator_issuer = _issuer(
        "experiment-evaluator",
        AuthorityKind.EXPERIMENT_EVALUATOR,
        (
            EvidenceCategory.DEMONSTRATED_CAPABILITY,
            EvidenceCategory.TASK_COMPLETION,
        ),
        seed,
        experiment_id,
    )
    identity_registry = AgentIdentityRegistry(identity_issuer)
    policy = TrustPolicy(
        (
            identity_issuer.trusted_issuer(),
            runtime_issuer.trusted_issuer(),
            tool_issuer.trusted_issuer(),
        )
    )
    verifier = RuntimeEvidenceVerifier(policy, identities=identity_registry)
    evaluator_verifier = RuntimeEvidenceVerifier(
        TrustPolicy((evaluator_issuer.trusted_issuer(),))
    )

    traces: list[InteractionTrace] = []
    messages: list[ClaimBearingMessage] = []
    evidence_records: list[RuntimeEvidence] = []
    evaluator_sidecar: list[dict[str, Any]] = []
    for task_index, scenario in enumerate(selected_scenarios):
        trace, task_messages, task_evidence, task_sidecar = _build_task(
            experiment_id=experiment_id,
            task_index=task_index,
            scenario_name=scenario,
            seed=seed,
            agents_per_task=agents_per_task,
            behaviours=selected_behaviours,
            selected_operator_index=selected_operator_index,
            stable_agent_namespace=stable_agent_namespace,
            identity_issuer=identity_issuer,
            runtime_issuer=runtime_issuer,
            tool_issuer=tool_issuer,
            identity_registry=identity_registry,
            evidence_verifier=verifier,
        )
        attestations = _evaluator_attestations(
            task_sidecar,
            task_index=task_index,
            evaluator_issuer=evaluator_issuer,
            seed=seed,
            evidence_verifier=evaluator_verifier,
        )
        task_sidecar["attestations"] = attestations
        traces.append(trace)
        messages.extend(task_messages)
        evidence_records.extend(task_evidence)
        evaluator_sidecar.append(task_sidecar)

    evidence_ids = [record.evidence_id for record in evidence_records]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("Evidence IDs must be unique across the experiment.")
    assertion_ids = [
        assertion.assertion_id
        for message in messages
        for assertion in message.assertions
    ]
    if len(assertion_ids) != len(set(assertion_ids)):
        raise ValueError("Assertion IDs must be unique across the experiment.")
    return ExperimentRun(
        experiment_id=experiment_id,
        seed=seed,
        scenarios=selected_scenarios,
        agents_per_task=agents_per_task,
        traces=tuple(traces),
        messages=tuple(messages),
        evidence=tuple(evidence_records),
        evaluator_sidecar=tuple(evaluator_sidecar),
        trust_policy=policy,
    )


def trace_jsonl(run: ExperimentRun) -> str:
    return "".join(trace_to_json(trace) + "\n" for trace in run.traces)


def message_jsonl(run: ExperimentRun) -> str:
    from dataset.claim_message_schemas import claim_message_to_json

    return "".join(claim_message_to_json(message) + "\n" for message in run.messages)


def claim_jsonl(run: ExperimentRun) -> str:
    rows = []
    for message in run.messages:
        for assertion in message.assertions:
            rows.append(
                {
                    "experiment_id": message.experiment_id,
                    "trace_id": message.trace_id,
                    "task_id": message.task_id,
                    "message_id": message.message_id,
                    "assertion_id": assertion.assertion_id,
                    "provenance_claim_id": assertion.provenance_claim_id,
                    "agent_id": assertion.agent_id,
                    "claim_type": assertion.claim_type.value,
                    "subject": assertion.subject,
                    "predicate": assertion.predicate,
                    "asserted_value": assertion.asserted_value,
                    "scope": assertion.scope,
                    "evidence_refs": list(assertion.evidence_refs),
                    "capability_state": (
                        assertion.capability_state.value
                        if assertion.capability_state is not None
                        else None
                    ),
                }
            )
    return "".join(_canonical_json(row) + "\n" for row in rows)


def evidence_jsonl(run: ExperimentRun) -> str:
    return "".join(record.to_json() + "\n" for record in run.evidence)


def evaluator_jsonl(run: ExperimentRun) -> str:
    return "".join(_canonical_json(row) + "\n" for row in run.evaluator_sidecar)


def trust_policy_json(run: ExperimentRun) -> str:
    return _canonical_json(run.trust_policy.to_public_dict()) + "\n"
