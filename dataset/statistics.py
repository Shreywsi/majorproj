"""Small command-line entry point for inspecting the downloaded TAMAS sample."""

import argparse
import json
from pathlib import Path

from dataset.download_tamas import DATASET_ROOT
from dataset.tamas_loader import dataset_statistics, load_tamas_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATASET_ROOT)
    args = parser.parse_args()
    dataset = load_tamas_dataset(args.data)
    print(json.dumps(dataset_statistics(dataset), indent=2))


if __name__ == "__main__":
    main()
