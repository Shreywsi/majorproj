import json

import pytest

from dataset.download_tamas import DATASET_ROOT
from dataset.execution_engine import SafeTAMASExecutionEngine
from dataset.experiment_schemas import (
    DetectorTaskInput,
    EventExecutionStatus,
    TraceEventType,
)
from dataset.safe_tools import SafeFinanceToolRegistry
from dataset.tamas_loader import TAMASAttack, load_tamas_dataset
from dataset.trace_store import (
    read_traces_jsonl,
    trace_to_json,
    write_trace_jsonl,
)
from dataset.workflow_preview import InteractionMode
from run_tamas_execution import run_validation_pair


@pytest.fixture(scope="module")
def dataset():
    return load_tamas_dataset(DATASET_ROOT)


def _task(record, task_id):
    return DetectorTaskInput.from_tamas_record(record, task_id)


def test_safe_registry_contains_and_inerts_all_33_official_definitions(dataset):
    registry = SafeFinanceToolRegistry(dataset.tool_definitions)

    assert len(registry.definitions) == 33
    for definition in registry.definitions:
        response = registry.invoke(definition, {"task_context": "controlled test"})
        assert response.tool_name == definition.name
        assert response.status == "simulated_response"
        assert response.task_completion == "not_evaluated"
        assert "no upstream code" in response.message


def test_engine_runs_actual_benign_and_dpi_records_without_claiming_success(dataset, tmp_path):
    benign = next(record for record in dataset.records if record.is_benign)
    dpi = next(
        record for record in dataset.records
        if record.attack_type == TAMASAttack.DIRECT_PROMPT_INJECTION
    )
    engine = SafeTAMASExecutionEngine(dataset.tool_definitions, InteractionMode.ROUND_ROBIN)
    summaries = (
        engine.run(_task(benign, "opaque-benign"), seed=19),
        engine.run(_task(dpi, "opaque-adversarial"), seed=19),
    )
    path = tmp_path / "traces.jsonl"
    for summary in summaries:
        write_trace_jsonl(summary.trace, path)

    traces = read_traces_jsonl(path)
    assert len(traces) == 2
    assert len(summaries[0].scheduled_agent_ids) >= 2
    assert len(summaries[1].scheduled_agent_ids) >= 2
    assert all(summary.llm_responses_generated == 0 for summary in summaries)
    assert all(summary.external_actions_executed == 0 for summary in summaries)
    assert all(summary.task_completion_status == "not_evaluated" for summary in summaries)
    for trace in traces:
        assert trace.seed == 19
        assert trace.interaction_mode == InteractionMode.ROUND_ROBIN
        assert trace.model_id is None
        assert trace.tool_registry_version == "tamas-finance-ast-catalog-v1"
        assert trace.tool_registry_hash
        assert trace.config_hash
        assert trace.events[0].event_type == TraceEventType.TASK_SUBMITTED
        assert trace.events[-1].event_type == TraceEventType.SIMULATION_ENDED
        assert not any(event.event_type == TraceEventType.TASK_COMPLETED for event in trace.events)
        assert not any(event.event_type == TraceEventType.AGENT_MESSAGE for event in trace.events)
        assert all(
            trace.events[index].elapsed_ms < trace.events[index + 1].elapsed_ms
            for index in range(len(trace.events) - 1)
        )
        assert all("RAS_finance" not in event.content if event.content else True
                   for event in trace.events)
        for event in trace.events:
            if event.event_type == TraceEventType.TOOL_INVOCATION:
                assert event.execution_status == EventExecutionStatus.LOCAL_SIMULATION
            if event.event_type == TraceEventType.TOOL_RESULT:
                assert event.execution_status == EventExecutionStatus.SIMULATED_RESPONSE
                assert event.tool_result["task_completion"] == "not_evaluated"
                assert "no upstream code" in event.tool_result["message"]
        serialized = trace_to_json(trace)
        assert "ground_truth_label" not in serialized
        assert "agents_involved" not in serialized
        assert "expected_output" not in serialized
        assert "sequence_of_tool_calls" not in serialized
        assert "source_file" not in serialized
        assert trace.execution_mode.value == "controlled_local_simulation"


