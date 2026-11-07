"""Schema-aware loader for the ICML 2025 TAMAS GitHub sample release.

The official checked-in sample contains Finance examples only. Attack labels
are assigned by the published filename, not by an in-record label field.
"""

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Any

from dataset.tool_catalog import TAMASToolDefinition, load_finance_tool_definitions


class TAMASAttack(StrEnum):
    BENIGN = "benign"
    DIRECT_PROMPT_INJECTION = "direct_prompt_injection"
    INDIRECT_PROMPT_INJECTION = "indirect_prompt_injection"
    IMPERSONATION = "impersonation"
    COLLUDING_AGENTS = "colluding_agents"
    CONTRADICTING_AGENTS = "contradicting_agents"


OFFICIAL_SOURCE_LABELS = {
    "PNA_finance.json": TAMASAttack.BENIGN,
    "RAS_finance_DPI.json": TAMASAttack.DIRECT_PROMPT_INJECTION,
    "RAS_finance_IPI.json": TAMASAttack.INDIRECT_PROMPT_INJECTION,
    "RAS_finance_Imp.json": TAMASAttack.IMPERSONATION,
    "RAS_finance_Coop.json": TAMASAttack.COLLUDING_AGENTS,
    "RAS_finance_contra.json": TAMASAttack.CONTRADICTING_AGENTS,
}

EXPECTED_FINANCE_RELEASE_FILES = tuple(OFFICIAL_SOURCE_LABELS)
OFFICIAL_REPOSITORY = "https://github.com/ameyar3103/MultiAgentHarmDataset"
OFFICIAL_COMMIT = "27fcf288e8c8c42a2c32c993d94623877a286016"


class TAMASSchemaError(ValueError):
    """Raised when an input file does not match the published TAMAS schema."""


@dataclass(frozen=True)
class TAMASAgent:
    agent_id: str
    name: str
    description: str
    backstory: str | None
    tools: tuple[Any, ...] | None
    original_fields: dict[str, Any]


@dataclass(frozen=True)
class TAMASRecord:
    record_id: str
    scenario: str
    original_user_query: str
    agents: tuple[TAMASAgent, ...]
    attack_type: TAMASAttack
    ground_truth_label: str
    source_file: str
    agents_involved: tuple[str, ...] | None
    expected_output: str | None
    sequence_of_tool_calls: tuple[str, ...] | None
    original_fields: dict[str, Any]

    @property
    def is_benign(self) -> bool:
        return self.attack_type == TAMASAttack.BENIGN

    @property
    def is_adversarial(self) -> bool:
        return not self.is_benign


@dataclass(frozen=True)
class TAMASDataset:
    records: tuple[TAMASRecord, ...]
    source_files: tuple[str, ...]
    missing_release_files: tuple[str, ...]
    tool_definitions: tuple[TAMASToolDefinition, ...] = ()
    missing_tool_modules: tuple[str, ...] = ()
    repository: str = OFFICIAL_REPOSITORY
    commit: str = OFFICIAL_COMMIT
    scenario_scope: tuple[str, ...] = ("finance",)

    @property
    def is_complete_icml_benchmark(self) -> bool:
        return False


def _record_from_json(source_file: str, index: int, raw: Any) -> TAMASRecord:
    location = f"{source_file}, record {index}"
    if not isinstance(raw, dict):
        raise TAMASSchemaError(f"{location}: expected an object.")

    query = raw.get("user_query", raw.get("user query"))
    if not isinstance(query, str) or not query.strip():
        raise TAMASSchemaError(
            f"{location}: expected a non-empty 'user_query' or 'user query' string."
        )
    raw_agents = raw.get("agents")
    if not isinstance(raw_agents, list) or not raw_agents:
        raise TAMASSchemaError(f"{location}: expected a non-empty 'agents' list.")

    agents = []
    for agent_index, agent in enumerate(raw_agents):
        agent_location = f"{location}, agent {agent_index}"
        if not isinstance(agent, dict):
            raise TAMASSchemaError(f"{agent_location}: expected an object.")
        name, description = agent.get("agent_name"), agent.get("agent_description")
        if not isinstance(name, str) or not name.strip():
            raise TAMASSchemaError(f"{agent_location}: missing non-empty 'agent_name'.")
        if not isinstance(description, str) or not description.strip():
            raise TAMASSchemaError(
                f"{agent_location}: missing non-empty 'agent_description'."
            )
        tools = agent.get("tools")
        if tools is not None and not isinstance(tools, list):
            raise TAMASSchemaError(f"{agent_location}: 'tools' must be a list when present.")
        agents.append(TAMASAgent(
            agent_id=f"{source_file}:{index:04d}:agent:{agent_index}",
            name=name,
            description=description,
            backstory=agent.get("backstory"),
            tools=tuple(tools) if tools is not None else None,
            original_fields=dict(agent),
        ))

    attack_type = OFFICIAL_SOURCE_LABELS[source_file]
    involved = raw.get("agents_involved")
    if involved is not None and (
        not isinstance(involved, list) or any(not isinstance(item, str) for item in involved)
    ):
        raise TAMASSchemaError(f"{location}: 'agents_involved' must be a list of strings.")
    tool_calls = raw.get("sequence_of_tool_calls")
    if tool_calls is not None and (
        not isinstance(tool_calls, list) or any(not isinstance(item, str) for item in tool_calls)
    ):
        raise TAMASSchemaError(
            f"{location}: 'sequence_of_tool_calls' must be a list of strings."
        )
    expected = raw.get("expected output")
    if expected is not None and not isinstance(expected, str):
        raise TAMASSchemaError(f"{location}: 'expected output' must be a string when present.")

    return TAMASRecord(
        record_id=f"{source_file}:{index:04d}",
        scenario="finance",
        original_user_query=query,
        agents=tuple(agents),
        attack_type=attack_type,
        ground_truth_label=attack_type.value,
        source_file=source_file,
        agents_involved=tuple(involved) if involved is not None else None,
        expected_output=expected,
        sequence_of_tool_calls=tuple(tool_calls) if tool_calls is not None else None,
        original_fields=dict(raw),
    )


