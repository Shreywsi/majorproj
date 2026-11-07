"""Auditable scheduling preview for TAMAS records; no model/tool execution."""

from dataclasses import dataclass
from enum import StrEnum

from dataset.tamas_loader import TAMASRecord


class InteractionMode(StrEnum):
    MAGENTIC_ONE = "magentic_one"
    ROUND_ROBIN = "round_robin"
    SWARM = "swarm"


@dataclass(frozen=True)
class PreviewEvent:
    step: int
    kind: str
    agent_id: str | None
    details: str
    execution_status: str = "scheduled_not_executed"


@dataclass(frozen=True)
class WorkflowPreview:
    record_id: str
    source_file: str
    interaction_mode: InteractionMode
    original_user_query: str
    events: tuple[PreviewEvent, ...]
    recorded_tool_calls: tuple[str, ...] | None
    notice: str = (
        "Offline scheduling preview only: no LLM responses were generated and "
        "no tools or external actions were executed."
    )


def preview_workflow(
    record: TAMASRecord,
    mode: InteractionMode | str = InteractionMode.ROUND_ROBIN,
) -> WorkflowPreview:
    """Create an explicit orchestration schedule from one original TAMAS record.

    The dataset does not contain model-generated interaction traces for these
    examples. This function therefore records only task receipt, roster
    membership, and scheduling decisions. It never uses ground-truth labels to
    route and never executes embedded or external tools.
    """
    mode = InteractionMode(mode)
    events = [
        PreviewEvent(0, "task_received", None, record.original_user_query)
    ]
    agents = record.agents
    events.append(PreviewEvent(
        1, "agent_roster_loaded", None,
        f"Loaded {len(agents)} original agent role(s) from {record.source_file}.",
    ))

    if mode == InteractionMode.MAGENTIC_ONE:
        coordinator = agents[0]
        events.append(PreviewEvent(
            2, "coordinator_assigned", coordinator.agent_id,
            f"Deterministic preview coordinator: {coordinator.name}. "
            "The dataset does not identify a coordinator; first-listed role is used.",
        ))
        for step, agent in enumerate(agents[1:], start=3):
            events.append(PreviewEvent(
                step, "delegation_scheduled", agent.agent_id,
                f"Coordinator schedule includes original role {agent.name}; no response generated.",
            ))
    elif mode == InteractionMode.ROUND_ROBIN:
        for step, agent in enumerate(agents, start=2):
            events.append(PreviewEvent(
                step, "turn_scheduled", agent.agent_id,
                f"Round-robin schedule includes original role {agent.name}; no response generated.",
            ))
    else:
        for step, agent in enumerate(agents, start=2):
            events.append(PreviewEvent(
                step, "fanout_scheduled", agent.agent_id,
                f"Swarm preview would fan out to original role {agent.name}; not executed.",
            ))

    if record.sequence_of_tool_calls is not None:
        events.append(PreviewEvent(
            len(events), "dataset_tool_trace_available", None,
            f"Original dataset contains {len(record.sequence_of_tool_calls)} recorded tool-call "
            "string(s); preserved as annotations, not executed.",
        ))

    return WorkflowPreview(
        record_id=record.record_id,
        source_file=record.source_file,
        interaction_mode=mode,
        original_user_query=record.original_user_query,
        events=tuple(events),
        recorded_tool_calls=record.sequence_of_tool_calls,
    )
