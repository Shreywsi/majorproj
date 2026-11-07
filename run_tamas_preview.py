"""Load actual TAMAS Finance records and print honest dry-run workflow previews."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from dataset.download_tamas import DATASET_ROOT
from dataset.tamas_loader import TAMASAttack, load_tamas_dataset
from dataset.workflow_preview import InteractionMode, preview_workflow


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATASET_ROOT)
    parser.add_argument(
        "--attack",
        choices=[attack.value for attack in TAMASAttack if attack != TAMASAttack.BENIGN],
        default=TAMASAttack.DIRECT_PROMPT_INJECTION.value,
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in InteractionMode],
        default=InteractionMode.ROUND_ROBIN.value,
    )
    args = parser.parse_args(argv)

    dataset = load_tamas_dataset(args.data)
    benign = next((record for record in dataset.records if record.is_benign), None)
    adversarial = next(
        (record for record in dataset.records if record.attack_type.value == args.attack),
        None,
    )
    if benign is None or adversarial is None:
        missing = []
        if benign is None:
            missing.append("a benign record")
        if adversarial is None:
            missing.append(f"attack category '{args.attack}'")
        raise SystemExit(
            f"Cannot preview {', '.join(missing)}; the selected dataset directory is incomplete."
        )
    previews = [
        preview_workflow(benign, args.mode),
        preview_workflow(adversarial, args.mode),
    ]
    for preview in previews:
        print(json.dumps(asdict(preview), indent=2, default=str))
    print(
        "\nThese are dataset-backed workflow scheduling previews, not TAMAS model "
        "evaluation results. No attacks or tools were executed."
    )


if __name__ == "__main__":
    main()
