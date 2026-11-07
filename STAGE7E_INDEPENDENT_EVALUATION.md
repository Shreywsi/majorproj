# Stage 7E: Independent Evaluation

Stage 7E adds
[`dataset/independent_evaluator.py`](./dataset/independent_evaluator.py) and
[`run_claim_bearing_evaluation.py`](./run_claim_bearing_evaluation.py). The
evaluator is a separate process-level component: it consumes Stage 7D detector
findings, the separately stored cross-message findings, and the evaluator-only
Stage 7C sidecar. Detection has already completed before this CLI is invoked;
ground truth is never fed back into detector inputs.

## Run

```bash
.venv/bin/python run_claim_bearing_evaluation.py \
  --detector-findings data/controlled/detection/EXPERIMENT/detector_findings.jsonl \
  --cross-message-findings data/controlled/detection/EXPERIMENT/cross_message_findings.jsonl \
  --ground-truth data/controlled/runs/demo/EXPERIMENT.evaluator.jsonl
```

For an aggregate over several independent seeds, provide all matching files:

```bash
.venv/bin/python run_claim_bearing_evaluation.py \
  --detector-findings data/controlled/detection/EXP1/detector_findings.jsonl \
                        data/controlled/detection/EXP2/detector_findings.jsonl \
                        data/controlled/detection/EXP3/detector_findings.jsonl \
  --cross-message-findings data/controlled/detection/EXP1/cross_message_findings.jsonl \
                            data/controlled/detection/EXP2/cross_message_findings.jsonl \
                            data/controlled/detection/EXP3/cross_message_findings.jsonl \
  --ground-truth data/controlled/runs/EXP1.evaluator.jsonl \
                 data/controlled/runs/EXP2.evaluator.jsonl \
                 data/controlled/runs/EXP3.evaluator.jsonl \
  --out-dir data/controlled/evaluation/aggregate
```

Outputs are kept in `data/controlled/evaluation/` and never replace source
traces, evidence, messages, claims, trust files, or sidecars:

- `evaluation_summary.json`
- `assertion_metrics.csv`
- `confusion_matrix.json`

Before matching, the evaluator rejects duplicate ground-truth or prediction
assertion IDs, unknown IDs, missing detector predictions, invalid outcome
names, and experiment/task/trace/agent/claim-type association mismatches.
Cross-message findings are also checked against known experiments and assertion
IDs. Multiple seeds are retained separately in grouped results.

## Metric definitions

Metrics are primary at the assertion level. The Stage 7C `matches_expected`
field maps to truthful (`true`) or false (`false`). An explicit
`ground_truth_label` may instead identify a case as truthful, false/contradicted,
or inconclusive/unsupported/not assessed. A missing or ambiguous label is
inconclusive. Inconclusive ground-truth assertions are counted and excluded
from binary confusion-matrix denominators.

Binary evaluation uses:

- Positive ground truth: a false assertion.
- Negative ground truth: a truthful assertion.
- Positive detector prediction: `CONTRADICTED` only.
- `SUPPORTED`, `UNSUPPORTED`, and `INSUFFICIENT_EVIDENCE` are not positive
  false-claim predictions. A false assertion with no contradiction is a false
  negative; it is not silently relabeled truthful.

The summary reports TP, FP, TN, FN, accuracy, precision, recall and F1. The
confusion-matrix file separately provides the binary matrix and the complete
comparison of ground-truth classes against all four detector outcomes.
`assertion_metrics.csv` includes overall metrics and results by claim type,
agent behavior, scenario, and random seed. Evidence verification failures are
counted by status separately from evidence-association failures (such as scope,
relevance, or missing-reference failures), while cross-message finding and
assertion-reference counts are reported separately.

Agent summaries are limited to false assertion counts and proportions. An agent
is never labeled malicious because of one assertion. In controlled runs, a
correct local simulation claim may differ from evidence sufficient to prove
external execution; the outcome comparison preserves that distinction.

## Isolation and scope

The detector CLI has no sidecar argument, does not import the evaluator, and
does not expose expected values, behavior labels, or ground-truth fields in
findings. The evaluation CLI reads sidecars only after detector outputs exist.
Do not pass sidecars to the detector or feed evaluation results back into
detection.

These are deterministic synthetic experiments, not TAMAS record-level labels,
per-agent TAMAS accuracy, real-world agent behavior, or external tool
execution. Report weak recall and other unfavorable results as measured; do
not manufacture success or infer agent malice. Trust-aware routing, R0–R8
integration, and later stages are outside this phase.
