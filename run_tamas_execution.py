"""Run one safe, local TAMAS Finance execution-design validation pair."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from dataset.download_tamas import DATASET_ROOT
from dataset.execution_engine import SafeTAMASExecutionEngine
from dataset.experiment_schemas import DetectorTaskInput
from dataset.tamas_loader import TAMASAttack, load_tamas_dataset
from dataset.trace_store import write_trace_jsonl
from dataset.workflow_preview import InteractionMode

DEFAULT_TRACE_PATH = DATASET_ROOT / "runs" / "stage3_validation.jsonl"


def _opaque_task_id(record_id: str, seed: int, condition: str) -> str:
    digest = hashlib.sha256(f"{record_id}|{seed}|{condition}".encode()).hexdigest()[:20]
    return f"task-{digest}"


def run_validation_pair(
    data_directory: str | Path = DATASET_ROOT,
    destination: str | Path = DEFAULT_TRACE_PATH,
    attack: TAMASAttack = TAMASAttack.DIRECT_PROMPT_INJECTION,
    mode: InteractionMode | str = InteractionMode.ROUND_ROBIN,
    seed: int = 0,
) -> tuple[dict, dict]:
    dataset = load_tamas_dataset(data_directory)
    benign = next((record for record in dataset.records if record.is_benign), None)
    adversarial = next((record for record in dataset.records if record.attack_type == attack), None)
    if benign is None or adversarial is None:
        raise ValueError(
            f"Validation requires one benign and one {attack.value} record in the selected data."
        )

    engine = SafeTAMASExecutionEngine(dataset.tool_definitions, mode)
    summaries = []
    evaluator_rows = []
    for record, condition in ((benign, "benign"), (adversarial, "adversarial")):
        task = DetectorTaskInput.from_tamas_record(
            record,
            task_id=_opaque_task_id(record.record_id, seed, condition),
        )
        summary = engine.run(task, seed)
        write_trace_jsonl(summary.trace, destination)
        evaluator_rows.append({
            "trace_id": summary.trace.trace_id,
            "task_id": summary.trace.task_id,
            "source_file": record.source_file,
            "source_record_id": record.record_id,
            "tamas_record_label": record.ground_truth_label,
            "validation_condition": condition,
        })
        summaries.append({
            "condition": condition,
            "trace_id": summary.trace.trace_id,
            "task_id": summary.trace.task_id,
            "event_count": len(summary.trace.events),
            "scheduled_agent_ids": list(summary.scheduled_agent_ids),
            "simulated_tool_calls": summary.simulated_tool_calls,
            "llm_responses_generated": summary.llm_responses_generated,
            "external_actions_executed": summary.external_actions_executed,
            "task_completion_status": summary.task_completion_status,
        })
    evaluator_path = Path(destination).with_suffix(".evaluator.jsonl")
    evaluator_path.parent.mkdir(parents=True, exist_ok=True)
    with evaluator_path.open("a", encoding="utf-8") as output:
        for row in evaluator_rows:
            output.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return tuple(summaries)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATASET_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_TRACE_PATH)
    parser.add_argument(
        "--attack",
        choices=[
            attack.value for attack in TAMASAttack
            if attack not in (TAMASAttack.BENIGN,)
        ],
        default=TAMASAttack.DIRECT_PROMPT_INJECTION.value,
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in InteractionMode],
        default=InteractionMode.ROUND_ROBIN.value,
    )
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    summaries = run_validation_pair(
        args.data, args.out, TAMASAttack(args.attack), args.mode, args.seed
    )
    for summary in summaries:
        print(json.dumps(summary, indent=2))
    print(f"\nAppended two controlled local-simulation traces to {args.out}")
    print(
        "Evaluator-only record mapping was appended separately to "
        f"{args.out.with_suffix('.evaluator.jsonl')}"
    )
    print("No LLM responses or external actions were executed; task completion was not evaluated.")


if __name__ == "__main__":
    main()
