"""CLI runner for deterministic Stage 7C controlled claim-bearing experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from dataset.claim_bearing_agents import (
    AgentBehaviour,
    ScenarioName,
    claim_jsonl,
    evidence_jsonl,
    evaluator_jsonl,
    message_jsonl,
    run_claim_bearing_experiment,
    trace_jsonl,
    trust_policy_json,
)


ROOT = Path(__file__).resolve().parent
CONTROLLED_RUNS_DIR = ROOT / "data" / "controlled" / "runs"


def _output_directory(value: str | Path, root: Path = CONTROLLED_RUNS_DIR) -> Path:
    allowed_root = root.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        repository_relative = ROOT / candidate
        if (
            repository_relative == allowed_root
            or repository_relative.is_relative_to(allowed_root)
        ):
            candidate = repository_relative
        else:
            candidate = allowed_root / candidate
    resolved = candidate.resolve()
    if resolved != allowed_root and not resolved.is_relative_to(allowed_root):
        raise ValueError(
            f"Output directory must be the controlled runs directory or a child of "
            f"{allowed_root}."
        )
    return resolved


def _selected_scenarios(values: Sequence[str]) -> tuple[ScenarioName, ...] | None:
    if "all" in values:
        if len(values) != 1:
            raise ValueError("'all' cannot be combined with named scenarios.")
        return None
    try:
        return tuple(ScenarioName(value) for value in values)
    except ValueError as exc:
        raise ValueError(f"Unknown scenario: {exc}") from exc


def _selected_behaviours(values: Sequence[str] | None) -> tuple[AgentBehaviour, ...] | None:
    if values is None:
        return None
    try:
        return tuple(AgentBehaviour(value) for value in values)
    except ValueError as exc:
        raise ValueError(f"Unknown agent behaviour: {exc}") from exc


def write_detector_outputs(run, output_dir: Path) -> dict[str, Path]:
    """Write only runtime-visible traces, messages, claims, evidence, and trust keys."""
    outputs = {
        "traces": output_dir / f"{run.experiment_id}.traces.jsonl",
        "messages": output_dir / f"{run.experiment_id}.messages.jsonl",
        "claims": output_dir / f"{run.experiment_id}.claims.jsonl",
        "evidence": output_dir / f"{run.experiment_id}.evidence.jsonl",
        "trust": output_dir / f"{run.experiment_id}.trust.json",
    }
    contents = {
        "traces": trace_jsonl(run),
        "messages": message_jsonl(run),
        "claims": claim_jsonl(run),
        "evidence": evidence_jsonl(run),
        "trust": trust_policy_json(run),
    }
    for key, path in outputs.items():
        path.write_text(contents[key], encoding="utf-8")
    return outputs


def write_evaluator_sidecar(run, destination: Path) -> Path:
    """Write evaluator-only truth through an independent output path."""
    destination.write_text(evaluator_jsonl(run), encoding="utf-8")
    return destination


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic controlled claim-bearing agents and safe local tasks. "
            "No LLM or external tool calls are made."
        )
    )
    parser.add_argument("--seed", type=int, default=0, help="Non-negative reproducibility seed.")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=["all", *(scenario.value for scenario in ScenarioName)],
        default=["all"],
        help="One or more task scenarios, or 'all'.",
    )
    parser.add_argument(
        "--behaviours",
        nargs="+",
        choices=[behaviour.value for behaviour in AgentBehaviour],
        help="Per-agent behaviour assignments; remaining agents default to HONEST_AGENT.",
    )
    parser.add_argument(
        "--agents-per-task",
        type=int,
        default=5,
        help="Number of participating agents per task (minimum 3).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(CONTROLLED_RUNS_DIR),
        help="Controlled output directory or a child directory within it.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace files for the same deterministic experiment ID.",
    )
    args = parser.parse_args(argv)
    try:
        scenarios = _selected_scenarios(args.scenarios)
        behaviours = _selected_behaviours(args.behaviours)
        output_dir = _output_directory(args.output_dir)
        run = run_claim_bearing_experiment(
            seed=args.seed,
            scenarios=scenarios,
            behaviours=behaviours,
            agents_per_task=args.agents_per_task,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = output_dir / f"{run.experiment_id}.evaluator.jsonl"
        predicted_outputs = {
            output_dir / f"{run.experiment_id}.traces.jsonl",
            output_dir / f"{run.experiment_id}.messages.jsonl",
            output_dir / f"{run.experiment_id}.claims.jsonl",
            output_dir / f"{run.experiment_id}.evidence.jsonl",
            output_dir / f"{run.experiment_id}.trust.json",
            sidecar_path,
        }
        existing = sorted(path for path in predicted_outputs if path.exists())
        if existing and not args.overwrite:
            raise FileExistsError(
                "Experiment output already exists; pass --overwrite to replace: "
                + ", ".join(str(path) for path in existing)
            )
        outputs = write_detector_outputs(run, output_dir)
        write_evaluator_sidecar(run, sidecar_path)
    except (OSError, ValueError, FileExistsError) as exc:
        print(f"Experiment failed: {exc}", file=sys.stderr)
        return 2

    summary = {
        "experiment_id": run.experiment_id,
        "seed": run.seed,
        "tasks": len(run.traces),
        "participating_agents": run.participating_agents,
        "messages": len(run.messages),
        "assertions": len(run.assertions),
        "evidence_records": len(run.evidence),
        "simulated_tool_invocations": run.simulated_tool_invocations,
        "output_files": {
            **{name: str(path) for name, path in outputs.items()},
            "evaluator_sidecar": str(sidecar_path),
        },
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
