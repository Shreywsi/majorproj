"""Fetch the official ICML 2025 Finance sample to an ignored local data folder.

The upstream repository currently has no declared license. The script does not
vendor dataset text into source control; check upstream usage terms before
redistributing or publishing any downloaded records.
"""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

import truststore

from dataset.tamas_loader import (
    EXPECTED_FINANCE_RELEASE_FILES,
    OFFICIAL_COMMIT,
    OFFICIAL_REPOSITORY,
)

DATASET_ROOT = Path(__file__).resolve().parents[1] / "data" / "tamas" / "icml2025_finance"
API_ROOT = "https://api.github.com/repos/ameyar3103/MultiAgentHarmDataset/contents"
TOOL_FILES = (
    "tools_identity_verification_agent.py",
    "tools_market_analysis_agent.py",
    "tools_risk_management_agent.py",
    "tools_trade_execution_agent.py",
)


def _fetch(path: str) -> bytes:
    encoded_path = urllib.parse.quote(path, safe="/")
    url = f"{API_ROOT}/{encoded_path}?ref={OFFICIAL_COMMIT}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "TrustRoute-TAMAS-dataset-loader",
        },
    )
    truststore.inject_into_ssl()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to download official TAMAS file {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise RuntimeError(f"GitHub API returned an unexpected response for {path}.")
    try:
        return base64.b64decode(payload["content"], validate=False)
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"GitHub API returned invalid base64 for {path}.") from exc


def download_dataset(destination: str | Path = DATASET_ROOT, force: bool = False) -> dict:
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "download_manifest.json"
    previous_manifest = {}
    if manifest_path.exists():
        try:
            previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to read existing download manifest: {exc}") from exc
        if (
            not isinstance(previous_manifest, dict)
            or previous_manifest.get("repository") != OFFICIAL_REPOSITORY
            or previous_manifest.get("commit") != OFFICIAL_COMMIT
            or not isinstance(previous_manifest.get("files"), dict)
        ):
            raise RuntimeError(
                f"Existing manifest does not describe the pinned TAMAS source: {manifest_path}"
            )

    files = [*(f"data/{name}" for name in EXPECTED_FINANCE_RELEASE_FILES),
             *(f"data/{name}" for name in TOOL_FILES)]
    manifest = {
        "repository": OFFICIAL_REPOSITORY,
        "commit": OFFICIAL_COMMIT,
        "scenario_scope": ["finance"],
        "files": {},
    }
    for relative_path in files:
        filename = Path(relative_path).name
        target = output / filename
        expected = previous_manifest.get("files", {}).get(filename, {})
        reusable = (
            isinstance(expected, dict)
            and target.is_file()
            and not force
            and expected.get("source_path") == relative_path
            and expected.get("sha256") == hashlib.sha256(target.read_bytes()).hexdigest()
        )
        if reusable:
            content = target.read_bytes()
        else:
            content = _fetch(relative_path)
            target.write_bytes(content)
        manifest["files"][filename] = {
            "source_path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        print(f"{'verified' if reusable else 'downloaded'} {filename}")

    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "\nUpstream declares no dataset license. Downloaded files are local research "
        "inputs; verify usage terms before redistribution."
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DATASET_ROOT)
    parser.add_argument("--force", action="store_true", help="replace existing local files")
    args = parser.parse_args(argv)
    download_dataset(args.out, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
