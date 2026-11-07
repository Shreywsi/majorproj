"""JSONL serialization for validated interaction traces."""

from dataclasses import asdict
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, TextIO

from dataset.experiment_schemas import (
    EventExecutionStatus,
    EventSource,
    InteractionTrace,
    TraceEvent,
    TraceEventType,
    TraceExecutionMode,
)
from dataset.workflow_preview import InteractionMode


def _json_default(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(f"Value of type {type(value).__name__} is not JSON serializable.")


def trace_to_json(trace: InteractionTrace) -> str:
    """Serialize a schema-validated trace to one canonical JSON line."""
    return json.dumps(
        asdict(trace),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
        allow_nan=False,
    )


def write_trace_jsonl(trace: InteractionTrace, destination: str | Path | TextIO) -> None:
    """Append one complete trace; callers may pass a path or open text stream."""
    line = trace_to_json(trace)
    if hasattr(destination, "write"):
        destination.write(line + "\n")
        destination.flush()
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(line + "\n")


def trace_from_dict(data: dict[str, Any]) -> InteractionTrace:
    """Reconstruct and revalidate a serialized trace."""
    if not isinstance(data, dict):
        raise ValueError("Trace JSON line must contain an object.")
    if not isinstance(data.get("events"), list):
        raise ValueError("Trace JSON must contain an events array.")
    events = tuple(
        TraceEvent(
            **{
                **event,
                "event_type": TraceEventType(event["event_type"]),
                "source": EventSource(event["source"]),
                "execution_status": EventExecutionStatus(
                    event.get("execution_status", EventExecutionStatus.RECORDED.value)
                ),
            }
        )
        for event in data["events"]
    )
    return InteractionTrace(
        **{
            **{key: value for key, value in data.items() if key != "events"},
            "interaction_mode": InteractionMode(data["interaction_mode"]),
            "execution_mode": TraceExecutionMode(data.get(
                "execution_mode", TraceExecutionMode.CONTROLLED_LOCAL_SIMULATION.value
            )),
            "events": events,
        }
    )


def read_traces_jsonl(source: str | Path | TextIO) -> tuple[InteractionTrace, ...]:
    """Read JSONL traces and report the exact malformed line."""
    if hasattr(source, "read"):
        lines = source
        return _read_lines(lines)
    path = Path(source)
    with path.open("r", encoding="utf-8") as input_file:
        return _read_lines(input_file)


def _read_lines(lines: TextIO) -> tuple[InteractionTrace, ...]:
    traces = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            traces.append(trace_from_dict(payload))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid trace JSONL at line {line_number}: {exc}") from exc
    return tuple(traces)
