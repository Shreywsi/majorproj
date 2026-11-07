"""Deterministic, side-effect-free execution over TAMAS Finance task projections."""

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from dataset.experiment_schemas import (
    DetectorTaskInput,
    EventExecutionStatus,
    EventSource,
    InteractionTrace,
    TraceEvent,
    TraceEventType,
)
from dataset.safe_tools import SafeFinanceToolRegistry
from dataset.tool_catalog import TAMASToolDefinition
from dataset.workflow_preview import InteractionMode


_ROLE_RULES = {
    "identity": (
        ("identity verification agent", "identity", "authenticate", "authentication", "otp", "kyc"),
        ("verify_otp", "verify_biometric", "verify_kyc_documents"),
    ),
    "market": (
        ("market analysis agent", "market", "sentiment", "forecast", "stock trend", "buy signal"),
        ("analyze_market_sentiment", "forecast_price_movement", "analyze_stock_trends"),
    ),
    "trade": (
        ("trade execution agent", "order", "execute", "place order", "buy order", "sell order"),
        ("place_order", "execute_limit_order", "check_order_status"),
    ),
    "risk": (
        ("risk management agent", "risk", "exposure", "stop-loss", "stop loss", "margin"),
        ("log_risk_event", "adjust_stop_loss", "evaluate_risk_exposure"),
    ),
}


def _role_family(agent_name: str) -> str | None:
    normalized = agent_name.casefold()
    if "identity verification agent" in normalized:
        return "identity"
    if "market analysis agent" in normalized:
        return "market"
    if "trade execution agent" in normalized:
        return "trade"
    if "risk management agent" in normalized:
        return "risk"
    return None


def _required_agents(task: DetectorTaskInput) -> tuple[int, ...]:
    query = task.user_query.casefold()
    trade_action = any(
        phrase in query for phrase in (
            "place a buy order", "place a sell order", "place buy order", "place sell order",
            "execute a buy", "execute a sell", "execute buy", "execute sell",
            "execute a buy order", "execute a sell order", "execute buy order",
            "execute sell order", "buy order", "sell order", "place an order",
            "place order", "execute an order", "execute order",
        )
    )
    selected = []
    for index, agent in enumerate(task.agents):
        family = _role_family(agent.claimed_role)
        if family is None:
            continue
        if family == "trade":
            if trade_action:
                selected.append(index)
            continue
        keywords, _ = _ROLE_RULES[family]
        if any(keyword in query for keyword in keywords):
            selected.append(index)
    return tuple(selected)


def _choose_tool(
    agent_name: str,
    query: str,
    registry: SafeFinanceToolRegistry,
) -> TAMASToolDefinition | None:
    family = _role_family(agent_name)
    if family is None:
        return None

    text = query.casefold()
    if family == "identity":
        preference = (
            ("verify_otp", "verify_biometric", "verify_kyc_documents")
            if "otp" in text else
            ("verify_kyc_documents", "verify_biometric", "verify_otp")
        )
    elif family == "market":
        preference = (
            ("analyze_market_sentiment", "forecast_price_movement", "analyze_stock_trends")
            if "sentiment" in text else
            ("forecast_price_movement", "analyze_stock_trends", "analyze_market_sentiment")
        )
    elif family == "trade":
        if not any(
            phrase in text for phrase in (
                "place a buy order", "place a sell order", "place buy order", "place sell order",
                "execute a buy", "execute a sell", "execute buy", "execute sell",
                "buy order", "sell order", "place an order", "place order",
                "execute an order", "execute order",
            )
        ):
            return None
        preference = (
            ("place_order", "execute_limit_order", "check_order_status")
            if any(word in text for word in ("buy", "sell", "order")) else
            ("check_order_status", "place_order", "execute_limit_order")
        )
    else:
        preference = (
            ("log_risk_event", "adjust_stop_loss", "evaluate_risk_exposure")
            if "log_risk_event" in text or "log all transaction" in text else
            ("adjust_stop_loss", "evaluate_risk_exposure", "log_risk_event")
            if "stop-loss" in text or "stop loss" in text else
            ("evaluate_risk_exposure", "adjust_stop_loss", "log_risk_event")
        )

    available = registry.for_role({
        "identity": "Identity Verification Agent",
        "market": "Market Analysis Agent",
        "trade": "Trade Execution Agent",
        "risk": "Risk Management Agent",
    }[family])
    by_name = {}
    for definition in available:
        by_name.setdefault(definition.name, definition)
    return next((by_name[name] for name in preference if name in by_name), None)


@dataclass(frozen=True)
class ExecutionSummary:
    trace: InteractionTrace
    scheduled_agent_ids: tuple[str, ...]
    simulated_tool_calls: int
    llm_responses_generated: int = 0
    external_actions_executed: int = 0
    task_completion_status: str = "not_evaluated"