def test_trace_output_is_deterministic_for_task_mode_and_seed(dataset):
    record = next(record for record in dataset.records if record.is_benign)
    task = _task(record, "opaque-deterministic-task")
    engine = SafeTAMASExecutionEngine(dataset.tool_definitions, InteractionMode.SWARM)

    first = engine.run(task, seed=7).trace
    second = engine.run(task, seed=7).trace
    other_seed = engine.run(task, seed=8).trace

    assert trace_to_json(first) == trace_to_json(second)
    assert first.trace_id != other_seed.trace_id
    assert [event.event_type for event in first.events] == [
        event.event_type for event in other_seed.events
    ]


def test_market_buy_signal_does_not_schedule_a_trade(dataset):
    record = next(
        record for record in dataset.records
        if record.attack_type == TAMASAttack.COLLUDING_AGENTS
    )
    task = _task(record, "opaque-collusion-task")
    summary = SafeTAMASExecutionEngine(dataset.tool_definitions).run(task, seed=0)

    selected_roles = {
        task.agents[int(agent_id.removeprefix("agent-"))].claimed_role
        for agent_id in summary.scheduled_agent_ids
    }
    assert any("Market Analysis Agent" in role for role in selected_roles)
    assert not any("Trade Execution Agent" in role for role in selected_roles)
    assert all(
        event.tool_name != "place_order"
        for event in summary.trace.events
        if event.event_type == TraceEventType.TOOL_INVOCATION
    )


@pytest.mark.parametrize(
    ("mode", "expected_handoffs"),
    [
        (InteractionMode.MAGENTIC_ONE, 3),
        (InteractionMode.ROUND_ROBIN, 3),
        (InteractionMode.SWARM, 0),
    ],
)
def test_interaction_patterns_encode_distinct_handoff_schedules(
    dataset, mode, expected_handoffs
):
    record = next(record for record in dataset.records if record.is_benign)
    trace = SafeTAMASExecutionEngine(dataset.tool_definitions, mode).run(
        _task(record, f"opaque-{mode.value}"), seed=0
    ).trace
    handoffs = [event for event in trace.events if event.event_type == TraceEventType.HANDOFF]
    assert len(handoffs) == expected_handoffs
    if mode == InteractionMode.MAGENTIC_ONE:
        assert {event.actor_id for event in handoffs} == {"agent-000"}
    if mode == InteractionMode.ROUND_ROBIN:
        assert [event.actor_id for event in handoffs] == [
            "agent-000", "agent-001", "agent-002"
        ]


def test_jsonl_round_trip_and_malformed_trace_are_reported(dataset, tmp_path):
    record = next(record for record in dataset.records if record.is_benign)
    trace = SafeTAMASExecutionEngine(dataset.tool_definitions).run(
        _task(record, "opaque-roundtrip"), seed=3
    ).trace
    path = tmp_path / "roundtrip.jsonl"
    write_trace_jsonl(trace, path)

    loaded = read_traces_jsonl(path)
    assert trace_to_json(loaded[0]) == trace_to_json(trace)

    path.write_text('{"events": [{}]}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        read_traces_jsonl(path)


def test_validation_pair_writes_evaluator_mapping_separately(dataset, tmp_path):
    trace_path = tmp_path / "validation.jsonl"
    summaries = run_validation_pair(
        DATASET_ROOT,
        trace_path,
        TAMASAttack.DIRECT_PROMPT_INJECTION,
        InteractionMode.ROUND_ROBIN,
        seed=11,
    )
    trace_text = trace_path.read_text(encoding="utf-8")
    mapping_path = trace_path.with_suffix(".evaluator.jsonl")
    mappings = [json.loads(line) for line in mapping_path.read_text().splitlines()]

    assert len(summaries) == len(mappings) == 2
    assert {row["tamas_record_label"] for row in mappings} == {
        "benign", "direct_prompt_injection"
    }
    assert "tamas_record_label" not in trace_text
    assert "source_file" not in trace_text
    assert [row["trace_id"] for row in mappings] == [
        summary["trace_id"] for summary in summaries
    ]


def test_tool_simulation_rejects_unregistered_definitions_and_non_json_args(dataset):
    registry = SafeFinanceToolRegistry(dataset.tool_definitions)
    definition = dataset.tool_definitions[0]
    with pytest.raises(ValueError, match="not registered"):
        registry.invoke(
            type(definition)(**{**definition.__dict__, "source_line": 9999}),
            {},
        )
    with pytest.raises(ValueError, match="finite JSON"):
        registry.invoke(definition, {"value": float("nan")})
