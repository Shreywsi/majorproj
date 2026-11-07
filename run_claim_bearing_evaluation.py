"""Evaluate Stage 7D detector findings against separate ground-truth sidecars."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

from dataset.independent_evaluator import (
    EvaluationInputError,
    evaluate_claim_bearing_experiments,
)


ROOT = Path(__file__).resolve().parent
EVALUATION_ROOT = ROOT / "data" / "controlled" / "evaluation"


def _read_jsonl(paths: list[Path], label: str) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"{label} file does not exist: {path}")
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise EvaluationInputError(
                        f"Invalid {label} JSONL {path}:{line_number}: {exc}"
                    ) from exc
                if not isinstance(value, dict):
                    raise EvaluationInputError(
                        f"{label} JSONL {path}:{line_number} must contain an object."
                    )
                rows.append(value)
    return rows


def _write_outputs(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = result["summary"]
    (output_dir / "evaluation_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "confusion_matrix.json").write_text(
        json.dumps(result["confusion_matrix"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rows = result["assertion_metrics_rows"]
    fieldnames = [
        "dimension", "group", "assertions_total", "inconclusive_ground_truth",
        "binary_metric_denominator", "true_positives", "false_positives",
        "true_negatives", "false_negatives", "accuracy", "precision",
        "recall", "f1_score",
        *(f"outcome_{outcome.lower()}" for outcome in (
            "SUPPORTED", "UNSUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"
        )),
    ]
    with (output_dir / "assertion_metrics.csv").open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            metrics = {key: value for key, value in row.items() if key != "detector_outcome_counts"}
            metrics.update({
                f"outcome_{outcome.lower()}": row["detector_outcome_counts"][outcome]
                for outcome in (
                    "SUPPORTED", "UNSUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"
                )
            })
            writer.writerow(metrics)


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--detector-findings",
        type=Path,
        nargs="+",
        required=True,
        help="One or more detector_findings.jsonl files.",
    )
    parser.add_argument(
        "--cross-message-findings",
        type=Path,
        nargs="*",
        default=[],
        help="Cross-message files corresponding to the detector input experiments.",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        nargs="+",
        required=True,
        help="Evaluator-only sidecar JSONL files; never supplied to detection.",
    )
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = evaluate_claim_bearing_experiments(
            _read_jsonl(args.detector_findings, "Detector findings"),
            _read_jsonl(args.cross_message_findings, "Cross-message findings"),
            _read_jsonl(args.ground_truth, "Ground-truth sidecar"),
        )
        output_dir = args.out_dir
        if output_dir is None:
            ids = result["summary"]["experiment_ids"]
            output_dir = EVALUATION_ROOT / (ids[0] if len(ids) == 1 else "aggregate")
        _write_outputs(result, output_dir)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "experiment_ids": result["summary"]["experiment_ids"],
        "random_seeds": result["summary"]["random_seeds"],
        "assertions": result["summary"]["assertion_metrics"]["assertions_total"],
        "binary_metric_denominator": (
            result["summary"]["assertion_metrics"]["binary_metric_denominator"]
        ),
        "out_dir": str(output_dir),
    }, indent=2, sort_keys=True))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
