# Stage 8 TAMAS Integration Report

## Status

The local TAMAS Finance sample and existing Stage 3–5 infrastructure were
examined separately from the controlled synthetic experiments. The Stage 3
local-simulation traces were passed through the existing Stage 5/TAMAS
provenance detection pipeline with no claims supplied. Both pipeline outputs
returned `NO_CLAIMS_AVAILABLE`.

| Item | Observed result |
|---|---:|
| TAMAS records loaded | 70 |
| Benign records | 20 |
| Adversarial records | 50 |
| Existing local-simulation traces loaded | 4 |
| Claims supplied / claim-bearing assertions available | 0 |
| Detector findings | 0 |
| Records with insufficient evidence | Not measurable |
| Claim-bearing real execution | No |

`Records with insufficient evidence` is not zero: no assertions were available
to assess. The detector correctly returned no findings and no four-outcome
assertion distribution. It would be inaccurate to infer either a per-record
evidence status or TAMAS detector accuracy from this run. The separate Stage 5
synthetic claim is not TAMAS ground truth and was excluded.

## Dataset-level labels and annotations

All 70 sample records are in the Finance scenario. The dataset loader reports
these record-level attack categories:

| Category | Records |
|---|---:|
| benign | 20 |
| colluding_agents | 10 |
| contradicting_agents | 10 |
| direct_prompt_injection | 10 |
| impersonation | 10 |
| indirect_prompt_injection | 10 |

Available annotations include 290 agent specifications across records, 50
records with `agents_involved`, 40 agent specifications with a `tools` field,
20 records with tool-call sequence annotations, 10 expected outputs, and 33
tool definitions. These are dataset annotations, not verified per-agent or
per-assertion behavioral truth. `agents_involved` was not treated as proof of
malicious behavior.

## What was exercised

The two existing trace files
(`stage3_validation.jsonl` and `provenanceguard_magentic_one_19.jsonl`) each
contained two traces. They are `controlled_local_simulation` runs: across the
four traces there were 14 simulated tool invocations, zero recorded external
tool invocations, and zero external actions. Detection outputs were preserved
as:

* [`stage3_validation_detection.json`](./data/tamas/stage8_integration_20261107/stage3_validation_detection.json)
* [`provenanceguard_magentic_one_detection.json`](./data/tamas/stage8_integration_20261107/provenanceguard_magentic_one_detection.json)

The machine-readable summary and annotation/category tables are:

* [`tamas_integration_summary.json`](./data/tamas/stage8_integration_20261107/tamas_integration_summary.json)
* [`tamas_attack_categories.csv`](./data/tamas/stage8_integration_20261107/tamas_attack_categories.csv)
* [`tamas_annotation_availability.csv`](./data/tamas/stage8_integration_20261107/tamas_annotation_availability.csv)

## Input isolation

The detector commands consumed trace inputs and did not receive TAMAS
filenames, record attack labels, expected outputs, reference tool-call
sequences, or evaluator information. The Stage 3 trace contents contained no
claim-bearing assertions; no claims were manufactured from traces. The
dashboard labels this view as TAMAS record-level integration and does not
display per-agent TAMAS accuracy.

## Limitations

This verifies the local loader, existing local-simulation traces, and
no-claims behavior of the detector pipeline. It does not evaluate detection
against the 70 record labels, because those labels do not establish claim-level
truth and no real claim-bearing TAMAS execution is available. The integration
therefore remains `NO_CLAIMS_AVAILABLE`, with no TAMAS precision, recall,
accuracy, or false-agent classification claim.