class SafeTAMASExecutionEngine:
    """Run a deterministic orchestration simulation without model/API calls.

    The role planner uses only the detector-safe projection. Every catalogued
    function is backed by an inert local stub. Original tool modules are never
    imported, and no completion/result is inferred from a stub response.
    """

    def __init__(
        self,
        tool_definitions: tuple[TAMASToolDefinition, ...],
        mode: InteractionMode | str = InteractionMode.ROUND_ROBIN,
    ):
        self.mode = InteractionMode(mode)
        self.tools = SafeFinanceToolRegistry(tool_definitions)
        registry_description = [
            (
                definition.source_file,
                definition.name,
                definition.source_line,
                definition.agent_role,
                definition.parameters,
            )
            for definition in self.tools.definitions
        ]
        self.tool_registry_hash = hashlib.sha256(
            json.dumps(registry_description, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _trace_id(task_id: str, mode: InteractionMode, seed: int) -> str:
        token = hashlib.sha256(f"{task_id}|{mode.value}|{seed}".encode()).hexdigest()[:20]
        return f"trace-{token}"

    def run(self, task: DetectorTaskInput, seed: int = 0) -> ExecutionSummary:
        if not isinstance(task, DetectorTaskInput):
            raise TypeError("Execution engine requires a detector-safe DetectorTaskInput.")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValueError("seed must be a non-negative integer.")

        selected_indices = _required_agents(task)
        events = []
        logical_clock_ms = 0

        def append(
            event_type: TraceEventType,
            source: EventSource,
            status: EventExecutionStatus = EventExecutionStatus.RECORDED,
            **fields: Any,
        ) -> None:
            nonlocal logical_clock_ms
            events.append(TraceEvent(
                sequence=len(events),
                event_type=event_type,
                source=source,
                execution_status=status,
                elapsed_ms=logical_clock_ms,
                **fields,
            ))
            logical_clock_ms += 1

        append(
            TraceEventType.TASK_SUBMITTED,
            EventSource.USER,
            content=task.user_query,
        )

        tool_calls = 0
        previous_agent_index = None
        for selected_position, agent_index in enumerate(selected_indices):
            agent = task.agents[agent_index]
            if selected_position > 0 and self.mode != InteractionMode.SWARM:
                handoff_from = (
                    selected_indices[0]
                    if self.mode == InteractionMode.MAGENTIC_ONE
                    else previous_agent_index
                )
                previous = task.agents[handoff_from]
                append(
                    TraceEventType.HANDOFF,
                    EventSource.AGENT,
                    actor_id=previous.agent_id,
                    recipient_id=agent.agent_id,
                    content=(
                        "Deterministic orchestration handoff; no LLM message or "
                        "agent-generated rationale was produced."
                    ),
                )
            append(
                TraceEventType.ROUTER_DECISION,
                EventSource.ROUTER,
                recipient_id=agent.agent_id,
                content=(
                    f"Scheduled original role '{agent.claimed_role}' from the task query "
                    "using deterministic role-keyword rules; this is not a quality or "
                    "capability verification."
                ),
            )

            definition = _choose_tool(agent.claimed_role, task.user_query, self.tools)
            if definition is not None:
                arguments = {"task_context": task.user_query}
                append(
                    TraceEventType.TOOL_INVOCATION,
                    EventSource.AGENT,
                    EventExecutionStatus.LOCAL_SIMULATION,
                    actor_id=agent.agent_id,
                    tool_name=definition.name,
                    tool_arguments=arguments,
                    content=(
                        "Deterministic simulated action requested by the role planner; "
                        "not an LLM-generated tool call."
                    ),
                )
                response = self.tools.invoke(definition, arguments)
                append(
                    TraceEventType.TOOL_RESULT,
                    EventSource.TOOL,
                    EventExecutionStatus.SIMULATED_RESPONSE,
                    actor_id=f"local-stub:{definition.agent_role}",
                    tool_name=definition.name,
                    tool_result={
                        "status": response.status,
                        "message": response.message,
                        "task_completion": response.task_completion,
                    },
                    content=response.message,
                )
                tool_calls += 1

            previous_agent_index = agent_index

        if not selected_indices:
            append(
                TraceEventType.ROUTER_DECISION,
                EventSource.ROUTER,
                content=(
                    "No original agent role matched the deterministic task-keyword rules; "
                    "no agent or tool was scheduled."
                ),
            )
        append(
            TraceEventType.SIMULATION_ENDED,
            EventSource.SYSTEM,
            EventExecutionStatus.NOT_EXECUTED,
            content=(
                "Scheduling simulation ended. No LLM response was generated and task "
                "completion was not evaluated."
            ),
        )

        trace = InteractionTrace(
            trace_id=self._trace_id(task.task_id, self.mode, seed),
            task_id=task.task_id,
            interaction_mode=self.mode,
            events=tuple(events),
            seed=seed,
            tool_registry_version="tamas-finance-ast-catalog-v1",
            tool_registry_hash=self.tool_registry_hash,
            config_hash=hashlib.sha256(
                f"safe-role-keyword-simulation-v1|{self.mode.value}".encode()
            ).hexdigest(),
        )
        return ExecutionSummary(
            trace=trace,
            scheduled_agent_ids=tuple(task.agents[index].agent_id for index in selected_indices),
            simulated_tool_calls=tool_calls,
        )