def load_tamas_dataset(directory: str | Path) -> TAMASDataset:
    """Load every recognized official Finance JSON file present in a directory.

    Missing files are exposed on the returned dataset instead of being silently
    replaced or treated as an empty class. Unknown JSON files are ignored.
    """
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"TAMAS data directory does not exist: {root}")

    source_files = tuple(
        filename for filename in EXPECTED_FINANCE_RELEASE_FILES
        if (root / filename).is_file()
    )
    if not source_files:
        raise FileNotFoundError(
            f"No recognized ICML 2025 Finance TAMAS JSON files found in {root}. "
            "Run `python -m dataset.download_tamas` to fetch the published sample."
        )

    records = []
    for filename in source_files:
        try:
            data = json.loads((root / filename).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TAMASSchemaError(f"Unable to read valid UTF-8 JSON from {filename}: {exc}") from exc
        if not isinstance(data, list):
            raise TAMASSchemaError(f"{filename}: expected a top-level JSON array.")
        for index, raw in enumerate(data):
            records.append(_record_from_json(filename, index, raw))

    tool_definitions, missing_tool_modules = load_finance_tool_definitions(root)
    return TAMASDataset(
        records=tuple(records),
        source_files=source_files,
        missing_release_files=tuple(
            filename for filename in EXPECTED_FINANCE_RELEASE_FILES
            if filename not in source_files
        ),
        tool_definitions=tool_definitions,
        missing_tool_modules=missing_tool_modules,
    )


def dataset_statistics(dataset: TAMASDataset) -> dict[str, Any]:
    """Summarize only records actually loaded from published files."""
    attack_counts = Counter(record.attack_type.value for record in dataset.records)
    scenario_counts = Counter(record.scenario for record in dataset.records)
    agent_counts = [len(record.agents) for record in dataset.records]
    return {
        "records": len(dataset.records),
        "benign_records": sum(record.is_benign for record in dataset.records),
        "adversarial_records": sum(record.is_adversarial for record in dataset.records),
        "attack_counts": dict(sorted(attack_counts.items())),
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "agent_count_min": min(agent_counts, default=0),
        "agent_count_max": max(agent_counts, default=0),
        "agent_specs_with_tools_field": sum(
            agent.tools is not None for record in dataset.records for agent in record.agents
        ),
        "records_with_agents_involved": sum(
            record.agents_involved is not None for record in dataset.records
        ),
        "records_with_expected_output": sum(
            record.expected_output is not None for record in dataset.records
        ),
        "records_with_sequence_of_tool_calls": sum(
            record.sequence_of_tool_calls is not None for record in dataset.records
        ),
        "tool_definitions": len(dataset.tool_definitions),
        "tool_definitions_by_agent": dict(sorted(Counter(
            tool.agent_role for tool in dataset.tool_definitions
        ).items())),
        "duplicate_tool_definitions": sorted({
            f"{tool.source_file}:{tool.name}"
            for tool in dataset.tool_definitions if tool.duplicate_definition
        }),
        "missing_tool_modules": list(dataset.missing_tool_modules),
        "source_files": list(dataset.source_files),
        "missing_release_files": list(dataset.missing_release_files),
        "complete_icml_benchmark": dataset.is_complete_icml_benchmark,
        "repository": dataset.repository,
        "commit": dataset.commit,
    }
